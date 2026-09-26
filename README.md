# AssureX Claims

AssureX is a claims-focused AI/ML project workspace.

## Project Structure

- `frontend/` — frontend application
- `backend/` — backend APIs and services
- `data/` — datasets and project data
- `dataset_generator/` — dataset generation utilities
- `notebooks/` — experiments and exploratory analysis
- `models/` — model code and model artifacts
- `gtm_model/` — GTM model components
- `policies/` — policy definitions and rules
- `document_processing/` — document ingestion and processing
- `tests/` — automated tests
- `reports/` — generated reports and evaluations
- `screenshots/` — screenshots and visual evidence
- `documentation/` — technical and project documentation
- `config/` — configuration templates and settings

## Development

Install Python dependencies with:

```bash
pip install -r requirements.txt
```

Add dependencies to `requirements.txt` as implementation progresses.

## Phase 2: Database

The core relational schema is in `backend/db/models.py`, its first Alembic migration is in `backend/db/migrations/versions/`, and isolated migration/relationship tests are in `tests/test_database.py`. PostgreSQL is the production target; SQLite is the local/test fallback. See [database setup and architecture](documentation/database.md) for environment variables, migration and seed commands, relationships and limitations.

## Phase 3: Authentication and RBAC

The Flask application factory is `backend:create_app`. Authentication uses Bcrypt, signed access/refresh JWTs, persisted session revocation, and database-backed customer/employee/reviewer/admin permissions. Protected claim and review workflows, private document uploads, administrator user management and audit records are included.

Start with [setup and component explanations](documentation/authentication.md), then use the [API reference](documentation/api.md). Copy `config/.env.example` to `.env`, generate a JWT secret, install requirements, run `python -m flask --app backend:create_app db upgrade`, and bootstrap an admin with `python -m flask --app backend:create_app create-admin`. Run the application with `python -m flask --app backend:create_app run` and tests with `python -m pytest -q`.

## Phase 4: Products and warranties

Open `/products` to register products, search/filter your portfolio, edit product details and manage original/extended warranties. Product age, expiry, remaining time and status are calculated on the server with UTC calendar dates. The responsive frontend uses Flask, HTML, CSS and JavaScript without a build step.

Run `python -m flask --app backend:create_app db upgrade` before starting the app. See [Phase 4 setup, API and date rules](documentation/products.md) for migration details, serial uniqueness, authorization, warranty history and browser tests.

## Phase 5: Claim submission

Open `/claims` for the React four-step claim wizard, automatic draft saving, private supporting-document uploads, review and sequential Claim IDs. Install Python requirements, run `npm --prefix frontend ci` and `npm --prefix frontend run build`, then upgrade the database before starting Flask. See [claim workflow setup, architecture, API examples and testing](documentation/claims.md).

## Phase 6: Document upload and OCR

Claim evidence now passes signature/corruption checks, SHA-256 duplicate detection, private storage, provider-neutral OCR, structured Nigerian receipt/warranty extraction and explicit user confirmation. Machine values and user corrections remain separate for audit, fraud and rule-engine use. See [document processing setup, security model and API](documentation/documents.md), including the required Tesseract system installation.
