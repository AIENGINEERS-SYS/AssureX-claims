# Phase 3: Authentication and access control

The Flask application reuses the Phase 2 SQLAlchemy models and migration history. It provides registration, login, rotating refresh tokens, session logout, self-service profile/password updates, administrator user management, and protected claim/review examples backed by real records. Python 3.11+ is required.

## Folder structure and components

```text
backend/
  __init__.py                 create_app(), blueprint and extension registration
  config.py                   environment settings and startup validation
  extensions.py               SQLAlchemy, JWT, Bcrypt, Migrate, Limiter
  security.py                 JWT callbacks and reusable role_required decorator
  middleware.py               JSON errors, rollback and security headers
  cli.py                      create-admin, reset-password, prune-auth
  api/
    schemas.py                strict Marshmallow input and safe response shapes
    common.py                 pagination, user creation and audit helpers
    auth.py                   registration, login, logout, refresh, profile
    claims.py                 ownership, assignment, status and private documents
    review.py                 manual review, decisions, notes and rule indicators
    admin.py                  users, roles, analytics and all claims
  db/
    models.py                 existing models plus user/auth and assignment fields
    auth_models.py            AuthSession and RevokedToken
    migrations/versions/      Phase 2 and Phase 3 Alembic revisions
tests/
  test_auth.py                HTTP integration and authorization tests
  test_auth_migrations.py     legacy migration and Flask-Migrate compatibility
  test_database.py            Phase 2 regression tests
config/.env.example           environment template, no usable secret
```

Extensions are initialized in the factory; blueprints do not construct app instances. Flask-SQLAlchemy provides request-scoped sessions over the shared declarative Base. Models remain usable by standalone database scripts. Marshmallow validates HTTP inputs and rejects unknown fields, while the existing Pydantic record schemas remain available to Phase 2 consumers.

## Local setup (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r tests/requirements.txt
Copy-Item config/.env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
# Put the generated value in .env as JWT_SECRET_KEY.
.\.venv\Scripts\python.exe -m flask --app backend:create_app db upgrade
.\.venv\Scripts\python.exe -m flask --app backend:create_app create-admin
.\.venv\Scripts\python.exe -m flask --app backend:create_app run
```

The admin command prompts for name, email, and a hidden, confirmed password. There are no default accounts or passwords. Bash users can substitute `.venv/bin/python` and `cp` for the Windows commands. The Flask CLI loads the root `.env`; plain Python, standalone Alembic and Waitress require process environment variables. Do not commit `.env`. No schema is created or migrated during application startup.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m flask --app backend:create_app db check
.\.venv\Scripts\python.exe -m flask --app backend:create_app prune-auth
```

Tests use disposable SQLite databases, real migrations and low Bcrypt rounds for speed. Production uses 12 rounds. A live PostgreSQL/Redis deployment and concurrency under production load need separate infrastructure validation. Run `prune-auth` daily; it only removes expired records.

## Environment

| Variable | Meaning |
| --- | --- |
| `JWT_SECRET_KEY` | Required random secret, at least 32 bytes; generate 48 random bytes or more. Changing it invalidates all tokens. |
| `ASSUREX_ENV` | `development` by default; set `production` for deployment checks. |
| `DATABASE_URL` | PostgreSQL URL in production, e.g. `postgresql+psycopg://user:password@host:5432/assurex`. SQLite defaults to the repository's `assurex_dev.db`. |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Alternative database settings when `DATABASE_URL` is absent. |
| `RATELIMIT_STORAGE_URI` | Local `memory://`; production requires shared `redis://` or TLS `rediss://`. |
| `UPLOAD_FOLDER` | Private file directory; default `instance/uploads`. All instances must share the same durable storage. |

Access tokens expire after 15 minutes, refresh sessions after 7 days. These are centralized in `config.py`. Tests may override settings through `create_app({...})`. Production refuses missing/placeholder secrets, SQLite, disabled rate limiting, a non-Redis limiter store, or debug/testing mode.

## Database migration and legacy accounts

Revision `8b42d17c9a03` follows the existing revision. It maps `administrator` to `admin` and `service_center_employee` to `employee`, fills `full_name` from existing first/last names, normalizes emails, adds `auth_version` and employee assignments, and creates token/session tables. It preserves existing products, claims and evidence. First/last name columns are retained for Phase 2 compatibility; HTTP profile writes keep them synchronized with `full_name`.

Back up production and stop writes while applying migrations. Case-insensitive duplicate legacy emails stop the upgrade before modification; resolve those duplicates deliberately before retrying. SQLite batch migrations temporarily disable foreign-key enforcement on the migration connection and validate all foreign keys before committing. PostgreSQL is the production database. The Phase 3 data migration requires an online connection; offline SQL generation is not supported.

Existing PBKDF2 development seed hashes cannot be converted to Bcrypt without passwords. Reset those accounts explicitly:

```powershell
.\.venv\Scripts\python.exe -m flask --app backend:create_app reset-password --email admin@example.invalid
```

New seed records use Bcrypt. Administrative deletion is soft deactivation because claims, reviews, documents and audit history reference users. Deactivation, password changes and role changes invalidate outstanding tokens through `auth_version`; reactivation does not restore old tokens. Downgrade deletes auth session/revocation tables and assignment data, so perform it only after a backup and an intentional rollback decision.

## Authentication and authorization behavior

Passwords use salted Bcrypt via `User.set_password()` and `User.check_password()`. Passwords need at least 12 characters and no more than 72 UTF-8 bytes; overly long Unicode passwords are rejected rather than truncated. Passwords and hashes never appear in user responses or audit values. Password reset is a trusted operator command; email verification and emailed recovery flows are outside Phase 3.

JWTs are accepted only in `Authorization: Bearer <token>`. Signature algorithm, issuer, audience, expiration, token type, session membership, account activation and credential version are checked. Tokens include string user identity, role, credential version and session ID. The role claim is informational: permissions come from the database on each request.

A login creates a persisted session. Refresh uses a database compare-and-swap to consume the current refresh token exactly once, puts its JTI in the revocation table, and issues a replacement pair without extending the original session lifetime. Logout accepts either the current access or refresh token and revokes the entire session, including previously issued access tokens. Other logins remain active. Revocation therefore survives process restarts and is shared across workers. Clients must serialize refresh requests and atomically replace their token pair; a replay fails with 401. Replayed refresh tokens are rejected without automatically logging out a successfully rotated session.

`@role_required("employee", "admin")` verifies authentication before checking the current role. Administrators inherit protected route access. Ownership still defines `/claims/my`; administrators use `/admin/claims` to list all claims. A signed-in user with the wrong role receives 403; inaccessible claim/document IDs return 404 to avoid disclosing another customer's records. Expired, malformed, missing, revoked, wrong-type or inactive-account tokens return 401.

Registration only accepts name, email and password. Only admin APIs create staff or change roles. Sensitive admin mutations and password changes require a fresh login token; refreshed tokens are insufficient. Admins cannot demote or deactivate themselves, and PostgreSQL row locks serialize administrator changes and claim decisions. Audit entries record user management, profile/password changes, logins/logouts, claims, documents and reviews without recording credentials.

## Deployment and rate limiting

Terminate HTTPS at a trusted reverse proxy and bind the WSGI server to a private interface. With environment variables supplied by your deployment secret manager:

```powershell
.\.venv\Scripts\waitress-serve.exe --listen=127.0.0.1:8000 --call backend:create_app
```

Do not use Flask's development server in production. The API sends no-store, nosniff, frame restrictions and production HSTS headers. Header bearer authentication does not rely on browser cookies; a future cookie-based integration must enable CSRF protection. Keep tokens out of URLs/logs and avoid persistent browser storage exposed to scripts. No permissive CORS policy is enabled.

Flask-Limiter enforces IP limits: registration 5/minute, login 10/minute, refresh 30/minute, password changes 5/minute, and other endpoints 200/minute. All workers must use the same authenticated Redis store. Behind a proxy, configure trusted forwarded-address handling only for the known proxy chain; otherwise all clients share the proxy IP. Never trust arbitrary client-supplied forwarded headers. For public deployments, add account-based abuse controls, monitoring, and gateway limits for distributed attacks. Limiter storage errors fail closed; they are not silently ignored.

Files are stored under random names outside the public web root. Uploads have a 10 MiB request limit and PDF/PNG/JPEG signature allowlist. Downloads require authorization and force attachment delivery with an octet-stream MIME type. Signature checking is not malware scanning. Before exposing uploads at scale, connect quarantine/scanning and private object storage or a shared durable volume; do not configure the web server to serve the upload directory directly. A crash between filesystem write and DB commit can leave an orphan file; operational cleanup should reconcile against Document records. This example has no OCR or rendering pipeline.

The administrator role is the authorization boundary for future policy and fraud-settings blueprints. Phase 3 does not implement policy CRUD, fraud configuration, or model inference; risk endpoints expose existing RuleResult records. Claims reference products and warranties created through Phase 2 scripts or future product APIs.

Implementation references: [Flask-JWT-Extended revocation](https://flask-jwt-extended.readthedocs.io/en/stable/blocklist_and_token_revoking.html), [Flask-SQLAlchemy declarative models](https://flask-sqlalchemy.palletsprojects.com/en/stable/models/), and [Flask-Migrate](https://flask-migrate.readthedocs.io/en/latest/).
