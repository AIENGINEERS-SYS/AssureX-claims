# Phase 4: Products and warranties

Customers can now register a product and its original warranty in one transaction, browse/search/filter their products, edit details, and add or manage warranty periods. Open `/products` in the Flask application. Administrators can manage all products; employees and reviewers cannot use the product-management APIs. Existing claim access remains unchanged.

## Run the feature

Use the Phase 3 [setup instructions](authentication.md) to configure a private `.env` and install the Python dependencies, then run:

```powershell
.\.venv\Scripts\python.exe -m flask --app backend:create_app db upgrade
.\.venv\Scripts\python.exe -m flask --app backend:create_app run
# Open http://127.0.0.1:5000/products
```

Sign in with an existing customer/admin account, or choose **Create an account** in the sign-in dialog. The frontend uses the existing authentication endpoints. JWTs remain in page memory, refresh is serialized, and no tokens are written to browser storage. Reloading the page requires signing in again. Normal navigation stays within the application; expired-session reauthentication preserves unsaved forms. Logout revokes the session through the API.

There is no frontend framework or build step: Flask serves the template and local CSS/JavaScript from `frontend/`. Browser code renders untrusted values as escaped text, uses a same-origin content security policy, and never embeds user data in scripts. Forms retain values after validation/network failures. Dialogs, visible text status badges, native controls, focus indicators, live notifications and a skip link support keyboard/screen-reader use. The list includes loading, empty, error and retry states and works on narrow screens through a horizontally scrollable table.

## Files and architecture

| Location | Responsibility |
| --- | --- |
| `backend/api/products.py` | Authenticated product/warranty endpoints, ownership, SQL filtering and pagination |
| `backend/api/product_schemas.py` | Marshmallow input validation, exact monetary values and allowlisted fields |
| `backend/services/warranty_calculations.py` | UTC date, month/year arithmetic, age, remaining time, warranty selection and status |
| `backend/services/products.py` | Serial checks, warranty timeline rules, evidence protection and response shapes |
| `backend/db/product_identity.py` | Unicode-normalized brand/model/serial identity |
| `backend/db/models.py` | Existing Product and Warranty entities with two new fields |
| `backend/db/migrations/versions/c731ef4209ab_product_warranty_management.py` | Serial uniqueness and warranty duration-unit migration |
| `backend/web.py` | Same-origin frontend routes and assets |
| `frontend/templates/products.html` | Application shell and authentication/dialog markup |
| `frontend/static/products.js` | Real API workflows, forms, previews, dashboard and details |
| `frontend/static/products.css` | Responsive styles and accessible status treatments |
| `tests/test_products.py` | Calculation, validation, ownership and API integration tests |
| `tests/test_product_migrations.py` | Populated-database migration and duplicate preflight checks |
| `tests/test_product_browser.py` | Opt-in real-browser workflow, mobile layout and script-injection checks |

The existing `Product.name`, `model_number`, and `purchase_price` fields represent Product Name, Model and Price. Duration is stored once in `Warranty.coverage_duration_months`, alongside the new `duration_unit`; the product response exposes its original `warranty_duration` and `warranty_duration_unit`. Coverage, exclusions and service-center conditions reuse existing fields. No duplicate user/product/warranty tables are introduced. Data queries load warranties in batches, and sorting/filtering/pagination run in SQL rather than loading every product into Python.

## Calendar rules

All Phase 4 request calculations use one captured **UTC calendar date**. Dates are date-only ISO `YYYY-MM-DD` values and the browser formats them in UTC, independent of the device timezone. The dashboard/details refresh when visible each minute and on focus; editing forms are not replaced by background refreshes. No warranty status is persisted, so status changes do not need a scheduler.

- Expiry is **start date + calendar duration**, clamping to the last valid day of the target month. January 31 + one month becomes February 28 (29 in a leap year); February 29 + one year becomes February 28.
- The expiry date is **inclusive**. A warranty expiring today is Near Expiry, with `0 days remaining`, until the UTC date changes. An adjacent extension starts the following day.
- Age and remaining time show complete calendar years/months, falling back to days below one month. They never display negative values.
- `WARRANTY_NEAR_EXPIRY_DAYS` is centralized in configuration, defaults to **30**, and accepts 0–365. Exactly 30 days remaining is Near Expiry under the default.
- An active extended warranty takes status precedence over an active original, even if the extension itself is nearly expired.
- A future extension is scheduled, not active. During a gap after the original ends, the product remains Expired until the extension starts.
- Two additional empty/scheduled states, **No Warranty** and **Not Started**, distinguish missing coverage and future-only coverage without falsely showing Active or Expired.

For purchase/start `2025-01-01`, duration `2 years`, and current date `2026-09-26`, the response contains age `1 year, 8 months`, expiry `2027-01-01`, remaining `3 months remaining`, and status `Active`.

New/edited warranty periods cannot overlap; extensions must follow the original. Gaps are allowed and visible. Multiple sequential extensions are supported. Legacy overlapping records are retained on migration; calculations choose an active extension first, then latest expiry, then stable record ID. Otherwise the latest expired record is shown, or the earliest future record if none has started. SQL status filtering uses this same ordering. Edits that would retain/create overlaps are rejected until the timeline is corrected. PUT treats existing start dates as explicit: editing the purchase date does not silently move warranty periods.

## Registration and validation

Name, brand, category, model, serial, purchase date, price, retailer, duration and duration unit are required. Product registration defaults warranty start to purchase date and provider to brand; both can be specified. Purchase dates cannot be future dates. Prices accept zero and exact, nonnegative decimal amounts with at most two decimal places, capped by the existing `Numeric(12,2)` storage. Responses encode money as decimal strings; no currency conversion or currency guessing is applied. Warranty duration accepts whole months or years, up to 100 years.

Serial uniqueness applies across owners to **brand + model + serial number**, normalized with Unicode NFKC, trimming and case folding. Different manufacturers/models may legitimately reuse a serial. A SHA-256 identity column has a database unique constraint, which protects against concurrent duplicate submissions as well as ordinary validation. The ORM maintains the identity when product fields change. External bulk SQL writers must preserve this invariant; use the API/service layer for application writes.

Product and original warranty creation are atomic. Field validation returns structured 400 errors. Duplicates return 409. Public input cannot set ownership, calculated expiry, status, identity keys, or whether a warranty is extended. The first warranty on an unwarrantied product is original; subsequent records are extensions. Original warranties cannot be deleted while extensions remain.

Claims, documents and repairs are evidence: products referenced by them cannot be deleted, and purchase/identity fields cannot be changed. Display name and retailer can still be corrected. Referenced warranties cannot be edited or deleted. Unreferenced product deletion removes its warranties transactionally; unreferenced warranty deletion may leave a product in No Warranty state. Audit records retain creation, change and deletion actions. Product locks serialize warranty changes in PostgreSQL; claim creation uses the same lock ordering so it cannot attach evidence halfway through a product/warranty edit.

## API

All routes require `Authorization: Bearer <access_token>` and customer/admin access. Customers can access only their own products; attempts to read/edit someone else's records return 404. PUT replaces all required editable fields; it is not a partial update. Unknown fields are rejected.

| Method | Route | Result |
| --- | --- | --- |
| POST | `/api/products` | Register product and original warranty; 201 |
| GET | `/api/products` | Search/filter/sort/paginate; 200 |
| GET | `/api/products/{id}` | Product, current warranty, history and timeline; 200 |
| PUT | `/api/products/{id}` | Update product information; 200 |
| DELETE | `/api/products/{id}` | Delete unreferenced product and warranties; 200 |
| POST | `/api/products/{id}/warranties` | Add original if missing, otherwise extension; 201 |
| GET | `/api/products/{id}/warranties` | Ordered warranty history; 200 |
| PUT | `/api/warranties/{id}` | Update and recalculate a warranty; 200 |
| DELETE | `/api/warranties/{id}` | Delete an unreferenced warranty; 200 |
| POST | `/api/warranties/preview` | Validate duration and calculate expiry without storing anything; 200 |

Registration body:

```json
{
  "name": "Living room television",
  "brand": "Example",
  "category": "Electronics",
  "model_number": "TV-42",
  "serial_number": "SN-001",
  "purchase_date": "2025-01-01",
  "purchase_price": "75000.25",
  "retailer": "Example Store",
  "warranty_duration": 2,
  "warranty_duration_unit": "years",
  "warranty_provider": "Example Care",
  "warranty_start_date": "2025-01-01",
  "coverage": "Parts and labour",
  "exclusions": ["Accidental damage"],
  "service_center_conditions": "Authorized centers only"
}
```

The last five fields are optional. Success returns `message` and `product`, including `id`, prefixed `product_id`, source fields, `product_age`, `warranty_expiry`, `warranty_remaining`, `warranty_status`, `current_warranty`, `warranties`, `timeline`, and `server_date`. Use the integer product/warranty IDs when creating a claim through the Phase 3 API. Product PUT takes only the first eight fields above.

Warranty create/update body:

```json
{
  "provider": "Extended Care",
  "start_date": "2027-01-02",
  "duration": 12,
  "duration_unit": "months",
  "coverage": "Parts and labour",
  "exclusions": ["Consumables"],
  "service_center_conditions": "Authorized centers only"
}
```

Provider, start date, duration and unit are required. Preview takes only `start_date`, `duration`, and `duration_unit`, returning `expiry_date` and `server_date`. Warranty responses include `is_extended`, original/extended `warranty_type`, original duration unit, derived expiry, status, remaining days and progress percentage. Timeline entries identify purchase, original/extended starts/expiries and today's position.

List parameters: `q` (name, brand, model or serial), `category` (exact), `warranty_status`, `sort` (`newest`, `oldest`, `name`, `purchase_date`, `expiry_date`), `page` and `per_page` (default 20, max 100). Text searches treat `%` and `_` literally. Filters and pagination apply only to the accessible product set. `items`, `page`, `per_page` and `total` describe the filtered page; `summary` and `categories` describe the whole accessible portfolio. Read-only metadata never leaks another customer's products or categories.

## Migration and verification

Revision **`c731ef4209ab`**, after `8b42d17c9a03`, adds `products.serial_key` and `warranties.duration_unit`. Legacy durations retain their existing month counts and expiry dates. No customer data is rewritten or deleted. Normalized duplicate brand/model/serial combinations abort the migration before modifying schema. Resolve genuine duplicate records deliberately before retrying; back up production and stop writes during migration. Downgrade removes the two new columns/constraints and preserves product and warranty rows.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m flask --app backend:create_app db check
```

Real-browser tests are opt-in so the ordinary suite does not require a browser. They use an installed Chrome/Edge and Node 22+ with built-in WebSocket support; no npm packages or browser download are required:

```powershell
$env:ASSUREX_BROWSER_TESTS = "1"
# Optional if Chrome/Edge is not found automatically:
# $env:ASSUREX_BROWSER_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
.\.venv\Scripts\python.exe -m pytest -q tests/test_product_browser.py
```

Browser tests start an isolated local server/database and run headlessly. They cover login, registration, expiry preview, confirmation, details, editing, extensions, search/filtering, mobile layout, retained duplicate form data and untrusted text rendering. Desktop/mobile screenshots are written under ignored `.pytest_cache/`. Unit/integration tests cover leap years, month ends, today/tomorrow/yesterday, the exact near-expiry threshold, future/gapped/active extensions, no warranty, duplicate serials, money, role/owner checks, evidence protection and migrations. SQLite tests do not substitute for a PostgreSQL deployment/concurrency test.

Framework references: [Flask blueprints](https://flask.palletsprojects.com/en/stable/blueprints/) and [SQLAlchemy correlated queries](https://docs.sqlalchemy.org/en/20/core/selectable.html).
