"""Shared, explicitly scoped report queries. No serializer exposes storage paths or credentials."""
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from flask import current_app
from marshmallow import Schema, fields, validate, validates_schema, ValidationError
from sqlalchemy import select, func, or_, false
from werkzeug.exceptions import Forbidden, NotFound
from backend.db.models import (Claim, Product, Warranty, Review, Document, RuleResult,
    PythonPrediction, GTMPrediction, EvaluationResult, RepairHistory, User)
from backend.extensions import db

STATUSES = ('draft', 'submitted', 'under_evaluation', 'additional_information_required',
            'manual_review', 'approved', 'rejected', 'closed')


class ReportFilters(Schema):
    advanced = fields.Dict()
    report_type = fields.String(load_default='claims', validate=validate.OneOf(
        ['claims', 'claim', 'customer', 'product', 'reviewer', 'administrative']))
    entity_id = fields.Integer(validate=validate.Range(min=1, max=2147483647))
    dataset = fields.String(load_default='claims', validate=validate.OneOf(
        ['claims', 'reviewer_queue', 'expiring_warranties', 'approved', 'rejected', 'manual_review']))
    status = fields.String(validate=validate.OneOf(STATUSES))
    search = fields.String(validate=validate.Length(max=120))
    customer_id = fields.Integer(validate=validate.Range(min=1, max=2147483647))
    product_id = fields.Integer(validate=validate.Range(min=1, max=2147483647))
    reviewer_id = fields.Integer(validate=validate.Range(min=1, max=2147483647))
    date_from = fields.Date()
    date_to = fields.Date()
    expiry_days = fields.Integer(load_default=30, validate=validate.Range(min=0, max=365))

    @validates_schema
    def check(self, data, **kwargs):
        if data['report_type'] in {'claim', 'customer', 'product', 'reviewer'} and not data.get('entity_id'):
            raise ValidationError({'entity_id': ['Required for this report type.']})
        if data.get('date_from') and data.get('date_to') and data['date_from'] > data['date_to']:
            raise ValidationError('date_from must be on or before date_to.')
        if data['dataset'] in STATUSES and data.get('status', data['dataset']) != data['dataset']:
            raise ValidationError('Status conflicts with the selected dataset.')


def normalize(raw):
    if not isinstance(raw, dict):
        raise ValidationError('Expected a JSON object.')
    data = ReportFilters().load(raw)
    if 'advanced' in data:
        from backend.api.search_schemas import normalize as search_filters, date_bounds
        data['advanced'] = search_filters(data['advanced'])
        if data['advanced'].get('date_preset') not in {None, 'custom'}:
            start, end = date_bounds(data['advanced'])
            data['advanced'].update(date_preset='custom', start_date=start.isoformat(), end_date=end.isoformat())
    return json.loads(json.dumps(data, default=json_value))


def json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value).__name__)


def claim_access(user):
    return {'customer': Claim.user_id == user.id,
            'employee': Claim.assigned_employee_id == user.id,
            'reviewer': Claim.assigned_reviewer_id == user.id,
            'admin': True}.get(user.role, false())


def product_access(user):
    if user.role == 'admin':
        return True
    if user.role == 'customer':
        return Product.user_id == user.id
    return Product.id.in_(select(Claim.product_id).where(claim_access(user)))


def warranty_access(user):
    if user.role in {'admin', 'customer'}:
        return Warranty.product_id.in_(select(Product.id).where(product_access(user)))
    return Warranty.id.in_(select(Claim.warranty_id).where(claim_access(user)))


def authorize(user, filters):
    kind, entity = filters['report_type'], filters.get('entity_id')
    if not user.is_active:
        raise Forbidden('Account is inactive.')
    if kind == 'administrative' and user.role != 'admin':
        raise Forbidden('Administrative reports require an administrator.')
    if kind == 'reviewer':
        if user.role != 'admin' and not (user.role == 'reviewer' and user.id == entity):
            raise NotFound('Report not found.')
        if not db.session.scalar(select(User.id).where(User.id == entity, User.role == 'reviewer')):
            raise NotFound('Report not found.')
    if kind == 'customer':
        if user.role != 'admin' and not (user.role == 'customer' and user.id == entity):
            raise NotFound('Report not found.')
        if not db.session.scalar(select(User.id).where(User.id == entity, User.role == 'customer')):
            raise NotFound('Report not found.')
    if kind == 'claim' and not db.session.scalar(select(Claim.id).where(Claim.id == entity, claim_access(user))):
        raise NotFound('Report not found.')
    if kind == 'product' and not db.session.scalar(select(Product.id).where(Product.id == entity, product_access(user))):
        raise NotFound('Report not found.')
    if filters['dataset'] == 'reviewer_queue' and user.role not in {'admin', 'reviewer'}:
        raise Forbidden('Reviewer queue requires a reviewer or administrator.')


def claim_ids(user, filters):
    query = select(Claim.id).where(claim_access(user))
    if 'advanced' in filters:
        from .search import claim_ids as search_claims
        query = query.where(Claim.id.in_(search_claims(user, filters['advanced'])))
    for key, column in [('customer_id', Claim.user_id), ('product_id', Claim.product_id),
                        ('reviewer_id', Claim.assigned_reviewer_id), ('status', Claim.status)]:
        if key in filters:
            query = query.where(column == filters[key])
    kind = filters['report_type']
    if kind in {'claim', 'customer', 'product', 'reviewer'}:
        column = {'claim': Claim.id, 'customer': Claim.user_id, 'product': Claim.product_id,
                  'reviewer': Claim.assigned_reviewer_id}[kind]
        query = query.where(column == filters['entity_id'])
    dataset = filters['dataset']
    if dataset in STATUSES:
        query = query.where(Claim.status == dataset)
    if dataset == 'reviewer_queue':
        query = query.where(Claim.status.in_(['manual_review', 'additional_information_required']))
    if dataset == 'expiring_warranties':
        query = query.where(Claim.warranty_id.in_(warranty_ids(user, filters, apply_claim_filters=False)))
    for key, comparison in [('date_from', Claim.submission_date.__ge__), ('date_to', Claim.submission_date.__le__)]:
        if key in filters:
            query = query.where(comparison(date.fromisoformat(filters[key])))
    if filters.get('search'):
        term = filters['search']
        query = query.where(or_(Claim.claim_id.icontains(term, autoescape=True),
            Claim.fault_description.icontains(term, autoescape=True), Claim.product_id.in_(
                select(Product.id).where(or_(Product.name.icontains(term, autoescape=True),
                    Product.serial_number.icontains(term, autoescape=True))))))
    return query


def warranty_ids(user, filters, apply_claim_filters=True):
    query = select(Warranty.id).where(warranty_access(user))
    if 'advanced' in filters:
        from .search import warranty_query
        query = query.where(Warranty.id.in_(warranty_query(user, filters['advanced'])
            .with_only_columns(Warranty.id, maintain_column_froms=True)))
    if filters['dataset'] == 'expiring_warranties':
        today = date.today()
        query = query.where(Warranty.expiry_date.between(today, today + timedelta(days=filters['expiry_days'])))
    kind = filters['report_type']
    if kind == 'product':
        query = query.where(Warranty.product_id == filters['entity_id'])
    if 'product_id' in filters:
        query = query.where(Warranty.product_id == filters['product_id'])
    if kind == 'customer':
        query = query.where(Warranty.product_id.in_(select(Product.id).where(Product.user_id == filters['entity_id'])))
    if 'customer_id' in filters:
        owner = filters['customer_id']
        query = query.where(Warranty.product_id.in_(select(Product.id).where(Product.user_id == owner)))
    claim_filters = {'status', 'date_from', 'date_to', 'reviewer_id'} & filters.keys()
    if apply_claim_filters and (kind in {'claim', 'reviewer'} or
            (kind == 'claims' and filters['dataset'] != 'expiring_warranties') or claim_filters or
            filters['dataset'] in {'reviewer_queue', 'approved', 'rejected', 'manual_review'}):
        query = query.where(Warranty.id.in_(select(Claim.warranty_id).where(Claim.id.in_(claim_ids(user, filters)))))
    if filters.get('search'):
        query = query.where(Warranty.product_id.in_(select(Product.id).where(or_(
            Product.name.icontains(filters['search'], autoescape=True),
            Product.serial_number.icontains(filters['search'], autoescape=True)))))
    return query


def section(model, names, condition, manifest=None):
    columns = [getattr(model, name) for name in names.split()]
    return {'query': select(*columns).where(condition), 'pk': model.id,
            'columns': names.split(), 'manifest': manifest,
            'dates': {c.key for c in columns if str(c.type) in {'DATE', 'DATETIME', 'TIMESTAMP'}}}


def sections(user, filters):
    authorize(user, filters)
    claims = claim_ids(user, filters)
    warranties = warranty_ids(user, filters)
    products = select(Product.id).where(product_access(user))
    if 'advanced' in filters:
        from .search import product_query
        products = products.where(Product.id.in_(product_query(user, filters['advanced'])
            .with_only_columns(Product.id)))
    kind = filters['report_type']
    if kind == 'product':
        products = products.where(Product.id == filters['entity_id'])
    elif kind == 'customer':
        products = products.where(Product.user_id == filters['entity_id'])
    else:
        products = products.where(or_(Product.id.in_(select(Claim.product_id).where(Claim.id.in_(claims))),
            Product.id.in_(select(Warranty.product_id).where(Warranty.id.in_(warranties)))))
    # Explicit claim filters constrain product data too, including customer/product reports.
    if {'status', 'date_from', 'date_to', 'reviewer_id', 'search', 'product_id', 'customer_id'} & filters.keys() or filters['dataset'] != 'claims':
        products = products.where(or_(Product.id.in_(select(Claim.product_id).where(Claim.id.in_(claims))),
            Product.id.in_(select(Warranty.product_id).where(Warranty.id.in_(warranties)))))
    claim_fields = ('id claim_id user_id product_id warranty_id status submission_date fault_date fault_type '
        'fault_description damage_type repair_history previous_replacement final_decision submitted_at closed_at created_at')
    if user.role != 'customer':
        claim_fields = claim_fields.replace(' status ', ' assigned_employee_id assigned_reviewer_id status ')
        claim_fields = claim_fields.replace(' submitted_at ', ' manual_review_required submitted_at ')

    result = {
        'Claims': section(Claim, claim_fields, Claim.id.in_(claims), 'claim'),
        'Products': section(Product, 'id product_id name category brand model_number serial_number purchase_date purchase_price retailer is_active', Product.id.in_(products), 'product'),
        'Customers': section(User, 'id user_id full_name email phone', or_(
            User.id.in_(select(Claim.user_id).where(Claim.id.in_(claims))),
            User.id.in_(select(Product.user_id).where(Product.id.in_(products))))),
        'Warranties': section(Warranty, 'id warranty_id product_id provider warranty_type start_date expiry_date coverage_duration_months coverage_conditions exclusions extended_warranty service_center_requirements', Warranty.id.in_(warranties), 'warranty'),
        'Documents': section(Document, 'id document_id claim_id product_id warranty_id document_type original_filename mime_type file_size ocr_status review_status created_at',
            or_(Document.claim_id.in_(claims), Document.claim_id.is_(None) & or_(
                Document.product_id.in_(products), Document.warranty_id.in_(warranties)))),
        'Repairs': section(RepairHistory, 'id product_id claim_id repair_date service_center_name authorized_service_center parts_replaced repair_outcome repair_cost notes',
            or_(RepairHistory.claim_id.in_(claims), (RepairHistory.claim_id.is_(None) & RepairHistory.product_id.in_(products))
                if user.role in {'admin', 'customer'} else false())),
    }

    if user.role == 'customer':
        # Match the customer decision API: expose the non-technical recommendation and
        # explanation, but never reviewer notes, override reasons, raw rule evidence,
        # model confidences or staff assignment identifiers.
        result['Evaluations'] = section(EvaluationResult,
            'id evaluation_id claim_id status recommendation explanation created_at',
            EvaluationResult.claim_id.in_(claims))
    else:
        result.update({
            'Reviews': section(Review, 'id review_id claim_id reviewer_user_id decision comments override_applied override_reason reviewed_at', Review.claim_id.in_(claims)),
            'Rules': section(RuleResult, 'id claim_id rule_name rule_code rule_category result severity details policy_version created_at', RuleResult.claim_id.in_(claims)),
            'Python predictions': section(PythonPrediction, 'id claim_id model_version_id predicted_class confidence_valid confidence_invalid confidence_manual_review top_confidence created_at', PythonPrediction.claim_id.in_(claims)),
            'GTM predictions': section(GTMPrediction, 'id claim_id model_version_id predicted_class confidence_valid confidence_invalid confidence_manual_review top_confidence created_at', GTMPrediction.claim_id.in_(claims)),
            'Evaluations': section(EvaluationResult, 'id evaluation_id claim_id status recommendation explanation policy_code policy_version created_at', EvaluationResult.claim_id.in_(claims)),
        })
    return result


def preview(user, filters, page=1, per_page=25):
    result = {}
    for name, spec in sections(user, filters).items():
        total = db.session.scalar(select(func.count()).select_from(spec['query'].subquery()))
        rows = db.session.execute(spec['query'].order_by(spec['pk']).offset((page - 1) * per_page).limit(per_page)).mappings()
        result[name] = {'items': [dict(row) for row in rows], 'total': total}
    return {'filters': filters, 'sections': result, 'analytics': analytics(user, filters),
            'page': page, 'per_page': per_page}


def analytics(user, filters):
    """Cache only aggregate results, never resource data or authorization decisions."""
    from time import monotonic
    query = claim_ids(user, filters).subquery()
    # Fingerprint scoped membership and revisions before consulting the short-lived cache.
    fingerprint = db.session.execute(select(func.count(), func.max(Claim.updated_at),
        func.sum(Claim.id)).where(Claim.id.in_(select(query.c.id)))).one()
    key = (user.id, user.role, user.auth_version, json.dumps(filters, sort_keys=True), tuple(fingerprint))
    cache = current_app.extensions.setdefault('report_aggregate_cache', {})
    cached = cache.get(key)
    if cached and cached[0] > monotonic():
        return cached[1]
    rows = db.session.execute(select(Claim.status, func.count()).where(
        Claim.id.in_(select(query.c.id))).group_by(Claim.status)).all()
    value = {'total_claims': sum(n for _, n in rows), 'statuses': dict(rows)}
    if len(cache) >= 128:
        cache.clear()
    cache[key] = (monotonic() + 15, value)
    return value
