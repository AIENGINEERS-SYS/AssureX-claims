# Advanced search and filtering

Phase 25 adds a shared search workspace at `/search`, reusable filters on the claims list, global navigation search, and links from product, reviewer, administrator and reporting pages. All searches run against the current user's database permissions. No search engine, duplicated document index or external service is required.

## Architecture

| Component | Responsibility |
| --- | --- |
| `backend/api/search_schemas.py` | Strict validation and normalization for query strings, saved criteria and report filters |
| `backend/services/search.py` | Role-scoped SQL, correlated latest prediction/current warranty lookups, composable filters, stable sorting and database pagination |
| `backend/api/search.py` | JWT APIs, suggestions, private saved/recent searches, telemetry and administrator analytics |
| `backend/db/search_models.py` | SavedSearch, SearchEvent and SearchFilterUse tables plus lookup indexes |
| Migration `2cb08e57a419` | Schema changes following the report migration; reversible on SQLite and PostgreSQL |
| `frontend/src/search/SearchWorkspace.jsx` | SearchBar, FilterDrawer, AdvancedFilterPanel, DatePicker, StatusSelector, ConfidenceSlider, SortControls, PaginationControls and results |
| `frontend/src/search/SearchApp.jsx` | Authenticated search page, global navigation search and administrator analytics |
| `backend/services/search_indexes.py` | Optional PostgreSQL trigram indexes for substring matching |

Filters are ANDed across fields; each multi-select uses OR within that field. Bound SQL parameters and escaped LIKE metacharacters keep text input separate from SQL. Related record filters use scalar lookups or `IN`/`EXISTS` subqueries rather than one-to-many joins, so multiple reviews or predictions cannot duplicate a claim or inflate counts.

## Authorization

- Customers: their own claims, products and warranties.
- Employees: assigned claims and the products/warranties referenced by those claims.
- Reviewers: assigned claims plus unassigned claims with `status=manual_review` and `manual_review_required=true`. Other reviewers' assignments remain hidden. Products and warranties are restricted to these visible claims.
- Administrators: all records.

`/api/review/search` requires reviewer/admin access and restricts results to the current manual-review queue. `/api/claims/search` also allows reviewers to find their assigned claim history. Grouped Users results expose only ID, display name and role: non-admins see themselves and people connected to their visible claims. They cannot enumerate an unrelated customer directory. Emails, passwords, storage paths and model artifact locations are never returned by search.

Saved searches contain criteria, never cached results. Reusing them, requesting suggestions and paging results always reruns SQL authorization. Clients clear search caches on logout/session expiry. Saved-search IDs are checked against the requesting account for deletion. No query parameter can disable or widen the role scope.

## API

All endpoints require `Authorization: Bearer <access_token>` and use the existing API error envelope.

| Method | Endpoint | Result |
| --- | --- | --- |
| GET | `/api/search` | Grouped Claims, Products, Warranties and Users, separately paginated |
| GET | `/api/claims/search` | Claim results |
| GET | `/api/products/search` | Product results |
| GET | `/api/warranties/search` | Warranty history records |
| GET | `/api/review/search` | Reviewer queue |
| GET | `/api/search/suggestions?q=ABC` | Up to five scoped suggestions per group; minimum two characters |
| GET | `/api/search/saved` | Up to 50 private saved searches |
| POST | `/api/search/saved` | Save named criteria; HTTP 201 |
| DELETE | `/api/search/saved/{id}` | Delete own saved criteria; HTTP 204 |
| GET | `/api/search/recent` | Up to ten distinct recent searches from the user's latest 100 successful/empty searches |
| GET | `/api/search/analytics?days=7` | Admin-only search usage and timing aggregates |

Search requests are limited to 60/minute per authenticated user per endpoint; suggestions allow 90/minute. Existing global API limits also apply. Unknown parameters, repeated scalar parameters, invalid enums/ranges, excessive lengths and invalid page sizes return 400. Ownership failures for saved-search IDs return 404. Reviewer/admin-only operations return 403 for other authenticated roles.

### Parameters

| Parameter | Meaning |
| --- | --- |
| `q` | Literal case-insensitive text, max 120 characters; searches public IDs, product details and authorized customer/reviewer names |
| `claim_id` | Exact public claim ID, e.g. `CLM-2026-000001` |
| `product_id` | Exact public product ID, e.g. `PRD-2026-000145` |
| `serial_number` | Serial value to match |
| `serial_match` | `partial` (default) or case-insensitive `exact` |
| `product_name`, `brand`, `model` | Case-insensitive substring filters |
| `category` | Multi-select category names; case-insensitive, including the existing categories in your database |
| `claim_status` | Multi-select `draft`, `submitted`, `under_evaluation`, `additional_information_required`, `manual_review`, `approved`, `rejected`, `closed`; `under_review` aliases `under_evaluation` |
| `warranty_status` | Multi-select `active`, `near_expiry`, `expired`, `extended_warranty`, `not_started`, `no_warranty` |
| `min_confidence`, `max_confidence` | Inclusive latest Python prediction confidence; fractions 0–1 or explicit strings such as `70%` |
| `reviewer_id`, `customer_id` | Internal positive integer ID or public `USR-...` user ID |
| `reviewer_name`, `customer` | Partial display-name match |
| `assignment` | `assigned` or `unassigned`; unassigned cannot also specify a reviewer |
| `provider` | Warranty provider substring |
| `extended_warranty` | `true` or `false`; independent of whether that warranty is currently active |
| `date_field` | `submission_date` (default), `review_date`, `warranty_expiry_date` |
| `date_preset` | `today`, `7d`, `30d`, `90d`, or `custom` |
| `start_date`, `end_date` | ISO dates, inclusive; custom requires both, while an explicit one-sided range is also supported without a preset |
| `sort` | `newest` (default), `oldest`, `highest_confidence`, `lowest_confidence`, `recently_updated`, `warranty_expiry_date` |
| `order` | Optional `asc` or `desc` override |
| `page` | 1–100000, default 1 |
| `page_size` | 1–100, default 25 |

Multi-selects accept repeated query parameters or comma-separated values. JSON saved/export filters use arrays. Supported category choices in the UI are Electronics, Mobile Devices, Home Appliances, Computers, Audio Equipment and Other. The API additionally accepts existing custom categories so older records remain searchable.

Confidence is the `top_confidence` of the latest persisted Python prediction, ordered by prediction time and ID. It is not the invalid-class probability, a fraud score or a blend of Python/GTM outputs. Unscored records never match a confidence range and sort last in either confidence direction. Bare `70` is rejected; use `.7` or `70%`. NaN and infinity are rejected.

Warranty status matches the existing product semantics: not-started and expired are checked first, then an active extension is Extended Warranty, an active standard warranty ending within `WARRANTY_NEAR_EXPIRY_DAYS` is Near Expiry, and the remaining current coverage is Active. To find every currently covered warranty, select Active + Near Expiry + Extended Warranty. Status is computed from current dates rather than stored as a stale column. Product searches use the currently selected authorized warranty; warranty searches return individual historical warranty records. Claims use the warranty attached to that claim. `no_warranty` can match products/claims, not actual warranty rows.

Date presets use UTC calendar dates and include today. Timestamp review filtering uses a half-open interval through midnight after the end date, which includes every review on the final day. Any review in the interval qualifies its claim once. Claims without a submission date do not match a submission-date range. Claim-related filters on product/warranty searches require a related visible claim; standalone products remain searchable when no claim filter is applied.

Newest/oldest use record creation time; recently updated uses update time. Every sort includes record ID as a deterministic tie-breaker and puts null values last. Confidence sorting applies to Claims; in grouped results the other groups use creation time. Users have no warranty expiry field and likewise use creation time for that sort. Totals and pages are computed in SQL. Concurrent edits can change page membership between requests; this is not a frozen snapshot.

### Combined request and response

```http
GET /api/claims/search?claim_status=manual_review&warranty_status=active&min_confidence=0.6&max_confidence=0.8&reviewer_id=15&start_date=2026-01-01&end_date=2026-01-31&page=1&page_size=25
Authorization: Bearer <access_token>
```

```json
{
  "items": [{
    "id": 42,
    "claim_id": "CLM-2026-000001",
    "status": "manual_review",
    "product_id": "PRD-2026-000145",
    "product_name": "Laptop",
    "serial_number": "ABC123456",
    "customer": "Ada Example",
    "reviewer": "John Smith",
    "reviewer_id": 15,
    "confidence_score": 0.65,
    "warranty_status": "active",
    "expiry_date": "2026-12-31",
    "submission_date": "2026-01-20",
    "created_at": "2026-01-20T09:00:00+00:00",
    "updated_at": "2026-01-21T09:00:00+00:00"
  }],
  "total": 1,
  "page": 1,
  "page_size": 25,
  "total_pages": 1,
  "elapsed_ms": 12,
  "filters": {"claim_status":["manual_review"],"warranty_status":["active"],"min_confidence":0.6,"max_confidence":0.8,"reviewer_id":"15","start_date":"2026-01-01","end_date":"2026-01-31","q":"","serial_match":"partial","date_field":"submission_date","sort":"newest","page":1,"page_size":25}
}
```

Global search wraps the same pagination shape under `groups.claims`, `groups.products`, `groups.warranties` and `groups.users`. A page/size applies independently to every group; it is not a combined limit of 25 across all groups.

```http
POST /api/search/saved
Content-Type: application/json
Authorization: Bearer <access_token>

{"name":"High Risk Claims","scope":"claims","filters":{"claim_status":["manual_review"],"max_confidence":"70%","date_preset":"30d"}}
```

Save responses include `id`, `name`, `scope` and normalized `filters`. Names are unique per account. Page resets to 1 on save/reuse. Relative presets in saved searches are evaluated again when used.

### Reporting integration

```http
POST /api/reports/export/excel
Content-Type: application/json
Authorization: Bearer <access_token>

{"advanced":{"claim_status":["manual_review"],"category":["computers"],"max_confidence":0.7,"date_preset":"30d"}}
```

The Reports `advanced` object accepts the same normalized filter schema and restricts claim, product and warranty sections using the shared SQL builder. Existing report parameters still work and combine with these filters. Pagination never limits exported records; exports retain their stable record-ID order. Relative report date ranges are fixed to concrete dates when requested, so a queued job does not silently move to a different day. The existing reporting authorization is deliberately stricter for reviewers: only assigned claims can be exported, even though eligible unassigned cases are searchable. Report access is checked again during generation and download.

## Telemetry and retention

SearchEvent stores requester ID, normalized criteria, query text, duration, result count and outcome (`success`, `empty`, `invalid`, `error`). SearchFilterUse stores one row per used filter for efficient SQL aggregation. Suggestions do not add history events. Query timing excludes authentication and the telemetry commit. Invalid requests record no raw search criteria. Telemetry failures roll back their transaction and do not replace a successful search response.

Only administrators can see common search terms, most-used filters, average/max response times and failed-search counts. Zero-result searches are counted separately from validation/server failures. Ordinary users see only their own recent searches. Search text can contain names, so telemetry has a configurable retention window (default 30 days); do not expose these tables to general reporting or application logs. Saved searches persist until deleted and are not part of telemetry cleanup.

## Deployment and performance

```powershell
python -m flask --app backend:create_app db upgrade
# PostgreSQL only; run with permission to install pg_trgm and create indexes:
python -m flask --app backend:create_app search-indexes
# Schedule daily:
python -m flask --app backend:create_app search-cleanup
```

| Setting | Default | Purpose |
| --- | --- | --- |
| `SEARCH_TIMEOUT_MS` | 5000 | PostgreSQL transaction-local statement timeout for searches/suggestions |
| `SEARCH_RETENTION_DAYS` | 30 | Recent/analytics window and cleanup cutoff; 1–365 |

Exact public IDs already have unique indexes. The migration adds lowercased serial/category indexes, owner/creation and updated claim indexes, current-warranty lookup indexes, latest-prediction/confidence indexes, review-date indexes and telemetry indexes. SQLAlchemy caches compiled statements; permission-sensitive result rows are not cached on the server. React debounces edits by 300 ms, cancels superseded requests and uses separate query keys for each user/scope/filter set. Claim management now fetches one page instead of downloading all claims.

PostgreSQL substring searches use `ILIKE`, with literal wildcard characters escaped. The optional command creates `pg_trgm` GIN indexes concurrently outside the migration transaction, so ordinary writes can continue during index creation. These deployment-managed indexes remain installed on a search-schema downgrade; remove them explicitly only if appropriate. The command requires PostgreSQL and fails clearly on SQLite. Existing installations with large tables should also schedule the ordinary migration's B-tree index creation during a maintenance window.

Trigram indexes help searches with usable trigrams; very short terms and broad filters may still scan many rows. Exact counts, deep offset pages and computed confidence/expiry sorting can be expensive. Statement timeouts bound PostgreSQL query work. SQLite has functional indexes and is supported for local development, not the production workload.

Tests inspect SQLite query plans for exact claim-ID and case-insensitive exact serial lookups and verify index usage. Review deployment plans with representative data and statistics:

```sql
ANALYZE claims;
ANALYZE products;
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM claims WHERE claim_id = 'CLM-2026-000001';
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM products WHERE lower(serial_number) = lower('ABC123456');
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM products WHERE serial_number ILIKE '%ABC123%';
```

PostgreSQL runtime query plans and production throughput must be checked on the deployment database; local SQLite tests do not establish a production latency guarantee.

## Verification

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_search.py
$env:ASSUREX_BROWSER_TESTS='1'
.venv\Scripts\python.exe -m pytest -q tests/test_search_browser.py tests/test_claim_browser.py
npm --prefix frontend run build
```

Coverage includes exact/partial IDs and serials, all warranty statuses, multi-select and combined filters, latest-confidence semantics, reviewer/customer filters, UTC date boundaries, pagination/sorting, wildcard/SQL-injection inputs, every role boundary, suggestions, private saved/recent searches, analytics, telemetry retention, migration round trips, query plans, and shared report-export filters. Browser coverage exercises filtering, saved-search reuse/deletion, empty states, navigation into claims and mobile overflow.

Local verification: 33 search tests passed, including pagination across 10,000 claims and PostgreSQL SQL compilation. The authentication, dashboard, claim submission and reporting regression run passed 119 tests (two `over_100000` cases excluded). Search and claim browser scenarios each passed, and the frontend production build passed. PostgreSQL runtime plans and throughput were not measured locally.

References: [PostgreSQL trigram index behavior](https://www.postgresql.org/docs/17/pgtrgm.html), [SQLAlchemy selectable and correlation APIs](https://docs.sqlalchemy.org/en/20/core/selectable.html).
