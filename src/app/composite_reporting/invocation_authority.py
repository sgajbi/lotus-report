"""Server-owned TWR invocation port; deployment owns verified identity and credentials.

These types enforce Report's binding contract, not signatures or bank authentication.
The unavailable default is intentional until a trusted deployment adapter is installed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, model_validator

if TYPE_CHECKING:
    from app.composite_reporting.models import CompositeReportSelection, CompositeReviewJobRequest
    from app.reporting_jobs.models import ReportJobLedgerRecord

PROVIDER_REFERENCE = "report.composite-twr.invocation.v1"
BINDING_OPTION = "composite_twr_invocation_binding"


class TwrAuthorityRefused(ValueError):
    """Product-safe refusal, without credential or provider exception text."""


class TwrCaptureRetryable(RuntimeError):
    """Only transient read transport/status failures yield to the durable worker."""

    def __init__(self) -> None:
        super().__init__("Transient TWR source read unavailable; durable retry required.")


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AuthenticatedTwrInitiator(_ImmutableModel):
    """Only trusted server context constructs this; transport diagnostics never do."""

    context_reference: str = Field(min_length=1, max_length=200)


class AcceptedTwrAdmission(_ImmutableModel):
    schema_version: Literal["composite-twr-admission.v1"] = "composite-twr-admission.v1"
    provider_reference: Literal["report.composite-twr.invocation.v1"] = (
        "report.composite-twr.invocation.v1"
    )
    binding_reference: str = Field(min_length=1, max_length=200)
    issuer: str = Field(min_length=1, max_length=200)
    audience: Literal["lotus-performance"] = "lotus-performance"
    trust_config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    principal_kind: Literal["service", "delegated"]
    subject: str = Field(min_length=1, max_length=200)
    delegated_application: str | None = Field(default=None, min_length=1, max_length=200)
    delegated_actor: str | None = Field(default=None, min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=200, pattern=r"^[!-+\--~]+$")
    method: Literal["POST"] = "POST"
    endpoint: Literal["/performance/composites/twr"] = "/performance/composites/twr"
    capability: Literal["operations.runtime.read"] = "operations.runtime.read"
    resolver_policy_version: str = Field(min_length=1, max_length=200)
    effective_scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    selector_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_revision: str | None = Field(default=None, min_length=1, max_length=200)
    admitted_at: AwareDatetime

    @model_validator(mode="after")
    def require_principal_shape(self) -> AcceptedTwrAdmission:
        delegated = self.delegated_application is not None and self.delegated_actor is not None
        if self.principal_kind == "delegated" and not delegated:
            raise ValueError("COMPOSITE_TWR_DELEGATION_REQUIRED")
        if self.principal_kind == "service" and (
            self.delegated_application is not None or self.delegated_actor is not None
        ):
            raise ValueError("COMPOSITE_TWR_SERVICE_DELEGATION_CONFLICT")
        return self

    def security_identity(self) -> dict[str, Any]:
        # Admission time is evidence, not a new principal or scope on retry.
        return self.model_dump(mode="json", exclude={"admitted_at"})


class TwrJobBinding(_ImmutableModel):
    admission: AcceptedTwrAdmission
    request_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)


@dataclass(frozen=True)
class TwrAttemptContext:
    job_id: str
    request_id: str
    correlation_id: str
    trace_id: str
    attempt_id: str


@dataclass(frozen=True)
class TwrInvocation:
    """Ephemeral provider output. Never persist or serialize this object."""

    admission: AcceptedTwrAdmission
    exact_selector_digest: str
    request_payload_digest: str
    credential_id: str
    validated_at: datetime
    credential: SecretStr = field(repr=False)

    def require_request(self, payload: dict[str, Any], tenant_id: str) -> None:
        if (
            not isinstance(self.admission, AcceptedTwrAdmission)
            or not isinstance(self.credential, SecretStr)
            or not isinstance(self.validated_at, datetime)
            or not isinstance(self.credential_id, str)
            or not isinstance(self.exact_selector_digest, str)
            or not isinstance(self.request_payload_digest, str)
        ):
            raise TwrAuthorityRefused("COMPOSITE_TWR_INVOCATION_INVALID")
        token = self.credential.get_secret_value()
        if (
            not isinstance(token, str)
            or not token
            or any(char.isspace() or not 33 <= ord(char) <= 126 for char in token)
            or self.admission.tenant_id != tenant_id
            or digest(payload) != self.request_payload_digest
            or not self.credential_id
            or len(self.credential_id) > 200
            or self.validated_at.tzinfo is None
        ):
            raise TwrAuthorityRefused("COMPOSITE_TWR_INVOCATION_INVALID")


class CompositeTwrInvocationProvider(Protocol):
    def admit(
        self,
        authenticated_initiator: AuthenticatedTwrInitiator,
        tenant: str,
        selector_set: tuple[dict[str, Any], ...],
    ) -> AcceptedTwrAdmission: ...

    def authorize_attempt(
        self,
        immutable_binding: TwrJobBinding,
        exact_selector: dict[str, Any],
        attempt_context: TwrAttemptContext,
    ) -> TwrInvocation: ...


class UnavailableTwrInvocationProvider:
    def admit(
        self,
        authenticated_initiator: AuthenticatedTwrInitiator,
        tenant: str,
        selector_set: tuple[dict[str, Any], ...],
    ) -> AcceptedTwrAdmission:
        raise TwrAuthorityRefused("COMPOSITE_TWR_AUTHORITY_UNAVAILABLE")

    def authorize_attempt(
        self,
        immutable_binding: TwrJobBinding,
        exact_selector: dict[str, Any],
        attempt_context: TwrAttemptContext,
    ) -> TwrInvocation:
        raise TwrAuthorityRefused("COMPOSITE_TWR_AUTHORITY_UNAVAILABLE")


_configured_provider: CompositeTwrInvocationProvider = UnavailableTwrInvocationProvider()


def configure_composite_twr_invocation_provider(provider: CompositeTwrInvocationProvider) -> None:
    """Trusted startup composition only; stores the port, never a resolved grant/token.

    Configure each API/worker process before serving work. No public route, config
    import string or request option invokes registration.
    """
    global _configured_provider
    _configured_provider = provider


def get_composite_twr_invocation_provider() -> CompositeTwrInvocationProvider:
    """Existing service-factory DI seam, replaced only by trusted deployment composition.

    No environment-selected import, public provider option or credential fallback.
    API dependency overrides alone do not provision a worker's authority provider.
    """
    return _configured_provider


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def selector_set_for(request: CompositeReviewJobRequest) -> tuple[dict[str, Any], ...]:
    if request.selection is None:
        return ()
    selections = [
        request.selection,
        *(product.selection for product in request.source_products or []),
    ]
    # Canonical exact full pins, including custody fields that aren't sent in the POST body.
    return tuple(sorted((item.model_dump(mode="json") for item in selections), key=digest))


def require_admission(
    request: CompositeReviewJobRequest, tenant: str, admission: AcceptedTwrAdmission | None
) -> None:
    selectors = selector_set_for(request)
    if not selectors:
        if admission is not None:
            raise TwrAuthorityRefused("COMPOSITE_TWR_UNEXPECTED_BINDING")
        return
    if (
        not isinstance(admission, AcceptedTwrAdmission)
        or admission.tenant_id != tenant
        or admission.selector_set_digest != digest(selectors)
    ):
        raise TwrAuthorityRefused("COMPOSITE_TWR_ADMISSION_REQUIRED")


def admit_request(
    request: CompositeReviewJobRequest,
    initiator: AuthenticatedTwrInitiator | None,
    tenant: str,
    provider: CompositeTwrInvocationProvider,
) -> AcceptedTwrAdmission | None:
    selectors = selector_set_for(request)
    if not selectors:
        return None
    if not isinstance(initiator, AuthenticatedTwrInitiator):
        raise TwrAuthorityRefused("COMPOSITE_TWR_AUTHENTICATED_INITIATOR_REQUIRED")
    try:
        admission = provider.admit(initiator, tenant, selectors)
    except Exception:
        raise TwrAuthorityRefused("COMPOSITE_TWR_AUTHORITY_UNAVAILABLE") from None
    require_admission(request, tenant, admission)
    return admission


def require_internal_admission(
    request: object,
    options: dict[str, Any],
    tenant: str,
    admission: AcceptedTwrAdmission | None,
) -> None:
    from app.composite_reporting.models import CompositeReviewJobRequest

    if BINDING_OPTION in options:
        raise TwrAuthorityRefused("COMPOSITE_TWR_CALLER_BINDING_FORBIDDEN")
    if isinstance(request, CompositeReviewJobRequest):
        require_admission(request, tenant, admission)
    elif admission is not None:
        raise TwrAuthorityRefused("COMPOSITE_TWR_UNEXPECTED_BINDING")


def bind_admission(
    options: dict[str, Any], admission: AcceptedTwrAdmission | None, **identity: str
) -> dict[str, Any]:
    if BINDING_OPTION in options:
        raise TwrAuthorityRefused("COMPOSITE_TWR_CALLER_BINDING_FORBIDDEN")
    if admission is None:
        return options
    binding = TwrJobBinding(admission=admission, **identity)
    return {**options, BINDING_OPTION: binding.model_dump(mode="json")}


def binding_for_job(job: ReportJobLedgerRecord) -> TwrJobBinding:
    try:
        binding = TwrJobBinding.model_validate(job.options.get(BINDING_OPTION))
    except ValueError:
        raise TwrAuthorityRefused("COMPOSITE_TWR_JOB_BINDING_REQUIRED") from None
    if (
        binding.job_id != job.job_id
        or binding.request_id != job.request_id
        or binding.correlation_id != job.correlation_id
        or binding.trace_id != job.trace_id
        or binding.admission.tenant_id != job.tenant_id
    ):
        raise TwrAuthorityRefused("COMPOSITE_TWR_JOB_BINDING_CONFLICT")
    return binding


def require_same_admission(
    job: ReportJobLedgerRecord, admission: AcceptedTwrAdmission | None
) -> None:
    if admission is None and BINDING_OPTION not in job.options:
        return
    binding = binding_for_job(job)
    if admission is None or binding.admission.security_identity() != admission.security_identity():
        raise TwrAuthorityRefused("COMPOSITE_TWR_REPLAY_AUTHORITY_CONFLICT")


def authorize_selection(
    job: ReportJobLedgerRecord,
    selection: CompositeReportSelection,
    provider: CompositeTwrInvocationProvider,
    attempt_context: TwrAttemptContext,
) -> TwrInvocation:
    binding = binding_for_job(job)
    exact = selection.model_dump(mode="json")
    try:
        invocation = provider.authorize_attempt(binding, exact, attempt_context)
    except Exception:
        raise TwrAuthorityRefused("COMPOSITE_TWR_CURRENT_AUTHORITY_REFUSED") from None
    if not isinstance(invocation, TwrInvocation):
        raise TwrAuthorityRefused("COMPOSITE_TWR_CURRENT_AUTHORITY_CONFLICT")
    invocation.require_request(selection.performance_request(), job.tenant_id)
    if (
        invocation.admission.security_identity() != binding.admission.security_identity()
        or invocation.exact_selector_digest != digest(exact)
    ):
        raise TwrAuthorityRefused("COMPOSITE_TWR_CURRENT_AUTHORITY_CONFLICT")
    return invocation
