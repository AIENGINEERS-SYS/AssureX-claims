#!/usr/bin/env python3
"""Prepare the AssureX warranty-claim CSV for reproducible ML training.

The source dataset is the single source of truth.  This script validates its
Claim IDs and class balance, preserves its supplied train/validation/test split
when present, and writes model-ready CSV files plus an auditable manifest.

It intentionally never fabricates claims or labels.  If a source CSV has no
usable ``split`` column, use ``--rebuild-splits`` to create a deterministic,
stratified 70/15/15 split by Claim ID.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


LABELS = {
    "likely valid": "valid",
    "valid claim": "valid",
    "valid": "valid",
    "likely invalid": "invalid",
    "invalid claim": "invalid",
    "invalid": "invalid",
    "manual review required": "manual_review",
    "manual review": "manual_review",
    "manual_review": "manual_review",
}
TARGET_CLASSES = ("valid", "invalid", "manual_review")
SPLITS = ("train", "validation", "test")
SPLIT_ALIASES = {"val": "validation", "valid": "validation", "validation": "validation",
                 "train": "train", "test": "test"}
EXCLUDED_FEATURES = {"claim_id", "claim_class", "label", "target", "split", "provenance_ref"}


class DatasetValidationError(ValueError):
    """Raised when a source dataset cannot safely be used for model training."""


@dataclass(frozen=True)
class PreparedDataset:
    rows: list[dict[str, str]]
    feature_columns: list[str]
    source_sha256: str
    split_counts: dict[str, dict[str, int]]
    split_strategy: str


def _normalise(value: str) -> str:
    return " ".join((value or "").replace("_", " ").strip().casefold().split())


def canonical_label(value: str) -> str:
    label = LABELS.get(_normalise(value))
    if label is None:
        raise DatasetValidationError(f"Unsupported claim class: {value!r}")
    return label


def canonical_split(value: str) -> str:
    split = SPLIT_ALIASES.get(_normalise(value))
    if split is None:
        raise DatasetValidationError(f"Unsupported dataset split: {value!r}")
    return split


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_source(path: Path, *, rebuild_splits: bool, seed: int) -> PreparedDataset:
    if not path.is_file():
        raise DatasetValidationError(f"Input CSV does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise DatasetValidationError("Input CSV has no header row.")
        required = {"claim_id", "claim_class"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise DatasetValidationError(f"Input CSV is missing required columns: {', '.join(sorted(missing))}")
        source_rows = list(reader)
        fieldnames = list(reader.fieldnames)
    if not source_rows:
        raise DatasetValidationError("Input CSV contains no claim records.")

    seen_ids: set[str] = set()
    rows: list[dict[str, str]] = []
    for number, source in enumerate(source_rows, start=2):
        claim_id = (source.get("claim_id") or "").strip()
        if not claim_id:
            raise DatasetValidationError(f"Row {number} has an empty claim_id.")
        if claim_id in seen_ids:
            raise DatasetValidationError(f"Duplicate claim_id detected: {claim_id}")
        seen_ids.add(claim_id)
        row = {field: (source.get(field) or "").strip() for field in fieldnames}
        row["claim_id"] = claim_id
        row["target"] = canonical_label(row["claim_class"])
        rows.append(row)

    supplied_splits = "split" in fieldnames and all((row.get("split") or "").strip() for row in rows)
    if supplied_splits and not rebuild_splits:
        for row in rows:
            row["split"] = canonical_split(row["split"])
    elif rebuild_splits:
        assign_stratified_splits(rows, seed=seed)
    else:
        raise DatasetValidationError(
            "Input CSV does not contain a complete split column. Use --rebuild-splits to create one."
        )

    verify_split_integrity(rows)
    features = [field for field in fieldnames if field not in EXCLUDED_FEATURES]
    if not features:
        raise DatasetValidationError("No model feature columns remain after excluding identity and target fields.")
    counts = split_counts(rows)
    return PreparedDataset(rows=rows, feature_columns=features, source_sha256=sha256_file(path), split_counts=counts,
                           split_strategy="source_supplied" if supplied_splits and not rebuild_splits else "stratified_70_15_15")


def assign_stratified_splits(rows: list[dict[str, str]], *, seed: int) -> None:
    """Assign every Claim ID to one split while keeping class proportions balanced."""
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["target"]].append(row)
    rng = random.Random(seed)
    for label, members in grouped.items():
        rng.shuffle(members)
        total = len(members)
        train_end = round(total * 0.70)
        validation_end = train_end + round(total * 0.15)
        for index, row in enumerate(members):
            row["split"] = "train" if index < train_end else "validation" if index < validation_end else "test"
        if not any(row["split"] == "test" for row in members):
            raise DatasetValidationError(f"Class {label!r} is too small to create a test split.")


def split_counts(rows: Iterable[dict[str, str]]) -> dict[str, dict[str, int]]:
    counts = {split: Counter() for split in SPLITS}
    for row in rows:
        counts[row["split"]][row["target"]] += 1
    return {split: {label: counts[split][label] for label in TARGET_CLASSES} for split in SPLITS}


def verify_split_integrity(rows: Iterable[dict[str, str]]) -> None:
    ids_by_split: dict[str, set[str]] = {split: set() for split in SPLITS}
    labels_by_split: dict[str, Counter[str]] = {split: Counter() for split in SPLITS}
    for row in rows:
        split, claim_id, target = row["split"], row["claim_id"], row["target"]
        if split not in ids_by_split:
            raise DatasetValidationError(f"Unsupported split after preparation: {split!r}")
        ids_by_split[split].add(claim_id)
        labels_by_split[split][target] += 1
    for left_index, left in enumerate(SPLITS):
        for right in SPLITS[left_index + 1:]:
            collision = ids_by_split[left] & ids_by_split[right]
            if collision:
                raise DatasetValidationError(f"Claim ID appears in multiple splits: {sorted(collision)[0]}")
    for split, counts in labels_by_split.items():
        if not counts:
            raise DatasetValidationError(f"The {split} split is empty.")
        missing = set(TARGET_CLASSES) - set(counts)
        if missing:
            raise DatasetValidationError(f"The {split} split is missing classes: {', '.join(sorted(missing))}")


def write_outputs(dataset: PreparedDataset, output_dir: Path, *, source_path: Path, seed: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_fields = ["claim_id", *dataset.feature_columns, "target"]
    for split in SPLITS:
        output = output_dir / f"{split}.csv"
        with output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=output_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(row for row in dataset.rows if row["split"] == split)
    mapping = output_dir / "claim_id_split_mapping.csv"
    with mapping.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["claim_id", "target", "split", "provenance_ref"])
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in writer.fieldnames} for row in dataset.rows)
    manifest = {
        "dataset_name": "assurex_warranty_claims",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_csv": str(source_path),
        "source_sha256": dataset.source_sha256,
        "record_count": len(dataset.rows),
        "target_column": "target",
        "target_classes": list(TARGET_CLASSES),
        "feature_columns": dataset.feature_columns,
        "split_counts": dataset.split_counts,
        "split_strategy": dataset.split_strategy,
        "random_seed": seed,
        "leakage_controls": [
            "claim_id is excluded from model features",
            "claim_class, target, split, and provenance_ref are excluded from model features",
            "a Claim ID may exist in exactly one split",
        ],
    }
    (output_dir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=root / "data" / "assurex_nigeria_warranty_claims_v2.csv",
                        help="Source warranty-claim CSV.")
    parser.add_argument("--output", type=Path, default=root / "data" / "prepared",
                        help="Directory for train.csv, validation.csv, test.csv, and manifest files.")
    parser.add_argument("--rebuild-splits", action="store_true",
                        help="Ignore supplied splits and deterministically create stratified 70/15/15 splits.")
    parser.add_argument("--seed", type=int, default=20260929, help="Random seed used only for --rebuild-splits.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        dataset = load_source(args.input, rebuild_splits=args.rebuild_splits, seed=args.seed)
        write_outputs(dataset, args.output, source_path=args.input, seed=args.seed)
    except DatasetValidationError as exc:
        print(f"Dataset preparation failed: {exc}", file=sys.stderr)
        return 2
    print(f"Prepared {len(dataset.rows)} claims in {args.output}")
    for split, counts in dataset.split_counts.items():
        print(f"  {split}: {sum(counts.values())} {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
