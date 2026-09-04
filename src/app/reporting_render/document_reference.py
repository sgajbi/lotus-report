"""The governed identity a client document carries (render#120 programme).

Report mints the document reference BEFORE rendering; lotus-render places it
verbatim in every family's footer (their #244) and refuses to invent or
coerce one; the evidence chain hands it onward with the exact bytes and SHA.
So the reference is the one name that follows a governed document from the
moment its content is decided to the moment it is archived.

It binds the financial question, never transport - the #262 identity
discipline applied to documents:

- **report job** - the lineage anchor;
- **snapshot** - the immutable evidence the document is composed from;
- **template identity** - which governed presentation of that evidence.

A RERENDER of the same snapshot with the same template is the same governed
document and converges on the same reference. A REGENERATE captures a new
snapshot and gets a new reference; a rerender under a corrected template is a
correction and carries its own identity, per the lifecycle's correction rule.
Per-attempt values - render_job_id, correlation, trace, timestamps - never
enter, because two attempts at the same document must not mint two names.

Locale, brand variant and output format are static constants today; if any
ever varies per order, whether it joins the identity is a recorded decision
for that change, not an accident of this module.

Format (recorded on render#120 and report#254): ``rdoc_<uuid5>`` where the
uuid is derived from the canonical JSON of the identity fields under a fixed
namespace. Opaque to every consumer; Render places it, Archive stores it,
nobody parses it.
"""

from __future__ import annotations

import hashlib
import json
import uuid

_DOCUMENT_REFERENCE_NAMESPACE = uuid.UUID("6c1f4bd2-8a1e-4e51-b7a3-2f9d0c5e8a41")


def mint_document_reference(
    *,
    report_job_id: str,
    snapshot_id: str,
    template_id: str,
    template_version: str,
) -> str:
    identity = {
        "report_job_id": report_job_id,
        "snapshot_id": snapshot_id,
        "template_id": template_id,
        "template_version": template_version,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return f"rdoc_{uuid.uuid5(_DOCUMENT_REFERENCE_NAMESPACE, canonical)}"


def derive_archive_request_id(document_reference: str, artifact_sha256: str) -> str:
    """One custody request per (financial question, exact bytes).

    Byte-for-byte the same derivation as lotus-render's
    ``archive_handoff.derive_archive_request_id`` (the delivering authority):
    any holder of the same reference and the same artifact digest computes the
    same id, so Report can reconcile custody by request id without the render
    response carrying it.
    """

    normalized = artifact_sha256.strip().lower().removeprefix("sha256:")
    digest = hashlib.sha256(f"{document_reference}\n{normalized}".encode()).hexdigest()
    return f"areq_{digest[:32]}"
