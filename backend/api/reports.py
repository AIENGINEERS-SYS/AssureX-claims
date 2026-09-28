"""Authenticated previews, durable export requests, private history and downloads."""
import json
from flask import Blueprint, request, Response, send_file
from flask_jwt_extended import current_user
from marshmallow import Schema, fields, validate
from sqlalchemy import select
from werkzeug.exceptions import BadRequest, Conflict, Gone
from backend.db.models import AuditLog, utcnow
from backend.db.report_models import ReportJob
from backend.extensions import db, limiter
from backend.security import role_required
from backend.services import reporting, report_jobs

bp = Blueprint('reports', __name__, url_prefix='/api/reports')
authenticated = role_required('customer', 'employee', 'reviewer', 'admin')


class Page(Schema):
    page = fields.Integer(load_default=1, validate=validate.Range(min=1, max=100000))
    per_page = fields.Integer(load_default=25, validate=validate.Range(min=1, max=100))


def generate_preview(kind=None, entity_id=None):
    args = request.args.to_dict()
    pagination = Page().load({key: args.pop(key) for key in ('page', 'per_page') if key in args})
    if kind:
        args['report_type'] = kind
    if entity_id is not None:
        args['entity_id'] = entity_id
    filters = reporting.normalize(args)
    data = reporting.preview(current_user, filters, **pagination)
    db.session.add(AuditLog(user_id=current_user.id, action='report.preview', entity_type='report_preview',
        entity_id=f'{filters["report_type"]}:{filters.get("entity_id", "all")}',
        new_values={'format': 'json', 'filters': filters}, ip_address=request.remote_addr))
    db.session.commit()
    return Response(json.dumps(data, default=reporting.json_value), mimetype='application/json')


@bp.get('')
@authenticated
def index():
    return generate_preview()


@bp.get('/claim/<int:entity_id>')
@authenticated
def claim(entity_id):
    return generate_preview('claim', entity_id)


@bp.get('/customer/<int:entity_id>')
@authenticated
def customer(entity_id):
    return generate_preview('customer', entity_id)


@bp.get('/product/<int:entity_id>')
@authenticated
def product(entity_id):
    return generate_preview('product', entity_id)


@bp.get('/reviewer/<int:entity_id>')
@authenticated
def reviewer(entity_id):
    return generate_preview('reviewer', entity_id)


@bp.get('/administrative')
@authenticated
def administrative():
    return generate_preview('administrative')


@bp.post('/export/<format>')
@authenticated
@limiter.limit('10 per minute', key_func=lambda: str(current_user.id))
def export(format):
    if format not in report_jobs.FORMATS:
        raise BadRequest('Choose csv, excel or pdf.')
    filters = reporting.normalize(request.get_json())
    job = report_jobs.enqueue(current_user, filters, format, request.remote_addr)
    return report_jobs.public(job), 202, {'Location': f'/api/reports/jobs/{job.id}'}


@bp.get('/history')
@authenticated
def history():
    args = Page().load(request.args.to_dict())
    result = db.paginate(select(ReportJob).where(ReportJob.user_id == current_user.id)
        .order_by(ReportJob.created_at.desc(), ReportJob.id), error_out=False, **args)
    return {'items': [report_jobs.public(job) for job in result.items], 'total': result.total, **args}


@bp.get('/jobs/<job_id>')
@authenticated
def status(job_id):
    return report_jobs.public(report_jobs.owned(job_id, current_user))


@bp.get('/jobs/<job_id>/download')
@authenticated
def download(job_id):
    job = report_jobs.owned(job_id, current_user)
    # SQLite may return naive dates; compare in SQL for consistent UTC handling.
    if db.session.scalar(select(ReportJob.id).where(ReportJob.id == job.id, ReportJob.expires_at <= utcnow())):
        raise Gone('This report has expired. Generate a new report.')
    if job.status != 'completed':
        raise Conflict('This report is not ready to download.')
    user = report_jobs.check_actor(job)
    report_jobs.check_manifest(job, user)
    path = report_jobs.artifact(job)
    if not path.is_file():
        raise Gone('The report file is unavailable. Generate a new report.')
    report_jobs.audit(job, 'report.download', request.remote_addr)
    db.session.commit()
    extension, mimetype = report_jobs.FORMATS[job.format]
    return send_file(path, mimetype=mimetype, as_attachment=True,
        download_name=f'assurex-{job.id}.{extension}', conditional=False, etag=False, max_age=0)


def init_cli(app):
    import click
    import time

    @app.cli.command('report-worker')
    @click.option('--once', is_flag=True, help='Process one job and exit (also performs cleanup).')
    @click.option('--poll-seconds', default=5, type=click.IntRange(1, 60))
    def worker(once, poll_seconds):
        """Run a dedicated report worker against the web application's DB and storage."""
        while True:
            report_jobs.cleanup()
            processed = report_jobs.process_next()
            if once:
                break
            if not processed:
                time.sleep(poll_seconds)
