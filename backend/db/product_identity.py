"""Serial identities are scoped to manufacturer and model, across all owners."""
import hashlib
import json
import unicodedata


def serial_identity(brand, model, serial):
    values = [unicodedata.normalize("NFKC", value).strip().casefold() for value in (brand, model, serial)]
    return hashlib.sha256(json.dumps(values, ensure_ascii=True).encode("utf-8")).hexdigest()
