"""Generate reviewable native producer evidence from immutable retained test captures.

This never reads or seeds a runtime database, calls a service, or claims a
registered delivery. The output is a local authoring candidate for review.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.composite_reporting.correction_notice.contract import (  # noqa: E402
    CorrectionNoticeData,
    OwnNoticeCustodyBinding,
)
from app.composite_reporting.correction_notice.hashing import (  # noqa: E402
    canonical_bytes,
    document_preimage,
    evidence_preimage,
    manifest_preimage,
    pair_preimage,
)
from app.composite_reporting.eligibility_tables import (  # noqa: E402
    CAPACITY_POLICY,
    eligibility_workbook_projection,
    preflight_eligibility_package,
)
from app.composite_reporting.render_package import build_composite_render_package  # noqa: E402
from tests.unit.composite_notice_fixture import (  # noqa: E402
    FIXTURES,
    notice_fixture,
    notice_job_and_capture,
    retained_bytes,
)
from tests.unit.composite_notice_refusals import CASES, refusal_variant  # noqa: E402


def generate(destination: Path, r5_baseline: Path | None = None) -> dict:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Refusing to overwrite an existing evidence revision")
    destination.mkdir(parents=True, exist_ok=True)
    notice = notice_fixture()
    job, snapshot = notice_job_and_capture(notice)
    package = build_composite_render_package(
        job=job,
        snapshot=notice,
        render_job_id="rdr_notice_test",
        snapshot_id=snapshot.snapshot_id,
        report_revision_id=snapshot.report_revision_id,
        snapshot_record=snapshot,
    )
    totals = preflight_eligibility_package(package, failure_prefix="COMPOSITE_NOTICE")
    files = {}

    def emit(name, value):
        data = canonical_bytes(value)
        (destination / name).write_bytes(data)
        files[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    emit("composite_review.v9.schema.json", CorrectionNoticeData.model_json_schema())
    emit("composite_notice_custody.v9.schema.json", OwnNoticeCustodyBinding.model_json_schema())
    emit("valid-full-render-package.json", package)
    emit("synthetic-notice-job.json", job.model_dump(mode="json"))
    emit("synthetic-notice-capture.json", snapshot.model_dump(mode="json"))
    preimages = {"pair": pair_preimage(notice), "manifest": manifest_preimage(notice)}
    for index, node in enumerate(notice["archive_path"]["nodes"]):
        preimages[f"document-{index}"] = document_preimage(node["document_response"])
    for index, edge in enumerate(notice["archive_path"]["edges"]):
        ack = edge["acknowledgement"]
        preimages[f"acknowledgement-{index}"] = evidence_preimage(ack["kind"], ack["raw_payload"])
    for name, preimage in preimages.items():
        emit(f"{name}.preimage.json", preimage)
    emit(
        "known-answer-digests.json",
        {
            name: "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()
            for name, value in preimages.items()
        },
    )
    emit("workbook-projection.json", eligibility_workbook_projection(package))
    refusals = []
    for case in CASES:
        variant = refusal_variant(notice, case)
        try:
            CorrectionNoticeData.model_validate(variant)
        except ValueError:
            emit("refusal-single-fault-" + case.lower() + ".json", variant)
            refusals.append(
                {
                    "case": case,
                    "native_admission": "REFUSED",
                    "input_layer": "report_data",
                    "wrappers_resealed": True,
                }
            )
        else:
            raise AssertionError("Native guard accepted controlled fault: " + case)
    emit("refusal-categories.json", refusals)
    if r5_baseline is not None:
        original_bytes = r5_baseline.read_bytes()
        original_package = json.loads(original_bytes)
        original = original_package["report_data"]
        preserved = {
            "complete_generations": original["generations"] == notice["generations"],
            "all_28_source_side_tables": original["tables"][:28] == notice["tables"][:28],
            "complete_coordinate_union": original["pair_index"] == notice["pair_index"],
            "eight_independent_obligations": original["obligations"] == notice["obligations"],
            "four_document_nodes": original["archive_path"]["nodes"]
            == notice["archive_path"]["nodes"],
            "both_technical_acknowledgements": all(
                original["archive_path"]["edges"][index] == notice["archive_path"]["edges"][index]
                for index in (0, 2)
            ),
            "six_field_source_custody": original_package["render_context"]["archive"][
                "composite_report_identity"
            ]["generations"]
            == package["render_context"]["archive"]["composite_report_identity"]["generations"],
        }
        if not all(preserved.values()):
            raise AssertionError("R6 amendment changed protected r5 evidence")
        emit(
            "r5-r6-preservation-proof.json",
            {
                "r5_package_sha256": hashlib.sha256(original_bytes).hexdigest(),
                "protected_evidence_equal": preserved,
                "changed": (
                    "Economic acknowledgement kind, eight-field record representation and "
                    "source-capture label; dependent pair/manifest/own identity and generated "
                    "digest cells follow the unchanged recipes."
                ),
            },
        )
    emit(
        "producer-budget-proof.json",
        {
            "state": "NATIVE_PRODUCER_PREFLIGHT_ONLY",
            "measured": totals,
            "limits": CAPACITY_POLICY,
            "historical_nine_capture_bytes": sum(
                len(retained_bytes(path.name.removesuffix(".json")))
                for path in FIXTURES.glob("*.json")
                if "request.bin" not in path.name and "-job-" not in path.name
            ),
            "writer_output_size": "NOT_EXECUTED",
            "live_capture_delivery": "NOT_EXECUTED",
            "source_authority": "IMMUTABLE_TEST_CAPTURES_ONLY",
            "economic_record_fixture": "CONTROLLED_TEST_RELATIONSHIP_RECORD_NOT_API_READ",
            "archive197_dto_agreement": "NATIVE_SCHEMA_KAT_ACCEPTED_AT_036c1412",
        },
    )
    manifest = {"state": "NATIVE_CONTRACT_FROZEN_OFFLINE_ONLY", "files": files}
    (destination / "manifest.json").write_bytes(canonical_bytes(manifest))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--r5-baseline", type=Path)
    arguments = parser.parse_args()
    result = generate(arguments.output.resolve(), arguments.r5_baseline)
    print(json.dumps({"files": len(result["files"]), "state": result["state"]}))
