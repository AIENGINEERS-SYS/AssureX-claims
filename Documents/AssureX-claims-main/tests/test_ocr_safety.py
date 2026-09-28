import pytest
from backend.db.models import Claim, Document
from backend.extensions import db
from backend.services.document_service import process_document
from backend.services.ocr_service import OCRResult, OCRProcessingError
from test_auth import app, client, accounts, claims


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -.1, 1.1])
def test_invalid_provider_confidence_becomes_processing_failure(app, claims, confidence):
    class Provider:
        def extract_text(self, *args):
            return OCRResult("Serial: ABC123", confidence, "test")
    with app.app_context():
        document = Document(id=1, claim_id=claims["customer"]["claim"], mime_type="image/png")
        assert process_document(document, b"fixture", provider=Provider()) == "ocr_failed"
        assert document.ocr_status == "failed" and document.ocr_confidence is None


def test_provider_secrets_do_not_reach_response_or_logs(app, claims, caplog):
    class Provider:
        def extract_text(self, *args):
            raise RuntimeError("secret-provider-token-and-personal-data")
    with app.app_context():
        document = Document(id=1, claim_id=claims["customer"]["claim"], mime_type="image/png")
        assert process_document(document, b"fixture", provider=Provider()) == "ocr_failed"
        assert "secret-provider-token" not in document.ocr_error
        assert "secret-provider-token" not in caplog.text
