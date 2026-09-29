"""Tests for the SRS-compliant Claim Summary Card dataset split."""
import csv
import json
import sys
from pathlib import Path

import pytest

from dataset_generator.claim_card_generator import (
    SPLITS,
    main,
    split_for,
    validate_splits,
)


def row(claim_id, split, label="Likely Valid"):
    return {
        "claim_id": claim_id,
        "split": split,
        "claim_class": label,
        "product_category": "Smartphone",
        "brand": "Example",
        "model_family": "M1",
        "purchase_date": "2026-01-01",
        "claim_date": "2026-02-01",
        "fault_category": "charging_failure",
        "damage_cause": "manufacturing_defect",
        "fault_description": "Device stopped charging during normal use.",
        "policy_code": "TEST",
        "receipt_available": "true",
        "product_photo_available": "true",
    }


def compliant_rows():
    rows = []
    for index in range(14):
        rows.append(row(f"TRAIN-{index:02d}", "train"))
    for index in range(3):
        rows.append(row(f"VAL-{index:02d}", "validation", "Manual Review Required"))
    for index in range(3):
        rows.append(row(f"TEST-{index:02d}", "test", "Likely Invalid"))
    return rows


def write_csv(path, rows):
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("train", "train"),
        ("Training", "train"),
        ("validation", "validation"),
        ("VAL", "validation"),
        ("test", "test"),
        ("Testing", "test"),
    ],
)
def test_split_for_uses_authoritative_split_column(raw, expected):
    assert split_for({"split": raw, "claim_id": "C1"}, 0) == expected


@pytest.mark.parametrize("raw", ["", None, "holdout", "80", "production"])
def test_split_for_rejects_missing_or_unknown_values(raw):
    with pytest.raises(ValueError):
        split_for({"split": raw, "claim_id": "C1"}, 0)


def test_validate_splits_accepts_70_15_15():
    resolved, counts, ratios = validate_splits(compliant_rows())

    assert len(resolved) == 20
    assert counts == {"train": 14, "validation": 3, "test": 3}
    assert ratios == pytest.approx(
        {"train": 0.70, "validation": 0.15, "test": 0.15}
    )


def test_validate_splits_rejects_old_80_10_10_behavior():
    rows = [
        row(f"C-{index:02d}", "train" if index < 16 else "validation" if index < 18 else "test")
        for index in range(20)
    ]

    with pytest.raises(ValueError, match="70% train / 15% validation / 15% test"):
        validate_splits(rows)


def test_validate_splits_rejects_duplicate_claim_ids():
    rows = compliant_rows()
    rows[-1]["claim_id"] = rows[0]["claim_id"]

    with pytest.raises(ValueError, match="duplicate claim_id"):
        validate_splits(rows)


def test_generator_uses_existing_split_and_only_augments_training(tmp_path, monkeypatch):
    source = tmp_path / "claims.csv"
    output = tmp_path / "gtm"
    write_csv(source, compliant_rows())

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "claim_card_generator.py",
            "--input",
            str(source),
            "--output",
            str(output),
            "--train-variants",
            "2",
        ],
    )
    main()

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["split_policy"] == "70/15/15-authoritative"
    assert manifest["split_rows"] == {"train": 14, "validation": 3, "test": 3}
    assert manifest["split_ratios"] == {
        "train": 0.7,
        "validation": 0.15,
        "test": 0.15,
    }
    assert manifest["split_images"] == {
        "train": 28,
        "validation": 3,
        "test": 3,
    }
    assert manifest["images"] == 34
    assert manifest["train_variants"] == 2
    assert manifest["split_column"] == "split"

    for split in SPLITS:
        images = list((output / split).rglob("*.png"))
        expected = 28 if split == "train" else 3
        assert len(images) == expected

    assert not list((output / "validation").rglob("*-v1.png"))
    assert not list((output / "test").rglob("*-v1.png"))
    assert len(list((output / "train").rglob("*-v1.png"))) == 14


def test_generator_refuses_csv_without_split_column(tmp_path, monkeypatch):
    rows = compliant_rows()
    for item in rows:
        item.pop("split")
    source = tmp_path / "claims.csv"
    output = tmp_path / "gtm"
    write_csv(source, rows)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "claim_card_generator.py",
            "--input",
            str(source),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_generator_requires_at_least_two_training_variants(tmp_path, monkeypatch):
    source = tmp_path / "claims.csv"
    output = tmp_path / "gtm"
    write_csv(source, compliant_rows())

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "claim_card_generator.py",
            "--input",
            str(source),
            "--output",
            str(output),
            "--train-variants",
            "1",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
