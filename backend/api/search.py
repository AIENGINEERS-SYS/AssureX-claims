"""Search APIs, user-owned saved criteria and aggregate administrative telemetry."""
from datetime import timedelta, timezone
import json
from time import perf_counter
from flask import Blueprint, current_app, g, request
from flask_jwt_extended import current_user
from marshmallow import ValidationError
from sqlalchemy import select, func, delete
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, NotFound, TooManyRequests, Forbidden
from backend.db.models import User, utcnow
from backend.db.search_models import SavedSearch, SearchEvent, SearchFilterUse
from backend.extensions import db, limiter
from backend.security import role_required
from backend.services import search as service
from .search_schemas import normalize, query_params, SavedSearchInput, KINDS

bp = Blueprint('search', __name__, url_prefix='/api')
authenticated = role_required('customer', 'employee', 'reviewer', 'admin')
SEARCH_DEFAULTS = {'q', 'page', 'page_size', 'sort', 'order', 'date_field', 'serial_match'}


def execute(scope):
    g.search_started, g.search_actor, g.search_scope = perf_counter(), current_user.id, scope
    filters = normalize(query_params(request.args))
    g.search_filters = filters
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(select(func.set_config('statement_timeout', str(current_app.config['SEARCH_TIMEOUT_MS']), True)))
    result = service.search(current_user, filters, scope)
    g.search_count = sum(group['total'] for group in result['groups'].values()) if scope == 'global' else result['total']
    return {**result, 'filters': filters, 'elapsed_ms': round((perf_counter() - g.search_started) * 1000)}


@bp.after_request
def track(response):
    if not getattr(g, 'search_actor', None):
        return response
    success = response.status_code < 400
    filters = getattr(g, 'search_filters', {}) if success else {}
    names = sorted(key for key, value in filters.items() if key not in SEARCH_DEFAULTS and value not in ('', [], None))
    # Telemetry failures never turn successful searches into failures.
    try:
        event = SearchEvent(user_id=g.search_actor, scope=g.search_scope, query=filters.get('q', ''),
            filters=filters, filter_names=names, elapsed_ms=round((perf_counter() - g.search_started) * 1000),
            result_count=getattr(g, 'search_count', 0), outcome=('success' if getattr(g, 'search_count', 0) else 'empty')
                if success else ('invalid' if response.status_code < 500 else 'error'))
        db.session.add(event);db.session.flush()
        if names:
            db.session.execute(SearchFilterUse.__table__.insert(), [{'event_id': event.id, 'name': name} for name in names])
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning('Search telemetry could not be stored')
    return response


@bp.get('/search')
@authenticated
@limiter.limit('60 per minute', key_func=lambda: str(current_user.id))
def global_search():
    return execute('global')


@bp.get('/claims/search')
@authenticated
@limiter.limit('60 per minute', key_func=lambda: str(current_user.id))
def claims():
    return execute('claims')


@bp.get('/products/search')
@authenticated
@limiter.limit('60 per minute', key_func=lambda: str(current_user.id))
def products():
    return execute('products')


@bp.get('/warranties/search')
@authenticated
@limiter.limit('60 per minute', key_func=lambda: str(current_user.id))
def warranties():
    return execute('warranties')


@bp.get('/review/search')
@role_required('reviewer')
@limiter.limit('60 per minute', key_func=lambda: str(current_user.id))
def review():
    return execute('review')


@bp.get('/search/suggestions')
@authenticated
@limiter.limit('90 per minute', key_func=lambda: str(current_user.id))
def suggestions():
    raw = query_params(request.args)
    if set(raw) - {'q'}:
        raise BadRequest('Suggestions accept only q.')
    filters = normalize({**raw, 'page_size': 5})
    if len(filters['q']) < 2:
        return {'groups': {}}
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(select(func.set_config('statement_timeout', str(current_app.config['SEARCH_TIMEOUT_MS']), True)))
    result = service.search(current_user, filters, count=False)
    return {'groups': {key: group['items'] for key, group in result['groups'].items()}}


def saved_json(item):
    return {'id': item.id, 'name': item.name, 'scope': item.scope, 'filters': item.filters}


@bp.get('/search/saved')
@authenticated
def saved_list():
    items = db.session.scalars(select(SavedSearch).where(SavedSearch.user_id == current_user.id)
        .order_by(SavedSearch.created_at.desc(), SavedSearch.id.desc()).limit(50))
    return {'items': [saved_json(item) for item in items]}


@bp.post('/search/saved')
@authenticated
def save():
    raw = request.get_json()
    if not isinstance(raw, dict):
        raise BadRequest('Expected a JSON object.')
    raw = dict(raw)
    if isinstance(raw.get('name'), str):
        raw['name'] = raw['name'].strip()
    values = SavedSearchInput().load(raw)
    if values['scope'] == 'review' and current_user.role not in {'reviewer', 'admin'}:
        raise Forbidden('Reviewer searches require reviewer access.')
    filters = normalize(values['filters'])
    filters['page'] = 1
    db.session.execute(select(User.id).where(User.id == current_user.id).with_for_update()).one()
    if db.session.scalar(select(func.count()).select_from(SavedSearch).where(SavedSearch.user_id == current_user.id)) >= 50:
        raise TooManyRequests('Delete a saved search before adding another (limit 50).')
    item = SavedSearch(user_id=current_user.id, name=values['name'], scope=values['scope'], filters=filters)
    db.session.add(item);db.session.commit()
    return saved_json(item), 201


@bp.delete('/search/saved/<int:saved_id>')
@authenticated
def remove(saved_id):
    item = db.session.scalar(select(SavedSearch).where(SavedSearch.id == saved_id, SavedSearch.user_id == current_user.id))
    if item is None:
        raise NotFound('Saved search not found.')
    db.session.delete(item);db.session.commit()
    return '', 204


@bp.get('/search/recent')
@authenticated
def recent():
    cutoff = utcnow() - timedelta(days=current_app.config['SEARCH_RETENTION_DAYS'])
    events = db.session.scalars(select(SearchEvent).where(SearchEvent.user_id == current_user.id,
        SearchEvent.created_at >= cutoff, SearchEvent.outcome.in_(['success', 'empty']))
        .order_by(SearchEvent.created_at.desc(), SearchEvent.id.desc()).limit(100))
    seen, result = set(), []
    for event in events:
        filters = {**event.filters, 'page': 1}
        signature = (event.scope, json.dumps(filters, sort_keys=True))
        if signature in seen:
            continue
        seen.add(signature)
        result.append({'scope': event.scope, 'filters': filters, 'query': event.query})
        if len(result) == 10:
            break
    return {'items': result}


@bp.get('/search/analytics')
@role_required('admin')
def analytics():
    if set(request.args) - {'days'}:
        raise BadRequest('Analytics accepts only days.')
    try:
        days = int(request.args.get('days', 7))
    except ValueError:
        raise BadRequest('days must be a whole number.')
    if not 1 <= days <= current_app.config['SEARCH_RETENTION_DAYS']:
        raise BadRequest('days is outside the configured retention window.')
    cutoff = utcnow() - timedelta(days=days)
    total, average, maximum = db.session.execute(select(func.count(), func.avg(SearchEvent.elapsed_ms),
        func.max(SearchEvent.elapsed_ms)).where(SearchEvent.created_at >= cutoff)).one()
    outcomes = dict(db.session.execute(select(SearchEvent.outcome, func.count()).where(
        SearchEvent.created_at >= cutoff).group_by(SearchEvent.outcome)).all())
    popular = db.session.execute(select(SearchEvent.query, func.count().label('count')).where(
        SearchEvent.created_at >= cutoff, SearchEvent.query != '').group_by(SearchEvent.query)
        .order_by(func.count().desc(), SearchEvent.query).limit(10)).all()
    used = db.session.execute(select(SearchFilterUse.name, func.count()).join(SearchEvent,
        SearchEvent.id == SearchFilterUse.event_id).where(SearchEvent.created_at >= cutoff)
        .group_by(SearchFilterUse.name).order_by(func.count().desc(), SearchFilterUse.name).limit(30)).all()
    return {'days': days, 'total_searches': total, 'average_ms': round(average or 0, 2), 'max_ms': maximum or 0,
        'outcomes': outcomes, 'failed_searches': outcomes.get('invalid', 0) + outcomes.get('error', 0),
        'common_searches': [{'query': term, 'count': count} for term, count in popular],
        'most_used_filters': [{'name': name, 'count': count} for name, count in used]}


def init_cli(app):
    import click

    @app.cli.command('search-cleanup')
    def cleanup():
        """Delete expired telemetry in bounded batches. Schedule daily."""
        cutoff, total = utcnow() - timedelta(days=app.config['SEARCH_RETENTION_DAYS']), 0
        while True:
            ids = list(db.session.scalars(select(SearchEvent.id).where(SearchEvent.created_at < cutoff).limit(5000)))
            if not ids:
                break
            db.session.execute(delete(SearchEvent).where(SearchEvent.id.in_(ids)))
            db.session.commit();total += len(ids)
        click.echo(f'Deleted {total} expired search events.')

    @app.cli.command('search-indexes')
    def indexes():
        """Install PostgreSQL substring indexes without blocking normal writes."""
        if db.engine.dialect.name != 'postgresql':
            raise click.ClickException('Trigram indexes require PostgreSQL.')
        from backend.services.search_indexes import install
        install(db.engine)
        click.echo('PostgreSQL search indexes installed.')
