"""OpenAPI schema generation and interactive Swagger UI routes."""
from __future__ import annotations

import re
from flask import current_app, jsonify, make_response, request

_ROUTE_PARAMETER = re.compile(r"<(?:(?P<converter>[^:<>]+):)?(?P<name>[^<>]+)>")

PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/register",
    "/api/auth/login",
}

SUMMARY_OVERRIDES = {
    ("POST", "/api/auth/register"): "Register a customer account",
    ("POST", "/api/auth/login"): "Log in and issue JWT tokens",
    ("POST", "/api/auth/refresh"): "Rotate a refresh token",
    ("POST", "/api/auth/logout"): "Log out and revoke the current session",
    ("GET", "/api/auth/me"): "Get the current user profile",
    ("PATCH", "/api/auth/me"): "Update the current user profile",
    ("POST", "/api/predict/python"): "Generate the Python model prediction",
    ("POST", "/api/predict/gtm"): "Generate the GTM model prediction",
    ("POST", "/api/claims/{claim_id}/evaluate"): "Run the complete claim evaluation",
    ("GET", "/api/claims/{claim_id}/decision"): "Get the latest claim decision",
    ("GET", "/api/review/manual"): "List claims awaiting manual review",
    ("POST", "/api/review/{claim_id}/approve"): "Approve a claim after review",
    ("POST", "/api/review/{claim_id}/reject"): "Reject a claim after review",
    ("POST", "/api/review/{claim_id}/override"): "Override the automated recommendation",
    ("GET", "/api/health"): "Check API health",
}

REQUEST_SCHEMAS = {
    ("POST", "/api/auth/login"): {
        "type": "object",
        "required": ["email", "password"],
        "additionalProperties": False,
        "properties": {
            "email": {"type": "string", "format": "email"},
            "password": {"type": "string", "format": "password"},
        },
    },
    ("POST", "/api/auth/register"): {
        "type": "object",
        "required": ["email", "password", "full_name"],
        "additionalProperties": False,
        "properties": {
            "email": {"type": "string", "format": "email"},
            "password": {"type": "string", "format": "password"},
            "full_name": {"type": "string"},
            "phone": {"type": "string", "nullable": True},
        },
    },
    ("POST", "/api/predict/python"): {"$ref": "#/components/schemas/ClaimIdRequest"},
    ("POST", "/api/predict/gtm"): {"$ref": "#/components/schemas/ClaimIdRequest"},
}

MULTIPART_PATHS = {
    ("POST", "/api/claims/upload"),
    ("POST", "/api/claims/{claim_id}/documents"),
}


def _openapi_path(rule: str) -> str:
    return _ROUTE_PARAMETER.sub(lambda match: "{" + match.group("name") + "}", rule)


def _tag(endpoint: str) -> str:
    blueprint = endpoint.split(".", 1)[0]
    names = {
        "auth": "Authentication",
        "admin": "Administration",
        "claims": "Claims",
        "claim_workflow": "Claim Workflow",
        "documents": "Documents & OCR",
        "products": "Products & Warranties",
        "review": "Review",
        "dashboard": "Dashboards",
        "predictions": "Predictions & Evaluation",
        "notifications": "Notifications",
    }
    return names.get(blueprint, "System")


def _summary(endpoint: str, method: str, path: str) -> str:
    override = SUMMARY_OVERRIDES.get((method, path))
    if override:
        return override
    function = endpoint.rsplit(".", 1)[-1].replace("_", " ").strip()
    return function[:1].upper() + function[1:]


def _path_parameters(rule: str):
    parameters = []
    for match in _ROUTE_PARAMETER.finditer(rule):
        converter = match.group("converter")
        schema = {"type": "integer", "minimum": 1} if converter == "int" else {"type": "string"}
        parameters.append({
            "name": match.group("name"),
            "in": "path",
            "required": True,
            "schema": schema,
        })
    return parameters


def _request_body(method: str, path: str):
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if (method, path) in MULTIPART_PATHS:
        return {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "document_type": {
                                "type": "string",
                                "description": "Supported AssureX document type.",
                            },
                            "draft_id": {"type": "integer"},
                            "version": {"type": "integer"},
                        },
                    }
                }
            },
        }
    schema = REQUEST_SCHEMAS.get((method, path), {
        "type": "object",
        "description": "Request fields are validated by the endpoint-specific Marshmallow schema.",
    })
    return {
        "required": method in {"POST", "PUT", "PATCH"},
        "content": {"application/json": {"schema": schema}},
    }


def build_openapi_spec(app):
    paths = {}
    tags = set()

    for rule in sorted(app.url_map.iter_rules(), key=lambda item: item.rule):
        if not rule.rule.startswith("/api/"):
            continue

        path = _openapi_path(rule.rule)
        operations = paths.setdefault(path, {})
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            tag = _tag(rule.endpoint)
            tags.add(tag)
            operation = {
                "tags": [tag],
                "summary": _summary(rule.endpoint, method, path),
                "operationId": (
                    f"{rule.endpoint.replace('.', '_')}_{method.lower()}_"
                    + re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")
                ),
                "responses": {
                    "200": {"description": "Successful response"},
                    "400": {"description": "Validation or request error",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    "401": {"description": "Authentication required",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    "403": {"description": "Insufficient permissions",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    "404": {"description": "Resource not found",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                },
            }
            parameters = _path_parameters(rule.rule)
            if parameters:
                operation["parameters"] = parameters

            body = _request_body(method, path)
            if body is not None:
                operation["requestBody"] = body

            if rule.rule not in PUBLIC_API_PATHS:
                operation["security"] = [{"bearerAuth": []}]
            else:
                operation["security"] = []

            operations[method.lower()] = operation

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "AssureX Claims API",
            "version": "1.0.0",
            "description": (
                "Interactive API documentation for the AssureX warranty and claims platform. "
                "Protected endpoints use JWT Bearer authentication. Use /api/auth/login to obtain "
                "an access token, then click Authorize in Swagger UI."
            ),
        },
        "servers": [{"url": "/", "description": "Current AssureX server"}],
        "tags": [{"name": tag} for tag in sorted(tags)],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
            "schemas": {
                "ClaimIdRequest": {
                    "type": "object",
                    "required": ["claim_id"],
                    "additionalProperties": False,
                    "properties": {"claim_id": {"type": "integer", "minimum": 1}},
                },
                "ErrorResponse": {
                    "type": "object",
                    "required": ["error"],
                    "properties": {
                        "error": {
                            "type": "object",
                            "required": ["code", "message"],
                            "properties": {
                                "code": {"type": "string"},
                                "message": {"type": "string"},
                                "details": {"type": "object", "additionalProperties": True},
                            },
                        }
                    },
                },
            },
        },
    }


SWAGGER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AssureX API Documentation</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
  <script>
    window.onload = () => {
      window.ui = SwaggerUIBundle({
        url: "/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        persistAuthorization: true,
        displayRequestDuration: true,
        filter: true,
        tryItOutEnabled: true,
        presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
        layout: "StandaloneLayout"
      });
    };
  </script>
</body>
</html>
"""


def init_api_docs(app):
    @app.get("/openapi.json")
    def openapi_document():
        return jsonify(build_openapi_spec(current_app))

    @app.get("/docs")
    @app.get("/docs/")
    def swagger_ui():
        response = make_response(SWAGGER_HTML)
        response.content_type = "text/html; charset=utf-8"
        return response
