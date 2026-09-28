"""Training feature selection must stay allowlisted when adjudication columns appear."""
from pathlib import Path
from dataset_generator.leakage_audit import FEATURE_COLUMNS, audit_dataset


def test_feature_contract_excludes_outcomes_and_post_adjudication_fields():
    assert not set(FEATURE_COLUMNS) & {"claim_class", "final_decision", "reviewer_result", "payout_result",
        "fraud_confirmation", "future_events", "warranty_status", "duplicate_claim_indicator", "contradiction_flag"}
    assert len(FEATURE_COLUMNS) == len(set(FEATURE_COLUMNS))


def test_dataset_identity_and_duplicate_split_leakage():
    report = audit_dataset(Path("data/assurex_nigeria_warranty_claims_v2.csv"))
    assert report["duplicate_claim_ids"] == report["duplicate_provenance_ids"] == 0
    assert report["feature_groups_across_splits"] == 0
    assert set(report["split_counts"]) == {"train", "validation", "test"}
