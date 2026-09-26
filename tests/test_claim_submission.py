"""End-to-end HTTP tests against a migrated DB and private temporary storage."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
import pytest
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import select
from backend.db.models import AuditLog, Claim, Document, Product
from backend.extensions import db
from backend.services.claim_submission import next_claim_id
from test_auth import app, client, accounts, claims, login, bearer


def png():
    stream = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(stream, format="PNG")
    return stream.getvalue()


def pdf(*, active=False):
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    if active:
        writer.add_js("app.alert('unsafe')")
    writer.write(stream)
    return stream.getvalue()


@pytest.fixture
def headers(client, accounts):
    return bearer(login(client)["access_token"])


def draft(client, headers, product_id=None, **changes):
    response = client.post("/api/claims/draft", headers=headers,
        json=({"product_id": product_id} if product_id else {}) | changes)
    assert response.status_code == 201, response.json
    return response.json["claim"]


def upload(client, headers, claim, kind="receipt", content=None, filename="receipt.png", mime="image/png"):
    return client.post("/api/claims/upload", headers=headers, data={"draft_id": str(claim["id"]),
        "version": str(claim["version"]), "document_type": kind,
        "file": (BytesIO(png() if content is None else content), filename, mime)})


def complete(client, headers, product_id):
    claim = draft(client, headers, product_id, fault_date=date.today().isoformat(),
        fault_type="Electrical Failure", description="The screen no longer turns on when connected to power.",
        damage_category="Moderate", repair_history="No earlier repairs", previous_replacement=False, current_step=3)
    for kind in ("receipt", "product_image", "serial_number_image", "damage_evidence"):
        result = upload(client, headers, claim, kind)
        assert result.status_code == 201, result.json
        claim = result.json["claim"]
    return claim


def submit(client, headers, claim):
    return client.post("/api/claims/submit", headers=headers, json={"draft_id": claim["id"], "version": claim["version"]})


def test_product_selection_and_inactive(client, app, headers, claims):
    response = client.get("/api/products/my", headers=headers)
    assert [p["id"] for p in response.json["items"]] == [claims["customer"]["product"]]
    assert response.json["items"][0]["is_active"] is True
    assert client.post("/api/claims/draft", headers=headers,
        json={"product_id": claims["other"]["product"]}).status_code == 400
    with app.app_context():
        db.session.get(Product, claims["customer"]["product"]).is_active = False
        db.session.commit()
    assert client.post("/api/claims/draft", headers=headers,
        json={"product_id": claims["customer"]["product"]}).status_code == 400


def test_incomplete_save_resume_stale_and_ownership(client, headers, claims):
    claim = draft(client, headers, description="Partial")
    assert claim["status"] == "DRAFT" and claim["claim_id"] is None and claim["submitted_at"] is None
    url = f"/api/claims/draft/{claim['id']}"
    data = {"version": claim["version"], "product_id": claims["customer"]["product"], "current_step": 2}
    changed = client.put(url, headers=headers, json=data)
    assert changed.status_code == 200, changed.json
    assert client.put(url, headers=headers, json=data).status_code == 409
    resumed = client.get(f"/api/claims/{claim['id']}", headers=headers).json["claim"]
    assert resumed["description"] == "Partial" and resumed["current_step"] == 2
    other = bearer(login(client, "other")["access_token"])
    assert client.get(f"/api/claims/{claim['id']}", headers=other).status_code == 404
    assert client.put(url, headers=other, json=data).status_code == 404
    assert submit(client, other, resumed).status_code == 404
    assert upload(client, other, resumed).status_code == 404


@pytest.mark.parametrize("changes", [
    {"fault_date": (date.today() + timedelta(days=1)).isoformat()}, {"fault_date": "2026-02-30"},
    {"description": "x" * 2001}, {"repair_history": "x" * 1001}, {"fault_type": "arbitrary"},
    {"damage_category": "unknown"}, {"current_step": 5}, {"previous_replacement": "yes"},
    {"customer_id": 99}, {"status": "SUBMITTED"}, {"submitted_at": "2026-01-01"},
])
def test_draft_validation(client, headers, changes):
    assert client.post("/api/claims/draft", headers=headers, json=changes).status_code == 400


def test_submission_validation_and_legacy_route_cannot_bypass(client, headers, claims):
    claim = draft(client, headers, claims["customer"]["product"], description="too short")
    result = submit(client, headers, claim)
    assert result.status_code == 400
    assert set(result.json["error"]["details"]) >= {"fault_date", "fault_type", "description", "damage_category", "documents"}
    assert client.post("/api/claims", headers=headers, json={"product_id": claims["customer"]["product"],
        "warranty_id": claims["customer"]["warranty"], "fault_date": date.today().isoformat(),
        "fault_type": "power", "fault_description": "No power"}).status_code == 400
    # Validation failure rolls back the mutation version.
    assert client.get(f"/api/claims/{claim['id']}", headers=headers).json["claim"]["version"] == claim["version"]


def test_submission_id_retry_immutability_and_audit(client, app, headers, claims):
    draft_claim = complete(client, headers, claims["customer"]["product"])
    response = submit(client, headers, draft_claim)
    assert response.status_code == 201, response.json
    claim = response.json["claim"]
    assert claim["claim_id"] == f"CLM-{date.today().year}-000001"
    assert claim["status"] == "SUBMITTED" and claim["submission_date"] == date.today().isoformat()
    assert claim["submitted_at"] and len(claim["documents"]) == 4
    retry = submit(client, headers, draft_claim)
    assert retry.status_code == 200 and retry.json["claim"]["claim_id"] == claim["claim_id"]
    assert client.get(f"/api/claims/{claim['claim_id']}", headers=headers).status_code == 200
    assert client.put(f"/api/claims/draft/{claim['id']}", headers=headers,
        json={"version": claim["version"], "description": "Changed"}).status_code == 409
    assert upload(client, headers, claim).status_code == 409
    another = complete(client, headers, claims["customer"]["product"])
    assert submit(client, headers, another).json["claim"]["claim_id"].endswith("000002")
    with app.app_context():
        assert len(db.session.scalars(select(AuditLog).where(AuditLog.action == "claim.submit")).all()) == 2
        assert db.session.get(Claim, claim["id"]).status == "submitted"  # Keep employee state machine compatible.


@pytest.mark.parametrize("content,filename,mime", [
    (b"MZ executable", "bad.exe", "application/octet-stream"),
    (b"MZ executable", "fake.png", "image/png"),
    (b"\x89PNG\r\n\x1a\ntruncated", "fake.png", "image/png"),
    (b"%PDF-fake", "fake.pdf", "application/pdf"),
    (b"hello", "text.svg", "image/svg+xml"),
])
def test_invalid_files(client, headers, claims, content, filename, mime):
    claim = draft(client, headers, claims["customer"]["product"])
    assert upload(client, headers, claim, content=content, filename=filename, mime=mime).status_code == 400


def test_file_security_metadata_download_and_remove(client, app, headers, claims):
    claim = draft(client, headers, claims["customer"]["product"])
    assert upload(client, headers, claim, content=png(), mime="application/pdf").status_code == 400
    assert upload(client, headers, claim, content=pdf(active=True), filename="active.pdf", mime="application/pdf").status_code == 400
    result = upload(client, headers, claim, content=pdf(), filename="../../receipt.pdf", mime="application/pdf")
    assert result.status_code == 201, result.json
    claim, document = result.json["claim"], result.json["document"]
    assert document["file_name"] == "receipt.pdf" and document["uploaded_at"] and document["uploaded_by"]
    assert "file_path" not in document
    path = f"/api/claims/{claim['id']}/documents/{document['id']}"
    assert client.get(path, headers=headers).data.startswith(b"%PDF-")
    assert client.get(path, headers=bearer(login(client, "other")["access_token"])).status_code == 404
    assert client.delete(f"/api/claims/draft/{claim['id']}/documents/{document['id']}", headers=headers,
        json={"draft_id": claim["id"], "version": claim["version"]}).json["claim"]["documents"] == []
    assert not list(Path(app.config["UPLOAD_FOLDER"]).rglob("*.pdf"))


def test_size_limits_exact_boundary_and_total(client, headers, claims):
    claim = draft(client, headers, claims["customer"]["product"])
    small = png()
    exact = small + b"\0" * (10 * 1024 * 1024 - len(small))
    assert upload(client, headers, claim, content=exact + b"x").status_code == 413
    for _ in range(5):
        response = upload(client, headers, claim, content=exact)
        assert response.status_code == 201, response.json
        claim = response.json["claim"]
    assert upload(client, headers, claim).status_code == 413


def test_revalidate_product_and_stored_files(client, app, headers, claims):
    claim = complete(client, headers, claims["customer"]["product"])
    with app.app_context():
        product = db.session.get(Product, claim["product_id"])
        product.is_active = False
        db.session.commit()
    assert submit(client, headers, claim).status_code == 400
    with app.app_context():
        product = db.session.get(Product, claim["product_id"])
        product.is_active = True
        document = db.session.get(Document, claim["documents"][0]["id"])
        (Path(app.config["UPLOAD_FOLDER"]) / document.storage_path).write_bytes(b"tampered")
        db.session.commit()
    result = submit(client, headers, claim)
    assert result.status_code == 400 and "documents" in result.json["error"]["details"]


@pytest.mark.parametrize("role", ["employee", "reviewer", "admin"])
def test_only_customers_submit(client, accounts, role):
    assert client.post("/api/claims/draft", json={}, headers=bearer(login(client, role)["access_token"])).status_code == 403


@pytest.mark.parametrize("method,path", [("get", "/api/products/my"), ("post", "/api/claims/draft"),
    ("put", "/api/claims/draft/1"), ("post", "/api/claims/upload"), ("post", "/api/claims/submit"),
    ("get", "/api/claims/1"), ("get", "/api/claims/my")])
def test_unauthenticated(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_concurrent_numbering_and_year_reset(app):
    def allocate(_):
        with app.app_context():
            result = next_claim_id(2026)
            db.session.commit()
            return result
    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers = list(pool.map(allocate, range(12)))
    assert sorted(identifiers) == [f"CLM-2026-{i:06d}" for i in range(1, 13)]
    with app.app_context():
        assert next_claim_id(2027) == "CLM-2027-000001"
        db.session.rollback()
        assert next_claim_id(2027) == "CLM-2027-000001"


def test_failed_storage_does_not_commit_metadata(client, app, headers, claims, monkeypatch):
    from backend.services.claim_storage import LocalStorage
    claim = draft(client, headers, claims["customer"]["product"])
    def fail(*args):
        raise OSError("Storage unavailable")
    monkeypatch.setattr(LocalStorage, "put", fail)
    assert upload(client, headers, claim).status_code == 500
    result = client.get(f"/api/claims/{claim['id']}", headers=headers).json["claim"]
    assert result["version"] == claim["version"] and result["documents"] == []


def test_concurrent_submission_creates_one_id(client, app, headers, claims):
    claim = complete(client, headers, claims["customer"]["product"])
    def send(_):
        with app.test_client() as independent:
            result = submit(independent, headers, claim)
            return result.status_code, result.json
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert sorted(status for status, _ in results) in ([200, 201], [201, 409])
    result = submit(client, headers, claim)
    assert result.json["claim"]["claim_id"].endswith("000001")
    with app.app_context():
        assert len(db.session.scalars(select(AuditLog).where(AuditLog.action == "claim.submit")).all()) == 1


def test_s3_adapter_uses_private_objects_and_preserves_keys(app, monkeypatch):
    import sys
    from types import SimpleNamespace
    from backend.services.claim_storage import storage
    objects = {}
    class FakeS3:
        def put_object(self, **kwargs):
            assert kwargs["Bucket"] == "private-claims" and kwargs["ServerSideEncryption"] == "AES256"
            assert "ACL" not in kwargs
            objects[kwargs["Key"]] = kwargs["Body"]
        def get_object(self, **kwargs):
            return {"Body": BytesIO(objects[kwargs["Key"]])}
        def delete_object(self, **kwargs):
            objects.pop(kwargs["Key"])
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda *args, **kwargs: FakeS3()))
    monkeypatch.setitem(sys.modules, "botocore.exceptions", SimpleNamespace(ClientError=RuntimeError))
    app.config.update(CLAIM_STORAGE="s3", S3_BUCKET="private-claims")
    with app.app_context():
        store = storage()
        store.put("claims/1/key.png", png(), "image/png")
        assert store.read("claims/1/key.png") == png()
        store.delete("claims/1/key.png")
        assert objects == {}
