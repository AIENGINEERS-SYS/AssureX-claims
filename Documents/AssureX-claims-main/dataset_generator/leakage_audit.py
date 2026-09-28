"""Read-only dataset checks; never relabel or silently repartition training data."""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

# Explicit selection stops newly added adjudication columns entering model training.
FEATURE_COLUMNS = (
    "region_zone", "state", "city", "customer_type", "seller_type", "purchase_channel",
    "product_category", "brand", "model_family", "purchase_price_ngn", "payment_method",
    "warranty_provider", "policy_code", "warranty_start_basis", "warranty_months",
    "days_from_purchase_to_claim", "fault_category", "damage_cause", "receipt_available",
    "purchase_document_type", "warranty_card_available", "serial_or_imei_available", "serial_evidence_type",
    "product_photo_available", "repair_report_available", "fault_evidence_type", "prior_repairs",
    "prior_repair_authorized", "return_window_days", "reporting_delay_days", "policy_reporting_deadline_days",
    "requested_remedy", "claim_amount_ngn", "document_completeness_score", "claim_channel",
    "manufacture_to_purchase_days", "purchase_to_fault_days", "fault_to_claim_days", "claim_month",
)


def select_features(frame):
    """Use a fixed contract even if future CSVs contain reviewer or payout columns."""
    missing = set(FEATURE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError("Missing training features: " + ", ".join(sorted(missing)))
    return frame.loc[:, list(FEATURE_COLUMNS)].copy()


def audit_dataset(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("Dataset is empty")
    splits = Counter(r["split"] for r in rows)
    features = [key for key in FEATURE_COLUMNS if key in rows[0]]
    groups = defaultdict(set)
    for row in rows:
        groups[tuple(row[k] for k in features)].add(row["split"])
    periods = {s: [min(r["claim_date"] for r in rows if r["split"] == s),
                   max(r["claim_date"] for r in rows if r["split"] == s)] for s in splits}
    return {"rows": len(rows), "split_counts": dict(splits),
        "duplicate_claim_ids": len(rows) - len({r["claim_id"] for r in rows}),
        "duplicate_provenance_ids": len(rows) - len({r["provenance_ref"] for r in rows}),
        "feature_groups_across_splits": sum(len(s) > 1 for s in groups.values()),
        "claim_date_ranges": periods,
        "chronological_holdout": bool("train" in periods and "test" in periods and
            max(periods[s][1] for s in periods if s != "test") < periods["test"][0]),
        "limitations": ["No stable product/customer group identifiers; entity leakage cannot be ruled out.",
            "Feature event timestamps are absent; point-in-time availability needs source provenance."]}


if __name__ == "__main__":
    print(json.dumps(audit_dataset(Path(__file__).resolve().parents[1] / "data" / "assurex_nigeria_warranty_claims_v2.csv"), indent=2))
