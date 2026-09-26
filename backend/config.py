"""Environment configuration; secrets have no usable built-in defaults."""
import os
from datetime import timedelta
from pathlib import Path
from sqlalchemy.engine import make_url
from backend.db.session import database_url

ROOT = Path(__file__).resolve().parent.parent


def _integer(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _decimal(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


def _boolean(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    if value.lower() not in {"true", "false"}:
        raise RuntimeError(f"{name} must be true or false")
    return value.lower() == "true"


def settings():
    url = make_url(database_url())
    if url.drivername == "sqlite" and url.database not in (None, "", ":memory:"):
        url = url.set(database=str((ROOT / url.database).resolve()))
    document_size = _integer("MAX_DOCUMENT_SIZE_MB", 10)
    storage_backend = os.getenv("DOCUMENT_STORAGE_BACKEND", os.getenv("CLAIM_STORAGE", "local"))
    storage_path = os.getenv("DOCUMENT_STORAGE_PATH", os.getenv("UPLOAD_FOLDER", str(ROOT / "instance" / "uploads")))
    return {
        "ASSUREX_ENV": os.getenv("ASSUREX_ENV", "development"),
        "SQLALCHEMY_DATABASE_URI": url.render_as_string(hide_password=False),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True},
        "JWT_SECRET_KEY": os.getenv("JWT_SECRET_KEY"),
        "JWT_ALGORITHM": "HS256",
        "JWT_DECODE_ALGORITHMS": ["HS256"],
        "JWT_ENCODE_ISSUER": "assurex",
        "JWT_DECODE_ISSUER": "assurex",
        "JWT_ENCODE_AUDIENCE": "assurex-api",
        "JWT_DECODE_AUDIENCE": "assurex-api",
        "JWT_TOKEN_LOCATION": ["headers"],
        "JWT_ACCESS_TOKEN_EXPIRES": timedelta(minutes=15),
        "JWT_REFRESH_TOKEN_EXPIRES": timedelta(days=7),
        "BCRYPT_LOG_ROUNDS": 12,
        "WARRANTY_NEAR_EXPIRY_DAYS": _integer("WARRANTY_NEAR_EXPIRY_DAYS", 30),
        # Multipart framing receives one bounded document per request.
        "MAX_CONTENT_LENGTH": (document_size * 1024 * 1024) + (1024 * 1024),
        "MAX_DOCUMENT_SIZE_MB": document_size,
        "MAX_DOCUMENTS_PER_CLAIM": _integer("MAX_DOCUMENTS_PER_CLAIM", 20),
        "MAX_CLAIM_UPLOAD_SIZE_MB": _integer("MAX_CLAIM_UPLOAD_SIZE_MB", 50),
        "DOCUMENT_STORAGE_BACKEND": storage_backend,
        "DOCUMENT_STORAGE_PATH": storage_path,
        # Backward-compatible Phase 5 configuration aliases.
        "CLAIM_STORAGE": storage_backend,
        "S3_BUCKET": os.getenv("S3_BUCKET"),
        "S3_ENDPOINT_URL": os.getenv("S3_ENDPOINT_URL"),
        "UPLOAD_FOLDER": storage_path,
        "OCR_PROVIDER": os.getenv("OCR_PROVIDER", "tesseract").lower(),
        "OCR_HIGH_CONFIDENCE_THRESHOLD": _decimal("OCR_HIGH_CONFIDENCE_THRESHOLD", 0.85),
        "OCR_REVIEW_THRESHOLD": _decimal("OCR_REVIEW_THRESHOLD", 0.65),
        "OCR_MAX_PDF_PAGES": _integer("OCR_MAX_PDF_PAGES", 20),
        "OCR_TIMEOUT_SECONDS": _integer("OCR_TIMEOUT_SECONDS", 30),
        "OCR_PREPROCESS_IMAGES": _boolean("OCR_PREPROCESS_IMAGES", True),
        "RATELIMIT_STORAGE_URI": os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
        "RATELIMIT_DEFAULT": "200 per minute",
        "RATELIMIT_HEADERS_ENABLED": True,
        "RATELIMIT_SWALLOW_ERRORS": False,
    }


def validate_config(app):
    if app.config["DOCUMENT_STORAGE_BACKEND"] not in {"local", "s3"}:
        raise RuntimeError("DOCUMENT_STORAGE_BACKEND must be local or s3")
    if app.config["DOCUMENT_STORAGE_BACKEND"] == "s3" and not app.config["S3_BUCKET"]:
        raise RuntimeError("S3_BUCKET is required for S3 storage")
    if app.config["OCR_PROVIDER"] not in {"tesseract", "disabled"}:
        raise RuntimeError("OCR_PROVIDER must be tesseract or disabled")
    for name, maximum in (("MAX_DOCUMENT_SIZE_MB", 100), ("MAX_DOCUMENTS_PER_CLAIM", 100),
                          ("MAX_CLAIM_UPLOAD_SIZE_MB", 1000), ("OCR_MAX_PDF_PAGES", 100)):
        if not 1 <= app.config[name] <= maximum:
            raise RuntimeError(f"{name} must be between 1 and {maximum}")
    if app.config["MAX_CLAIM_UPLOAD_SIZE_MB"] < app.config["MAX_DOCUMENT_SIZE_MB"]:
        raise RuntimeError("MAX_CLAIM_UPLOAD_SIZE_MB cannot be smaller than MAX_DOCUMENT_SIZE_MB")
    review, high = app.config["OCR_REVIEW_THRESHOLD"], app.config["OCR_HIGH_CONFIDENCE_THRESHOLD"]
    if not 0 <= review <= high <= 1:
        raise RuntimeError("OCR confidence thresholds must satisfy 0 <= review <= high <= 1")
    if not 1 <= app.config["OCR_TIMEOUT_SECONDS"] <= 300:
        raise RuntimeError("OCR_TIMEOUT_SECONDS must be between 1 and 300")
    if not 0 <= app.config["WARRANTY_NEAR_EXPIRY_DAYS"] <= 365:
        raise RuntimeError("WARRANTY_NEAR_EXPIRY_DAYS must be between 0 and 365")
    secret = app.config.get("JWT_SECRET_KEY")
    if not isinstance(secret, str) or len(secret.encode()) < 32 or secret.startswith("replace-"):
        raise RuntimeError("Set JWT_SECRET_KEY to a randomly generated secret of at least 32 bytes")
    if app.config["ASSUREX_ENV"] == "production":
        if app.debug or app.testing:
            raise RuntimeError("Debug and testing must be disabled in production")
        if not make_url(app.config["SQLALCHEMY_DATABASE_URI"]).drivername.startswith("postgresql"):
            raise RuntimeError("Production requires PostgreSQL")
        if not app.config.get("RATELIMIT_ENABLED", True) or not app.config["RATELIMIT_STORAGE_URI"].startswith(("redis://", "rediss://")):
            raise RuntimeError("Production requires a shared Redis rate-limit store")
