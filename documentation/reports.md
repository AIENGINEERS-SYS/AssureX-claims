# Reports and exports

The report center at `/reports` uses the existing JWT session and database roles. Customers see their own records; employees see assigned claims; reviewers see explicitly assigned claims; administrators can request system-wide reports. An unassigned manual-review claim is **not** reportable by a reviewer. Customer-wide reports require the customer themselves or an administrator. Staff product reports include only claims and warranties within their assignment scope.

## Components

- `backend/services/reporting.py`: strict shared filter validation, scoped SQL queries, paginated previews, aggregate analytics. Field allowlists exclude credentials, document storage paths and internal model artifact paths.
- `backend/db/report_models.py`: persistent job history and a manifest of claim/product/warranty IDs. Migration `09a3b710ef42` adds the queue, manifest and assignment lookup indexes.
- `backend/services/report_jobs.py`: durable database queue, atomic claiming, bounded keyset batches, progress, owner quotas, expiry, audit records and download authorization.
- `backend/services/report_exports.py`: CSV, OpenPyXL write-only workbooks and paginated ReportLab PDFs.
- `backend/api/reports.py`: JWT-protected API and worker command.
- `frontend/src/reports/ReportsApp.jsx`: React Query polling, Axios downloads, filters, previews, progress, history, authentication and responsive styling with the existing Tailwind pipeline.

## Run locally or deploy

Install the updated root/backend requirements and build the frontend as usual. Apply migrations **before** starting the new API. Start a dedicated worker with the same database, configuration and private storage volume as the web process:

```powershell
.venv\Scripts\python.exe -m flask --app backend:create_app db upgrade
.venv\Scripts\python.exe -m flask --app backend:create_app report-worker
# For a scheduler or smoke test, process at most one queued job:
.venv\Scripts\python.exe -m flask --app backend:create_app report-worker --once
```

| Setting | Default | Purpose |
| --- | --- | --- |
| `REPORT_STORAGE_PATH` | `instance/reports` | Private shared durable volume; never expose through a static file server |
| `REPORT_RETENTION_HOURS` | 24 | Lifetime from request; queued and completed reports both expire |
| `REPORT_BATCH_SIZE` | 1000 | Maximum database rows buffered per export query |
| `REPORT_MAX_PENDING` | 3 | Maximum queued/running jobs per requester |
| `REPORT_LEASE_MINUTES` | 15 | Heartbeat timeout; interrupted jobs fail and can be requested again |
| `REPORT_PDF_FONT` | bundled Bitstream Vera | Optional readable TrueType font path with the glyphs required for your languages |

The existing production validation still requires PostgreSQL, HTTPS frontend origins and shared Redis rate limiting. PostgreSQL supports concurrent workers via `FOR UPDATE SKIP LOCKED`; SQLite is for local development, with one worker. Use a process supervisor to restart workers. The worker sweeps expired jobs and abandoned leases on every loop. Deployments with separate web/worker hosts must mount the same private volume. Configure disk monitoring and sufficient space for JSON spools plus final files. Encryption at rest is a storage/deployment responsibility.

## API

All endpoints require `Authorization: Bearer <access_token>`. Errors use the existing `{"error":{"code":"...","message":"..."}}` envelope. Numeric IDs are internal record IDs, as used by the existing claims/products API.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/reports` | Paginated report preview |
| GET | `/api/reports/claim/{claim_id}` | Individual claim and related records |
| GET | `/api/reports/customer/{customer_id}` | Customer claims, products and warranty history |
| GET | `/api/reports/product/{product_id}` | Authorized product, claim and warranty history |
| GET | `/api/reports/reviewer/{reviewer_id}` | Assigned reviewer claims and history |
| GET | `/api/reports/administrative` | Admin-only system report and status analytics |
| POST | `/api/reports/export/csv` | Queue CSV generation, HTTP 202 |
| POST | `/api/reports/export/excel` | Queue Excel generation, HTTP 202 |
| POST | `/api/reports/export/pdf` | Queue PDF generation, HTTP 202 |
| GET | `/api/reports/history?page=1&per_page=10` | Requester's private paginated history |
| GET | `/api/reports/jobs/{id}` | Status and progress |
| GET | `/api/reports/jobs/{id}/download` | Stream authorized completed artifact |

Preview filters are query parameters; export filters are the top-level JSON body. Both use the same schema. Unknown fields and conflicting statuses return 400. Pagination is preview-only (`page >= 1`, `per_page <= 100`) and never limits exports. Each preview section is independently paginated using those values and reports its own `total`.

| Filter | Values |
| --- | --- |
| `report_type` | `claims` (default), `claim`, `customer`, `product`, `reviewer`, `administrative` |
| `entity_id` | Required positive ID for claim/customer/product/reviewer reports |
| `dataset` | `claims`, `reviewer_queue`, `expiring_warranties`, `approved`, `rejected`, `manual_review` |
| `status` | Existing lowercase claim status |
| `search` | Up to 120 characters; literal, case-insensitive claim reference, description, product name or serial search |
| `customer_id`, `product_id`, `reviewer_id` | Additional claim scope restrictions; cannot expand permissions |
| `date_from`, `date_to` | Inclusive ISO submission dates; drafts without a submission date do not match |
| `expiry_days` | 0–365, default 30; warranties expiring today through this many days ahead |

Warranty text search applies to product name/serial. The `advanced` filter object integrates the [Phase 25 search service](search.md), including multi-select statuses/categories, confidence, reviewer and date filters. It is shared by report previews and every export format while retaining reporting's stricter assignment scope.

### Examples

```http
POST /api/reports/export/excel
Authorization: Bearer <access_token>
Content-Type: application/json

{"report_type":"claims","dataset":"approved","search":"Laptop","date_from":"2026-01-01","date_to":"2026-12-31"}
```

```json
{
  "id": "bf89d3e0-dc9f-4de0-8bda-41b1d59b7f43",
  "format": "excel",
  "filters": {"report_type":"claims","dataset":"approved","expiry_days":30,"search":"Laptop","date_from":"2026-01-01","date_to":"2026-12-31"},
  "status": "queued",
  "processed": 0,
  "total": 0,
  "progress": 0,
  "error": null,
  "created_at": "2026-09-28T10:00:00+00:00",
  "expires_at": "2026-09-29T10:00:00+00:00",
  "download_url": null
}
```

Poll the `Location` response header (or `/api/reports/jobs/{id}`). A completed response has `status: "completed"`, `progress: 100` and `download_url`. Download with the same authenticated session; a direct unauthenticated link is intentionally unusable. Pending downloads return 409, expired/missing artifacts return 410, and lost record permissions return 403. Another user's job ID returns 404, including for administrators; administrators can generate their own system reports.

Example preview shape (sections abbreviated):

```json
{
  "page":1,"per_page":25,
  "sections":{"Claims":{"items":[{"id":1,"claim_id":"CLM-EXAMPLE-001","status":"approved"}],"total":1}},
  "analytics":{"total_claims":1,"statuses":{"approved":1}}
}
```

## Output, consistency and performance

Reports include Claims, Customers (contact information), Products, Warranties, Reviews, Documents (metadata), Rules, Python/GTM predictions, Evaluations, Repairs and Analytics. CSV is one rectangular table with a `section` column and the union of section fields. Excel uses separate sheets with headers, full-data column width measurement (capped for readability), date cells, frozen headers, filters and conditional status colors. Long Excel text is continued in adjacent columns and sheets split at Excel's row limit. PDF uses wrapped labeled records and page numbers. Set a font with appropriate glyph coverage for non-Latin scripts.

The worker reads keyset batches into disk-backed JSON spools, then writes the artifact. No entire SQL result or Excel workbook is loaded into memory. Final downloads use Flask's file wrapper. ReportLab retains PDF page metadata until finalization, so PDF memory grows with page count; use CSV/Excel for very large machine-readable datasets. A browser's Axios Blob download also occupies client memory proportional to file size; API clients may stream directly to disk.

Each section has an ID high-water mark; rows added above it during generation are excluded. Rows reflect values observed during the scan, **not** a cross-table, repeatable-read financial snapshot. Final analytics are computed from the exported claim rows. Progress counts records across sections and stays below 100 until finalization. Counts may adjust when records change during generation. Revocation is checked at request, during work, before publication and at download. An already-started HTTP download cannot be recalled after a later permission change.

Completed reports are immutable files with a per-resource manifest. Downloads execute scoped `NOT EXISTS` checks against that manifest to catch assignment/ownership changes, and also verify current account activity, role and authentication version. User IDs alone are never treated as authorization. Values starting with spreadsheet formula characters are neutralized; XLSX strings are explicitly strings. Credentials and storage locations are never serialized.

Only small aggregate analytics are cached, per requester, role, authentication version, filter set and current scoped revision fingerprint, for at most 15 seconds. Authorization and report contents are not cached. SQLAlchemy also caches compiled SQL. Indexes cover assignment queues, product/warranty relationships, status and dates; broad substring searches may still need PostgreSQL trigram indexes at deployment scale after measuring query plans.

Audit events: `report.preview`, `report.requested`, `report.started`, `report.generated`, `report.download`, `report.failed`, `report.expired`. They store the actor, timestamp, report ID, format/status and request IP where available. Download events record authorized download initiation, not proof that a client received every byte. History survives file expiry. Generation failures expose a generic message; server logs record exception classes without row data or SQL parameters.

## Verification and examples

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_reports.py
.venv\Scripts\python.exe -m backend.scripts.report_examples
# Optional real Chromium desktop/mobile test:
$env:ASSUREX_BROWSER_TESTS='1'
.venv\Scripts\python.exe -m pytest -q tests/test_report_browser.py
```

The tests exercise JWTs, ownership and assignment boundaries, admin scope, all formats, PDF text extraction, Excel formatting, CSV formula protection, filters, pagination, quotas, failed leases, expiry, changed roles/assignments and migration round trips. Both CSV and Excel are tested with 100,001 claim records using SQLite. PostgreSQL concurrency and production throughput should also be validated against the deployment database and storage.

The example command creates a temporary isolated SQLite database with synthetic records and writes CSV/XLSX/PDF files under `documentation/examples/reports`. It never reads the application's real data.

Local verification: 20 reporting tests, 135 selected existing backend regression tests, and the real browser test passed. The production frontend build passed. CSV and Excel exports each contained all 100,001 test claims; generated Excel was reopened and every row counted. The existing evaluation regression tests emitted model-library version warnings in this local environment. PostgreSQL worker concurrency was not exercised locally.

Implementation references: [OpenPyXL optimized writing](https://openpyxl.readthedocs.io/en/stable/optimized.html), [SQLAlchemy large-result behavior](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#fetching-large-result-sets-with-yield-per). This implementation uses bounded keyset queries so progress can commit between batches without holding a server-side cursor open.
