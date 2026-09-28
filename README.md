# AssureX Claims

AssureX is a warranty and insurance claims platform for registering products, maintaining warranty information, submitting evidence-backed claims, and routing claims through automated evaluation and manual review.

This guide describes the workflows that are available in the repository today. It also calls out the two requested capabilities that are not yet implemented as end-user features: **Claim Summary Card generation** and **claim-report export**.

## Prerequisites

- Python 3.11+
- Node.js 20+ and npm
- PostgreSQL for production (SQLite is the default local configuration)
- Tesseract OCR for local image/scanned-PDF OCR; see [document processing notes](documentation/documents.md)

## Install and run

Clone the repository and create local configuration:

```bash
git clone <repository-url>
cd assurex
cp config/.env.example .env
```

Set a strong, unique `JWT_SECRET_KEY` in `.env`. For production, also set `DATABASE_URL` to PostgreSQL and configure a non-public document storage location.

Install the backend and frontend dependencies, then apply the database migrations:

```bash
python -m pip install -r requirements.txt
npm --prefix frontend ci
python -m flask --app backend:create_app db upgrade
```

Create an administrator when needed:

```bash
python -m flask --app backend:create_app create-admin
```

Start the backend and build or run the frontend using the project’s normal frontend command:

```bash
python -m flask --app backend:create_app run
npm --prefix frontend run build
```

The React application uses the configured API base URL. During development, use its existing development-server configuration if present in your local frontend setup.

## Register or log in

Use the registration or login screens in the application. New self-service registrations are customer accounts.

API clients may register with `POST /api/auth/register`:

```json
{
  "full_name": "Ada Okafor",
  "email": "ada@example.com",
  "password": "a-long-unique-password"
}
```

Log in with `POST /api/auth/login` using the email and password. Save the returned access token and send it with protected requests:

```http
Authorization: Bearer <access-token>
```

Log out with `POST /api/auth/logout`. The backend revokes the current JWT, so it cannot be reused.

## Register a product

1. Sign in as a customer and open **My products** or `/products`.
2. Select **Register product**.
3. Enter the product name, brand, category, model, serial number, purchase date, price, retailer, and warranty duration/unit.
4. Save the form. Purchase dates in the future, negative prices, missing required fields, and duplicate serial numbers are rejected by the backend.

The product API is available at `POST /api/products`. Customers can only view and change their own products.

## Add warranty information

The initial warranty is supplied during product registration. Its expiry is calculated from the start date and duration; do not manually calculate the expiry date.

To add an extended warranty:

1. Open the product details page.
2. Select **Add extended warranty**.
3. Provide provider, start date, duration, coverage, exclusions, and service-center conditions.
4. Save it and verify the resulting warranty timeline.

The product warranty endpoints follow the product resource, including `POST /api/products/<product-id>/warranties`. Warranty status is calculated server-side as Active, Near Expiry, Expired, or Extended Warranty.

## Create a claim

1. Open **Claims** or `/claims` and select **New claim**.
2. Select one of your active registered products. The backend verifies ownership and prevents claims for another customer’s product.
3. Enter the fault date, fault type, damage category, and a 20–2000 character description. Optionally add repair history and previous-replacement details.
4. Use **Save draft** to leave and resume later, or continue to the evidence step.

The wizard creates and updates drafts through `POST /api/claims/draft` and `PUT /api/claims/draft/<id>`. Drafts can be edited before final submission.

## Upload documents

In the evidence step, choose the document type and drag files into the uploader or use the file picker. The usual required categories are receipt, product image, serial-number image, and damage evidence; the applicable warranty policy can require additional documents.

Supported formats are PDF, JPG, JPEG, and PNG. The default limits are 10 MB per document, 20 documents per claim, and 50 MB total per claim. AssureX validates extension, MIME type, file signature, decodability, file size, content hash, duplicate files, and ownership before storage.

The claim wizard uses `POST /api/claims/upload`; customers responding to a reviewer request use `POST /api/claims/<claim-id>/documents`. A duplicate file within the same claim is rejected. A matching file on another claim is retained and flagged for authorized reviewer investigation.

## Verify extracted information

After a supported document is uploaded, wait for its OCR state to finish. Open the document’s extraction review and compare the detected purchase date, invoice number, product, model, serial number, retailer, price, and warranty duration against the source document.

1. Correct any incorrect or missing values.
2. Save the corrections and explicitly confirm the extraction.
3. Continue only after reviewing information that requires attention.

API clients can read OCR output with `GET /api/documents/<document-id>/ocr` and submit corrections with `PATCH /api/documents/<document-id>/ocr-review`. The original OCR result is preserved separately from user-confirmed values for auditability; OCR never overwrites product or claim data automatically.

## Submit and track a claim

Review the product, claim details, and uploaded evidence, then select **Submit claim**. The backend verifies ownership, required fields, document completeness, and file validation before creating a submitted claim and its human-readable claim ID.

Track progress from `/claims` or `/dashboard/customer`, or with `GET /api/claims/my`. Claim statuses include Draft, Submitted, Under Evaluation, Additional Information Requested, Manual Review, Approved, Rejected, and Closed. Notifications and required actions are visible from the customer dashboard.

## Generate a Python prediction

The bundled Python model can evaluate a submitted claim independently. Send the claim ID to:

```bash
curl -X POST http://127.0.0.1:5000/api/predict/python \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{"claim_id": 123}'
```

The response contains a display prediction, canonical class, per-class confidences, top confidence, and model metadata. Customers can run this only for their own claims; reviewers and administrators have role-appropriate access.

### Interpret Python confidence scores

`confidence` is a probability distribution across the canonical classes `valid`, `invalid`, and `manual_review`; the values should total approximately 1.0. `top_confidence` is the largest value and supports the displayed prediction. A high score means the model prefers that class, not that the claim is automatically approved or rejected. Warranty rules, contradictions, missing evidence, duplicate signals, and reviewer decisions remain authoritative.

## Claim Summary Card status

**Claim Summary Card generation is not implemented in this repository as a user-facing API or UI.** The GTM integration can retain a `GTM_SUMMARY_CARD_PATH` supplied by an external provider, but AssureX does not generate or render a summary card itself. Do not treat this metadata field as an export or generated report.

## Obtain a Google Teachable Machine prediction

The GTM endpoint is present at `POST /api/predict/gtm` and uses the same body as the Python endpoint. It deliberately operates independently from the Python model.

Before it can produce a result, configure an installed provider in `.env`:

```dotenv
GTM_MODEL_PROVIDER=your_package.gtm:predict
GTM_MODEL_VERSION=your-model-version
```

The configured callable receives normalized claim features and must return a canonical `prediction_class` plus a valid confidence distribution for `valid`, `invalid`, and `manual_review`. If no provider is configured, the endpoint returns `503` with `MODEL_UNAVAILABLE`; it does not invent a prediction. Restart the backend after changing configuration.

## Compare both model results

Trigger a full evaluation after the Python and GTM providers are available:

```bash
curl -X POST http://127.0.0.1:5000/api/claims/123/evaluate \
  -H "Authorization: Bearer <access-token>"
```

Read the stored result with `GET /api/claims/123/decision`. The comparison reports both classes, each top confidence, the absolute confidence difference, and a status:

- **Strong Match** — same class, sufficiently confident, and a very small gap.
- **Acceptable Match** — same class with a moderate gap.
- **Weak Match** — same class but weaker confidence or a noticeable gap.
- **Model Disagreement** — the predicted classes differ.
- **Uncertain Result** — either model is insufficiently confident or unavailable.

The thresholds are centrally configured through `MODEL_COMPARISON_MIN_CONFIDENCE` (default `0.55`), `MODEL_COMPARISON_STRONG_GAP` (default `0.08`), and `MODEL_COMPARISON_ACCEPTABLE_GAP` (default `0.20`). A failed or unconfigured GTM model preserves the working Python result and routes the automated outcome toward manual review instead of fabricating a comparison.

## Review warranty-rule results

The combined evaluation loads the warranty policy for the product category and returns structured checks for warranty expiry, serial-number verification, required/missing documents, repair authorization, and excluded damage. Policies live in `policies/warranty_policies.json` and are configured for electronics, appliances, and mobile devices.

Open the claim decision to review each check’s pass/warning/failure state, severity, evidence, and message. A missing or invalid policy is treated safely and is not considered a valid warranty.

## Check contradictions

The same decision response includes structured contradiction findings. Typical checks include purchase date after claim date, repair date before purchase, fault date after claim date, serial-number conflicts, and product-model conflicts.

Each finding lists its severity, source fields, and conflicting values. Missing data is not automatically labelled a contradiction. High-severity contradictions route the claim to manual review rather than silently discarding evidence.

## Identify duplicate claims

Every uploaded document is hashed with SHA-256. Exact matches are checked within the current claim and across claims. Cross-claim matches are retained as evidence and exposed only to authorized reviewers and administrators.

The combined evaluation also produces a claim duplicate-risk result from normalized signals such as serial number, invoice number, product/model, claimant, claim date, description similarity, and document hashes. Review the score, risk level, related claim IDs, and contributing signals in the reviewer view; customers do not receive another claimant’s private details.

## Access the manual-review queue

Sign in as a reviewer or administrator and open `/dashboard/reviewer`. The queue lists manual-review claims with recommendation, model-comparison status, duplicate risk, contradiction count, documents, and priority.

Reviewer APIs include:

- `GET /api/reviewer/queue`
- `GET /api/reviewer/claims/<claim-id>`
- `GET /api/reviewer/claims/<claim-id>/audit-history`
- `POST /api/review/<claim-id>/approve`
- `POST /api/review/<claim-id>/reject`
- `POST /api/review/<claim-id>/notes`
- `POST /api/review/<claim-id>/override`

Use the claim detail view to inspect original machine results, documents, OCR values, rule results, contradictions, duplicate warnings, and the automated explanation. Approvals, rejections, information requests, comments, and overrides are written to append-oriented review history. Overrides require a reason and never overwrite the original model outputs.

## Access the administrator dashboard

Administrators can open `/dashboard/admin` for system-wide claim, warranty, fraud, reviewer-workload, and model-monitoring metrics. The dashboard APIs are JWT-protected and role-scoped; customers cannot access reviewer or administrator data, and reviewers cannot access administrator-only data.

Customer, reviewer, and administrator dashboard data is available through the `/api/dashboard/*` endpoints. See [dashboard documentation](documentation/dashboards.md) for the available responses and widgets.

## Export a claim report status

**Claim-report export is not implemented in the current repository.** There is no supported PDF/CSV export endpoint or frontend export control. Authorized users can read claim and decision data through the normal API, but that is not a substitute for a production report export. Add a dedicated, access-controlled export feature before relying on this workflow.

## Run automated tests

Apply migrations first, then run the backend suite from the repository root:

```bash
python -m pytest -q
```

Build the frontend to catch TypeScript/bundling regressions:

```bash
npm --prefix frontend run build
```

Run targeted evaluation tests when changing prediction, rules, duplicates, decisions, or reviewer workflows:

```bash
python -m pytest tests/test_evaluation.py -q
```

## Further documentation

- [Authentication and RBAC](documentation/authentication.md)
- [Products and warranties](documentation/products.md)
- [Claim submission](documentation/claims.md)
- [Document upload and OCR](documentation/documents.md)
- [Prediction, rules, decisions, and manual review](documentation/evaluation.md)
- [Dashboards](documentation/dashboards.md)

## Security notes

Keep `.env` out of version control, use a unique production JWT secret, serve the application over HTTPS, and restrict document storage to non-public paths. The backend enforces JWT authentication, RBAC, ownership checks, ORM-backed database access, secure document validation, and token revocation. Rate limiting should be enabled at the deployment edge or application layer for authentication, upload, prediction, and dashboard routes.
