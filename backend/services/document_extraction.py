"""Layered, document-aware field extraction for Nigerian receipts and warranties."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
import unicodedata

FIELDS = ("purchase_date", "invoice_number", "product_name", "model_number", "serial_number",
          "retailer", "purchase_price", "warranty_duration")

LABELS = {
    "invoice_number": ("invoice no", "invoice number", "receipt no", "receipt number"),
    "product_name": ("product name", "item description", "description", "product"),
    "model_number": ("model number", "model no", "model"),
    "serial_number": ("serial number", "serial no", "s/n", "serial"),
    "retailer": ("retailer", "merchant", "seller", "store"),
    "purchase_date": ("purchase date", "purchased", "date of purchase", "invoice date", "date"),
}


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(char for char in text if char in "\n\t" or not unicodedata.category(char).startswith("C"))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)[:250_000]


def _empty():
    return {"value": None, "confidence": 0.0, "state": "not_detected"}


def _found(value, confidence, **extra):
    return {"value": value, "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "state": "detected", **extra}


def _value_after_label(lines: list[str], labels: tuple[str, ...]):
    for index, line in enumerate(lines):
        lower = line.casefold()
        for label in labels:
            match = re.search(rf"\b{re.escape(label)}\b\s*(?:[:#-]|is)?\s*(.+)$", lower, re.I)
            if match and match.group(1).strip():
                offset = match.start(1)
                return line[offset:].strip(), 0.91
            if lower.rstrip(" :#-") == label and index + 1 < len(lines):
                return lines[index + 1], 0.78
    return None, 0.0


def _date_result(raw: str | None):
    if not raw:
        return _empty()
    candidates = re.findall(
        r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}|"
        r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b",
        raw, re.I)
    if not candidates:
        return _empty()
    value = re.sub(r"\bSept\b", "Sep", candidates[0], flags=re.I)
    numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", value)
    if numeric and int(numeric.group(1)) <= 12 and int(numeric.group(2)) <= 12:
        day, month, year = map(int, numeric.groups())
        alternatives = [f"{year:04d}-{month:02d}-{day:02d}", f"{year:04d}-{day:02d}-{month:02d}"]
        return {"value": None, "confidence": 0.35, "state": "ambiguous", "raw": value,
                "candidates": alternatives}
    formats = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%b %d %Y", "%b %d, %Y", "%d %B %Y")
    for fmt in formats:
        try:
            return _found(datetime.strptime(value, fmt).date().isoformat(), 0.91, raw=value)
        except ValueError:
            continue
    return _empty()


def _price_result(text: str):
    pattern = re.compile(r"(?:(₦|NGN|N)\s*)?([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d{1,2})?|[0-9]+(?:\.\d{1,2})?)\s*(NGN)?", re.I)
    lines = text.splitlines()
    prioritized = [line for line in lines if re.search(r"\b(total|amount paid|grand total|purchase price)\b", line, re.I)]
    for line in prioritized + lines:
        matches = list(pattern.finditer(line))
        for match in reversed(matches):
            if not (match.group(1) or match.group(3) or line in prioritized):
                continue
            try:
                amount = Decimal(match.group(2).replace(",", ""))
            except InvalidOperation:
                continue
            if amount < 0 or amount > Decimal("9999999999.99"):
                continue
            number = int(amount) if amount == amount.to_integral() else float(amount)
            return _found(number, 0.94 if line in prioritized else 0.76, currency="NGN", raw=match.group(0))
    return _empty()


def _warranty_result(text: str):
    match = re.search(r"\b(?:warranty(?: period| duration)?|coverage)\s*(?:[:#-]|is)?\s*"
                      r"(\d{1,3})\s*(months?|mos?|years?|yrs?)\b", text, re.I)
    if not match:
        return _empty()
    unit = "years" if match.group(2).lower().startswith(("y", "yr")) else "months"
    duration = int(match.group(1))
    if duration < 1 or (unit == "months" and duration > 1200) or (unit == "years" and duration > 100):
        return _empty()
    return _found(duration, 0.9, unit=unit, raw=match.group(0))


def extract_fields(raw_text: str, document_type: str) -> dict:
    """Return all supported fields without inventing absent or ambiguous values."""
    text = normalize_text(raw_text)
    lines = text.splitlines()
    result = {field: _empty() for field in FIELDS}
    date_source, _ = _value_after_label(lines, LABELS["purchase_date"])
    result["purchase_date"] = _date_result(date_source or text)
    for field in ("invoice_number", "product_name", "model_number", "serial_number", "retailer"):
        value, confidence = _value_after_label(lines, LABELS[field])
        if value:
            value = value.strip(" :#-")[:200]
            if value:
                result[field] = _found(value, confidence)
    # Receipts sometimes place the merchant in the first prominent line without a label.
    if result["retailer"]["value"] is None and document_type in {"receipt", "invoice"} and lines:
        first = lines[0]
        if 2 <= len(first) <= 120 and not re.search(r"invoice|receipt|tax", first, re.I):
            result["retailer"] = _found(first, 0.66)
    result["purchase_price"] = _price_result(text)
    result["warranty_duration"] = _warranty_result(text)
    return result


def detected_confidence(fields: dict) -> float | None:
    values = [float(item["confidence"]) for item in fields.values() if item.get("value") is not None]
    return round(sum(values) / len(values), 4) if values else None
