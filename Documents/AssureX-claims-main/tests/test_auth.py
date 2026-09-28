"""Integration tests use real migrations, Bcrypt, JWTs and HTTP requests."""
from datetime import date, timedelta
from io import BytesIO
import pytest
from alembic import command
from alembic.config import Config
from flask_jwt_extended import create_access_token, decode_token
from sqlalchemy import select
from backend import create_app
from backend.db.models import AuditLog, Claim, Product, Review, User, Warranty
from backend.extensions import db

PASSWORD = "test-only-password-123"


@pytest.fixture
def app(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'auth.sqlite3'}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("backend/db/alembic.ini"), "head")
    app = create_app({"TESTING": True, "ASSUREX_ENV": "development", "JWT_SECRET_KEY": "test-key-" * 8,
        "SQLALCHEMY_DATABASE_URI": url, "BCRYPT_LOG_ROUNDS": 4, "RATELIMIT_ENABLED": False,
        "UPLOAD_FOLDER": str(tmp_path / "uploads"), "DOCUMENT_STORAGE_PATH": str(tmp_path / "uploads"),
        "OCR_PROVIDER": "disabled"})
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def accounts(app):
    result = {}
    with app.app_context():
        for role in ("customer", "employee", "reviewer", "admin", "other"):
            user = User(email=f"{role}@example.com", full_name=role, first_name=role, last_name="User",
                        role=role if role != "other" else "customer")
            user.set_password(PASSWORD)
            db.session.add(user)
            db.session.flush()
            result[role] = user.id
        db.session.commit()
    return result


def login(client, role="customer"):
    response = client.post("/api/auth/login", json={"email": f"{role}@example.com", "password": PASSWORD})
    assert response.status_code == 200, response.json
    return response.json


def bearer(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def claims(app, accounts):
    result = {}
    with app.app_context():
        for role in ("customer", "other"):
            product = Product(user_id=accounts[role], name="Laptop", category="electronics", brand="Example",
                model_number="M1", serial_number=role, purchase_date=date.today(), purchase_price=100, retailer="Shop")
            db.session.add(product)
            db.session.flush()
            warranty = Warranty(product_id=product.id, provider="Example", start_date=date.today(),
                expiry_date=date.today() + timedelta(days=365), coverage_duration_months=12)
            db.session.add(warranty)
            db.session.flush()
            claim = Claim(user_id=accounts[role], product_id=product.id, warranty_id=warranty.id,
                fault_date=date.today(), fault_type="power", fault_description="No power", status="submitted",
                assigned_employee_id=accounts["employee"] if role == "customer" else None)
            db.session.add(claim)
            db.session.flush()
            result[role] = {"claim": claim.id, "product": product.id, "warranty": warranty.id}
        db.session.commit()
    return result


def test_registration_hash_duplicate_and_login(client, app):
    data = {"full_name": "Ada Example", "email": " Ada@Example.com ", "password": PASSWORD}
    response = client.post("/api/auth/register", json=data)
    assert response.status_code == 201
    assert response.json["user"]["role"] == "customer"
    assert response.json["user"]["email"] == "ada@example.com"
    assert "password" not in str(response.json)
    with app.app_context():
        user = db.session.scalar(select(User))
        assert user.password_hash.startswith("$2b$")
        assert user.password_hash != PASSWORD and user.check_password(PASSWORD)
    assert client.post("/api/auth/register", json=data).status_code == 409
    assert client.post("/api/auth/login", json={"email": "ADA@example.com", "password": PASSWORD}).status_code == 200


@pytest.mark.parametrize("changes", [
    {"email": "bad"}, {"password": "short"}, {"password": "x" * 73}, {"password": "\U0001f600" * 19},
    {"full_name": " "}, {"role": "admin"}, {"is_active": True}, {"password_hash": "hash"}])
def test_registration_validation(client, changes):
    data = {"full_name": "A User", "email": "a@example.com", "password": PASSWORD} | changes
    assert client.post("/api/auth/register", json=data).status_code == 400


@pytest.mark.parametrize("payload", [[], None, "string", {"email": "a@example.com"}])
def test_invalid_json_shape(client, payload):
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code in (400, 415)
    assert "error" in response.json


def test_login_and_jwt_claims(client, app, accounts):
    result = login(client)
    with app.app_context():
        token = decode_token(result["access_token"])
        refresh = decode_token(result["refresh_token"])
    assert token["sub"] == str(accounts["customer"])
    assert token["role"] == "customer" and token["type"] == "access"
    assert token["exp"] > token["iat"] and token["sid"] == refresh["sid"]
    response = client.get("/api/auth/me", headers=bearer(result["access_token"]))
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"


def test_invalid_login_is_generic(client, app, accounts):
    wrong = client.post("/api/auth/login", json={"email": "customer@example.com", "password": "wrong"})
    unknown = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
    with app.app_context():
        db.session.get(User, accounts["customer"]).is_active = False
        db.session.commit()
    inactive = client.post("/api/auth/login", json={"email": "customer@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == inactive.status_code == 401
    assert wrong.json == unknown.json == inactive.json
    assert client.post("/api/auth/login", json={"email": "none@example.com", "password": "\U0001f600" * 30}).status_code == 401


def test_missing_malformed_expired_and_wrong_type_tokens(client, app, accounts):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers=bearer("broken")).status_code == 401
    result = login(client)
    assert client.get("/api/auth/me", headers=bearer(result["refresh_token"])).status_code == 401
    assert client.post("/api/auth/refresh", headers=bearer(result["access_token"])).status_code == 401
    with app.app_context():
        claims = decode_token(result["access_token"])
        expired = create_access_token(str(accounts["customer"]), expires_delta=timedelta(seconds=-1),
            additional_claims={key: claims[key] for key in ("sid", "ver", "role")})
        no_session = create_access_token(str(accounts["customer"]))
    assert client.get("/api/auth/me", headers=bearer(expired)).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(no_session)).status_code == 401


def test_reject_bad_signature_issuer_and_audience(client, app, accounts):
    import jwt as pyjwt
    result = login(client)
    with app.app_context():
        payload = decode_token(result["access_token"])
    for body, secret in (
        (payload, "wrong-signing-key-" * 4),
        (payload | {"iss": "other-app"}, app.config["JWT_SECRET_KEY"]),
        (payload | {"aud": "other-api"}, app.config["JWT_SECRET_KEY"]),
    ):
        invalid = pyjwt.encode(body, secret, algorithm="HS256")
        assert client.get("/api/auth/me", headers=bearer(invalid)).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("post", "/api/admin/users"), ("patch", "/api/admin/users/1"),
    ("delete", "/api/admin/users/1"), ("patch", "/api/claims/1/assignment"),
    ("post", "/api/review/1/approve"), ("post", "/api/review/1/reject"),
])
def test_mutation_authorization_precedes_body_validation(client, accounts, method, path):
    request = getattr(client, method)
    assert request(path, json={}).status_code == 401
    customer = bearer(login(client)["access_token"])
    assert request(path, headers=customer, json={}).status_code == 403


@pytest.mark.parametrize("token_type", ["access_token", "refresh_token"])
def test_logout_revokes_entire_session(client, app, accounts, token_type):
    first, second = login(client), login(client)
    refreshed = client.post("/api/auth/refresh", headers=bearer(first["refresh_token"]))
    assert refreshed.status_code == 200, refreshed.json
    latest = refreshed.json
    assert client.post("/api/auth/logout", headers=bearer(latest[token_type])).status_code == 200
    for token in (first["access_token"], latest["access_token"]):
        assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401
    assert client.post("/api/auth/refresh", headers=bearer(latest["refresh_token"])).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(second["access_token"])).status_code == 200
    # A different application instance must observe the persisted revocation.
    other_app = create_app(dict(app.config))
    assert other_app.test_client().get("/api/auth/me", headers=bearer(latest["access_token"])).status_code == 401
    with other_app.app_context():
        db.session.remove()
        db.engine.dispose()


def test_refresh_rotation_and_fresh_admin_actions(client, accounts):
    result = login(client, "admin")
    new = client.post("/api/auth/refresh", headers=bearer(result["refresh_token"]))
    assert new.status_code == 200
    assert client.post("/api/auth/refresh", headers=bearer(result["refresh_token"])).status_code == 401
    assert client.get("/api/admin/users", headers=bearer(new.json["access_token"])).status_code == 200
    assert client.delete(f"/api/admin/users/{accounts['other']}", headers=bearer(new.json["access_token"])).status_code == 401


@pytest.mark.parametrize("role", ["customer", "employee", "reviewer", "admin"])
@pytest.mark.parametrize("path,allowed", [
    ("/api/claims/my", {"customer", "admin"}),
    ("/api/claims/assigned", {"employee", "admin"}),
    ("/api/review/manual", {"reviewer", "admin"}),
    ("/api/admin/users", {"admin"}),
    ("/api/admin/analytics", {"admin"}),
])
def test_role_matrix(client, accounts, role, path, allowed):
    token = login(client, role)["access_token"]
    assert client.get(path, headers=bearer(token)).status_code == (200 if role in allowed else 403)
    assert client.get(path).status_code == 401


def test_claim_ownership_and_assignment(client, accounts, claims):
    customer = bearer(login(client)["access_token"])
    mine = claims["customer"]
    payload = {"product_id": mine["product"], "fault_date": date.today().isoformat(),
               "fault_type": "Electrical Failure", "description": "No power"}
    assert client.post("/api/claims/draft", json=payload, headers=customer).status_code == 201
    assert client.post("/api/claims/draft", json=payload | {"user_id": accounts["other"]}, headers=customer).status_code == 400
    assert client.post("/api/claims/draft", json=payload | {"product_id": claims["other"]["product"]}, headers=customer).status_code == 400
    assert {item["user_id"] for item in client.get("/api/claims/my", headers=customer).json["items"]} == {accounts["customer"]}
    employee = bearer(login(client, "employee")["access_token"])
    assert len(client.get("/api/claims/assigned", headers=employee).json["items"]) == 1
    assert client.patch(f"/api/claims/{claims['other']['claim']}/status", json={"status": "under_evaluation"}, headers=employee).status_code == 404
    assert client.patch(f"/api/claims/{mine['claim']}/status", json={"status": "approved"}, headers=employee).status_code == 400
    assert client.patch(f"/api/claims/{mine['claim']}/status", json={"status": "under_evaluation"}, headers=employee).status_code == 200
    assert client.patch(f"/api/claims/{mine['claim']}/status", json={"status": "manual_review"}, headers=customer).status_code == 403


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_review_workflow(client, app, accounts, claims, decision):
    employee = bearer(login(client, "employee")["access_token"])
    reviewer = bearer(login(client, "reviewer")["access_token"])
    claim_id = claims["customer"]["claim"]
    assert client.post(f"/api/review/{claim_id}/{decision}", json={"notes": "Evidence checked"}, headers=reviewer).status_code == 409
    assert client.patch(f"/api/claims/{claim_id}/status", json={"status": "manual_review"}, headers=employee).status_code == 200
    assert client.get("/api/review/manual", headers=reviewer).json["total"] == 1
    assert client.get(f"/api/review/{claim_id}/risk", headers=reviewer).status_code == 200
    assert client.post(f"/api/review/{claim_id}/notes", json={"notes": "Checking evidence"}, headers=reviewer).status_code == 200
    assert client.post(f"/api/review/{claim_id}/{decision}", json={"notes": "Evidence checked"}, headers=employee).status_code == 403
    response = client.post(f"/api/review/{claim_id}/{decision}", json={"notes": "Evidence checked"}, headers=reviewer)
    assert response.status_code == 200
    assert response.json["claim"]["status"] == ("approved" if decision == "approve" else "rejected")
    assert client.post(f"/api/review/{claim_id}/{decision}", json={"notes": "Again"}, headers=reviewer).status_code == 409
    with app.app_context():
        assert len(db.session.scalars(select(Review).where(Review.claim_id == claim_id)).all()) == 2


def test_admin_management_immediate_revocation(client, app, accounts, claims):
    admin = bearer(login(client, "admin")["access_token"])
    customer_tokens = login(client)
    customer = bearer(customer_tokens["access_token"])
    assert client.patch("/api/auth/me", json={"role": "admin"}, headers=customer).status_code == 400
    assert client.patch("/api/auth/me", json={"full_name": "Updated User"}, headers=customer).status_code == 200
    result = client.post("/api/admin/users", headers=admin,
        json={"full_name": "New Staff", "email": "staff@example.com", "password": PASSWORD, "role": "employee"})
    assert result.status_code == 201
    assert client.patch(f"/api/claims/{claims['other']['claim']}/assignment", headers=admin,
                        json={"employee_id": result.json["user"]["id"]}).status_code == 200
    assert client.patch(f"/api/admin/users/{accounts['customer']}", json={"role": "reviewer"}, headers=admin).status_code == 200
    assert client.get("/api/auth/me", headers=customer).status_code == 401
    assert client.post("/api/auth/refresh", headers=bearer(customer_tokens["refresh_token"])).status_code == 401
    assert client.delete(f"/api/admin/users/{accounts['admin']}", headers=admin).status_code == 400
    assert client.delete(f"/api/admin/users/{accounts['customer']}", headers=admin).status_code == 200
    assert client.post("/api/auth/login", json={"email": "customer@example.com", "password": PASSWORD}).status_code == 401
    with app.app_context():
        assert db.session.get(Claim, claims["customer"]["claim"]) is not None
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "admin.user.update"))


def test_authorization_uses_database_role(client, app, accounts):
    headers = bearer(login(client, "admin")["access_token"])
    with app.app_context():
        # Even a direct operator edit without version increment cannot retain admin powers.
        db.session.get(User, accounts["admin"]).role = "customer"
        db.session.commit()
    assert client.get("/api/admin/users", headers=headers).status_code == 403


def test_password_change_invalidates_all_tokens(client, accounts):
    one, two = login(client), login(client)
    response = client.post("/api/auth/password", headers=bearer(one["access_token"]),
        json={"current_password": PASSWORD, "new_password": "changed-password-123"})
    assert response.status_code == 200
    for token in (one["access_token"], two["access_token"]):
        assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401
    assert client.post("/api/auth/refresh", headers=bearer(one["refresh_token"])).status_code == 401
    assert client.post("/api/auth/login", json={"email": "customer@example.com", "password": "changed-password-123"}).status_code == 200


def test_private_documents(client, accounts, claims):
    from pypdf import PdfWriter
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    writer.write(stream)
    stream.seek(0)
    employee = bearer(login(client, "employee")["access_token"])
    customer = bearer(login(client)["access_token"])
    other = bearer(login(client, "other")["access_token"])
    path = f"/api/claims/{claims['customer']['claim']}/documents"
    response = client.post(path, headers=employee, data={"file": (stream, "../../receipt.pdf")})
    assert response.status_code == 201, response.json
    assert response.json["document"]["filename"] == "receipt.pdf"
    url = path + "/" + str(response.json["document"]["id"])
    download = client.get(url, headers=customer)
    assert download.status_code == 200 and "attachment" in download.headers["Content-Disposition"]
    download.close()
    assert client.get(url, headers=other).status_code == 404
    assert client.post(path, headers=employee, data={"file": (BytesIO(b"<script>alert(1)</script>"), "fake.pdf")}).status_code == 400
    assert client.post(f"/api/claims/{claims['other']['claim']}/documents", headers=employee,
                       data={"file": (BytesIO(b"%PDF-1.7"), "receipt.pdf")}).status_code == 404


def test_pagination_and_error_responses(client, accounts):
    headers = bearer(login(client, "admin")["access_token"])
    assert client.get("/api/admin/users?per_page=2", headers=headers).json["total"] == 5
    assert client.get("/api/admin/users?per_page=999", headers=headers).status_code == 400
    assert client.get("/api/admin/users?page=bad", headers=headers).status_code == 400
    assert client.get("/not-found").json["error"]["code"] == "not_found"


def test_rate_limit_enforced(app):
    config = dict(app.config) | {"RATELIMIT_ENABLED": True}
    limited = create_app(config)
    client = limited.test_client()
    statuses = [client.post("/api/auth/login", json={"email": "missing@example.com", "password": "wrong"}).status_code for _ in range(11)]
    assert statuses[-1] == 429 and statuses[0] == 401
    with limited.app_context():
        db.session.remove()
        db.engine.dispose()


def test_reject_insecure_configuration(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        create_app()
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        create_app({"JWT_SECRET_KEY": "x" * 48, "ASSUREX_ENV": "production", "SQLALCHEMY_DATABASE_URI": "sqlite://"})


def test_cli_bootstrap_and_reset(app, client):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-admin", "--email", "bootstrap@example.com", "--full-name", "First Admin"],
                           input=f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0, result.output
    assert client.post("/api/auth/login", json={"email": "bootstrap@example.com", "password": PASSWORD}).json["user"]["role"] == "admin"
    reset = runner.invoke(args=["reset-password", "--email", "bootstrap@example.com"], input="new-password-123\nnew-password-123\n")
    assert reset.exit_code == 0, reset.output
    assert client.post("/api/auth/login", json={"email": "bootstrap@example.com", "password": "new-password-123"}).status_code == 200
    assert runner.invoke(args=["prune-auth"]).exit_code == 0
