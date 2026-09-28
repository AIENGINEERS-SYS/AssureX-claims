# Phase 6: Secure document upload and OCR

Phase 6 extends the existing `Document` entity, Phase 5 claim wizard and local/S3 storage adapters. It does not create a second evidence system. Customers upload and review evidence inside `/claims`; assigned employees may continue adding supporting evidence through the existing staff route.

## Processing flow

```text
JWT/RBAC → request limit → extension → declared MIME → magic signature → decode/parse
         → SHA-256 → duplicate check → private storage → OCR/native PDF text
         → field extraction → user review → preserved confirmation
```

Uploads and OCR are separate committed phases. The current application invokes OCR synchronously, but `process_document()` is an isolated service boundary that a future worker can call without changing response shapes or persistence fields. An OCR failure does not remove valid evidence or automatically reject a claim; it is stored as `failed` and marks the submitted claim for downstream manual handling.

## Supported evidence and limits

Allowed formats are PDF, JPG/JPEG and PNG. Supported categories are `receipt`, `invoice`, `warranty_card`, `product_image`, `serial_number_image`, `damage_evidence`/`fault_evidence`, `diagnostic_report`, `repair_report`, and `other`.

Defaults are configurable in `.env`:

```dotenv
DOCUMENT_STORAGE_BACKEND=local
DOCUMENT_STORAGE_PATH=./instance/uploads
MAX_DOCUMENT_SIZE_MB=10
MAX_DOCUMENTS_PER_CLAIM=20
MAX_CLAIM_UPLOAD_SIZE_MB=50
OCR_PROVIDER=tesseract
OCR_HIGH_CONFIDENCE_THRESHOLD=0.85
OCR_REVIEW_THRESHOLD=0.65
OCR_MAX_PDF_PAGES=20
OCR_TIMEOUT_SECONDS=30
OCR_PREPROCESS_IMAGES=true
```

`DOCUMENT_STORAGE_BACKEND=s3` reuses the private S3-compatible adapter documented in `claims.md`; set `S3_BUCKET` and optionally `S3_ENDPOINT_URL`. AWS credentials come from the normal SDK provider chain and are never stored in the repository. Legacy `CLAIM_STORAGE` and `UPLOAD_FOLDER` variables remain accepted during migration.

Each request contains one document. Flask applies an overall request cap with multipart allowance, then the service enforces the exact decoded file limit, document count and aggregate claim size. Limits are exposed in the claim response so the React client does not need a separate policy constant.

## Validation and storage security

The server never trusts the browser filename or `Content-Type`. It checks the final extension, declared MIME, PDF/JPEG/PNG signature, and then parses the complete bounded file. Pillow verifies and decodes images, rejects zero-sized or over-25-megapixel images and treats decompression warnings as errors. pypdf rejects malformed, encrypted, empty/oversized, active-script/action, XFA and embedded-file PDFs.

Original names are sanitized for display only. Random UUID names are stored below `documents/{internal_claim_id}/`; path resolution is confined to the configured private root. Objects are served only through authenticated API routes with attachment disposition, CSP, `nosniff`, and ownership/staff checks. OCR text is treated as untrusted text and React never renders it as HTML.

SHA-256 is indexed. A `(claim_id, file_hash)` unique constraint closes concurrent same-claim duplicate races. Exact same-claim content is rejected with `duplicate_document`, regardless of filename. Matching content on another claim is preserved and internally flagged rather than disclosed to customers; later fraud services can query the hash index.

## OCR and extraction architecture

- `claim_storage.py`: bounded validation and private local/S3 objects.
- `ocr_service.py`: `OCRProvider` contract, native PDF extraction, scanned-page rendering and Tesseract.
- `document_extraction.py`: normalization, keyword proximity, validated patterns and confidence.
- `document_service.py`: OCR status transitions, serialization, completeness and review preservation.
- `api/documents.py`: authorized metadata/content/OCR/review/retry/delete operations.

Text PDFs use PyMuPDF text extraction first. Only pages without reliable embedded text are rendered and sent through Tesseract. Images are orientation-corrected; optional conservative grayscale/contrast/denoise preprocessing is applied. Stored provenance includes provider/version, raw text, structured values, confidence, processing timestamp/duration, status and safe error text.

Extraction covers purchase date, invoice/receipt number, product, model, serial, retailer, NGN purchase price and warranty duration. Nigerian values such as `₦450,000`, `NGN 450,000`, `N450,000`, `26/09/2026`, `Sep 26 2026` and `26 September 2026` are supported. Ambiguous numeric dates such as `01/02/2026` are returned as ambiguous candidates with no chosen value.

## Install OCR dependencies

Python packages are installed by `pip install -r requirements.txt`.

Tesseract is also a system dependency:

- Ubuntu/Debian: `sudo apt-get update && sudo apt-get install -y tesseract-ocr`
- Amazon Linux: enable the appropriate EPEL/package source, then install `tesseract` and verify with `tesseract --version`.
- Windows: install the maintained UB Mannheim Tesseract build, add its installation directory to `PATH`, restart the terminal, and run `tesseract --version`.

Set `OCR_PROVIDER=disabled` only when OCR is intentionally unavailable. Valid uploads remain usable and record a controlled failed OCR result for manual review. Production health checks should verify the Tesseract executable, PostgreSQL, Redis and the chosen object store.

## API

All routes require a Bearer access token. Customer mutations derive ownership from the JWT/database relationship; supplied user IDs are never accepted.

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/claims/{internal_claim_id}/documents` | Upload one customer/staff document; multipart `version`, `document_type`, `file` for customers |
| POST | `/api/claims/upload` | Backward-compatible Phase 5 customer upload |
| GET | `/api/claims/{id_or_claim_id}/documents` | List accessible document metadata |
| GET | `/api/documents/{document_id}` | Get accessible metadata and extraction result |
| GET | `/api/documents/{document_id}/content` | Authenticated attachment/preview content |
| GET | `/api/documents/{document_id}/ocr` | Raw OCR, structured extraction and review data |
| PATCH | `/api/documents/{document_id}/ocr-review` | Confirm/correct extraction using the current claim `version` |
| POST | `/api/documents/{document_id}/ocr/retry` | Retry failed/warning OCR using the current claim `version` |
| DELETE | `/api/documents/{document_id}` | Delete owned draft evidence using the current claim `version` |

Correction example:

```json
{
  "version": 6,
  "confirm": true,
  "purchase_date": "2026-01-17",
  "invoice_number": "INV-83927",
  "serial_number": "SN78299104",
  "purchase_price": "425000.00",
  "warranty_duration": 24,
  "warranty_duration_unit": "months"
}
```

The response retains `extracted_data.serial_number.value` and separately records `verified_data.serial_number` with `ocr_value`, `confirmed_value`, `was_corrected`, confidence, actor and time. Confirmation never overwrites product, warranty or claim input.

Representative errors use stable codes: `unsupported_file_type`, `mime_mismatch`, `file_too_large`, `claim_upload_too_large`, `corrupt_file`, `encrypted_pdf`, `duplicate_document`, `document_limit_reached`, `storage_failure`, and ordinary JWT/404 ownership responses. Technical exceptions are logged without tokens, document contents or raw OCR.

## Submission, completeness and future consumers

Claim responses include `document_completeness` (`required`, `uploaded`, `missing`, `completeness_score`) and `document_policy`. The completeness helper accepts a requirement list; policy-specific requirements are not embedded in the OCR engine.

Submission blocks missing mandatory categories, in-progress OCR and detected values that have not been reviewed. Low confidence does not block after confirmation. Failed OCR does not block a valid claim, but sets `manual_review_required`. Per-document original/confirmed serial, model, purchase date and invoice values remain available for contradiction rules; indexed cross-claim hashes support duplicate/fraud analysis.

## Migration and verification

Revision `b761a04c9f2e` follows Phase 5. It adds OCR provenance/status/error/confidence, processing timing, review actor/time/status, cross-claim signal, constraints and same-claim hash uniqueness. Existing evidence is preserved and marked `review_status=not_required`. Upgrade stops before schema changes if legacy same-claim duplicate hashes require deliberate resolution. Downgrade removes only Phase 6 fields and constraints.

Run:

```bash
python -m flask --app backend:create_app db upgrade
python -m pytest -q
npm --prefix frontend ci
npm --prefix frontend run build
```

Deterministic tests use a provider fixture rather than depending on OCR wording from an OS package. Native text-PDF and scanned-page routing are tested separately. A deployment acceptance test should additionally use its installed Tesseract language packs and representative Nigerian receipts.
