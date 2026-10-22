"""Regressions for implementation review: legacy data, uncertainty and retries."""
from types import SimpleNamespace
from PIL import Image
from io import BytesIO
import pytest
from backend.db.models import Claim, Document
from sqlalchemy import select
from backend.extensions import db
from backend.services.ocr_service import OCRResult
from test_auth import app, client, accounts, claims, bearer, login
from test_claim_submission import headers, draft, upload, png, submit
from test_evidence_workflow import ready


def test_legacy_damage_purchase_review_does_not_deadlock_draft(client, app, headers, claims):
    claim = ready(client, headers, claims['customer']['product'])
    for kind, color in [('product_image','red'),('damage_evidence','blue')]:
        result = upload(client, headers, claim, kind, content=png(color))
        assert result.status_code == 201
        claim = result.json['claim']
    with app.app_context():
        for doc in db.session.get(Document, claim['documents'][0]['id']), db.session.get(Document, claim['documents'][1]['id']):
            doc.ocr_status = 'completed'
            doc.processing_metadata = None
            doc.extracted_data = {'invoice_number': {'value':'OLD-PURCHASE-DATA','confidence':.99}}
            doc.review_status = 'pending'
        db.session.commit()
    response = client.get(f"/api/claims/{claim['id']}",headers=headers)
    assert response.status_code == 200
    for doc in response.json['claim']['documents']:
        assert doc['review_status'] == 'not_required'
        assert doc['processing_status'] == 'NEEDS_REVIEW'
        assert doc['validation_warnings']
    assert 'documents' not in response.json['validation_errors']
    assert submit(client,headers,response.json['claim']).status_code == 201


@pytest.mark.parametrize("legacy_invoice", ["OLD-INVOICE", {"value":"OLD-INVOICE","confidence":.9}])
def test_legacy_scalar_metadata_loads_and_can_be_reviewed(client, app, headers, claims, legacy_invoice):
    claim = draft(client,headers,claims['customer']['product'])
    response = upload(client,headers,claim)
    doc_id = response.json['document']['id']; claim = response.json['claim']
    with app.app_context():
        doc = db.session.get(Document,doc_id)
        doc.extracted_data = {'invoice_number':'OLD-INVOICE','model_number':'M1'}
        doc.verified_data = {'invoice_number':legacy_invoice,'model_number':'M1'}
        doc.ocr_status = 'completed'
        doc.review_status = 'confirmed'
        db.session.commit()
    response = client.get(f'/api/documents/{doc_id}',headers=headers)
    assert response.status_code == 200
    assert response.json['document']['verified_data']['invoice_number']['confirmed_value'] == 'OLD-INVOICE'
    response = client.patch(f'/api/documents/{doc_id}/ocr-review',headers=headers,
        json={'version':claim['version'],'confirm':True,'invoice_number':'CORRECTED'})
    assert response.status_code == 200,response.json
    assert response.json['document']['verified_data']['model_number']['confirmed_value'] == 'M1'


def test_partial_review_preserves_warranty_units_and_stable_provenance(client,headers,claims):
    response = upload(client,headers,draft(client,headers,claims['customer']['product']))
    doc = response.json['document']; claim = response.json['claim']
    response = client.patch(f"/api/documents/{doc['id']}/ocr-review",headers=headers,
        json={'version':claim['version'],'confirm':True,'warranty_duration':2,'warranty_duration_unit':'years','invoice_number':'FIRST'})
    assert response.status_code == 200,response.json
    claim = response.json['claim']
    response = client.patch(f"/api/documents/{doc['id']}/ocr-review",headers=headers,
        json={'version':claim['version'],'confirm':True,'invoice_number':'SECOND'})
    assert response.status_code == 200,response.json
    assert response.json['document']['verified_data']['warranty_duration']['unit'] == 'years'
    metadata = response.json['claim']['product']['purchase_metadata']
    assert metadata['invoice_number']['confirmed_value'] == 'SECOND'
    assert metadata['invoice_number']['source_claim_id'] == claim['id']


@pytest.mark.parametrize('data',[{'warranty_duration':2},{'warranty_duration':101,'warranty_duration_unit':'years'}])
def test_warranty_corrections_require_valid_effective_units(client,headers,claims,data):
    response = upload(client,headers,draft(client,headers,claims['customer']['product']))
    doc = response.json['document']; claim = response.json['claim']
    response = client.patch(f"/api/documents/{doc['id']}/ocr-review",headers=headers,
        json={'version':claim['version'],'confirm':True,**data})
    assert response.status_code == 400
    assert client.get(f"/api/claims/{claim['id']}",headers=headers).json['claim']['version'] == claim['version']


def assessment(fields=None, **extra):
    return {'fields': fields or {'severity':{'value':'minor','confidence':.95}},
        'provider':'test-vision','version':'1','warnings':[], 'needs_review':False,**extra}


@pytest.mark.parametrize('bad',[
    assessment(fields={'invoice_number':{'value':'WRONG-PIPELINE','confidence':.9}}),
    assessment(fields={'severity':None}), assessment(fields={'severity':{'value':'minor','confidence':float('nan')}}),
    assessment(needs_review='false'), assessment(warnings='bad-shape'),
])
def test_malformed_vision_results_fail_safely(client,app,headers,claims,bad):
    app.extensions['evidence_vision'] = SimpleNamespace(assess=lambda *args:bad)
    response = upload(client,headers,draft(client,headers,claims['customer']['product']),'damage_evidence')
    assert response.status_code == 201,response.json
    assert response.json['document']['processing_status'] == 'FAILED'
    assert response.json['document']['extracted_data'] == {}


def test_failed_visual_retry_preserves_successful_assessment(client,app,headers,claims):
    app.extensions['evidence_vision'] = SimpleNamespace(assess=lambda *args:assessment(warnings=['Recheck photograph']))
    response = upload(client,headers,draft(client,headers,claims['customer']['product']),'damage_evidence')
    claim = response.json['claim']; doc = response.json['document']
    previous = doc['analysis']
    app.extensions['evidence_vision'] = SimpleNamespace(assess=lambda *args:None)
    response = client.post(f"/api/documents/{doc['id']}/ocr/retry",headers=headers,json={'version':claim['version']})
    assert response.status_code == 200,response.json
    assert response.json['document']['ocr_status'] == 'failed'
    assert response.json['document']['analysis'] == previous


def test_low_confidence_visual_result_cannot_claim_completed_assessment(client,app,headers,claims):
    app.extensions['evidence_vision'] = SimpleNamespace(assess=lambda *args:
        assessment(fields={'severity':{'value':'minor','confidence':.2}}))
    stream=BytesIO(); Image.new('RGB',(640,640),'white').save(stream,format='PNG')
    response = upload(client,headers,draft(client,headers,claims['customer']['product']),
        'damage_evidence',content=stream.getvalue())
    assert response.status_code == 201
    assert response.json['document']['processing_status'] == 'NEEDS_REVIEW'
    assert response.json['document']['analysis']['needs_review'] is True


def test_visual_product_identity_is_compared_with_registration(client,app,headers,claims):
    app.extensions['evidence_vision'] = SimpleNamespace(assess=lambda *args:
        assessment(fields={'model_number':{'value':'WRONG-MODEL','confidence':.99}}))
    response = upload(client,headers,draft(client,headers,claims['customer']['product']),'product_image')
    assert response.status_code == 201,response.json
    doc = response.json['document']
    assert doc['extracted_data']['model_number']['value'] == 'WRONG-MODEL'
    assert any(item['field'] == 'model_number' for item in doc['metadata_conflicts'])


@pytest.mark.parametrize("kind", ["receipt", "product_image"])
def test_invalid_ocr_confidence_cannot_break_upload_transaction(client,headers,claims,monkeypatch,kind):
    provider=SimpleNamespace(extract_text=lambda *args:OCRResult('Invoice Number: ABC',float('nan'),'test'))
    monkeypatch.setattr('backend.services.document_service.get_ocr_provider',lambda:provider)
    response = upload(client,headers,draft(client,headers,claims['customer']['product']),kind)
    assert response.status_code == 201,response.json
    assert response.json['document']['processing_status'] == ('FAILED' if kind == 'receipt' else 'NEEDS_REVIEW')
    assert response.json['document']['ocr_confidence'] is None


def test_unassessed_photos_escalate_even_when_both_models_agree(client,app,headers,claims,monkeypatch):
    from test_evaluation import output
    class Model:
        def predict(self,claim,policy):
            return output('model',top=.95),None
    monkeypatch.setattr('backend.services.evaluation.PythonPredictionService',Model)
    monkeypatch.setattr('backend.services.evaluation.GTMPredictionService',Model)
    claim = ready(client,headers,claims['customer']['product'])
    for kind,color in [('product_image','red'),('damage_evidence','blue')]:
        claim = upload(client,headers,claim,kind,content=png(color)).json['claim']
    claim = submit(client,headers,claim).json['claim']
    # A submitted customer claim does not implicitly grant every reviewer access.
    with app.app_context():
        from backend.db.models import User
        stored = db.session.get(Claim, claim['id'])
        stored.assigned_reviewer_id = db.session.scalar(select(User.id).where(User.role == 'reviewer'))
        db.session.commit()
    response = client.post(f"/api/claims/{claim['id']}/evaluate",headers=bearer(login(client,'reviewer')['access_token']))
    assert response.status_code == 201,response.json
    assert response.json['evaluation']['recommendation'] == 'manual_review_required'
    assert any(rule['rule_code']=='evidence_assessment' and rule['result']=='manual_review' for rule in response.json['rules'])


def test_future_warranty_start_is_not_treated_as_active_coverage(app,claims):
    from datetime import date,timedelta
    from backend.db.models import Claim
    from backend.services.claim_rules import ClaimRuleEngine
    from backend.services.evaluation import policy_for_claim
    with app.app_context():
        claim = db.session.get(Claim,claims['customer']['claim'])
        claim.submission_date = date.today()
        claim.warranty.start_date = date.today()+timedelta(days=10)
        policy,_ = policy_for_claim(claim)
        rules,_ = ClaimRuleEngine().evaluate(claim,policy)
        rule=next(item for item in rules if item['rule_code']=='warranty_expiry')
        assert rule['result']=='failed'
        assert 'not started' in rule['message']


def test_visual_brand_conflicts_are_preserved_for_review(client,app,headers,claims):
    app.extensions['evidence_vision'] = SimpleNamespace(assess=lambda *args:
        assessment(fields={'brand':{'value':'WRONG BRAND','confidence':.99}}))
    response=upload(client,headers,draft(client,headers,claims['customer']['product']),'product_image')
    assert response.status_code == 201,response.json
    assert any(item['field']=='brand' for item in response.json['document']['metadata_conflicts'])
    assert response.json['claim']['product']['brand'] == 'Example'


def test_interrupted_processing_can_be_retried_after_timeout(client,app,headers,claims,monkeypatch):
    from datetime import timedelta
    from backend.db.models import utcnow
    response=upload(client,headers,draft(client,headers,claims['customer']['product']))
    claim=response.json['claim']; doc=response.json['document']
    with app.app_context():
        stored=db.session.get(Document,doc['id'])
        stored.ocr_status='processing'
        stored.processing_metadata={'attempt_id':'interrupted','attempt_started_at':utcnow().isoformat()}
        db.session.commit()
    response=client.post(f"/api/documents/{doc['id']}/ocr/retry",headers=headers,json={'version':claim['version']})
    assert response.status_code == 409  # A genuinely active request is protected.
    with app.app_context():
        stored=db.session.get(Document,doc['id'])
        stored.processing_metadata={'attempt_id':'interrupted','attempt_started_at':(utcnow()-timedelta(minutes=5)).isoformat()}
        db.session.commit()
    response=client.get(f"/api/documents/{doc['id']}",headers=headers)
    assert response.json['document']['can_retry'] is True
    monkeypatch.setattr('backend.services.document_service.get_ocr_provider',lambda:
        SimpleNamespace(extract_text=lambda *args:OCRResult('Invoice Number: RECOVERED',.99,'test')))
    response=client.post(f"/api/documents/{doc['id']}/ocr/retry",headers=headers,json={'version':claim['version']})
    assert response.status_code == 200,response.json
    assert response.json['document']['extracted_data']['invoice_number']['value'] == 'RECOVERED'
    assert response.json['document']['processing_status']=='COMPLETED'


def test_late_processing_result_cannot_overwrite_newer_attempt(client,app,headers,claims,monkeypatch):
    claim=draft(client,headers,claims['customer']['product'])
    from sqlalchemy import select
    def supersede(*args):
        with app.app_context():
            document=db.session.scalar(select(Document).where(Document.claim_id==claim['id']))
            document.processing_metadata={'attempt_id':'newer-attempt','warnings':[]}
            document.ocr_status='completed'
            document.extracted_data={'invoice_number':{'value':'NEWER','confidence':.99,'state':'detected'}}
            db.session.commit()
        return OCRResult('Invoice Number: STALE',.99,'slow-provider')
    monkeypatch.setattr('backend.services.document_service.get_ocr_provider',lambda:SimpleNamespace(extract_text=supersede))
    response=upload(client,headers,claim)
    assert response.status_code == 201,response.json
    assert response.json['document']['extracted_data']['invoice_number']['value']=='NEWER'
    with app.app_context():
        document=db.session.get(Document,response.json['document']['id'])
        assert document.processing_metadata['attempt_id']=='newer-attempt'


def test_deletion_during_processing_returns_not_found_instead_of_server_error(client,app,headers,claims,monkeypatch):
    from sqlalchemy import select
    claim=draft(client,headers,claims['customer']['product'])
    def remove_during_processing(*args):
        with app.app_context():
            document=db.session.scalar(select(Document).where(Document.claim_id==claim['id']))
            db.session.delete(document); db.session.commit()
        return OCRResult('Invoice Number: OBSOLETE',.99,'slow-provider')
    monkeypatch.setattr('backend.services.document_service.get_ocr_provider',lambda:SimpleNamespace(extract_text=remove_during_processing))
    response=upload(client,headers,claim)
    assert response.status_code == 404,response.json
    assert client.get(f"/api/claims/{claim['id']}",headers=headers).json['claim']['documents']==[]


def test_model_completeness_reuses_registration_and_recognizes_stored_damage(app,claims):
    from backend.db.models import Claim
    from backend.services.predictions import python_features
    from backend.services.evaluation import policy_for_claim
    from fuzzy_helpers import attach_evidence
    with app.app_context():
        claim=db.session.get(Claim,claims['customer']['claim'])
        owner=claim.user_id
    attach_evidence(app,claims['customer']['claim'],owner,kinds=('product_image','damage_evidence'))
    with app.app_context():
        claim=db.session.get(Claim,claims['customer']['claim'])
        policy,_=policy_for_claim(claim)
        features=python_features(claim,policy,['document_completeness_score','receipt_available'])
        assert features['document_completeness_score']==1.0
        assert features['receipt_available'] is False  # Do not fabricate a receipt attachment.


def test_reviewer_dashboard_retains_conditional_missing_receipt(client,app,claims):
    from backend.db.models import Claim
    from fuzzy_helpers import attach_evidence
    with app.app_context():
        claim=db.session.get(Claim,claims['customer']['claim'])
        claim.manual_review_required=True; claim.status='manual_review'
        claim.product.retailer=' '
        owner=claim.user_id
        db.session.commit()
    attach_evidence(app,claims['customer']['claim'],owner,kinds=('product_image','damage_evidence'))
    response=client.get('/api/dashboard/reviewer',headers=bearer(login(client,'reviewer')['access_token']))
    assert response.status_code == 200,response.json
    assert response.json['missing_documents'][0]['missing']==['receipt']


def test_reused_internal_document_id_cannot_expose_another_users_evidence(client,app,headers,claims,monkeypatch):
    from sqlalchemy import select
    claim=draft(client,headers,claims['customer']['product'])
    def replace_during_processing(*args):
        with app.app_context():
            original=db.session.scalar(select(Document).where(Document.claim_id==claim['id']))
            identifier=original.id
            uploader=original.uploaded_by
            db.session.delete(original); db.session.flush()
            db.session.add(Document(id=identifier,document_id='DOC-OTHER-USER',
                claim_id=claims['other']['claim'],uploaded_by=uploader,document_type='receipt',
                original_filename='other-user-file.png',stored_filename='private.png',mime_type='image/png',
                file_size=100,storage_path='private.png',file_hash='f'*64,ocr_status='completed',
                processing_metadata={'attempt_id':'different-document'}))
            db.session.commit()
        return OCRResult('Invoice Number: OLD',.99,'slow-provider')
    monkeypatch.setattr('backend.services.document_service.get_ocr_provider',lambda:SimpleNamespace(extract_text=replace_during_processing))
    response=upload(client,headers,claim)
    assert response.status_code == 404,response.json
    assert 'other-user-file' not in response.get_data(as_text=True)
    assert 'DOC-OTHER-USER' not in response.get_data(as_text=True)


def test_committed_upload_is_reloaded_by_public_identity_before_processing(client,app,headers,claims,monkeypatch):
    from backend.services import document_uploads as endpoint
    original_reload=endpoint.processing_document
    intercepted=[]
    def replace_before_reload(identifier,public_id,claim_id):
        if not intercepted:
            intercepted.append(True)
            with app.app_context():
                stored=db.session.get(Document,identifier)
                db.session.delete(stored); db.session.flush()
                db.session.add(Document(id=identifier,document_id='DOC-REPLACEMENT',claim_id=claims['other']['claim'],
                    uploaded_by=stored.uploaded_by,document_type='receipt',original_filename='private-replacement.png',
                    stored_filename='private.png',mime_type='image/png',file_size=1,storage_path='private.png',
                    file_hash='a'*64,ocr_status='completed'))
                db.session.commit()
        return original_reload(identifier,public_id,claim_id)
    monkeypatch.setattr(endpoint,'processing_document',replace_before_reload)
    response=upload(client,headers,draft(client,headers,claims['customer']['product']))
    assert response.status_code == 404,response.json
    assert 'private-replacement' not in response.get_data(as_text=True)
