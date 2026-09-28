"""Generate reviewable sample files from synthetic records in an isolated database."""
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import secrets
import shutil
from backend import create_app
from backend.extensions import db
from backend.db.models import User, Product, Warranty, Claim, Review
from backend.services.report_jobs import enqueue, process_next, artifact
from backend.services.reporting import normalize


def main():
    destination = Path(__file__).resolve().parents[2] / 'documentation' / 'examples' / 'reports'
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='assurex-report-examples-') as temp:
        app = create_app({'ASSUREX_ENV': 'development', 'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': f'sqlite:///{Path(temp) / "examples.sqlite3"}',
            'JWT_SECRET_KEY': secrets.token_hex(32), 'BCRYPT_LOG_ROUNDS': 4,
            'RATELIMIT_ENABLED': False, 'REPORT_STORAGE_PATH': str(Path(temp) / 'reports')})
        with app.app_context():
            db.create_all()
            owner = User(email='sample@example.invalid', first_name='Sample', last_name='Customer', role='customer')
            reviewer = User(email='reviewer@example.invalid', first_name='Sample', last_name='Reviewer', role='reviewer')
            for user in (owner, reviewer):
                user.set_password(secrets.token_urlsafe(24))
            db.session.add_all([owner, reviewer]);db.session.flush()
            product = Product(user_id=owner.id, name='Example Laptop', category='electronics', brand='Example',
                model_number='AX-1', serial_number='SYNTHETIC-001', purchase_date=date(2026, 1, 1),
                purchase_price=250000, retailer='Example Store')
            db.session.add(product);db.session.flush()
            warranty = Warranty(product_id=product.id, provider='Example Warranty', start_date=date(2026, 1, 1),
                expiry_date=date(2026, 12, 31), coverage_duration_months=12)
            db.session.add(warranty);db.session.flush()
            claim = Claim(claim_id='CLM-EXAMPLE-001', user_id=owner.id, product_id=product.id,
                warranty_id=warranty.id, assigned_reviewer_id=reviewer.id, status='approved',
                submission_date=date(2026, 9, 20), fault_date=date(2026, 9, 19), fault_type='power',
                fault_description='The example laptop no longer powers on.', final_decision='likely_valid')
            db.session.add(claim);db.session.flush()
            db.session.add(Review(claim_id=claim.id, reviewer_user_id=reviewer.id, decision='approve',
                comments='Synthetic example: warranty coverage and purchase evidence verified.'))
            db.session.commit()
            owner_id = owner.id
            for format, extension in [('csv', 'csv'), ('excel', 'xlsx'), ('pdf', 'pdf')]:
                job = enqueue(db.session.get(User, owner_id), normalize({'report_type': 'customer', 'entity_id': owner_id}), format)
                job_id = job.id
                process_next()
                from backend.db.report_models import ReportJob
                job = db.session.get(ReportJob, job_id)
                if job.status != 'completed':
                    raise RuntimeError(f'Example {format} export failed')
                shutil.copyfile(artifact(job), destination / f'sample-customer-report.{extension}')
            db.session.remove();db.engine.dispose()
    print(f'Generated synthetic CSV, Excel and PDF examples in {destination}')


if __name__ == '__main__':
    main()
