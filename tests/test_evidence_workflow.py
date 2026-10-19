"""Category routing, registration reuse, recovery and real OCR regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
from types import SimpleNamespace
import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from backend.db.models import Document, Product
from backend.extensions import db
from backend.services.document_extraction import extract_fields
from backend.services.document_service import process_document
from backend.services.ocr_service import OCRResult, OCRProcessingError, TesseractOCRProvider
from test_auth import app, client, accounts, claims, bearer, login
from test_claim_submission import draft, upload, png, submit, headers


def ready(client, headers, product_id):
    return draft(client, headers, product_id, fault_date=date.today().isoformat(),
        fault_type='Electrical Failure', description='The screen will no longer start when connected to power.',
        damage_category='Moderate', current_step=3)


@pytest.mark.parametrize('kind,keys', [
    ('receipt', {'purchase_date','invoice_number','product_name','model_number','serial_number','retailer','purchase_price','warranty_duration'}),
    ('product_image', {'product_name','model_number','serial_number'}),
    ('serial_number_image', {'model_number','serial_number'}),
    ('warranty_card', {'model_number','serial_number','warranty_duration'}),
    ('damage_evidence', set()), ('other', set()),
])
def test_scoped_extraction(kind, keys):
    result = extract_fields('Invoice Number: INV-1\nModel: M1\nTotal: NGN 100\nWarranty: 12 months', kind)
    assert set(result) == keys


def test_registered_product_requires_only_photographs_and_survives_refresh(client, headers, claims, monkeypatch):
    calls = []
    class IdentityOCR:
        def extract_text(self, content, mime):
            calls.append(content)
            return OCRResult('Model: M1', .99, 'test')
    monkeypatch.setattr('backend.services.document_service.get_ocr_provider', lambda: IdentityOCR())
    claim = ready(client, headers, claims['customer']['product'])
    original = claim['product']
    assert claim['document_policy']['required_types'] == ['product_image','damage_evidence']
    for kind, color in [('product_image','red'), ('damage_evidence','blue')]:
        response = upload(client, headers, claim, kind, content=png(color))
        assert response.status_code == 201, response.json
        claim, evidence = response.json['claim'], response.json['document']
        assert evidence['evidence_category'] == kind.upper()
        assert 'invoice_number' not in evidence['extracted_data']
        assert not any(key in evidence['extracted_data'] for key in ['purchase_date','retailer','purchase_price'])
        assert claim['product'] == original
        if kind == 'product_image':
            response = client.patch(f"/api/documents/{evidence['id']}/ocr-review", headers=headers,
                json={'version':claim['version'], 'confirm':True})
            assert response.status_code == 200, response.json
            claim = response.json['claim']
        else:
            assert evidence['processing_status'] == 'NEEDS_REVIEW'
            assert evidence['analysis']['quality']['width'] == 8
    assert len(calls) == 1  # Damage does not invoke OCR.
    resumed = client.get(f"/api/claims/{claim['id']}", headers=headers).json['claim']
    assert resumed['product'] == original and len(resumed['documents']) == 2
    assert resumed['document_completeness']['missing'] == []
    response = submit(client, headers, resumed)
    assert response.status_code == 201, response.json
    assert submit(client, headers, resumed).json['claim']['id'] == response.json['claim']['id']


def test_manual_receipt_confirmation_after_failure_is_reused(client, app, headers, claims):
    claim = ready(client, headers, claims['customer']['product'])
    response = upload(client, headers, claim)
    claim, document = response.json['claim'], response.json['document']
    assert document['ocr_status'] == 'failed'
    reviewed = client.patch(f"/api/documents/{document['id']}/ocr-review", headers=headers,
        json={'version':claim['version'], 'confirm':True, 'invoice_number':'INV-USER', 'serial_number':'conflict'})
    assert reviewed.status_code == 200, reviewed.json
    claim = reviewed.json['claim']
    assert reviewed.json['document']['metadata_conflicts'][0]['field'] == 'serial_number'
    assert claim['product']['serial_number'] == 'customer'
    metadata = claim['product']['purchase_metadata']['invoice_number']
    assert metadata['confirmed_value'] == 'INV-USER' and metadata['provenance'] == 'user_confirmed'
    retried = client.post(f"/api/documents/{document['id']}/ocr/retry", headers=headers,
        json={'version':claim['version']})
    assert retried.status_code == 200
    assert retried.json['document']['verified_data']['invoice_number']['confirmed_value'] == 'INV-USER'
    another = draft(client, headers, claims['customer']['product'])
    assert another['product']['purchase_metadata']['invoice_number'] == metadata
    with app.app_context():
        doc = db.session.get(Document, document['id'])
        assert doc.product_id == claims['customer']['product'] and doc.warranty_id == claims['customer']['warranty']


def test_category_alias_and_inapplicable_correction_rejected(client, headers, claims):
    claim = draft(client, headers, claims['customer']['product'])
    url = f"/api/claims/{claim['id']}/documents"
    response = client.post(url, headers=headers, data={'version':str(claim['version']),
        'evidence_category':'DAMAGE_EVIDENCE', 'document_type':'receipt',
        'file':(BytesIO(png()),'photo.png','image/png')})
    assert response.status_code == 400
    response = client.post(url, headers=headers, data={'version':str(claim['version']),
        'evidence_category':'DAMAGE_EVIDENCE', 'file':(BytesIO(png()),'photo.png','image/png')})
    assert response.status_code == 201, response.json
    claim, doc = response.json['claim'], response.json['document']
    assert doc['applicable_fields'] == []
    response = client.patch(f"/api/documents/{doc['id']}/ocr-review", headers=headers,
        json={'version':claim['version'], 'confirm':True, 'invoice_number':'FAKE'})
    assert response.status_code == 400


def test_simultaneous_uploads_reject_stale_then_allow_retry_without_loss(client, app, headers, claims):
    claim = ready(client, headers, claims['customer']['product'])
    def send(color):
        with app.test_client() as independent:
            response = upload(independent, headers, claim, 'damage_evidence', content=png(color))
            return color, response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, ['red','blue']))
    assert sorted(code for _,code in results) == [201,409]
    resumed = client.get(f"/api/claims/{claim['id']}", headers=headers).json['claim']
    color = next(color for color,code in results if code == 409)
    response = upload(client, headers, resumed, 'damage_evidence', content=png(color))
    assert response.status_code == 201
    documents = response.json['claim']['documents']
    assert len({doc['evidence_id'] for doc in documents}) == 2
    assert response.json['claim']['product'] == claim['product']


@pytest.mark.parametrize('kind', ['serial_number_image','receipt'])
def test_real_tesseract_scoped_serial_ocr(app, kind):
    import shutil
    if not shutil.which('tesseract'):
        pytest.skip('Real Tesseract binary unavailable')
    picture = Image.new('RGB',(1600,420),'white')
    font = ImageFont.load_default(size=42)
    ImageDraw.Draw(picture).multiline_text((50,50),'Model Number: MODEL123\nSerial Number: SERIAL987\nInvoice Number: INV456',font=font,fill='black',spacing=35)
    content=BytesIO(); picture.save(content,format='PNG')
    with app.app_context():
        result = TesseractOCRProvider().extract_text(content.getvalue(),'image/png')
        fields = extract_fields(result.text,kind)
    assert fields['model_number']['value'] == 'MODEL123'
    assert fields['serial_number']['value'] == 'SERIAL987'
    if kind == 'serial_number_image':
        assert 'retailer' not in fields
    else:
        assert fields['invoice_number']['value'] == 'INV456'


def test_low_confidence_observations_do_not_become_material_contradictions():
    from backend.services.claim_rules import ClaimRuleEngine
    doc = SimpleNamespace(id=1,document_type='serial_number_image',verified_data=None,
        extracted_data={'serial_number':{'value':'WRONG', 'confidence':.2}})
    assert ClaimRuleEngine._document_evidence([doc])[0][1] == {}
    doc.verified_data={'serial_number':{'confirmed_value':'REVIEWED'}}
    assert ClaimRuleEngine._document_evidence([doc])[0][1]['serial_number'] == 'REVIEWED'


def test_successful_retry_preserves_human_confirmation_and_processing_is_idempotent(client, app, headers, claims, monkeypatch):
    claim = draft(client, headers, claims['customer']['product'])
    response = upload(client, headers, claim)
    document = response.json['document']; claim = response.json['claim']
    response = client.patch(f"/api/documents/{document['id']}/ocr-review", headers=headers,
        json={'version':claim['version'], 'confirm':True, 'invoice_number':'USER-CONFIRMED'})
    claim = response.json['claim']
    calls = []
    class ChangedOCR:
        def extract_text(self, content, mime):
            calls.append(content)
            return OCRResult('Invoice Number: NEW-PREDICTION', .99, 'test', '2')
    monkeypatch.setattr('backend.services.document_service.get_ocr_provider', lambda: ChangedOCR())
    response = client.post(f"/api/documents/{document['id']}/ocr/retry", headers=headers,
        json={'version':claim['version']})
    assert response.status_code == 200, response.json
    observed = response.json['document']
    assert observed['extracted_data']['invoice_number']['value'] == 'NEW-PREDICTION'
    assert observed['verified_data']['invoice_number']['confirmed_value'] == 'USER-CONFIRMED'
    assert response.json['claim']['product']['purchase_metadata']['invoice_number']['confirmed_value'] == 'USER-CONFIRMED'
    with app.app_context():
        stored = db.session.get(Document, document['id'])
        before = stored.ocr_processed_at
        process_document(stored, png(), provider=ChangedOCR())
        assert stored.ocr_processed_at == before
    assert len(calls) == 1
