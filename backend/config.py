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


def _integer_list(name, default):
    raw = os.getenv(name, default)
    try:
        values = tuple(dict.fromkeys(int(item.strip()) for item in raw.split(",") if item.strip()))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a comma-separated list of whole numbers") from exc
    if not values or any(value <= 0 or value > 3650 for value in values):
        raise RuntimeError(f"{name} values must be between 1 and 3650 days")
    return values


def settings():
    environment = os.getenv("ASSUREX_ENV", "development")
    url = make_url(database_url())
    if url.drivername == "sqlite" and url.database not in (None, "", ":memory:"):
        url = url.set(database=str((ROOT / url.database).resolve()))
    document_size = _integer("MAX_DOCUMENT_SIZE_MB", 10)
    storage_backend = os.getenv("DOCUMENT_STORAGE_BACKEND", os.getenv("CLAIM_STORAGE", "local"))
    storage_path = os.getenv("DOCUMENT_STORAGE_PATH", os.getenv("UPLOAD_FOLDER", str(ROOT / "instance" / "uploads")))
    default_origins = "http://localhost:5173,http://127.0.0.1:5173" if environment == "development" else ""
    frontend_origins = tuple(origin.strip().rstrip("/") for origin in
        os.getenv("FRONTEND_ORIGINS", default_origins).split(",") if origin.strip())
    frontend_url = os.getenv("FRONTEND_URL", frontend_origins[0] if frontend_origins else "").strip().rstrip("/")
    return {
        "ASSUREX_ENV": environment,
        "FRONTEND_ORIGINS": frontend_origins,
        "FRONTEND_URL": frontend_url,
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
        "WARRANTY_NOTIFICATION_THRESHOLDS": _integer_list("WARRANTY_NOTIFICATION_THRESHOLDS", "90,60,30,7"),
        "DASHBOARD_DISAGREEMENT_GAP": _decimal("DASHBOARD_DISAGREEMENT_GAP", 0.20),
        "MODEL_STRONG_CONFIDENCE": _decimal("MODEL_STRONG_CONFIDENCE", 0.80),
        "MODEL_ACCEPTABLE_CONFIDENCE": _decimal("MODEL_ACCEPTABLE_CONFIDENCE", 0.65),
        "MODEL_MINIMUM_CONFIDENCE": _decimal("MODEL_MINIMUM_CONFIDENCE", 0.45),
        "MODEL_STRONG_MAX_GAP": _decimal("MODEL_STRONG_MAX_GAP", 0.10),
        "MODEL_ACCEPTABLE_MAX_GAP": _decimal("MODEL_ACCEPTABLE_MAX_GAP",
                                               os.getenv("DASHBOARD_DISAGREEMENT_GAP", "0.20")),
        "DUPLICATE_HIGH_THRESHOLD": _decimal("DUPLICATE_HIGH_THRESHOLD", 0.70),
        "DUPLICATE_MEDIUM_THRESHOLD": _decimal("DUPLICATE_MEDIUM_THRESHOLD", 0.40),
        "DUPLICATE_DESCRIPTION_THRESHOLD": _decimal("DUPLICATE_DESCRIPTION_THRESHOLD", 0.82),
        "PYTHON_MODEL_PATH": os.getenv("PYTHON_MODEL_PATH", str(ROOT / "models" / "assurex_xgboost_final.joblib")),
        "GTM_MODEL_PATH": os.getenv("GTM_MODEL_PATH", str(ROOT / "gtm_model" / "model.json")),
        "WARRANTY_POLICY_PATH": os.getenv("WARRANTY_POLICY_PATH", str(ROOT / "data" / "warranty_policies.json")),
        "MODEL_CARD_PATH": os.getenv("MODEL_CARD_PATH", str(ROOT / "instance" / "model_cards")),
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
        # Forwarding headers are attacker-controlled unless the deployment sits
        # behind a trusted reverse proxy that appends/overwrites X-Forwarded-For.
        "RATELIMIT_TRUST_PROXY_HEADERS": _boolean("RATELIMIT_TRUST_PROXY_HEADERS", False),
        "RATELIMIT_TRUSTED_PROXY_COUNT": _integer("RATELIMIT_TRUSTED_PROXY_COUNT", 1),
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
    thresholds = app.config["WARRANTY_NOTIFICATION_THRESHOLDS"]
    if not thresholds or any(type(value) is not int or value <= 0 or value > 3650 for value in thresholds):
        raise RuntimeError("WARRANTY_NOTIFICATION_THRESHOLDS must contain day values between 1 and 3650")
    if not 0 <= app.config["DASHBOARD_DISAGREEMENT_GAP"] <= 1:
        raise RuntimeError("DASHBOARD_DISAGREEMENT_GAP must be between 0 and 1")
    probability_settings = (
        "MODEL_STRONG_CONFIDENCE", "MODEL_ACCEPTABLE_CONFIDENCE", "MODEL_MINIMUM_CONFIDENCE",
        "MODEL_STRONG_MAX_GAP", "MODEL_ACCEPTABLE_MAX_GAP", "DUPLICATE_HIGH_THRESHOLD",
        "DUPLICATE_MEDIUM_THRESHOLD", "DUPLICATE_DESCRIPTION_THRESHOLD",
    )
    if any(not 0 <= app.config[name] <= 1 for name in probability_settings):
        raise RuntimeError("Model and duplicate thresholds must be between 0 and 1")
    if not (app.config["MODEL_MINIMUM_CONFIDENCE"] <= app.config["MODEL_ACCEPTABLE_CONFIDENCE"] <=
            app.config["MODEL_STRONG_CONFIDENCE"]):
        raise RuntimeError("Model confidence thresholds must be ordered minimum <= acceptable <= strong")
    if app.config["MODEL_STRONG_MAX_GAP"] > app.config["MODEL_ACCEPTABLE_MAX_GAP"]:
        raise RuntimeError("MODEL_STRONG_MAX_GAP cannot exceed MODEL_ACCEPTABLE_MAX_GAP")
    if app.config["DUPLICATE_MEDIUM_THRESHOLD"] > app.config["DUPLICATE_HIGH_THRESHOLD"]:
        raise RuntimeError("Duplicate thresholds must be ordered medium <= high")
    trust_proxy = app.config["RATELIMIT_TRUST_PROXY_HEADERS"]
    trusted_proxy_count = app.config["RATELIMIT_TRUSTED_PROXY_COUNT"]
    if type(trust_proxy) is not bool:
        raise RuntimeError("RATELIMIT_TRUST_PROXY_HEADERS must be true or false")
    if type(trusted_proxy_count) is not int or not 1 <= trusted_proxy_count <= 10:
        raise RuntimeError("RATELIMIT_TRUSTED_PROXY_COUNT must be between 1 and 10")
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
        origins = app.config["FRONTEND_ORIGINS"]
        if not origins or "*" in origins or any(not origin.startswith("https://") for origin in origins):
            raise RuntimeError("Production FRONTEND_ORIGINS must contain explicit HTTPS origins")
        frontend_url = app.config["FRONTEND_URL"]
        if not frontend_url.startswith("https://") or frontend_url not in origins:
            raise RuntimeError("Production FRONTEND_URL must be an allowed HTTPS frontend origin")
