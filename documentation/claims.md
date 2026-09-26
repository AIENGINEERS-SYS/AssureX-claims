# Phase 5: Claim submission

Open `/claims` after installing dependencies, building the frontend and upgrading the database. Customers can select a product, describe the fault, upload evidence, review and submit. The server returns a permanent identifier such as `CLM-2026-000001`.

## Setup

From the repository root (activate the Python virtual environment first):

```bash
pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
python -m flask --app backend:create_app db upgrade
python -m flask --app backend:create_app run
```

On Windows PowerShell, use `npm.cmd` if execution policy blocks `npm.ps1`. Set `JWT_SECRET_KEY` as described in `authentication.md`. The frontend is served by Flask at the same origin; no CDN, CORS allowance or separate frontend server is required. `npm --prefix frontend run watch` rebuilds during development. Build output is ignored by Git and must be included in deployment artifacts. Node 22.12+ is supported.

Sign in with a customer account. Tokens are held in memory, not localStorage. Reloading asks for sign-in again and resumes a saved draft through its URL. Expired access tokens are refreshed through the existing rotating JWT flow; if reauthentication is needed, the form stays mounted behind a sign-in dialog. Products and claims currently use separate page sessions.

## Folder structure and component responsibilities

```text
backend/
  api/claim_schemas.py                Draft allowlist, enums, text/date validation
  api/claim_workflow.py               Customer draft/upload/submit/detail routes
  api/claims.py                      Existing assignment/review document access, claim list
  db/models.py                       Claim, ClaimDocument alias, ClaimSequence, active products
  db/migrations/versions/d582ab901c42_claim_submission.py
  services/claim_submission.py        Ownership, optimistic versions, validation, ID allocation
  services/claim_storage.py           File parsing, private local and S3 adapters
frontend/
  package.json, package-lock.json     Reproducible React build dependencies
  vite.config.js                     Bundles local assets for Flask
  templates/claims.html               Same-origin application shell
  src/claims/
    main.jsx                         Routes, customer login, claim list and confirmation
    Wizard.jsx                       Four-step orchestration, autosave, mutation queue
    components.jsx                   Product, details, documents, review and progress UI
    api.js                           Axios JWT attachment, single-flight refresh, API errors
    styles.css                       Responsive layout, focus states and upload presentation
tests/
  test_claim_submission.py            HTTP validation, security, uploads, concurrency
  test_claim_migrations.py            Existing-data preservation and downgrade safeguards
  test_claim_browser.py               Opt-in real-browser workflow
documentation/claims.md               Setup, design, API contracts and examples
```

## Data model and compatibility

`Claim` extends the existing table: incomplete product/warranty/fault fields are nullable, and new fields record repair history, previous replacement, current step, optimistic version and submission timestamp. `customer_id`, `description` and `damage_category` are ORM aliases for existing `user_id`, `fault_description` and `damage_type` columns. This preserves existing employee/reviewer relationships and audit references.

`ClaimDocument` aliases the existing `Document` mapped class. It stores the claim reference, semantic document category, safe original name, detected MIME, byte count, private random storage key, SHA-256, uploader and timestamp. `file_name`, `file_path`, `file_type`, and `uploaded_at` are ORM aliases. Public responses omit storage paths and hashes. The public `damage_evidence` category maps to the existing database `fault_evidence` category.

New customer API responses use `DRAFT` and `SUBMITTED`; the database and existing employee/reviewer state machine retain lowercase states. Drafts have an internal random `DRF-…` reference and expose `claim_id: null`. Submission allocates the permanent public ID. Legacy claim IDs and submission records are preserved, without inventing precise timestamps for older date-only records.

`ClaimSequence` has one row per UTC year. A database atomic upsert increments and returns the counter in the submission transaction. PostgreSQL and SQLite serialize increments; rollback does not consume an ID. A unique constraint also protects public IDs. IDs increase within each year and are padded to at least six digits; drafts do not consume numbers. Internal legacy seed/service helpers still support existing historical IDs.

The migration initializes existing products as active. `Product.is_active` is enforced separately from warranty status: an expired warranty is displayed for review, rather than treated as a deactivated product. Deactivation is currently an internal administrative data operation; this phase does not add a customer deactivation endpoint. Warranty eligibility decisions remain part of claim evaluation.

## Authentication, validation and transaction behavior

JWT verification uses the existing persisted-session, revocation and current-user checks. Creating/editing/uploading/submitting is strictly customer-only, even for administrators. Product and claim lookups always scope to the current customer. Authenticated customer document downloads use existing ownership checks. Staff permissions remain unchanged.

Draft creation accepts incomplete input. Provided dates must be valid and not future UTC dates; provided enum values must be allowlisted. Draft descriptions can be shorter than 20 characters while being written, but cannot exceed 2000. Repair history cannot exceed 1000. On final submission all required fields, description minimum, product ownership/activity and all four required evidence categories are checked again. The server verifies stored file sizes and hashes before committing the submission timestamp and ID.

Text is Unicode-normalized, trimmed and stripped of control characters (except newline/tab). React renders it as text, including HTML-looking strings; do not later interpolate it into raw HTML. Marshmallow rejects unknown fields, preventing changes to ownership/status/timestamps through customer input. Database queries use bound SQLAlchemy parameters.

Every draft mutation requires its last returned `version`. A conditional database update both serializes changes and increments the version. Stale edits return `409`, so another tab cannot silently overwrite newer work. Upload totals are checked inside that serialized mutation. The frontend queues mutations and debounces autosaves for 900 ms. It does not reset active form values when an older save response returns.

Submitting a draft transitions its existing record, without inserting a duplicate claim. Retrying a completed submission returns `200` with the same permanent ID, even if the client still has the pre-submission version. A simultaneous stale request may return `409`; reload/retry to obtain the committed result. A failed validation rolls back version changes. Uploads and updates are rejected after submission.

Draft/product selection, edits, upload/removal and submission produce append-only audit entries within their database transaction. Audit entries avoid copying claim descriptions or document contents.

## Upload storage and limits

Uploads accept one file per request, up to 10 MiB (10 × 1024² bytes), and 50 MiB total per claim. Flask permits 11 MiB requests to allow multipart overhead around a full 10 MiB file. Required categories are `receipt`, `product_image`, `serial_number_image`, `damage_evidence`; optional categories are `warranty_card`, `diagnostic_report`, `repair_report`. Multiple files per category are allowed within the total limit.

The extension and submitted MIME must agree; Pillow then parses and verifies JPEG/PNG data, and pypdf parses PDFs. Truncated/spoofed images, encrypted PDFs, active PDF actions/scripts/embedded attachments, PDFs over 100 pages, overly complex PDF object graphs and images over 25 million pixels are rejected. Files are never served as executable or inline content; downloads use authenticated attachment responses with `nosniff`. Format validation is not a malware scanner; production deployments may add a quarantine/scanning stage according to their threat model.

Local storage defaults to private `instance/uploads/claims/<internal-id>/<random-id>.<extension>`, outside Flask static paths. A failed database commit cleans up the newly uploaded object. Removal commits database deletion first; storage cleanup failure is logged as an inaccessible orphan. Process crashes can also leave orphan objects; production operations should reconcile unreferenced keys after a retention grace period.

For S3-compatible storage:

Install the optional adapter dependency with `pip install -r backend/requirements-s3.txt`, then configure:

```dotenv
CLAIM_STORAGE=s3
S3_BUCKET=assurex-private-claims
# Optional for an S3-compatible endpoint:
# S3_ENDPOINT_URL=https://object-storage.example.com
```

Use the AWS credential provider chain / workload role, a private bucket with public access blocked, least-privilege GetObject/PutObject/DeleteObject permissions and server-side AES256 encryption support. The adapter passes AES256 encryption on uploads and downloads through the authenticated API. Local and S3 adapters share `put/read/delete`; do not switch a populated deployment without copying existing objects while preserving keys. Configure upstream body limits and request timeouts consistently. PostgreSQL and shared Redis remain production requirements from Phase 3.

## API reference

Every request below requires `Authorization: Bearer <access_token>`. JSON input uses `Content-Type: application/json`, except uploads.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/products/my` | Paginated products belonging to the current customer, including activity/warranty status |
| POST | `/api/claims/draft` | Create incomplete draft; `{}` is valid; returns 201 |
| PUT | `/api/claims/draft/{id}` | Partial update with required `version`; returns 200 |
| POST | `/api/claims/upload` | Multipart file/category/draft/version; returns 201 |
| DELETE | `/api/claims/draft/{id}/documents/{document_id}` | Remove draft evidence, JSON `draft_id` and `version` |
| GET | `/api/claims/{id_or_claim_id}` | Owned claim details and review `validation_errors` |
| POST | `/api/claims/submit` | JSON `draft_id` and `version`; returns 201 or 200 for completed retry |
| POST | `/api/claims` | Alias of validated submit; obsolete one-call claim payloads now return 400 |
| GET | `/api/claims/my` | Paginated owned claims and drafts |
| GET | `/api/claims/{internal_id}/documents/{document_id}` | Authenticated attachment download |

Lists accept `page` (default 1) and `per_page` (default 20, maximum 100), returning `items`, `page`, `per_page`, `total`. Error statuses: `400` invalid input or missing required evidence, `401` missing/invalid JWT, `403` wrong role, `404` absent/not-owned claim, `409` stale or immutable draft, `413` upload size limit. Wrong-owner product selection returns a generic field error without disclosing another customer's product.

Create a draft:

```http
POST /api/claims/draft
Content-Type: application/json

{"product_id": 12, "current_step": 2}
```

Example response excerpt (the actual response includes all fields and product/document details):

```json
{"claim":{"id":81,"claim_id":null,"customer_id":7,"product_id":12,"status":"DRAFT","current_step":2,"version":1,"documents":[],"submitted_at":null}}
```

Save details:

```http
PUT /api/claims/draft/81
Content-Type: application/json

{
  "version": 1,
  "fault_date": "2026-09-20",
  "fault_type": "Electrical Failure",
  "description": "The television no longer turns on when connected to power.",
  "damage_category": "Moderate",
  "repair_history": "No previous repairs.",
  "previous_replacement": false,
  "current_step": 3
}
```

Use the returned `claim.version` on the next mutation. Upload each required category:

```bash
curl -X POST http://localhost:5000/api/claims/upload \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F draft_id=81 -F version=2 -F document_type=receipt \
  -F 'file=@receipt.pdf;type=application/pdf'
```

Response excerpt:

```json
{"document":{"id":25,"document_type":"receipt","file_name":"receipt.pdf","file_type":"application/pdf","file_size":42310,"uploaded_at":"2026-09-26T12:00:00+00:00","uploaded_by":7},"claim":{"id":81,"status":"DRAFT","version":3}}
```

Review: `GET /api/claims/81` returns the claim plus `validation_errors`, an empty object if all metadata checks pass. Then submit using the latest version:

```http
POST /api/claims/submit
Content-Type: application/json

{"draft_id":81,"version":6}
```

Successful response excerpt:

```json
{"claim":{"id":81,"claim_id":"CLM-2026-000001","status":"SUBMITTED","submitted_at":"2026-09-26T12:05:00+00:00","submission_date":"2026-09-26","version":7}}
```

Example validation error:

```json
{"error":{"code":"validation_error","message":"Input validation failed.","details":{"description":["Use at least 20 characters."],"documents":["Upload: damage_evidence, serial_number_image."]}}}
```

## Verification

```bash
python -m pytest -q
```

The backend suite exercises real migrations, JWTs, customer ownership, incomplete drafts, stale writes, dates/text bounds, malformed/executable/active-document rejection, exact file/aggregate boundaries, storage integrity, immutable submissions, retry behavior, audit records, concurrent annual numbering and migration preservation. No live S3 account is required for local tests. Validate your PostgreSQL and S3 deployment separately before release.

Real browser test (after building; requires Node and Chrome/Edge):

```powershell
$env:ASSUREX_BROWSER_TESTS="1"
python -m pytest -q tests/test_claim_browser.py
```

The browser test covers login, product selection, detail validation, autosave/resume, document uploads, review, confirmation and mobile layout. Browser artifacts are stored under `.pytest_cache`.

Atomic upsert behavior follows the [SQLAlchemy SQLite documentation](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html). PDF parsing uses the [pypdf reader API](https://pypdf.readthedocs.io/en/6.13.0/modules/PdfReader.html).
