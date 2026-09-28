"""Generate deterministic GTM claim-card datasets from an AssureX CSV export."""
import argparse
import csv
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.services.claim_card import render_claim_card  # noqa: E402

LABEL_COLUMNS = ("prediction_class", "claim_outcome", "label", "target", "decision")


def canonical_label(value):
    text = str(value or "").strip().casefold().replace("_", " ")
    if "manual" in text or "review" in text:
        return "manual_review"
    if "invalid" in text or "reject" in text:
        return "invalid"
    if "valid" in text or "approve" in text:
        return "valid"
    raise ValueError(f"Unsupported label: {value!r}")


def value(row, *names, default="unknown"):
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    return default


def payload(row, index):
    documents = [name.removesuffix("_available") for name, raw in row.items()
                 if name.endswith("_available") and str(raw).strip().casefold() in {"1", "true", "yes"}]
    return {"claim_id": value(row, "claim_id", default=f"row-{index}"),
            "product": value(row, "product_name", "product_category"),
            "category": value(row, "product_category", "category"), "brand": value(row, "brand"),
            "model": value(row, "model_family", "model_number"),
            "purchase_date": value(row, "purchase_date"), "claim_date": value(row, "claim_date", "submission_date"),
            "fault_type": value(row, "fault_category", "fault_type"),
            "damage_type": value(row, "damage_cause", "damage_type"),
            "description": value(row, "fault_description", "description", default=""),
            "documents": documents, "policy": value(row, "policy_code")}


def split_for(row, index):
    key = value(row, "claim_id", default=str(index))
    bucket = int(hashlib.sha256(str(key).encode()).hexdigest()[:8], 16) % 100
    return "train" if bucket < 80 else "validation" if bucket < 90 else "test"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--label-column")
    parser.add_argument("--train-variants", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.train_variants <= 20 or not 1 <= args.workers <= 64:
        parser.error("train variants must be 1-20 and workers must be 1-64")
    with args.input.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            parser.error("input CSV has no header")
        label_column = args.label_column or next((name for name in LABEL_COLUMNS if name in reader.fieldnames), None)
        if not label_column:
            parser.error("could not identify a label column; pass --label-column")
        rows = list(reader)
    args.output.mkdir(parents=True, exist_ok=True)

    jobs, counts = [], {key: 0 for key in ("valid", "invalid", "manual_review")}
    for index, row in enumerate(rows):
        try:
            label = canonical_label(row.get(label_column))
        except ValueError as exc:
            parser.error(f"row {index + 2}: {exc}")
        split = split_for(row, index)
        variants = args.train_variants if split == "train" else 1
        for variant in range(variants):
            target = args.output / split / label / f"claim-{index:07d}-v{variant}.png"
            if target.exists() and not args.overwrite:
                parser.error(f"output exists: {target}; pass --overwrite")
            jobs.append((payload(row, index), target, variant))
            counts[label] += 1
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(lambda job: render_claim_card(job[0], job[1], variant=job[2]), jobs))
    manifest = {"source": str(args.input.resolve()), "rows": len(rows), "images": len(jobs),
                "label_column": label_column, "labels": counts, "schema_version": 1}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
