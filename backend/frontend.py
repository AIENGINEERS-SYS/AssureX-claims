"""Browser-route handoff for the separately deployed React application."""
from flask import current_app, redirect, request
from werkzeug.exceptions import ServiceUnavailable


def init_frontend_routes(app):
    @app.get("/")
    def frontend_root():
        return _redirect_to_frontend("/products")

    @app.get("/products")
    @app.get("/claims")
    @app.get("/dashboard")
    @app.get("/reports")
    @app.get("/search")
    def frontend_section():
        return _redirect_to_frontend(request.path)

    @app.get("/products/<path:path>")
    @app.get("/claims/<path:path>")
    @app.get("/dashboard/<path:path>")
    @app.get("/reports/<path:path>")
    def frontend_deep_link(path):
        del path
        return _redirect_to_frontend(request.path)


def _redirect_to_frontend(path):
    base_url = current_app.config.get("FRONTEND_URL", "").rstrip("/")
    if not base_url:
        raise ServiceUnavailable("The frontend application is not configured.")
    query = request.query_string.decode("ascii")
    target = f"{base_url}{path}"
    if query:
        target = f"{target}?{query}"
    return redirect(target, code=307)
