"""Phase 6 validation, OCR, duplicate, correction and IDOR coverage."""
from datetime import date
from io import BytesIO
from pathlib import Path
import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select
from backend.db.models import AuditLog, Claim, Document
from backend.extensions import db
from backend.services.document_extraction import extract_fields
from backend.services.ocr_service import OCRResult, TesseractOCRProvider
from test_auth import app, client, accounts, claims, bearer, login
from test_claim_submission import complete, draft, submit


def image_bytes(fmt="PNG", color="navy"):
    stream = BytesIO()
    Image.new("RGB", (24, 16), color).save(stream, format=fmt)
    return stream.getvalue()


def pdf_bytes(*, encrypted=False, pages=1):
    stream = BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(72, 72)
    if encrypted:
        writer.encrypt("secret")
    writer.write(stream)
    return stream.getvalue()


class ReceiptProvider:
    def extract_text(self, content, mime_type):
        return OCRResult("""Example Electronics
Invoice Number: INV-83927
Purchase Date: 26/09/2026
Product Name: Samsung Refrigerator
Model Number: RT38K5982SL
Serial Number: SN78299I04
Total: NGN 450,000
        Warranty Period: 24 months""", 0.72, "fixture-ocr", "1.0")


@pytest.fixture
def headers(client, accounts):
    return bearer(login(client)["access_token"])


@pytest.fixture
def ocr(monkeypatch):
    monkeypatch.setattr("backend.services.document_service.get_ocr_provider", lambda: ReceiptProvider())


def upload_new(client, headers, claim, *, content=None, filename="receipt.png", mime="image/png",
               kind="receipt"):
    return client.post(f"/api/claims/{claim['id']}/documents", headers=headers,
        data={"version": str(claim["version"]), "document_type": kind,
              "file": (BytesIO(content if content is not None else image_bytes()), filename, mime)})


@pytest.mark.parametrize("filename,mime,content", [
    ("receipt.png", "image/png", image_bytes("PNG")),
    ("receipt.jpg", "image/jpeg", image_bytes("JPEG", "red")),
    ("receipt.jpeg", "image/jpeg", image_bytes("JPEG", "green")),
    ("receipt.pdf", "application/pdf", pdf_bytes()),
])
def test_supported_files_validate_store_and_process(client, headers, claims, ocr, filename, mime, content):
    claim = draft(client, headers, claims["customer"]["product"])
    response = upload_new(client, headers, claim, content=content, filename=filename, mime=mime)
    assert response.status_code == 201, response.json
    document = response.json["document"]
    assert document["upload_status"] == "stored" and document["ocr_status"] in {"COMPLETED", "completed", "review_required"}
    assert document["file_name"] == filename and document["review_status"] == "pending"
    assert document["extracted_data"]["invoice_number"]["value"] == "INV-83927"
    assert document["ocr_confidence_label"] == "Needs review"


@pytest.mark.parametrize("filename,mime,content,code", [
    ("receipt.pdf.exe", "application/octet-stream", b"MZ", "unsupported_file_type"),
    ("invoice.jpg.js", "application/javascript", b"alert(1)", "unsupported_file_type"),
    ("receipt.pdf", "application/pdf", b"MZ executable", "mime_mismatch"),
    ("invoice.jpg", "image/jpeg", b"<html>unsafe</html>", "mime_mismatch"),
    ("empty.png", "image/png", b"", "corrupt_file"),
    ("bad.png", "image/png", b"\x89PNG\r\n\x1a\ntruncated", "corrupt_file"),
    ("bad.pdf", "application/pdf", b"%PDF-corrupt", "corrupt_file"),
    ("wrong.png", "application/pdf", image_bytes(), "mime_mismatch"),
    ("secret.pdf", "application/pdf", pdf_bytes(encrypted=True), "encrypted_pdf"),
])
def test_structured_validation_errors(client, headers, claims, filename, mime, content, code):
    claim = draft(client, headers, claims["customer"]["product"])
    response = upload_new(client, headers, claim, content=content, filename=filename, mime=mime)
    assert response.status_code in {400, 413}
    assert response.json["error"]["code"] == code


def test_same_claim_duplicate_hash_rejected_not_filename(client, headers, claims):
    claim = draft(client, headers, claims["customer"]["product"])
    first = upload_new(client, headers, claim, content=image_bytes("PNG", "red"), filename="one.png")
    assert first.status_code == 201
    claim = first.json["claim"]
    same = upload_new(client, headers, claim, content=image_bytes("PNG", "red"), filename="renamed.png")
    assert same.status_code == 409 and same.json["error"]["code"] == "duplicate_document"
    different = upload_new(client, headers, claim, content=image_bytes("PNG", "blue"), filename="one.png")
    assert different.status_code == 201


def test_cross_claim_duplicate_is_preserved_and_flagged(client, headers, claims):
    content = image_bytes("PNG", "purple")
    first = draft(client, headers, claims["customer"]["product"])
    assert upload_new(client, headers, first, content=content).status_code == 201
    second = draft(client, headers, claims["customer"]["product"])
    response = upload_new(client, headers, second, content=content)
    assert response.status_code == 201 and "cross_claim_duplicate" not in response.json["document"]
    with client.application.app_context():
        assert db.session.get(Document, response.json["document"]["id"]).cross_claim_duplicate is True


def test_extraction_nigerian_formats_and_ambiguity():
    result = extract_fields("""Example Electronics
Receipt No: RCPT-20
Purchased: 26 September 2026
Product: Deep Freezer
Model: DF-20
S/N: SN-00991
Amount Paid: ₦425,000
Warranty: 2 years""", "receipt")
    assert result["purchase_date"]["value"] == "2026-09-26"
    assert result["invoice_number"]["value"] == "RCPT-20"
    assert result["product_name"]["value"] == "Deep Freezer"
    assert result["model_number"]["value"] == "DF-20"
    assert result["serial_number"]["value"] == "SN-00991"
    assert result["retailer"]["value"] == "Example Electronics"
    assert result["purchase_price"]["value"] == 425000 and result["purchase_price"]["currency"] == "NGN"
    assert result["warranty_duration"]["value"] == 2 and result["warranty_duration"]["unit"] == "years"
    ambiguous = extract_fields("Purchase Date: 01/02/2026", "receipt")["purchase_date"]
    assert ambiguous["value"] is None and ambiguous["state"] == "ambiguous" and len(ambiguous["candidates"]) == 2
    assert extract_fields("unstructured image", "product_image")["serial_number"]["state"] == "not_detected"


def test_text_pdf_uses_native_extraction_without_tesseract(app, monkeypatch):
    import fitz
    pdf = fitz.open(); page = pdf.new_page(); page.insert_text((30, 50), "Invoice Number INV-100 Purchase Date 26/09/2026")
    content = pdf.tobytes(); pdf.close()
    provider = TesseractOCRProvider()
    monkeypatch.setattr(provider, "_ocr_image", lambda image, timeout=None: pytest.fail("native PDF text should not run OCR"))
    with app.app_context():
        result = provider.extract_text(content, "application/pdf")
    assert "INV-100" in result.text and result.confidence == pytest.approx(0.99)


def test_scanned_multipage_pdf_uses_page_ocr(app, monkeypatch):
    import fitz
    image = image_bytes()
    pdf = fitz.open()
    for _ in range(2):
        page = pdf.new_page(width=100, height=100); page.insert_image(page.rect, stream=image)
    content = pdf.tobytes(); pdf.close()
    provider = TesseractOCRProvider()
    monkeypatch.setattr(provider, "_ocr_image", lambda image, timeout=None: ("Serial Number: SCAN-1", 0.8, "fixture"))
    with app.app_context():
        result = provider.extract_text(content, "application/pdf")
    assert result.text.count("SCAN-1") == 2 and result.provider == "pymupdf+tesseract"


def test_ocr_review_preserves_original_and_correction_audit(client, app, headers, claims, ocr):
    claim = draft(client, headers, claims["customer"]["product"])
    response = upload_new(client, headers, claim)
    document, claim = response.json["document"], response.json["claim"]
    assert document["extracted_data"]["serial_number"]["value"] == "SN78299I04"
    reviewed = client.patch(f"/api/documents/{document['id']}/ocr-review", headers=headers, json={
        "version": claim["version"], "confirm": True, "purchase_date": "2026-09-26",
        "invoice_number": "INV-83927", "product_name": "Samsung Refrigerator",
        "model_number": "RT38K5982SL", "serial_number": "SN78299104",
        "retailer": "Example Electronics", "purchase_price": "450000.00",
        "warranty_duration": 24, "warranty_duration_unit": "months"})
    assert reviewed.status_code == 200, reviewed.json
    result = reviewed.json["document"]
    assert result["extracted_data"]["serial_number"]["value"] == "SN78299I04"
    correction = result["verified_data"]["serial_number"]
    assert correction["ocr_value"] == "SN78299I04" and correction["confirmed_value"] == "SN78299104"
    assert correction["was_corrected"] and correction["corrected_by"] and correction["corrected_at"]
    assert result["review_status"] == "confirmed" and result["reviewed_at"]
    with app.app_context():
        actions = set(db.session.scalars(select(AuditLog.action)).all())
        assert {"document_uploaded", "ocr_started", "ocr_completed", "ocr_reviewed", "ocr_corrected"} <= actions


def test_owner_and_staff_document_authorization(client, app, headers, claims, ocr):
    claim = draft(client, headers, claims["customer"]["product"])
    uploaded = upload_new(client, headers, claim)
    document, claim = uploaded.json["document"], uploaded.json["claim"]
    assert client.get(f"/api/documents/{document['id']}", headers=headers).status_code == 200
    assert client.get(f"/api/documents/{document['id']}/ocr", headers=headers).status_code == 200
    assert client.get(f"/api/documents/{document['id']}/content", headers=headers).status_code == 200
    other = bearer(login(client, "other")["access_token"])
    assert client.get(f"/api/documents/{document['id']}", headers=other).status_code == 404
    assert client.patch(f"/api/documents/{document['id']}/ocr-review", headers=other,
        json={"version": claim["version"], "confirm": True}).status_code == 404
    assert client.delete(f"/api/documents/{document['id']}", headers=other,
        json={"version": claim["version"]}).status_code == 404
    reviewer = bearer(login(client, "reviewer")["access_token"])
    assert client.get(f"/api/documents/{document['id']}", headers=reviewer).status_code == 404
    with app.app_context():
        stored = db.session.get(Claim, claim["id"]); stored.manual_review_required = True; db.session.commit()
    assert client.get(f"/api/documents/{document['id']}", headers=reviewer).status_code == 200
    admin = bearer(login(client, "admin")["access_token"])
    assert client.get(f"/api/documents/{document['id']}", headers=admin).status_code == 200
    deleted = client.delete(f"/api/documents/{document['id']}", headers=headers,
        json={"version": claim["version"]})
    assert deleted.status_code == 200
    assert client.get(f"/api/documents/{document['id']}", headers=headers).status_code == 404


def test_review_required_blocks_submit(client, headers, claims, ocr):
    claim = draft(client, headers, claims["customer"]["product"], fault_date=date.today().isoformat(),
        fault_type="Electrical Failure", description="The product no longer starts when it is connected to power.",
        damage_category="Moderate")
    response = upload_new(client, headers, claim)
    claim = response.json["claim"]
    validation = client.get(f"/api/claims/{claim['id']}", headers=headers).json["validation_errors"]
    assert any("Review" in message for message in validation["documents"])


def test_full_upload_ocr_review_and_submit_integration(client, headers, claims, ocr):
    claim = draft(client, headers, claims["customer"]["product"], fault_date=date.today().isoformat(),
        fault_type="Electrical Failure", description="The product no longer starts when it is connected to power.",
        damage_category="Moderate", current_step=3)
    for kind, color in zip(("receipt", "product_image", "serial_number_image", "damage_evidence"),
                           ("red", "green", "blue", "yellow")):
        uploaded = upload_new(client, headers, claim, content=image_bytes("PNG", color), kind=kind,
            filename=f"{kind}.png")
        assert uploaded.status_code == 201, uploaded.json
        claim, document = uploaded.json["claim"], uploaded.json["document"]
        reviewed = client.patch(f"/api/documents/{document['id']}/ocr-review", headers=headers,
            json={"version": claim["version"], "confirm": True})
        assert reviewed.status_code == 200, reviewed.json
        claim = reviewed.json["claim"]
    response = submit(client, headers, claim)
    assert response.status_code == 201 and response.json["claim"]["status"] == "SUBMITTED"


def test_ocr_failure_does_not_block_valid_claim(client, app, headers, claims):
    claim = complete(client, headers, claims["customer"]["product"])
    response = submit(client, headers, claim)
    assert response.status_code == 201
    with app.app_context():
        assert db.session.get(Claim, response.json["claim"]["id"]).manual_review_required is True


def test_configured_document_count_limit(client, app, headers, claims):
    app.config["MAX_DOCUMENTS_PER_CLAIM"] = 1
    claim = draft(client, headers, claims["customer"]["product"])
    first = upload_new(client, headers, claim, content=image_bytes("PNG", "red"))
    second = upload_new(client, headers, first.json["claim"], content=image_bytes("PNG", "green"))
    assert second.status_code == 413 and second.json["error"]["code"] == "document_limit_reached"


def test_document_storage_uses_random_private_name(client, app, headers, claims):
    claim = draft(client, headers, claims["customer"]["product"])
    response = upload_new(client, headers, claim, filename="../../receipt.png")
    assert response.status_code == 201 and response.json["document"]["file_name"] == "receipt.png"
    with app.app_context():
        stored = db.session.get(Document, response.json["document"]["id"])
        assert stored.stored_filename != stored.original_filename
        assert stored.storage_path.startswith(f"documents/{claim['id']}/")
        assert (Path(app.config["UPLOAD_FOLDER"]) / stored.storage_path).is_file()
