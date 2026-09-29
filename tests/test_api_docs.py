"""OpenAPI and Swagger UI regression coverage."""
from backend.docs import _openapi_path
from test_auth import app, client  # noqa: F401


def test_swagger_ui_is_available_without_authentication(client):
    response = client.get("/docs")

    assert response.status_code == 200
    assert response.content_type.startswith("text/html")
    html = response.get_data(as_text=True)
    assert "AssureX API Documentation" in html
    assert "SwaggerUIBundle" in html
    assert 'url: "/openapi.json"' in html

    csp = response.headers["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" in csp
    assert "frame-ancestors 'none'" in csp


def test_docs_trailing_slash_is_supported(client):
    response = client.get("/docs/")
    assert response.status_code == 200
    assert "SwaggerUIBundle" in response.get_data(as_text=True)


def test_openapi_document_exposes_auth_and_core_claim_workflows(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.content_type.startswith("application/json")
    spec = response.get_json()
    assert spec["openapi"] == "3.0.3"
    assert spec["info"]["title"] == "AssureX Claims API"
    assert spec["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"

    paths = spec["paths"]
    assert "post" in paths["/api/auth/login"]
    assert "post" in paths["/api/predict/python"]
    assert "post" in paths["/api/predict/gtm"]
    assert "post" in paths["/api/claims/{claim_id}/evaluate"]
    assert "get" in paths["/api/claims/{claim_id}/decision"]
    assert "post" in paths["/api/review/{claim_id}/override"]

    assert paths["/api/auth/login"]["post"]["security"] == []
    assert paths["/api/health"]["get"]["security"] == []
    assert paths["/api/predict/python"]["post"]["security"] == [{"bearerAuth": []}]


def test_openapi_document_covers_every_registered_api_route(app, client):
    spec = client.get("/openapi.json").get_json()
    documented = set(spec["paths"])

    registered = {
        _openapi_path(rule.rule)
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/")
    }

    assert registered <= documented


def test_openapi_integer_path_parameters_are_typed(client):
    spec = client.get("/openapi.json").get_json()
    operation = spec["paths"]["/api/claims/{claim_id}/evaluate"]["post"]
    claim_id = next(item for item in operation["parameters"] if item["name"] == "claim_id")

    assert claim_id["in"] == "path"
    assert claim_id["required"] is True
    assert claim_id["schema"] == {"type": "integer", "minimum": 1}


def test_document_upload_routes_use_multipart_schema(client):
    spec = client.get("/openapi.json").get_json()

    direct = spec["paths"]["/api/claims/{claim_id}/documents"]["post"]["requestBody"]
    workflow = spec["paths"]["/api/claims/upload"]["post"]["requestBody"]

    for request_body in (direct, workflow):
        assert "multipart/form-data" in request_body["content"]
        schema = request_body["content"]["multipart/form-data"]["schema"]
        assert schema["properties"]["file"] == {"type": "string", "format": "binary"}


def test_normal_api_csp_remains_locked_down(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )
    assert "cdn.jsdelivr.net" not in response.headers["Content-Security-Policy"]
