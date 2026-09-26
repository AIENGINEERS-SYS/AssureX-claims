# Phase 2 database architecture

The database stores operational records. The training CSV in `data/` is a separate research dataset and is not silently imported into customer accounts.

## Setup

From repository root, use Python 3.11+:

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt -r tests/requirements.txt
# For local SQLite, DATABASE_URL may be omitted (./assurex_dev.db).
# For PostgreSQL, set DATABASE_URL=postgresql+psycopg://user:password@host:5432/assurex
python -m alembic -c backend/db/alembic.ini upgrade head
python -m pytest -q
```

Production requires a provisioned PostgreSQL database and a restricted DB account. `DATABASE_URL` takes precedence over `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`. Environment variables are read by `backend/db/session.py`; `.env` files are examples, not auto-loaded. Do not commit real credentials. To create the next migration:

```bash
python -m alembic -c backend/db/alembic.ini revision --autogenerate -m "description"
```

Review generated changes before application; test downgrade on a disposable database. The initial migration is `53a58d50e1e4_initial_claim_schema.py`. Production upgrades should have a backup. Development-only example records can be added after migration by setting `ASSUREX_ENV=development`, `ASSUREX_DEV_ADMIN_PASSWORD`, and `ASSUREX_DEV_CUSTOMER_PASSWORD` (each 12+ characters), then running `python -m backend.scripts.seed_dev`. The seed refuses a nonempty user table and creates inactive placeholder model versions, not trained model artifacts.

## Relationships

| Parent | Children | Delete policy |
| --- | --- | --- |
| User | Products, Claims, Notifications, Reviews as reviewer, Documents as uploader, Audit logs | Restrict referenced deletes |
| Product | Warranties, Claims, Repairs; optional Documents | Restrict referenced deletes |
| Warranty | Claims; optional Documents | Restrict referenced deletes |
| Claim | Documents, Repairs, both Predictions, Rules, Reviews, Notifications, Audit logs | Restrict referenced deletes |
| ModelVersion | Python Predictions, GTM Predictions | Restrict referenced deletes |

`claims.status` is the workflow state; `claims.final_decision` is the eventual recommendation. Prediction records retain their own result and precise `model_version_id`. Historical predictions, rules, reviews, and audit logs are append-only through the ORM, and foreign keys prevent deletion of referenced claims/models. This is an application guard, not a database privilege or trigger: direct SQL by privileged users can still alter records. Production permissions and archival policy should be defined before personal data goes live.

Internal integer primary keys are distinct from public `CLM-<20 random hex digits>` and corresponding prefixed IDs. UUID4 entropy provides collision resistance and unique constraints enforce it transactionally; there is no count-based allocation. Public IDs are not sequential. Indexes cover ownership, status, dates, serials, document SHA-256 hashes, and lookup of prediction/review/rule history. Hashes and storage paths are metadata; files belong in private object storage. Policy and extracted OCR fields use JSON rather than pickle or binary content.

`create_claim` checks user/product/warranty ownership and `record_prediction` verifies model type, scores, and class before insertion. Future API writers must use these service functions or equivalent transaction-safe checks; separate foreign keys alone do not enforce that a claim's product and warranty share an owner. A later migration can add composite foreign keys if all legacy data is validated first. Database constraints cover core enums, date order, nonnegative values and confidence bounds; Python input schemas supply more detailed validation.

Phase 3 now supplies authentication, protected HTTP endpoints and private document storage examples. See [authentication setup](authentication.md) and the [API reference](api.md). OCR, model inference, rule execution and dashboards remain later phases. No classifier accuracy or production performance claim is made by these database tests.
