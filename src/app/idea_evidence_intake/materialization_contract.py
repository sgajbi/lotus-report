IDEA_EVIDENCE_MATERIALIZATION_ROUTE = "POST /reports/idea-evidence-packs/materializations"
IDEA_EVIDENCE_MATERIALIZATION_RECOVERY_ROUTE = "GET /reports/idea-evidence-packs/materializations"
IDEA_EVIDENCE_MATERIALIZATION_EVIDENCE_REFS = (
    IDEA_EVIDENCE_MATERIALIZATION_ROUTE,
    IDEA_EVIDENCE_MATERIALIZATION_RECOVERY_ROUTE,
    "contracts/idea-evidence-materialization/"
    "lotus-report-idea-evidence-pack-materialization.v1.json",
    "src/app/idea_evidence_intake/recovery.py",
    "src/app/routers/idea_evidence_intake.py",
    "src/app/reporting_lineage/capture_service.py",
    "src/app/reporting_render/package_builder.py",
    "tests/unit/test_idea_evidence_materialization_contract.py",
    "tests/unit/test_idea_evidence_recovery.py",
    "tests/integration/test_idea_evidence_intake_api.py",
)
IDEA_EVIDENCE_MATERIALIZATION_REMAINING_BLOCKERS = (
    "client_publication_authority_blocked",
    "supported_feature_promotion_missing",
)
IDEA_MATERIALIZATION_RECOVERY_IDENTITY_OPTION = "idea_materialization_recovery_identity"
