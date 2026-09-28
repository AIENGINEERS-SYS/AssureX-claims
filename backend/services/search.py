"""Scoped, composable SQL searches; related filters never fan out result rows."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from flask import current_app
from sqlalchemy import and_, or_, select, func, case, false
from sqlalchemy.orm import aliased
from backend.db.models import Claim, Product, Warranty, User, PythonPrediction, Review
from backend.extensions import db
from backend.api.search_schemas import date_bounds
from .warranty_calculations import current_date

CLAIM_FILTERS = {'claim_id', 'claim_status', 'min_confidence', 'max_confidence',
                 'reviewer_id', 'reviewer_name', 'assignment'}


def claim_access(user, *, reviewable=True):
    if user.role == 'admin':
        return True
    if user.role == 'customer':
        return Claim.user_id == user.id
    if user.role == 'employee':
        return Claim.assigned_employee_id == user.id
    if user.role == 'reviewer':
        assigned = Claim.assigned_reviewer_id == user.id
        if reviewable:
            return or_(assigned, and_(Claim.assigned_reviewer_id.is_(None),
                Claim.status == 'manual_review', Claim.manual_review_required.is_(True)))
        return assigned
    return false()


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


def user_id(model, value):
    return model.id == int(value) if value.isdigit() else model.user_id == value


def contains(column, value):
    # ILIKE on PostgreSQL uses optional trigram indexes. Escape LIKE metacharacters.
    escaped = value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return column.ilike('%' + escaped + '%', escape='\\')


def warranty_status(warranty=Warranty):
    today = current_date()
    return case((warranty.id.is_(None), 'no_warranty'), (warranty.start_date > today, 'not_started'),
        (warranty.expiry_date < today, 'expired'), (warranty.extended_warranty.is_(True), 'extended_warranty'),
        (warranty.expiry_date <= today + timedelta(days=current_app.config['WARRANTY_NEAR_EXPIRY_DAYS']), 'near_expiry'),
        else_='active')


def latest_confidence():
    return select(PythonPrediction.top_confidence).where(PythonPrediction.claim_id == Claim.id).order_by(
        PythonPrediction.created_at.desc(), PythonPrediction.id.desc()).limit(1).correlate(Claim).scalar_subquery()


def product_conditions(filters, owner=None):
    conditions = []
    for key, column in [('product_id', Product.product_id)]:
        if key in filters:
            conditions.append(column == filters[key])
    for key, column in [('product_name', Product.name), ('brand', Product.brand), ('model', Product.model_number)]:
        if key in filters:
            conditions.append(contains(column, filters[key]))
    if filters.get('category'):
        conditions.append(func.lower(Product.category).in_(filters['category']))
    if filters.get('serial_number'):
        conditions.append(func.lower(Product.serial_number) == filters['serial_number'].lower()
            if filters['serial_match'] == 'exact' else contains(Product.serial_number, filters['serial_number']))
    if owner is not None:
        if 'customer_id' in filters:
            conditions.append(user_id(owner, filters['customer_id']))
        if 'customer' in filters:
            conditions.append(contains(owner.full_name, filters['customer']))
    return conditions


def warranty_conditions(filters, warranty=Warranty):
    result = []
    if filters.get('warranty_status'):
        result.append(warranty_status(warranty).in_(filters['warranty_status']))
    if 'provider' in filters:
        result.append(contains(warranty.provider, filters['provider']))
    if 'extended_warranty' in filters:
        result.append(warranty.extended_warranty == filters['extended_warranty'])
    if filters['date_field'] == 'warranty_expiry_date':
        start, end = date_bounds(filters)
        if start:
            result.append(warranty.expiry_date >= start)
        if end:
            result.append(warranty.expiry_date <= end)
    return result


def bounded_date(column, filters, timestamp=False):
    start, end = date_bounds(filters)
    conditions = []
    if start:
        conditions.append(column >= (datetime.combine(start, time.min, timezone.utc) if timestamp else start))
    if end:
        if timestamp and end.year < 9999:
            conditions.append(column < datetime.combine(end + timedelta(days=1), time.min, timezone.utc))
        else:
            conditions.append(column <= (datetime.combine(end, time.max, timezone.utc) if timestamp else end))
    return conditions


def claim_query(user, filters, review=False):
    owner, reviewer = aliased(User), aliased(User)
    confidence = latest_confidence()
    query = select(Claim.id, Claim.claim_id, Claim.status, Claim.created_at, Claim.updated_at,
        Claim.submission_date, Product.product_id, Product.name.label('product_name'), Product.serial_number,
        owner.full_name.label('customer'), reviewer.full_name.label('reviewer'),
        Claim.assigned_reviewer_id.label('reviewer_id'), confidence.label('confidence_score'),
        Warranty.expiry_date, warranty_status().label('warranty_status')).select_from(Claim).outerjoin(
            Product, Claim.product_id == Product.id).outerjoin(Warranty, Claim.warranty_id == Warranty.id).join(
                owner, owner.id == Claim.user_id).outerjoin(reviewer, reviewer.id == Claim.assigned_reviewer_id)
    query = query.where(claim_access(user), *product_conditions(filters, owner), *warranty_conditions(filters))
    if review:
        query = query.where(Claim.status == 'manual_review', Claim.manual_review_required.is_(True))
    if 'claim_id' in filters:
        query = query.where(Claim.claim_id == filters['claim_id'])
    if filters.get('claim_status'):
        query = query.where(Claim.status.in_(['under_evaluation' if v == 'under_review' else v for v in filters['claim_status']]))
    for key, comparison in [('min_confidence', confidence.__ge__), ('max_confidence', confidence.__le__)]:
        if key in filters:
            query = query.where(comparison(filters[key]))
    if 'reviewer_id' in filters:
        query = query.where(user_id(reviewer, filters['reviewer_id']))
    if 'reviewer_name' in filters:
        query = query.where(contains(reviewer.full_name, filters['reviewer_name']))
    if filters.get('assignment'):
        query = query.where(Claim.assigned_reviewer_id.is_(None) if filters['assignment'] == 'unassigned'
                            else Claim.assigned_reviewer_id.is_not(None))
    if filters['date_field'] == 'submission_date':
        query = query.where(*bounded_date(Claim.submission_date, filters))
    elif filters['date_field'] == 'review_date' and any(date_bounds(filters)):
        query = query.where(select(Review.id).where(Review.claim_id == Claim.id,
            *bounded_date(Review.reviewed_at, filters, True)).exists())
    if filters['q']:
        query = query.where(or_(*[contains(column, filters['q']) for column in
            (Claim.claim_id, Product.product_id, Product.name, Product.serial_number,
             owner.full_name, reviewer.full_name)]))
    return query


def claim_ids(user, filters, review=False):
    return claim_query(user, filters, review).with_only_columns(Claim.id, maintain_column_froms=True)


def has_claim_filters(filters):
    return any(key in filters and filters[key] not in ([], '') for key in CLAIM_FILTERS) or (
        filters['date_field'] != 'warranty_expiry_date' and any(date_bounds(filters)))


def current_warranty_id(user):
    today = current_date()
    active = and_(Warranty.start_date <= today, Warranty.expiry_date >= today)
    rank = case((and_(active, Warranty.extended_warranty.is_(True)), 4), (active, 3),
        (Warranty.expiry_date < today, 2), else_=1)
    return select(Warranty.id).where(Warranty.product_id == Product.id, warranty_access(user)).order_by(
        rank.desc(), case((Warranty.start_date <= today, Warranty.expiry_date)).desc().nullslast(),
        case((Warranty.start_date > today, Warranty.start_date)).asc().nullslast(), Warranty.id.desc()
    ).limit(1).correlate(Product).scalar_subquery()


def product_query(user, filters):
    owner, warranty = aliased(User), aliased(Warranty)
    query = select(Product.id, Product.product_id, Product.name, Product.brand, Product.category,
        Product.model_number, Product.serial_number, Product.created_at, Product.updated_at,
        warranty_status(warranty).label('warranty_status'), warranty.expiry_date).select_from(Product).join(owner, owner.id == Product.user_id)
    query = query.outerjoin(warranty, warranty.id == current_warranty_id(user)).where(product_access(user),
        *product_conditions(filters, owner), *warranty_conditions(filters, warranty))
    matched_claims = claim_query(user, filters).with_only_columns(Claim.product_id, maintain_column_froms=True)
    if has_claim_filters(filters):
        query = query.where(Product.id.in_(matched_claims))
    if filters['q']:
        query = query.where(or_(Product.id.in_(matched_claims), *[contains(column, filters['q']) for column in
            (Product.product_id, Product.name, Product.brand, Product.category, Product.model_number,
             Product.serial_number, owner.full_name)]))
    return query


def warranty_query(user, filters):
    owner = aliased(User)
    query = select(Warranty.id, Warranty.warranty_id, Warranty.provider, Warranty.start_date, Warranty.expiry_date,
        Warranty.extended_warranty, Warranty.created_at, Warranty.updated_at,
        Product.product_id, Product.name.label('product_name'), Product.serial_number,
        warranty_status().label('warranty_status')).select_from(Warranty).join(Product, Product.id == Warranty.product_id).join(owner, owner.id == Product.user_id)
    query = query.where(warranty_access(user), *product_conditions(filters, owner), *warranty_conditions(filters))
    matched_claims = claim_query(user, filters).with_only_columns(Claim.warranty_id, maintain_column_froms=True)
    if has_claim_filters(filters):
        query = query.where(Warranty.id.in_(matched_claims))
    if filters['q']:
        query = query.where(or_(Warranty.id.in_(matched_claims), *[contains(column, filters['q']) for column in
            (Warranty.warranty_id, Warranty.provider, Product.product_id, Product.name, Product.serial_number, owner.full_name)]))
    return query


def user_query(user, filters):
    visible = select(Claim.id).where(claim_access(user))
    allowed = or_(User.id == user.id, User.id.in_(select(Claim.user_id).where(Claim.id.in_(visible))),
        User.id.in_(select(Claim.assigned_reviewer_id).where(Claim.id.in_(visible))))
    query = select(User.id, User.user_id, User.full_name, User.role, User.created_at, User.updated_at)
    if user.role != 'admin':
        query = query.where(allowed)
    active_filters = set(filters) - {'q', 'page', 'page_size', 'sort', 'order', 'serial_match', 'date_field'}
    if active_filters:
        matches = claim_ids(user, {**filters, 'q': ''})
        products = product_query(user, {**filters, 'q': ''}).with_only_columns(Product.user_id)
        query = query.where(or_(User.id.in_(select(Claim.user_id).where(Claim.id.in_(matches))),
            User.id.in_(select(Claim.assigned_reviewer_id).where(Claim.id.in_(matches))), User.id.in_(products)))
    if filters['q']:
        query = query.where(or_(contains(User.full_name, filters['q']), contains(User.user_id, filters['q'])))
    return query


def sorted_query(query, filters, model):
    kind = filters['sort']
    descending = filters.get('order', 'asc' if kind in {'oldest', 'lowest_confidence', 'warranty_expiry_date'} else 'desc') == 'desc'
    if kind in {'highest_confidence', 'lowest_confidence'}:
        column = query.selected_columns.get('confidence_score', model.created_at)
    elif kind == 'warranty_expiry_date':
        column = query.selected_columns.get('expiry_date', model.created_at)
    else:
        column = model.updated_at if kind == 'recently_updated' else model.created_at
    return query.order_by((column.desc() if descending else column.asc()).nullslast(),
        model.id.desc() if descending else model.id.asc())


def json_row(row):
    return {key: value.isoformat() if hasattr(value, 'isoformat') else float(value) if isinstance(value, Decimal)
            else value for key, value in row.items()}


def paginate(query, model, filters, *, count=True):
    total = db.session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) if count else None
    rows = db.session.execute(sorted_query(query, filters, model).limit(filters['page_size'])
        .offset((filters['page'] - 1) * filters['page_size'])).mappings()
    return {'items': [json_row(row) for row in rows], 'total': total, 'page': filters['page'],
            'page_size': filters['page_size'], 'total_pages': (total + filters['page_size'] - 1) // filters['page_size'] if total is not None else None}


def search(user, filters, scope='global', *, count=True):
    builders = {'claims': (claim_query, Claim), 'products': (product_query, Product),
                'warranties': (warranty_query, Warranty), 'users': (user_query, User)}
    if scope == 'review':
        return paginate(claim_query(user, filters, True), Claim, filters, count=count)
    if scope != 'global':
        builder, model = builders[scope]
        return paginate(builder(user, filters), model, filters, count=count)
    return {'groups': {name: paginate(builder(user, filters), model, filters, count=count)
                       for name, (builder, model) in builders.items()}}
