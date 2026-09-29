"""Consistent API errors, transaction cleanup and response security headers."""
from flask import current_app, jsonify, request
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import HTTPException
from backend.extensions import db


def init_middleware(app):
    def error(code, message, status, **extra):
        return jsonify(error={"code": code, "message": message, **extra}), status

    def allowed_origin():
        origin = request.headers.get("Origin", "").rstrip("/")
        return origin if origin in current_app.config["FRONTEND_ORIGINS"] else None

    @app.before_request
    def cors_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            if not allowed_origin():
                return error("origin_not_allowed", "This frontend origin is not allowed.", 403)
            return current_app.make_default_options_response()

    @app.errorhandler(ValidationError)
    def validation(exc):
        db.session.rollback()
        return error("validation_error", "Input validation failed.", 400, details=exc.messages)

    @app.errorhandler(IntegrityError)
    def conflict(exc):
        db.session.rollback()
        return error("conflict", "A record already exists or conflicts with related data.", 409)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        db.session.rollback()
        response = exc.get_response()
        default_code = "file_too_large" if exc.code == 413 else exc.name.lower().replace(" ", "_")
        response.data = app.json.dumps({"error": {
            "code": getattr(exc, "error_code", default_code),
            "message": exc.description,
            **({"details": exc.details} if getattr(exc, "details", None) else {})}})
        response.content_type = "application/json"
        return response

    @app.errorhandler(SQLAlchemyError)
    def database_error(exc):
        db.session.rollback()
        # Do not log SQL parameters: they can include password hashes or personal data.
        current_app.logger.error("Database request failed (%s)", type(exc).__name__)
        return error("service_unavailable", "Service temporarily unavailable.", 503)

    @app.errorhandler(Exception)
    def unexpected(exc):
        db.session.rollback()
        current_app.logger.error("Unhandled request error (%s)", type(exc).__name__)
        return error("internal_error", "An unexpected error occurred.", 500)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.path in {"/docs", "/docs/"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data:; "
                "font-src 'self' data: https://cdn.jsdelivr.net; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        origin = allowed_origin() if request.path.startswith("/api/") else None
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.vary.add("Origin")
        response.headers["Referrer-Policy"] = "same-origin"
        if app.config["ASSUREX_ENV"] == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
