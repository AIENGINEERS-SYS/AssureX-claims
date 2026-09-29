"""Generate deterministic GTM claim-card datasets from an AssureX CSV export."""
import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.services.claim_card import render_claim_card  # noqa: E402

LABEL_COLUMNS = (
    "prediction_class",
    "claim_outcome",
    "label",
    "target",
    "decision",
    "claim_class",
)
SPLITS = ("train", "validation", "test")
SPLIT_ALIASES = {
    "train": "train",
    "training": "train",
    "validation": "validation",
    "validate": "validation",
    "val": "validation",
    "test": "test",
    "testing": "test",
}
EXPECTED_SPLIT_RATIOS = {"train": 0.70, "validation": 0.15, "test": 0.15}
# The project dataset has 2,400 rows, so the expected split is exactly
# 1,680 / 360 / 360. A 0.5 percentage-point tolerance still catches material
# drift while allowing tiny rounding differences in other valid exports.
SPLIT_RATIO_TOLERANCE = 0.005


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
    documents = [
        name.removesuffix("_available")
        for name, raw in row.items()
        if name.endswith("_available")
        and str(raw).strip().casefold() in {"1", "true", "yes"}
    ]
    return {
        "claim_id": value(row, "claim_id", default=f"row-{index}"),
        "product": value(row, "product_name", "product_category"),
        "category": value(row, "product_category", "category"),
        "brand": value(row, "brand"),
        "model": value(row, "model_family", "model_number"),
        "purchase_date": value(row, "purchase_date"),
        "claim_date": value(row, "claim_date", "submission_date"),
        "fault_type": value(row, "fault_category", "fault_type"),
        "damage_type": value(row, "damage_cause", "damage_type"),
        "description": value(row, "fault_description", "description", default=""),
        "documents": documents,
        "policy": value(row, "policy_code"),
    }


def split_for(row, index, split_column="split"):
    """Return the row's authoritative dataset split.

    The generator must never independently re-split claims because Python and
    GTM are required to share the same underlying train/validation/test
    partition.
    """
    raw = row.get(split_column)
    if raw in (None, ""):
        raise ValueError(
            f"row {index + 2}: missing required split value in column {split_column!r}"
        )
    normalized = str(raw).strip().casefold().replace("-", "_").replace(" ", "_")
    normalized = SPLIT_ALIASES.get(normalized, normalized)
    if normalized not in SPLITS:
        raise ValueError(
            f"row {index + 2}: unsupported split {raw!r}; "
            f"expected one of {', '.join(SPLITS)}"
        )
    return normalized


def validate_splits(rows, split_column="split", *, allow_nonstandard=False):
    """Validate one authoritative split per claim and enforce 70/15/15.

    The ratio check is based on claim rows, not generated images. Training
    variants intentionally increase only the number of training images.
    """
    if not rows:
        raise ValueError("input CSV contains no data rows")

    counts = Counter()
    resolved = []
    seen_claims = {}

    for index, row in enumerate(rows):
        split = split_for(row, index, split_column)
        claim_id = str(value(row, "claim_id", default=f"row-{index}")).strip()
        if claim_id in seen_claims:
            previous = seen_claims[claim_id]
            raise ValueError(
                f"row {index + 2}: duplicate claim_id {claim_id!r}; "
                f"first seen in split {previous!r}. Each claim must appear once so "
                "visual variants cannot leak across partitions."
            )
        seen_claims[claim_id] = split
        counts[split] += 1
        resolved.append(split)

    for split in SPLITS:
        if counts[split] == 0 and not allow_nonstandard:
            raise ValueError(f"split {split!r} contains no claims")

    total = len(rows)
    ratios = {split: counts[split] / total for split in SPLITS}
    if not allow_nonstandard:
        drift = {
            split: (ratios[split], EXPECTED_SPLIT_RATIOS[split])
            for split in SPLITS
            if abs(ratios[split] - EXPECTED_SPLIT_RATIOS[split])
            > SPLIT_RATIO_TOLERANCE
        }
        if drift:
            details = ", ".join(
                f"{split}={actual:.2%} (expected {expected:.0%})"
                for split, (actual, expected) in drift.items()
            )
            raise ValueError(
                "dataset split must be 70% train / 15% validation / 15% test; "
                + details
            )

    return resolved, {split: counts[split] for split in SPLITS}, ratios


def _clean_generated_output(output):
    """Remove only generator-owned split folders before an overwrite run."""
    for split in SPLITS:
        shutil.rmtree(output / split, ignore_errors=True)
    manifest = output / "manifest.json"
    if manifest.exists():
        manifest.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--label-column")
    parser.add_argument(
        "--split-column",
        default="split",
        help="Authoritative train/validation/test column. Defaults to 'split'.",
    )
    parser.add_argument(
        "--train-variants",
        type=int,
        default=2,
        help="Visual variants per training claim. SRS minimum is 2.",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--allow-nonstandard-split",
        action="store_true",
        help=(
            "Allow a non-70/15/15 split for tiny demos/tests only. "
            "Do not use for the final GTM dataset."
        ),
    )
    args = parser.parse_args()

    if not 2 <= args.train_variants <= 20:
        parser.error("train variants must be 2-20 to satisfy the SRS")
    if not 1 <= args.workers <= 64:
        parser.error("workers must be 1-64")

    with args.input.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            parser.error("input CSV has no header")
        if args.split_column not in reader.fieldnames:
            parser.error(
                f"input CSV must contain authoritative split column {args.split_column!r}"
            )
        label_column = args.label_column or next(
            (name for name in LABEL_COLUMNS if name in reader.fieldnames), None
        )
        if not label_column:
            parser.error("could not identify a label column; pass --label-column")
        rows = list(reader)

    try:
        row_splits, split_rows, split_ratios = validate_splits(
            rows,
            args.split_column,
            allow_nonstandard=args.allow_nonstandard_split,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.output.exists() and args.overwrite:
        _clean_generated_output(args.output)
    args.output.mkdir(parents=True, exist_ok=True)

    jobs = []
    label_images = {key: 0 for key in ("valid", "invalid", "manual_review")}
    split_images = {key: 0 for key in SPLITS}

    for index, (row, split) in enumerate(zip(rows, row_splits)):
        try:
            label = canonical_label(row.get(label_column))
        except ValueError as exc:
            parser.error(f"row {index + 2}: {exc}")

        variants = args.train_variants if split == "train" else 1
        for variant in range(variants):
            target = (
                args.output
                / split
                / label
                / f"claim-{index:07d}-v{variant}.png"
            )
            if target.exists() and not args.overwrite:
                parser.error(f"output exists: {target}; pass --overwrite")
            jobs.append((payload(row, index), target, variant))
            label_images[label] += 1
            split_images[split] += 1

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(
            pool.map(
                lambda job: render_claim_card(job[0], job[1], variant=job[2]),
                jobs,
            )
        )

    manifest = {
        "source": str(args.input.resolve()),
        "rows": len(rows),
        "images": len(jobs),
        "label_column": label_column,
        "split_column": args.split_column,
        "split_rows": split_rows,
        "split_ratios": {
            split: round(split_ratios[split], 6) for split in SPLITS
        },
        "split_images": split_images,
        "labels": label_images,
        "train_variants": args.train_variants,
        "split_policy": (
            "nonstandard-demo"
            if args.allow_nonstandard_split
            else "70/15/15-authoritative"
        ),
        "schema_version": 2,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
