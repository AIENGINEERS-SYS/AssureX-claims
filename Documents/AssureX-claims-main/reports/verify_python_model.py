"""Read-only real-artifact verification. Run from the repository root with ML dependencies."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify():
    import joblib
    import numpy as np
    import pandas as pd
    import sklearn
    import xgboost
    from backend.services.predictions import PythonBundlePredictor, CLASSES
    from dataset_generator.leakage_audit import FEATURE_COLUMNS, audit_dataset
    artifact = ROOT / "models" / "assurex_xgboost_final.joblib"
    bundle = joblib.load(artifact)
    frame = pd.read_csv(ROOT / "data" / "assurex_nigeria_warranty_claims_v2.csv")
    for column in ("manufacture_date", "purchase_date", "fault_date", "claim_date"):
        frame[column] = pd.to_datetime(frame[column], errors="raise")
    frame["manufacture_to_purchase_days"] = (frame.purchase_date - frame.manufacture_date).dt.days
    frame["purchase_to_fault_days"] = (frame.fault_date - frame.purchase_date).dt.days
    frame["fault_to_claim_days"] = (frame.claim_date - frame.fault_date).dt.days
    frame["claim_month"] = frame.claim_date.dt.month
    if set(bundle["feature_columns"]) != set(FEATURE_COLUMNS):
        raise ValueError("Artifact feature contract differs from the allowlist")
    records = frame.loc[frame.split.eq("test"), bundle["feature_columns"]].to_dict("records")
    predictor = PythonBundlePredictor(str(artifact), model_version_id=0)
    results = [predictor.predict(r) for r in records]
    from backend.services.predictions import probabilities
    checked = [probabilities(r["probabilities"]) for r in results]
    with ThreadPoolExecutor(max_workers=4) as pool:
        concurrent = list(pool.map(predictor.predict, records[:8]))
    assert concurrent == results[:8], "Concurrent inference differs from sequential inference"
    labels = {"Likely Valid": "valid", "Likely Invalid": "invalid", "Manual Review Required": "manual_review"}
    expected = [labels[v] for v in frame.loc[frame.split.eq("test"), "claim_class"]]
    report = {"artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "test_rows": len(checked), "accuracy_on_existing_test_split": sum(r["predicted_class"] == y for r, y in zip(checked, expected)) / len(checked),
        "label_mapping": bundle["label_encoder"].classes_.tolist(), "feature_count": len(FEATURE_COLUMNS),
        "all_distributions_finite_and_normalized": True, "concurrent_calls_match": True,
        "runtime_versions": {"numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__, "xgboost": xgboost.__version__},
        "dataset_audit": audit_dataset(ROOT / "data" / "assurex_nigeria_warranty_claims_v2.csv")}
    return report


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2, allow_nan=False))
