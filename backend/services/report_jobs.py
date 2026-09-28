"""Database queue with atomic claiming, heartbeat fencing and private, expiring files."""
from datetime import timedelta, timezone
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from flask import current_app
from sqlalchemy import select, update, delete, func
from werkzeug.exceptions import Forbidden, NotFound, TooManyRequests
from backend.db.models import User, Claim, Product, Warranty, AuditLog, utcnow
from backend.db.report_models import ReportJob, ReportItem
from backend.extensions import db
from . import reporting as reports
from .report_exports import cell, write_csv, write_excel, write_pdf

FORMATS = {'csv': ('csv', 'text/csv'), 'excel': ('xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
           'pdf': ('pdf', 'application/pdf')}


def audit(job, action, ip=None):
    db.session.add(AuditLog(user_id=job.user_id, action=action, entity_type='report_jobs', entity_id=job.id,
        new_values={'format': job.format, 'status': job.status, 'processed': job.processed}, ip_address=ip))


def enqueue(user, filters, format, ip=None):
    reports.authorize(user, filters)
    # Lock the owner to serialize the quota check across web processes.
    db.session.execute(select(User.id).where(User.id == user.id).with_for_update()).one()
    active = db.session.scalar(select(func.count()).select_from(ReportJob).where(
        ReportJob.user_id == user.id, ReportJob.status.in_(['queued', 'running'])))
    if active >= current_app.config['REPORT_MAX_PENDING']:
        raise TooManyRequests('Wait for your pending reports to finish.')
    job = ReportJob(id=str(uuid4()), user_id=user.id, role=user.role, auth_version=user.auth_version,
        format=format, filters=filters, expires_at=utcnow() + timedelta(hours=current_app.config['REPORT_RETENTION_HOURS']))
    db.session.add(job)
    db.session.flush()
    audit(job, 'report.requested', ip)
    db.session.commit()
    return job


def public(job):
    def timestamp(value):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return {'id': job.id, 'format': job.format, 'filters': job.filters, 'status': job.status,
        'processed': job.processed, 'total': job.total,
        'progress': 100 if job.status == 'completed' else min(99, round(100 * job.processed / max(1, job.total))),
        'error': job.error, 'created_at': timestamp(job.created_at), 'expires_at': timestamp(job.expires_at),
        'download_url': f'/api/reports/jobs/{job.id}/download' if job.status == 'completed' else None}


def root():
    path = Path(current_app.config['REPORT_STORAGE_PATH']).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifact(job, partial=False):
    # IDs come exclusively from the database; paths are never taken from a request.
    return root() / f'{job.id}.{FORMATS[job.format][0]}{ ".part" if partial else ""}'


def check_actor(job):
    user = db.session.get(User, job.user_id, populate_existing=True)
    if not user or not user.is_active or user.role != job.role or user.auth_version != job.auth_version:
        raise Forbidden('Report permissions changed. Generate a new report.')
    reports.authorize(user, job.filters)
    return user


def check_manifest(job, user):
    for kind, model, access in [('claim', Claim, reports.claim_access), ('product', Product, reports.product_access),
                               ('warranty', Warranty, reports.warranty_access)]:
        allowed = select(model.id).where(model.id == ReportItem.resource_id, access(user)).exists()
        denied = db.session.scalar(select(ReportItem.resource_id).where(
            ReportItem.job_id == job.id, ReportItem.kind == kind, ~allowed).limit(1))
        if denied is not None:
            raise Forbidden('Access to report records changed. Generate a new report.')


def owned(job_id, user):
    job = db.session.scalar(select(ReportJob).where(ReportJob.id == job_id, ReportJob.user_id == user.id))
    if job is None:
        raise NotFound('Report not found.')
    return job


def heartbeat(job, **values):
    changed = db.session.execute(update(ReportJob).where(ReportJob.id == job.id,
        ReportJob.status == 'running', ReportJob.expires_at > utcnow()).values(heartbeat_at=utcnow(), **values)
        .execution_options(synchronize_session='fetch'))
    if changed.rowcount != 1:
        db.session.rollback()
        raise RuntimeError('Report lease expired.')
    db.session.commit()


def process_next():
    """Process at most one job. PostgreSQL workers claim with SKIP LOCKED."""
    candidate = db.session.scalar(select(ReportJob.id).where(ReportJob.status == 'queued',
        ReportJob.expires_at > utcnow()).order_by(ReportJob.created_at).limit(1).with_for_update(skip_locked=True))
    if candidate is None:
        db.session.rollback()
        return False
    claimed = db.session.execute(update(ReportJob).where(ReportJob.id == candidate, ReportJob.status == 'queued')
        .values(status='running', heartbeat_at=utcnow()))
    db.session.commit()
    if not claimed.rowcount:
        return False
    job = db.session.get(ReportJob, candidate)
    try:
        user = check_actor(job)
        specs = reports.sections(user, job.filters)
        for spec in specs.values():
            count, high = db.session.execute(select(func.count(), func.max(spec['pk'])).where(
                spec['pk'].in_(spec['query'].with_only_columns(spec['pk'])))).one()
            spec.update(count=count, high=high or 0, widths={}, long_columns={})
        heartbeat(job, total=sum(spec['count'] for spec in specs.values()))
        audit(job, 'report.started')
        db.session.commit()
        processed = 0
        with TemporaryDirectory(prefix=f'{job.id}-', dir=root()) as temp:
            for index, (name, spec) in enumerate(specs.items()):
                spec['path'] = Path(temp) / f'{index}.jsonl'
                last, actual = 0, 0
                with spec['path'].open('w', encoding='utf-8') as output:
                    while True:
                        check_actor(job)
                        batch = db.session.execute(spec['query'].where(spec['pk'] > last, spec['pk'] <= spec['high'])
                            .order_by(spec['pk']).limit(current_app.config['REPORT_BATCH_SIZE'])).mappings().all()
                        if not batch:
                            break
                        for row in batch:
                            data = dict(row)
                            output.write(json.dumps(data, default=reports.json_value, ensure_ascii=False) + '\n')
                            for key, value in data.items():
                                length = len(str(cell(value)))
                                spec['widths'][key] = max(spec['widths'].get(key, 0), min(65, length))
                                if length > 32000:
                                    spec['long_columns'][key] = max(spec['long_columns'].get(key, 1), (length + 31999) // 32000)
                        if spec['manifest']:
                            db.session.execute(ReportItem.__table__.insert(), [
                                {'job_id': job.id, 'kind': spec['manifest'], 'resource_id': row['id']} for row in batch])
                        last = batch[-1]['id']
                        actual += len(batch)
                        processed += len(batch)
                        heartbeat(job, processed=processed)
                spec['count'] = actual
            # Analytics reflect the exported claim rows, not a second live database query.
            from collections import Counter
            from .report_exports import rows
            statuses = Counter(row['status'] for row in rows(specs['Claims']))
            analytics = [{'metric': 'total_claims', 'value': sum(statuses.values())}] + [
                {'metric': key, 'value': value} for key, value in sorted(statuses.items())]
            analytics += [{'metric': 'total_' + name.lower().replace(' ', '_'), 'value': spec['count']}
                          for name, spec in specs.items() if name != 'Claims']
            outcomes = Counter(row['decision'] for row in rows(specs['Reviews']))
            analytics += [{'metric': f'review_{key}', 'value': value} for key, value in sorted(outcomes.items())]
            stats_path = Path(temp) / 'analytics.jsonl'
            stats_path.write_text(''.join(json.dumps(row) + '\n' for row in analytics), encoding='utf-8')
            specs['Analytics'] = {'path': stats_path, 'columns': ['metric', 'value'], 'dates': set(),
                'count': len(analytics), 'widths': {'metric': 35, 'value': 16}}
            heartbeat(job, total=processed)
            writer = {'csv': write_csv, 'excel': write_excel, 'pdf': write_pdf}[job.format]
            writer(artifact(job, partial=True), specs, lambda: heartbeat(job))
            user = check_actor(job)
            check_manifest(job, user)
            artifact(job, partial=True).replace(artifact(job))
            heartbeat(job, status='completed', completed_at=utcnow())
            audit(job, 'report.generated')
            db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('Report generation failed (%s)', type(exc).__name__)
        db.session.execute(update(ReportJob).where(ReportJob.id == candidate, ReportJob.status == 'running')
            .values(status='failed', error='Generation failed. Please request a new report.'))
        audit(job, 'report.failed')
        db.session.commit()
        artifact(job, partial=True).unlink(missing_ok=True)
        artifact(job).unlink(missing_ok=True)
    finally:
        db.session.remove()
    return True


def cleanup():
    """Mark abandoned jobs failed; expire artifacts without removing audit/history."""
    cutoff = utcnow() - timedelta(minutes=current_app.config['REPORT_LEASE_MINUTES'])
    abandoned = db.session.scalars(select(ReportJob).where(ReportJob.status == 'running',
        ReportJob.heartbeat_at < cutoff).limit(100)).all()
    for job in abandoned:
        changed = db.session.execute(update(ReportJob).where(ReportJob.id == job.id,
            ReportJob.status == 'running', ReportJob.heartbeat_at < cutoff)
            .values(status='failed', error='Worker stopped. Please request a new report.')
            .execution_options(synchronize_session='fetch'))
        if changed.rowcount:
            audit(job, 'report.failed')
    db.session.commit()
    expired = db.session.scalars(select(ReportJob).where(ReportJob.expires_at <= utcnow(),
        ReportJob.status != 'expired').limit(100)).all()
    for job in expired:
        try:
            artifact(job).unlink(missing_ok=True)
            artifact(job, partial=True).unlink(missing_ok=True)
            storage = root()
            for spool in storage.glob(f'{job.id}-*'):
                # Remove only this job's direct, non-symlink spool directories.
                if spool.is_dir() and not spool.is_symlink() and spool.resolve().parent == storage:
                    shutil.rmtree(spool)
        except OSError:
            current_app.logger.warning('Report cleanup deferred for an open or inaccessible file')
            continue
        db.session.execute(delete(ReportItem).where(ReportItem.job_id == job.id))
        job.status = 'expired'
        audit(job, 'report.expired')
        db.session.commit()
    return len(expired)
