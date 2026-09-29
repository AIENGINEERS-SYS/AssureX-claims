import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "dataset_generator" / "prepare_claim_dataset.py"


def write_csv(path: Path, rows: list[dict[str, str]]):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["claim_id", "product_category", "claim_class", "split", "provenance_ref"])
        writer.writeheader()
        writer.writerows(rows)


def test_prepares_existing_srs_split_with_manifest(tmp_path):
    source = ROOT / "data" / "assurex_nigeria_warranty_claims_v2.csv"
    output = tmp_path / "prepared"
    result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)],
                            check=True, text=True, capture_output=True)
    assert "Prepared 2400 claims" in result.stdout
    manifest = json.loads((output / "dataset_manifest.json").read_text())
    assert manifest["split_counts"] == {
        "train": {"invalid": 560, "manual_review": 560, "valid": 560},
        "validation": {"invalid": 120, "manual_review": 120, "valid": 120},
        "test": {"invalid": 120, "manual_review": 120, "valid": 120},
    }
    with (output / "train.csv").open() as stream:
        assert "claim_id" in next(csv.DictReader(stream)).keys()


def test_rejects_duplicate_claim_id(tmp_path):
    source = tmp_path / "bad.csv"
    write_csv(source, [
        {"claim_id": "C-1", "product_category": "electronics", "claim_class": "Likely Valid", "split": "train", "provenance_ref": "x"},
        {"claim_id": "C-1", "product_category": "electronics", "claim_class": "Likely Invalid", "split": "validation", "provenance_ref": "y"},
    ])
    result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(source), "--output", str(tmp_path / "out")],
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert "Duplicate claim_id" in result.stderr
