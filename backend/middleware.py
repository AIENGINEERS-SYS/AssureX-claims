"""Consistent API errors, transaction cleanup and response security headers."""
from flask import current_app, jsonify, request
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import HTTPException
from backend.extensions import db


def init_middleware(app):
    def error(code, message, status, **extra):
        return jsonify(error={"code": code, "message": message, **extra}), status

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
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if request.blueprint == "web":
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self' blob:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        response.headers["Referrer-Policy"] = "same-origin"
        if app.config["ASSUREX_ENV"] == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
