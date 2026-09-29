# AssureX Claims

AssureX is a warranty and insurance claims application. Customers can register products and warranties, create claims, upload evidence, review OCR results, submit claims, and track their status. Reviewers and administrators get separate dashboards for claim handling, duplicate-document review, workload, and operational reporting.

## Feature status

| Capability | Status |
| --- | --- |
| Registration, login, products, and warranties | Implemented |
| Claim drafts, uploads, OCR review, submission, and tracking | Implemented |
| Customer, reviewer, and administrator dashboards | Implemented |
| Document and evidence-based claim duplicate detection | Implemented |
| Python model prediction generation | Implemented |
| Google Teachable Machine prediction generation | Implemented |
| Warranty-rule, contradiction, and decision generation | Implemented |
| Private Claim Summary Card generation | Implemented |
| Interactive OpenAPI / Swagger API documentation | Implemented |
| PDF, CSV and Excel reports with background exports and access-controlled history | Implemented |
| Advanced search, combined filters, saved searches and search analytics | Implemented |

Model operation and deployment details are documented in `documentation/model-evaluation.md`.

Use `/search` for scoped global search and advanced filters, or search directly from My claims. See [search setup, API and indexing](documentation/search.md) for migration, PostgreSQL trigram indexing and telemetry-retention commands.

## Install the application

### Requirements

- Python 3.11 or 3.12 for the complete Python and GTM inference stack
- Node.js 20+ and npm
- Tesseract OCR for image and scanned-PDF extraction
- SQLite for local development; PostgreSQL is required in production
- Redis for production rate limiting

### Local setup

From the repository root, create a virtual environment and install both services:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm --prefix frontend ci
```

Create the local environment files:

```powershell
Copy-Item config/.env.example .env
Copy-Item frontend/.env.example frontend/.env.local
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the generated value in `.env` as `JWT_SECRET_KEY`. The default development configuration uses SQLite and permits the Vite origins `http://localhost:5173` and `http://127.0.0.1:5173`.

Apply the database migrations:

```powershell
python -m flask --app backend:create_app db upgrade
```

Start the API in the first terminal. The Flask CLI loads the repository `.env` file:

```powershell
python -m flask --app backend:create_app run --host=127.0.0.1 --port=8000
```

Start React in a second terminal:

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1
```

To process queued report exports during local development, run a worker in a third terminal:

```powershell
python -m flask --app backend:create_app report-worker
```

Open `http://127.0.0.1:5173`. Confirm the API is available at `http://127.0.0.1:8000/api/health`; it should return `{"status":"ok"}`.

Interactive API documentation is available at `http://127.0.0.1:8000/docs`, with the machine-readable OpenAPI document at `http://127.0.0.1:8000/openapi.json`. Protected endpoints use JWT Bearer authentication: log in through the documented auth endpoint, copy the access token, and use Swagger UI's **Authorize** control.

For a local production-style API process, export the values from `.env` into the process environment before starting Waitress. Waitress does not load `.env` automatically:

```powershell
waitress-serve --listen=127.0.0.1:8000 --call backend:create_app
```

## Railway deployment

Railway must contain two services created from the same repository.

### API service

- Root directory: repository root (leave Railway's **Root Directory** setting empty; do not set it to `backend`)
- Railway config: `/railway.json`
- Railpack config: `/railpack.json`
- Health check: `/api/health`
- Start command: `sh scripts/railpack-start.sh`

Required production variables include:

```dotenv
ASSUREX_ENV=production
DATABASE_URL=${{Postgres.DATABASE_URL}}
JWT_SECRET_KEY=<random-secret-at-least-32-bytes>
RATELIMIT_STORAGE_URI=${{Redis.REDIS_URL}}
FRONTEND_ORIGINS=https://${{Frontend.RAILWAY_PUBLIC_DOMAIN}}
FRONTEND_URL=https://${{Frontend.RAILWAY_PUBLIC_DOMAIN}}
```

The start script applies migrations, starts the report worker with ML preloading disabled, and then starts Waitress on Railway's assigned `PORT`. Set `REPORT_WORKER_ENABLED=false` only when a separately supervised worker processes the same report queue and storage. Keep a single API replica while report files use local storage, and mount persistent private storage at `REPORT_STORAGE_PATH` if completed downloads must survive redeploys.

### Frontend service

- Root directory: `/frontend`
- Railway config: `/frontend/railway.json`
- Railpack config: `/frontend/railpack.json`
- Health check: `/`

Set the public API URL at build time:

```dotenv
VITE_API_URL=https://${{API.RAILWAY_PUBLIC_DOMAIN}}/api
```

Generate a public domain for each service. When using custom domains, set `VITE_API_URL` to the final API domain and set both `FRONTEND_URL` and `FRONTEND_ORIGINS` to the canonical frontend HTTPS origin. Wildcard production origins are rejected. See [Railway deployment](documentation/railway-deployment.md) for the complete setup.

## Register or log in

1. Open `/products` in the React application.
2. Select **Create an account** to register, then enter a full name, email address, and password of at least 12 characters.
3. AssureX signs in the new customer after successful registration.
4. Returning users select **Sign in** and enter their email address and password.

Self-service registration always creates a customer. Create the first administrator from the repository root:

```powershell
python -m flask --app backend:create_app create-admin
```

The command securely prompts for the email address, full name, and password. Reviewer accounts are provisioned by an administrator through `POST /api/admin/users`.

## Register a product

1. Sign in and open `/products`.
2. Select **Register product**.
3. Enter the product name, brand, category, model, serial number, purchase date, purchase price, and retailer.
4. Complete the original-warranty section.
5. Check the calculated expiry preview and select **Register product**.

The API rejects future purchase dates, invalid prices, missing required fields, and duplicate product identities. Customers can only access their own products.

## Add warranty information

An original warranty can be created with the product. Enter its duration and unit, and optionally provide its provider, start date, coverage, exclusions, and service-center conditions.

To add or extend coverage later:

1. Open `/products` and select the product.
2. Select **Add warranty** or **Extend warranty**.
3. Enter the provider, start date, duration, coverage, exclusions, and service-center conditions.
4. Check the calculated expiry date and select **Add warranty**.

Warranty periods cannot overlap. AssureX calculates the status as Active, Near Expiry, Expired, Extended Warranty, Not Started, or No Warranty.

## Create a claim

1. Open `/claims` and select **New claim**.
2. Select one of your registered products.
3. Enter the fault date, fault type, damage category, and a description of at least 20 characters.
4. Optionally add repair history and indicate whether the product was previously replaced.
5. Select **Next** to continue through the wizard.

Drafts save automatically. **Save and exit** returns to the claim list, where **Resume draft** continues an unfinished claim.

## Upload documents

Use step 3 of the claim wizard to upload evidence. Choose a document type and then select a file. A complete submission requires:

- Receipt
- Product image
- Serial-number image
- Damage evidence

PDF, JPG, JPEG, and PNG files are accepted. Defaults are 10 MB per file, 20 files per claim, and 50 MB total. These limits are configurable in `.env`.

Each file is checked for its extension, MIME type, file signature, decodability, size, ownership, and SHA-256 content hash before private storage. The same content cannot be added twice to one claim.

## Verify extracted information

After upload, expand **Document analysis** for the document. AssureX shows the OCR status, preview, raw OCR text, extracted fields, field confidence, and values that need attention.

1. Compare each detected value with the original document.
2. Correct any missing or incorrect purchase date, invoice number, product, model, serial number, retailer, price, or warranty duration.
3. Select **Confirm extracted information**.
4. Use **Retry analysis** if processing failed and OCR should be attempted again.

Submission is blocked while OCR is processing or while extracted values remain unconfirmed. A failed OCR result can proceed after review, but the claim is flagged for manual attention.

## Submit a claim

1. Continue to step 4 after all required evidence is present and reviewed.
2. Check the product, incident details, and evidence summary.
3. Select **Submit claim**.

The API rechecks ownership, required fields, document completeness, review state, upload limits, and stored-file integrity. A successful submission receives an ID such as `CLM-2026-000001`. Retrying after a lost response returns the same submitted claim instead of creating a duplicate.

## Generate the Python prediction

An assigned employee, eligible reviewer, or administrator can run the independent Python adapter with
`POST /api/predict/python` and `{ "claim_id": 123 }`. The adapter prepares the model's declared feature frame,
normalizes labels and probabilities, registers the artifact version, and appends an immutable prediction row.

## Interpret Python confidence scores

Stored prediction records use three canonical classes:

- `valid`: evidence favors a valid claim.
- `invalid`: evidence favors an invalid claim.
- `manual_review`: evidence is uncertain or requires a person.

The corresponding fields are `confidence_valid`, `confidence_invalid`, and `confidence_manual_review`; each must be between `0` and `1`. `top_confidence` is the largest score and must belong to `predicted_class`.

A confidence value expresses model preference, not a final claim decision and not a calibrated fraud probability. Reviewers must also consider documents, warranty coverage, duplicate warnings, and rule results.

## Generate the Claim Summary Card

The GTM adapter creates a deterministic 224x224 card containing non-PII claim evidence and stores it under
the private `MODEL_CARD_PATH`. Cards are not exposed as public static assets.

## Obtain the Google Teachable Machine prediction

Run the independent GTM adapter with `POST /api/predict/gtm` and `{ "claim_id": 123 }`. TensorFlowJS model
loading is lazy, and GTM failure does not prevent the Python route or a partial combined evaluation from succeeding.

## Compare both model results

`POST /api/claims/<numeric-claim-id>/evaluate` runs both adapters independently, evaluates policy and evidence,
scores duplicate signals, creates a transparent recommendation, and stores an immutable evaluation snapshot.
Comparison statuses and their centralized thresholds are documented in `documentation/model-evaluation.md`.

## Review warranty-rule results

The database and reviewer API can retain immutable `RuleResult` rows with a rule code, category, result, severity, details, and policy version. For a claim that is currently in manual review and visible to the signed-in reviewer, existing results can be read with:

```http
GET /api/review/<numeric-claim-id>/risk
Authorization: Bearer <access-token>
```

`WarrantyPolicyService` validates `data/warranty_policies.json`. `ClaimRuleEngine` records warranty expiry,
serial verification, contradictions, missing evidence, repair authorization, and excluded-damage results.
A missing or invalid policy forces manual review rather than being treated as valid coverage.

## Check contradictions

Combined evaluation compares registered values with confirmed or extracted document values and repair records.
Date, serial-number, and product-model contradictions are stored as structured findings and rendered in the reviewer case dialog.

## Identify duplicate claims

AssureX detects both exact duplicate documents and evidence-based claim similarity.

1. Every accepted document receives a SHA-256 content hash.
2. Re-uploading the same content to the same claim is rejected, even under a different filename.
3. Matching content on another claim is retained and privately flagged.
4. A reviewer or administrator opens `/dashboard/reviewer` and checks **Duplicate warning queue**.
5. Select **Review**, inspect the related claim, and choose **Confirm duplicate** or **Reject warning**.

Combined evaluation narrows candidate claims in SQL, then scores document hashes, serial and invoice numbers,
claimant, product model, description similarity, and claim dates. Related claim IDs remain reviewer/admin-only.

## Access the manual-review queue

1. Sign in with a reviewer or administrator account at `/dashboard`.
2. Open `/dashboard/reviewer`.
3. Search the **Manual review queue** and select **Open case**.
4. Inspect the claim details and attached evidence.
5. Inspect model comparison, policies, rules, contradictions, duplicate signals, and prior actions.
6. Assign a reviewer, add notes, request evidence, approve, reject, or explicitly override with a reason.

The queue contains claims whose status is `manual_review` and whose `manual_review_required` flag is true. Administrators can see all eligible cases; reviewers see unassigned cases and cases assigned to them.

## Access the administrator dashboard

Create an administrator with the `create-admin` command, then sign in at `/dashboard`. Administrators are routed to `/dashboard/admin`.

The dashboard includes claim volume and outcomes, warranty distribution, model-record confidence, model disagreement, exact duplicate alerts, reviewer workload, fraud-rule aggregates, customer growth, and model-version history. Administrators can also open the reviewer workspace and view a customer dashboard by customer ID.

Metrics based on predictions or rule results remain empty until trusted records exist; the dashboard does not run models or rule engines.

## Track claim status

Customers can track claims from `/claims` or `/dashboard/customer`. The claim list shows drafts and submitted claims, while the dashboard shows recent claims, action items, notifications, and aggregate status counts.

Supported statuses are:

- Draft
- Submitted
- Under Evaluation
- Additional Information Required
- Manual Review
- Approved
- Rejected
- Closed

When more evidence is requested, open the claim from the notification or claim list, upload the requested document, verify its extraction, and wait for the reviewer to resume the case.

## Export a claim report

Open `/reports`, choose a report type and filters, then preview or request a PDF, CSV or Excel export. Completed files appear in your private report history. Customers, employees, reviewers and administrators receive reports limited to their current ownership or assignments.

Apply the database migrations and run a worker alongside the API in local development:

```powershell
python -m flask --app backend:create_app db upgrade
python -m flask --app backend:create_app report-worker
```

On Railway, `scripts/railpack-start.sh` starts that worker automatically and disables ML preloading in the worker process so the model artifacts are not loaded twice. Web and worker processes must share `REPORT_STORAGE_PATH`. See [reporting architecture, API examples and deployment settings](documentation/reports.md). Synthetic [PDF](documentation/examples/reports/sample-customer-report.pdf), [CSV](documentation/examples/reports/sample-customer-report.csv) and [Excel](documentation/examples/reports/sample-customer-report.xlsx) examples are included.

## Run automated tests

Install the test dependencies and ensure the frontend dependencies are present:

```powershell
python -m pip install -r tests/requirements.txt
npm --prefix frontend ci
```

Run the backend and API test suite from the repository root:

```powershell
python -m pytest -q
```

Build the standalone frontend to catch bundling errors:

```powershell
npm --prefix frontend run build
```

Browser tests are opt-in and require Node.js plus Chrome or Edge:

```powershell
$env:ASSUREX_BROWSER_TESTS="1"
python -m pytest tests/test_product_browser.py tests/test_claim_browser.py -q
```

Set `ASSUREX_BROWSER_PATH` when Chrome or Edge is not installed in a location detected by the tests.

## Additional documentation

- [Authentication and role-based access](documentation/authentication.md)
- [Products and warranties](documentation/products.md)
- [Claim submission](documentation/claims.md)
- [Document upload and OCR](documentation/documents.md)
- [Dashboards](documentation/dashboards.md)
- [Database design](documentation/database.md)
- [Railway deployment](documentation/railway-deployment.md)

## Blog Post
https://medium.com/@xavenordu/building-assurex-claim-engine-an-ai-assisted-warranty-claim-management-system-656fce987a0e

## Security notes

Never commit `.env`, production secrets, uploaded documents, tokens, or database credentials. Production requires HTTPS, PostgreSQL, a shared Redis rate-limit store, an explicit HTTPS `FRONTEND_ORIGINS` list, and private document storage. Keep `JWT_SECRET_KEY` unique per environment and at least 32 bytes long.
