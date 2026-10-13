"""Independent, resealed single-fault authoring controls for native notice admission."""

from copy import deepcopy

from app.composite_reporting.correction_notice.hashing import (
    digest,
    document_preimage,
    evidence_preimage,
    manifest_preimage,
    pair_preimage,
)
from app.composite_reporting.correction_notice.projection import derive_facts, project_tables


def reseal(data, *, project=True):
    for node in data["archive_path"]["nodes"]:
        node["document_response_digest"] = digest(document_preimage(node["document_response"]))
    for edge in data["archive_path"]["edges"]:
        ack = edge["acknowledgement"]
        ack["digest"] = digest(evidence_preimage(ack["kind"], ack["raw_payload"]))
    data["pair_fingerprint"] = digest(pair_preimage(data))
    data["manifest_digest"] = digest(manifest_preimage(data))
    if project:
        data["notice_facts"] = derive_facts(data)
        data["tables"] = project_tables(data)
    return data


CASES = (
    "OLD_POST_KIND",
    "DYNAMIC_HEAD_FIELD",
    "NAIVE_TIMESTAMP",
    "MISSING_RECORD_REASON",
    "WRONG_RELATIONSHIP",
    "TECHNICAL_ECONOMIC_PROMOTION",
    "PURGED_SOURCE",
    "SOURCE_HASH",
    "DUPLICATE_COORDINATE",
    "OMITTED_COORDINATE",
    "WRONG_CELL_REF",
    "CHANGED_SOURCE_POLICY",
    "OMITTED_SIDE_CELL",
    "NULL_ZERO_SUBSTITUTE",
    "MATERIALITY_INFERENCE",
)


def refusal_variant(notice, case):
    data = deepcopy(notice)
    edge = data["archive_path"]["edges"][1]
    raw = edge["acknowledgement"]["raw_payload"]
    project = True
    if case == "OLD_POST_KIND":
        edge["acknowledgement"]["kind"] = "ARCHIVE_RESPONSE"
    elif case == "DYNAMIC_HEAD_FIELD":
        raw["current_document_id"] = edge["target_document_id"]
    elif case == "NAIVE_TIMESTAMP":
        raw["requested_at"] = "2026-10-11T19:30:22.153880"
    elif case == "MISSING_RECORD_REASON":
        del raw["transition_reason"]
    elif case == "WRONG_RELATIONSHIP":
        raw["lifecycle_relationship_id"] = "life_unknown"
    elif case == "TECHNICAL_ECONOMIC_PROMOTION":
        data["archive_path"]["edges"][0]["semantic_role"] = "ECONOMIC_CORRECTION"
    elif case == "PURGED_SOURCE":
        data["archive_path"]["nodes"][0]["document_response"]["purge_status"] = "purged"
    elif case == "SOURCE_HASH":
        data["generations"]["before"]["selection"]["snapshot_hash"] = "sha256:" + "0" * 64
    elif case in ("DUPLICATE_COORDINATE", "OMITTED_COORDINATE", "WRONG_CELL_REF"):
        if case == "DUPLICATE_COORDINATE":
            data["pair_index"][-1] = deepcopy(data["pair_index"][0])
        elif case == "OMITTED_COORDINATE":
            data["pair_index"].pop()
        else:
            data["pair_index"][0]["before_ref"]["row_index"] = 999
    elif case in ("CHANGED_SOURCE_POLICY", "OMITTED_SIDE_CELL", "NULL_ZERO_SUBSTITUTE"):
        project = False
        table = data["tables"][0]
        if case == "CHANGED_SOURCE_POLICY":
            table["columns"][0]["label"] = "Altered source policy"
        elif case == "OMITTED_SIDE_CELL":
            del table["rows"][0]["cells"]["return"]
        else:
            table["rows"][0]["cells"]["authority_receipt"]["canonical_value"] = "0"
    elif case == "MATERIALITY_INFERENCE":
        data["obligations"]["materiality"]["reason_code"] = "REASON_IMPLIES_MATERIALITY"
    else:
        raise ValueError("Unknown controlled refusal case")
    return reseal(data, project=project)
