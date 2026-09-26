"""Environment configuration; secrets have no usable built-in defaults."""
import os
from datetime import timedelta
from pathlib import Path
from sqlalchemy.engine import make_url
from backend.db.session import database_url

ROOT = Path(__file__).resolve().parent.parent


def settings():
    url = make_url(database_url())
    if url.drivername == "sqlite" and url.database not in (None, "", ":memory:"):
        url = url.set(database=str((ROOT / url.database).resolve()))
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
        "WARRANTY_NEAR_EXPIRY_DAYS": int(os.getenv("WARRANTY_NEAR_EXPIRY_DAYS", "30")),
        "MAX_CONTENT_LENGTH": 11 * 1024 * 1024,  # 10 MB file plus multipart framing
        "CLAIM_STORAGE": os.getenv("CLAIM_STORAGE", "local"),
        "S3_BUCKET": os.getenv("S3_BUCKET"),
        "S3_ENDPOINT_URL": os.getenv("S3_ENDPOINT_URL"),
        "UPLOAD_FOLDER": os.getenv("UPLOAD_FOLDER", str(ROOT / "instance" / "uploads")),
        "RATELIMIT_STORAGE_URI": os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
        "RATELIMIT_DEFAULT": "200 per minute",
        "RATELIMIT_HEADERS_ENABLED": True,
        "RATELIMIT_SWALLOW_ERRORS": False,
    }


def validate_config(app):
    if app.config["CLAIM_STORAGE"] not in {"local", "s3"}:
        raise RuntimeError("CLAIM_STORAGE must be local or s3")
    if app.config["CLAIM_STORAGE"] == "s3" and not app.config["S3_BUCKET"]:
        raise RuntimeError("S3_BUCKET is required for S3 storage")
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
