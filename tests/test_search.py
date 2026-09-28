"""Migrated SQL + JWT integration coverage for composable, authorization-scoped search."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import csv
from io import StringIO
import pytest
from sqlalchemy import select, func, text
from backend.db.models import Claim, Product, Warranty, User, Review, ModelVersion, PythonPrediction, utcnow
from backend.db.search_models import SearchEvent, SearchFilterUse, SavedSearch
from backend.extensions import db
from test_auth import app, client, accounts, claims, login, bearer  # noqa: F401


def auth(client, role='customer'):
    return bearer(login(client, role)['access_token'])


@pytest.fixture
def records(app, accounts, claims):
    with app.app_context():
        claim = db.session.get(Claim, claims['customer']['claim'])
        claim.claim_id, claim.status, claim.manual_review_required = 'CLM-2026-000001', 'manual_review', True
        claim.assigned_reviewer_id, claim.submission_date = accounts['reviewer'], date.today()
        product = db.session.get(Product, claims['customer']['product'])
        product.product_id, product.serial_number, product.category = 'PRD-2026-000145', 'ABC123456', 'Computers'
        product.name, product.brand, product.model_number = 'Personal Laptop', 'ExampleBrand', 'X1'
        warranty = db.session.get(Warranty, claims['customer']['warranty'])
        warranty.expiry_date = date.today() + timedelta(days=60)
        owner = db.session.get(User, accounts['customer']);owner.full_name = 'Ada Customer'
        reviewer = db.session.get(User, accounts['reviewer']);reviewer.full_name = 'John Smith'
        model = ModelVersion(model_type='python', model_name='Search model', version='test',
            artifact_path='private.pkl', training_dataset_version='test', is_active=True)
        db.session.add(model);db.session.flush()
        for score, created in [(.98, utcnow() - timedelta(days=1)), (.65, utcnow())]:
            db.session.add(PythonPrediction(claim_id=claim.id, model_version_id=model.id,
                predicted_class='valid', confidence_valid=score, confidence_invalid=1-score,
                confidence_manual_review=0, top_confidence=score, created_at=created))
        for reviewed_at in [utcnow() - timedelta(days=2), utcnow()]:
            db.session.add(Review(claim_id=claim.id, reviewer_user_id=accounts['reviewer'],
                decision='manual_review_continue', comments='Search test', reviewed_at=reviewed_at))
        approved = Claim(claim_id='CLM-2026-000002', user_id=accounts['customer'], product_id=product.id,
            warranty_id=warranty.id, status='approved', submission_date=date.today()-timedelta(days=45))
        draft = Claim(claim_id='DRF-SEARCH', user_id=accounts['customer'], status='draft')
        db.session.add_all([approved, draft]);db.session.commit()
        return {**claims, 'approved': approved.id, 'draft': draft.id}


def get(client, path='/api/claims/search', headers=None, **filters):
    response = client.get(path, headers=headers or auth(client), query_string=filters)
    assert response.status_code == 200, response.json
    return response.json


def test_exact_ids_and_serial_matches(client, records):
    headers = auth(client)
    assert get(client, headers=headers, claim_id='CLM-2026-000001')['total'] == 1
    assert get(client, headers=headers, claim_id='CLM-2026-00000')['total'] == 0
    assert get(client, headers=headers, product_id='PRD-2026-000145')['total'] == 2
    assert get(client, '/api/products/search', headers, product_id='PRD-2026-000145')['total'] == 1
    assert get(client, headers=headers, serial_number='abc123')['total'] == 2
    assert get(client, headers=headers, serial_number='abc123', serial_match='exact')['total'] == 0
    assert get(client, headers=headers, serial_number='abc123456', serial_match='exact')['total'] == 2
    assert get(client, headers=headers, serial_number='%')['total'] == 0
    for key, value in [('brand', 'examplebrand'), ('model', 'x1'), ('product_name', 'Personal')]:
        assert get(client, '/api/products/search', headers, **{key: value})['total'] == 1


def test_combined_multi_filters_latest_confidence_and_people(client, accounts, records):
    headers = auth(client)
    filters = {'claim_status': 'manual_review', 'warranty_status': 'active', 'category': 'Electronics,Computers',
        'min_confidence': '.60', 'max_confidence': '70%', 'date_preset': '30d', 'reviewer_name': 'john', 'customer': 'Ada'}
    data = get(client, headers=headers, **filters)
    assert data['total'] == 1 and data['items'][0]['confidence_score'] == .65
    assert get(client, headers=headers, min_confidence='.90')['total'] == 0
    assert get(client, headers=headers, reviewer_id=accounts['reviewer'])['total'] == 1
    assert get(client, headers=headers, assignment='unassigned')['total'] == 2
    assert get(client, headers=headers, customer_id=accounts['other'])['total'] == 0
    response = client.get('/api/claims/search', headers=headers, query_string=[('claim_status', 'manual_review'), ('claim_status', 'approved')])
    assert response.status_code == 200 and response.json['total'] == 2
    assert get(client, '/api/products/search', headers, **filters)['total'] == 1
    assert get(client, '/api/warranties/search', headers, **filters)['total'] == 1


@pytest.mark.parametrize('status,days,extended', [('active',60,False), ('near_expiry',3,False),
    ('expired',-1,False), ('extended_warranty',60,True)])
def test_warranty_status(app, client, records, status, days, extended):
    with app.app_context():
        warranty = db.session.get(Warranty, records['customer']['warranty'])
        warranty.start_date = date.today()-timedelta(days=100)
        warranty.expiry_date = date.today()+timedelta(days=days)
        warranty.extended_warranty = extended
        db.session.commit()
    headers = auth(client)
    for path in ['/api/products/search', '/api/warranties/search', '/api/claims/search']:
        data = get(client, path, headers, warranty_status=status)
        assert data['total'] >= 1 and all(item['warranty_status'] == status for item in data['items'])
    assert get(client, '/api/warranties/search', headers, extended_warranty=str(not extended).lower())['total'] == 0


def test_date_boundaries_review_dates_and_presets(client, records):
    headers = auth(client)
    today = date.today().isoformat()
    for preset, total in [('today',1), ('7d',1), ('30d',1), ('90d',2)]:
        assert get(client, headers=headers, date_preset=preset)['total'] == total
    data = get(client, headers=headers, start_date=today, end_date=today)
    assert data['total'] == 1
    data = get(client, headers=headers, date_field='review_date', start_date=today, end_date=today)
    assert data['total'] == 1  # Multiple matching reviews never duplicate a claim.
    data = get(client, headers=headers, date_field='review_date', date_preset='7d')
    assert data['total'] == 1
    expiry = (date.today()+timedelta(days=60)).isoformat()
    assert get(client, '/api/warranties/search', headers, date_field='warranty_expiry_date', start_date=expiry, end_date=expiry)['total'] == 1


def test_pagination_and_stable_sorting(client, records):
    headers = auth(client)
    first = get(client, headers=headers, page_size=1, sort='oldest')
    second = get(client, headers=headers, page_size=1, sort='oldest', page=2)
    assert first['total'] == 3 and first['total_pages'] == 3 and first['page'] == 1
    assert first['items'][0]['id'] != second['items'][0]['id']
    assert get(client, headers=headers, page=5)['items'] == []
    for sort in ['highest_confidence', 'lowest_confidence']:
        rows = get(client, headers=headers, sort=sort)['items']
        assert rows[0]['confidence_score'] == .65 and rows[-1]['confidence_score'] is None
    ascending = [r['id'] for r in get(client, headers=headers, sort='newest', order='asc')['items']]
    descending = [r['id'] for r in get(client, headers=headers, sort='newest', order='desc')['items']]
    assert ascending == descending[::-1]


def test_role_scopes_reviewable_queue_and_global_group_privacy(app, client, accounts, records):
    for path in ['/api/search', '/api/claims/search', '/api/products/search', '/api/warranties/search', '/api/review/search']:
        assert client.get(path).status_code == 401
    customer, employee, reviewer, admin = [auth(client, role) for role in ['customer', 'employee', 'reviewer', 'admin']]
    assert get(client, headers=customer)['total'] == 3
    assert get(client, headers=employee)['total'] == 1
    assert get(client, headers=reviewer)['total'] == 1
    assert get(client, headers=admin)['total'] == 4
    assert client.get('/api/review/search', headers=customer).status_code == 403
    assert get(client, '/api/review/search', reviewer)['total'] == 1
    with app.app_context():
        other = db.session.get(Claim, records['other']['claim'])
        other.status, other.manual_review_required = 'manual_review', True
        db.session.commit()
    assert get(client, headers=reviewer, assignment='unassigned')['total'] == 1
    with app.app_context():
        second = User(email='second-reviewer@example.com', first_name='Second', last_name='Reviewer',
            role='reviewer', password_hash='not-a-password')
        db.session.add(second);db.session.flush()
        db.session.get(Claim, records['other']['claim']).assigned_reviewer_id = second.id
        db.session.commit()
    assert get(client, headers=reviewer, assignment='unassigned')['total'] == 0
    groups = get(client, '/api/search', customer, q='John')['groups']
    assert groups['claims']['total'] == 1 and groups['users']['total'] == 1
    assert 'email' not in groups['users']['items'][0] and 'password_hash' not in str(groups)
    hidden = get(client, '/api/search', customer, q='other')['groups']
    assert all(group['total'] == 0 for group in hidden.values())
    assert get(client, '/api/search', customer, q="' OR 1=1 --")['groups']['claims']['total'] == 0
    assert get(client, '/api/search/suggestions', customer, q='other')['groups']['users'] == []
    suggestions = get(client, '/api/search/suggestions', customer, q='ABC')
    assert len(suggestions['groups']['products']) == 1


@pytest.mark.parametrize('filters', [
    {'min_confidence': 'NaN'}, {'min_confidence': 'Infinity'}, {'min_confidence': '70'},
    {'min_confidence': '-.1'}, {'min_confidence': '.8', 'max_confidence': '.6'},
    {'start_date': '2026-02-30'}, {'start_date': '2026-02-01', 'end_date': '2026-01-01'},
    {'date_preset': 'custom'}, {'date_preset': '7d', 'start_date': '2026-01-01'},
    {'page_size': 101}, {'page': 0}, {'sort': 'random()'}, {'customer_id': '99999999999999999999'},
    {'claim_status': 'bogus'}, {'warranty_status': 'bogus'}, {'unknown': 'x'},
    {'assignment': 'unassigned', 'reviewer_name': 'John'}, {'q': 'x' * 121},
])
def test_reject_invalid_filters(client, accounts, filters):
    assert client.get('/api/claims/search', query_string=filters, headers=auth(client)).status_code == 400


def test_saved_searches_recent_searches_analytics_and_retention(app, client, accounts, records):
    headers = auth(client)
    payload = {'name': 'High risk queue', 'scope': 'claims', 'filters': {'claim_status': ['manual_review'], 'max_confidence': '70%'}}
    saved = client.post('/api/search/saved', json=payload, headers=headers)
    assert saved.status_code == 201, saved.json
    saved_id = saved.json['id']
    assert saved.json['filters']['max_confidence'] == .7
    assert client.post('/api/search/saved', json=payload, headers=headers).status_code == 409
    assert client.post('/api/search/saved', json={**payload, 'name': ' '}, headers=headers).status_code == 400
    other = auth(client, 'other')
    assert client.get('/api/search/saved', headers=other).json['items'] == []
    assert client.delete(f'/api/search/saved/{saved_id}', headers=other).status_code == 404
    get(client, headers=headers, q='Laptop', claim_status='manual_review')
    get(client, headers=headers, q='no such record')
    client.get('/api/claims/search?page=0', headers=headers)
    assert client.get('/api/search/recent', headers=other).json['items'] == []
    assert client.get('/api/search/recent', headers=headers).json['items'][0]['query'] == 'no such record'
    assert client.get('/api/search/analytics', headers=headers).status_code == 403
    metrics = get(client, '/api/search/analytics', auth(client, 'admin'))
    assert metrics['total_searches'] == 3 and metrics['failed_searches'] == 1
    assert metrics['most_used_filters'][0] == {'name': 'claim_status', 'count': 1}
    assert metrics['average_ms'] >= 0
    assert client.delete(f'/api/search/saved/{saved_id}', headers=headers).status_code == 204
    with app.app_context():
        event = db.session.scalar(select(SearchEvent).order_by(SearchEvent.id))
        event.created_at = utcnow() - timedelta(days=40);db.session.commit()
    result = app.test_cli_runner().invoke(args=['search-cleanup'])
    assert result.exit_code == 0, result.output
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(SearchFilterUse)) == 0


def test_saved_criteria_recheck_assignments_and_report_filter_integration(app, client, accounts, records, tmp_path):
    headers = auth(client, 'employee')
    filters = {'claim_status': ['manual_review'], 'min_confidence': .6, 'max_confidence': .7}
    saved = client.post('/api/search/saved', headers=headers, json={'name': 'Assigned', 'scope': 'claims', 'filters': filters}).json
    assert get(client, headers=headers, **saved['filters'])['total'] == 1
    report = client.post('/api/reports/export/csv', headers=headers, json={'advanced': filters})
    assert report.status_code == 202, report.json
    from backend.services.report_jobs import process_next
    app.config['REPORT_STORAGE_PATH'] = str(tmp_path / 'exports')
    with app.app_context():
        assert process_next()
    download = client.get(f'/api/reports/jobs/{report.json["id"]}/download', headers=headers)
    assert download.status_code == 200, download.json
    rows = list(csv.DictReader(StringIO(download.data.decode('utf-8-sig'))))
    assert [row['claim_id'] for row in rows if row['section'] == 'Claims'] == ['CLM-2026-000001']
    with app.app_context():
        db.session.get(Claim, records['customer']['claim']).assigned_employee_id = None
        db.session.commit()
    assert get(client, headers=headers, **saved['filters'])['total'] == 0
    assert client.get(f'/api/reports/jobs/{report.json["id"]}/download', headers=headers).status_code == 403


def test_search_migration_and_indexed_lookup_plan(app):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    config = Config('backend/db/alembic.ini')
    with app.app_context():
        plan = db.session.execute(text('EXPLAIN QUERY PLAN SELECT id FROM claims WHERE claim_id = :value'), {'value': 'CLM-example'}).all()
        assert any('INDEX' in row[-1].upper() for row in plan)
        plan = db.session.execute(text('EXPLAIN QUERY PLAN SELECT id FROM products WHERE lower(serial_number) = :value'), {'value': 'abc123'}).all()
        assert any('ix_products_serial_lower' in row[-1] for row in plan)
        db.session.remove()
        command.downgrade(config, '09a3b710ef42')
        assert 'saved_searches' not in inspect(db.engine).get_table_names()
        command.upgrade(config, 'head')
        assert 'search_filter_uses' in inspect(db.engine).get_table_names()


def test_large_dataset_pagination_remains_bounded(app, client, accounts):
    with app.app_context():
        for first in range(0, 10000, 1000):
            db.session.execute(Claim.__table__.insert(), [{'claim_id': f'CLM-SEARCH-{i:08}',
                'user_id': accounts['customer'], 'status': 'submitted'} for i in range(first, first+1000)])
        db.session.commit()
    headers = auth(client)
    data = get(client, headers=headers, page=50, page_size=100, sort='oldest', claim_status='submitted')
    assert data['total'] == 10000 and data['total_pages'] == 100 and len(data['items']) == 100
    assert len({row['id'] for row in data['items']}) == 100
    assert get(client, headers=headers, claim_id='CLM-SEARCH-00009999')['total'] == 1


def test_empty_groups_invalid_duplicates_and_failed_telemetry(app, client, accounts, monkeypatch):
    headers = auth(client)
    assert all(group['total'] == 0 for key, group in get(client, '/api/search', headers)['groups'].items() if key != 'users')
    assert client.get('/api/search', headers=headers, query_string=[('page', '1'), ('page', '2')]).status_code == 400
    from backend.services import search
    from sqlalchemy.exc import SQLAlchemyError
    def fail(*args, **kwargs):
        raise SQLAlchemyError('test database failure')
    monkeypatch.setattr(search, 'search', fail)
    response = client.get('/api/search?q=private', headers=headers)
    assert response.status_code == 503 and 'test database failure' not in str(response.json)
    with app.app_context():
        latest = db.session.scalar(select(SearchEvent).order_by(SearchEvent.id.desc()))
        assert latest.outcome == 'error' and latest.query == '' and latest.filters == {}


def test_relative_report_dates_are_frozen_and_postgres_queries_compile(app, accounts, records):
    from backend.services import search, reporting
    from backend.api.search_schemas import normalize
    from sqlalchemy.dialects import postgresql
    with app.app_context():
        frozen = reporting.normalize({'advanced': {'date_preset': '7d'}})['advanced']
        assert frozen['date_preset'] == 'custom' and frozen['end_date'] == date.today().isoformat()
        actor = db.session.get(User, accounts['reviewer'])
        filters = normalize({'q': 'Laptop', 'min_confidence': '.6', 'warranty_status': ['active']})
        for builder in (search.claim_query, search.product_query, search.warranty_query, search.user_query):
            compiled = str(builder(actor, filters).compile(dialect=postgresql.dialect()))
            assert 'ILIKE' in compiled and 'Laptop' not in compiled
