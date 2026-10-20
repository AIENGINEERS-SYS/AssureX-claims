# Phase 5: Claim submission

Phase 6 extends this workflow with OCR, duplicate detection and user-confirmed extraction. See [`documents.md`](documents.md) for the current upload limits, document APIs and review rules.

Open `/claims` after installing dependencies, building the frontend and upgrading the database. Customers can select a product, describe the fault, upload evidence, review and submit. The server returns a permanent identifier such as `CLM-2026-000001`.

## Setup

From the repository root (activate the Python virtual environment first):

```bash
pip install -r requirements.txt
npm --prefix frontend ci
python -m flask --app backend:create_app db upgrade
waitress-serve --listen=127.0.0.1:8000 --call backend:create_app
# In a second terminal:
npm --prefix frontend run dev
```

On Windows PowerShell, use `npm.cmd` if execution policy blocks `npm.ps1`. Set `JWT_SECRET_KEY` as described in `authentication.md`, copy `frontend/.env.example` to `frontend/.env.local`, and open `http://localhost:5173/claims`. The Vite frontend calls the Flask API directly using `VITE_API_URL`; Flask allows only origins listed in `FRONTEND_ORIGINS`. Node 22+ is supported.

Sign in with a customer account. Tokens are held in memory, not localStorage. Reloading asks for sign-in again and resumes a saved draft through its URL. Expired access tokens are refreshed through the existing rotating JWT flow; if reauthentication is needed, the form stays mounted behind a sign-in dialog. Internal navigation between products, claims and dashboards shares the in-memory session.

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
  index.html, src/main.jsx            Standalone Vite SPA entrypoint
  src/App.jsx                         Product/claim/dashboard feature routing
  vite.config.js, Staticfile          Production build and SPA fallback
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

Draft creation accepts incomplete input. Provided dates must be valid and not future UTC dates; provided enum values must be allowlisted. Draft descriptions can be shorter than 20 characters while being written, but cannot exceed 2000. Repair history cannot exceed 1000. On final submission all required fields, description minimum, product ownership/activity and the applicable evidence requirements are checked again. The server verifies stored file sizes and hashes before committing the submission timestamp and ID.

Text is Unicode-normalized, trimmed and stripped of control characters (except newline/tab). React renders it as text, including HTML-looking strings; do not later interpolate it into raw HTML. Marshmallow rejects unknown fields, preventing changes to ownership/status/timestamps through customer input. Database queries use bound SQLAlchemy parameters.

Every draft mutation requires its last returned `version`. A conditional database update both serializes changes and increments the version. Stale edits return `409`, so another tab cannot silently overwrite newer work. Upload totals are checked inside that serialized mutation. The frontend queues mutations and debounces autosaves for 900 ms. It does not reset active form values when an older save response returns.

Submitting a draft transitions its existing record, without inserting a duplicate claim. Retrying a completed submission returns `200` with the same permanent ID, even if the client still has the pre-submission version. A simultaneous stale request may return `409`; reload/retry to obtain the committed result. A failed validation rolls back version changes. Uploads and updates are rejected after submission.

Draft/product selection, edits, upload/removal and submission produce append-only audit entries within their database transaction. Audit entries avoid copying claim descriptions or document contents.

## Upload storage and limits

Uploads accept one file per request, up to 10 MiB (10 × 1024² bytes), and 50 MiB total per claim. Flask permits 11 MiB requests to allow multipart overhead around a full 10 MiB file. Required categories are `product_image` and `damage_evidence`. Receipt/invoice and serial labels are conditional on missing registration data; `warranty_card`, `diagnostic_report`, `repair_report` and `other` are optional. Multiple files per category are allowed within the total limit.

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

Use the returned `claim.version` on the next mutation. Upload each category in `claim.document_policy.required_types`; for example, an optional receipt:

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

## Context-aware claim evidence

Select a registered product, describe the incident, add a product photograph and
one or more damage photographs, then review and submit. Registered purchase,
identity and warranty information is reused from the server. Receipts and serial
label photographs are requested only when the corresponding registration data is
missing; an invoice satisfies the receipt requirement. Coverage and ownership
checks remain separate from upload completeness. Missing optional invoice data
never blocks a claim.

Upload contracts remain backward-compatible (`document_type`, `version`, `file`
in multipart form data). Alternatively supply `evidence_category`; conflicting
category/type combinations are rejected rather than silently reclassified.

| Evidence category | Existing document type | Processing |
| --- | --- | --- |
| PURCHASE_RECEIPT | receipt, invoice | Purchase OCR and human confirmation |
| PRODUCT_IMAGE | product_image | Image quality, visual provider when configured, visible identity-label OCR |
| DAMAGE_EVIDENCE | damage_evidence (stored as fault_evidence) | Image quality and visual provider when configured; no purchase OCR |
| SERIAL_NUMBER_LABEL | serial_number_image | Model/serial OCR |
| WARRANTY_DOCUMENT | warranty_card | Model/serial and warranty-duration OCR |
| OTHER | other, diagnostic_report, repair_report | Private attachment storage, no forced purchase extraction |

Responses add `evidence_id`, `evidence_category`, `processing_status`,
`applicable_fields`, `validation_warnings`, `analysis`, and `metadata_conflicts`.
`document_policy.required_types` is the authoritative list for the selected
product; `document_completeness.missing` accounts for already supplied documents.
Each attachment retains independent persistent processing results. Processing is
synchronous; the public states currently emitted are UPLOADED, PROCESSING,
COMPLETED, NEEDS_REVIEW and FAILED. There is no background queue in this change.

Review uses `PATCH /api/documents/:id/ocr-review` with the current claim version,
`confirm: true`, and only fields applicable to that category. Failed OCR permits
manual correction. Retry uses `POST /api/documents/:id/ocr/retry`; confirmed values
and provenance survive both failed and successful retries. New supplementary
confirmed metadata is retained on the product and reused by subsequent claims.
Existing registered values and confirmations from other evidence are preserved;
conflicts are shown for review. Product identity changes use the existing product
management flow, not implicit OCR updates.

Mutations use the existing optimistic claim-version lock. The wizard serializes
uploads and saves; simultaneous independent API writers receive a 409 on a stale
version and must reload before retrying. Duplicate file hashes are rejected with
`duplicate_document` and cannot create another attachment. Repeating processing
of an already processed attachment is a no-op unless an explicit retry is used.
Submitting an already submitted draft returns its existing claim identifier.

### Visual assessment limitations

The repository's GTM classifier evaluates generated claim cards, not photographic
damage. It is not used to invent brands, damage categories, affected components or
severity. The built-in photo processor measures dimensions/brightness and reports
manual review when a photographic model is unavailable. A deployment can register
`app.extensions['evidence_vision']` with an `assess(content, mime, category)` method
returning `fields` (field observations with value/confidence), `warnings`,
`provider`, `version` and `needs_review`. This is an integration boundary; no new
trained visual model or paid external provider is supplied. Warranty-document OCR
currently extracts duration and visible model/serial labels; free-text coverage
terms, exclusions and warranty-date interpretation remain manual review.

### Upgrade and verify

Run the additive migration before starting the updated application:

```bash
python -m flask --app backend:create_app db upgrade
python -m pytest -q tests/test_claim_submission.py tests/test_document_ocr.py tests/test_evidence_workflow.py tests/test_document_migrations.py tests/test_migration_heads.py
npm ci --prefix frontend
npm run test:evidence --prefix frontend
npm run build --prefix frontend
```

The two new JSON columns are nullable, preserve existing product/claim rows, and
retain attachment provenance. Legacy claim attachments are linked to their
existing product/warranty during migration. Real OCR tests use Tesseract when
installed; no photographic model inference is claimed. For the opt-in browser
journey, use the existing `ASSUREX_BROWSER_TESTS=1` instructions above. The browser
scenario covers photographs without a repeat receipt, draft re-entry, review,
submission and mobile layout.

### Processing recovery and implementation review fixes

If a request stops after its file has been saved, its evidence remains recoverable.
The API exposes `can_retry` after the processing attempt exceeds
`max(60, OCR_TIMEOUT_SECONDS + 30)` seconds. The same retry endpoint starts a fresh
attempt. Completion updates are conditional on the attempt and immutable evidence
identity; late requests cannot overwrite a newer result or reveal a replacement
record if an internal row ID is reused. No additional migration is needed beyond
the existing evidence metadata migration.

The wizard and follow-up evidence view poll active processing and queue mutations
using the latest server version. Both support multiple-file selection. Duplicate
upload responses reload saved evidence before subsequent queued actions; other
session conflicts still require reload instead of silently overwriting changes.

Legacy scalar or value-wrapped confirmations remain readable. Historical damage
OCR review flags cannot block a claim behind an irrelevant purchase-field editor.
Older photographs without recorded visual assessment require human review; they
are not represented as completed damage classification. Unresolved assessment
warnings and metadata conflicts are included in final decision evidence even when
the claim-card models agree.

Visual providers must supply a nonempty `provider` and `version`, a boolean
`needs_review`, a list of warning strings and category-specific `fields`. Each field
is an object containing `value` and finite confidence between 0 and 1. Product
fields are `product_name`, `product_category`, `brand`, `model_number`, and
`serial_number`. Damage fields are `damage_category`, `description`,
`affected_components` (a list of strings), and `severity`. Unsupported fields,
malformed values and nonfinite confidence fail safely. Low-confidence/empty
assessments require review regardless of a provider's completion flag. Failed
retries preserve previous observations and confirmations.

Partial corrections preserve confirmed warranty units. A nonempty warranty duration
needs a known months/years unit, with at most 1200 effective months. Supplementary
purchase provenance references the stable internal claim ID, including while the
claim is a draft. Warranty evaluation checks both coverage start and expiry dates.
Prediction completeness and reviewer missing-evidence lists use the applicable
registration-aware requirements without inventing receipt attachments.
