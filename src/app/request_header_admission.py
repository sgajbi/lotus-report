"""One raw-header cardinality policy for caller authority and replay identity."""

SINGLETON_REQUEST_HEADERS = frozenset(
    {
        "authorization",
        "x-service-identity",
        "x-actor-id",
        "x-caller-application",
        "x-tenant-id",
        "x-region",
        "x-booking-center-code",
        "x-role",
        "x-correlation-id",
        "x-trace-id",
        "x-request-id",
        "traceparent",
        "idempotency-key",
    }
)


class AmbiguousRequestHeadersError(ValueError):
    def __init__(self, names: list[str], unambiguous_headers: dict[str, str]) -> None:
        super().__init__("ambiguous_request_headers")
        self.names = names
        self.unambiguous_headers = unambiguous_headers


def admitted_request_headers(raw_headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    """Reject every repeated scalar, including equal values; join capability lists."""
    values: dict[str, list[str]] = {}
    for raw_name, raw_value in raw_headers:
        name = raw_name.decode("latin-1").lower()
        values.setdefault(name, []).append(raw_value.decode("latin-1"))
    ambiguous = sorted(name for name in SINGLETON_REQUEST_HEADERS if len(values.get(name, [])) > 1)
    if ambiguous:
        raise AmbiguousRequestHeadersError(
            ambiguous, {name: parts[0] for name, parts in values.items() if len(parts) == 1}
        )
    return {
        name: ",".join(parts) if name == "x-capabilities" else parts[0]
        for name, parts in values.items()
    }
