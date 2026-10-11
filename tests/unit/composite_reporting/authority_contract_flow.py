"""Synthetic server-owned port fixtures: contract flow only, NEVER native authority proof."""

from datetime import UTC, datetime

from pydantic import SecretStr

from app.composite_reporting.invocation_authority import (
    BINDING_OPTION,
    AcceptedTwrAdmission,
    AuthenticatedTwrInitiator,
    TwrInvocation,
    bind_admission,
    digest,
    selector_set_for,
)
from app.composite_reporting.models import CompositeReportSelection, CompositeReviewJobRequest


def contract_admission(request, tenant=None, **updates):
    return AcceptedTwrAdmission(
        binding_reference="synthetic-contract-flow-only",
        issuer="synthetic-contract-flow-only",
        trust_config_digest="a" * 64,
        principal_kind="delegated",
        subject="synthetic-application:synthetic-person",
        delegated_application="synthetic-application",
        delegated_actor="synthetic-person",
        tenant_id=tenant or request.primary_selection.tenant_id,
        resolver_policy_version="synthetic-contract-flow.v1",
        effective_scope_digest="b" * 64,
        selector_set_digest=digest(selector_set_for(request)),
        admitted_at=datetime.now(UTC),
    ).model_copy(update=updates)


def bind_contract_job(job, request=None):
    request = request or CompositeReviewJobRequest.model_validate(
        {
            "selection": job.options.get("composite_selection"),
            "source_products": job.options.get("composite_source_products"),
            "source_context": job.options.get("composite_source_context"),
        }
    )
    options = {key: value for key, value in job.options.items() if key != BINDING_OPTION}
    options = bind_admission(
        options,
        contract_admission(request),
        request_id=job.request_id,
        job_id=job.job_id,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    return job.model_copy(update={"options": options})


class ContractFlowAuthority:
    """No signatures/IdP/grant verification. Explicit controlled-provider fixture."""

    def __init__(self):
        self.attempts = []
        self.current_updates = {}
        self.refused = False

    def admit(self, authenticated_initiator, tenant, selector_set):
        assert isinstance(authenticated_initiator, AuthenticatedTwrInitiator)
        request = CompositeReviewJobRequest(
            selection=CompositeReportSelection.model_validate(selector_set[0])
        )
        return contract_admission(request, tenant).model_copy(
            update={"selector_set_digest": digest(selector_set)}
        )

    def authorize_attempt(self, immutable_binding, exact_selector, attempt_context):
        self.attempts.append(attempt_context)
        if self.refused:
            raise ValueError("untrusted-provider-exception-and-secret-must-not-escape")
        selection = CompositeReportSelection.model_validate(exact_selector)
        return TwrInvocation(
            admission=immutable_binding.admission.model_copy(update=self.current_updates),
            exact_selector_digest=digest(exact_selector),
            request_payload_digest=digest(selection.performance_request()),
            credential_id="synthetic-rotation-" + str(len(self.attempts)),
            validated_at=datetime.now(UTC),
            credential=SecretStr("synthetic-contract-flow-credential-" + str(len(self.attempts))),
        )
