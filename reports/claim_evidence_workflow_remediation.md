# Assure-X claim evidence remediation

Base commit: `b4f46c1e785b7f6b3934ded8123f2950170c8a3d`.
Branch: `fix/context-aware-claim-evidence`.

## Confirmed causes

1. `backend/services/document_service.py::process_document` invoked the same OCR
   provider for every uploaded file. `document_extraction.py::extract_fields`
   always ran every purchase extractor and returned the same eight fields. The
   document-type argument only changed one merchant heuristic.
2. `frontend/src/claims/components.jsx::OCRReview` unconditionally rendered its
   eight-field receipt editor for product, serial-label and damage photographs.
3. `claim_schemas.py::REQUIRED_DOCUMENTS`, server validation and wizard navigation
   required a new receipt and serial-label image for every claim, even though the
   selected registered product already supplied purchase and identity records.
4. Successful reprocessing explicitly assigned `verified_data = None`, losing
   the user's earlier confirmations. Failed OCR also replaced extraction data.
   The review API prohibited manual corrections after failed OCR.
5. `claim_rules.py::_document_evidence` passed nested OCR objects into string
   comparisons, causing matching confirmed serials to appear contradictory.
   Several historical tests explicitly pinned that defect.

No upload-triggered whole-form reset was found. The wizard already kept form
values separate from returned claim records and serialized mutations. Product
purchase fields were already persisted, scoped by owner, and returned with claims;
the redundant upload requirements and generic editor caused the visible problem.

## Resulting behavior

- Evidence categories are explicit and validated. Existing lower-case upload types
  remain compatible; category/type disagreements are rejected.
- Receipts alone run purchase OCR. Product photographs use image quality, an
  optional photographic model boundary, and visible-label OCR. Damage photographs
  never run receipt or serial OCR. Serial labels and warranty cards run only
  applicable extractors. Other files are stored privately without purchase OCR.
- Responses expose category, stable evidence identity, processing state, scoped
  fields, warnings, image quality, provider/version and review conflicts.
- Claim requirements reuse registered purchase/identity details. Product and damage
  photographs remain required. Invoice substitutes for receipt where receipt
  evidence is needed. Coverage and ownership remain independent authoritative checks.
- Human confirmations survive successful and failed retries. Supplementary confirmed
  purchase metadata, including invoice numbers and source evidence provenance, is
  persisted with the product. Original registration and other confirmed sources
  are never silently replaced. Corrections to the same source can update its
  supplementary confirmation.
- Scoped editors, multiple-file selection, queued wizard mutations, secure previews,
  retry controls and server draft recovery preserve prior form values and evidence.
  A duplicate-upload error no longer unnecessarily blocks every later draft edit.
- Matching confirmed serial containers compare correctly. Unconfirmed low-confidence
  OCR is excluded from automatic contradiction decisions; reviewed genuine
  mismatches still trigger the existing evaluation rules.

## Database and security

Migration `a35e91b70412` follows the existing search migration head. It adds nullable
`products.purchase_metadata` and `documents.processing_metadata`, and links legacy
claim attachments to their existing product/warranty without replacing existing
links. Product and claim records remain intact. Apply with:

```bash
python -m flask --app backend:create_app db upgrade
```

Existing JWT/role restrictions, owner queries, optimistic claim-version updates,
private storage, file signatures/MIME checks, image decompression limits, PDF active
content rejection, size/count limits, duplicate hashes and idempotent final claim
submission are preserved. Confirmations of a shared product serialize through the
product row lock. Browser uploads are queued; simultaneous independent API writers
must reload and retry a stale 409, preventing attachment loss or stale overwrites.

## Verification

- Focused claim/OCR/evidence/migration suite: 79 passed before the additional
  processing-idempotency regression was added.
- Expanded evidence workflow suite: 14 passed, including actual Tesseract OCR for
  receipt and serial-label images, refresh/recovery, registration reuse, category
  routing, concurrent upload retry and failed/successful retry preservation.
- Rule and scenario suite after correcting defect-pinning assertions: 118 passed.
- Dashboard and notification suite: 21 passed.
- Three frontend component render checks: passed.
- Production frontend build: passed (existing dependency directive warnings only).
- Python compilation, browser-test JavaScript syntax, and git whitespace checks: passed.
- Real warm dual-model benchmark: failed to initialize because `tensorflowjs` is
  absent from this local test environment. This is not reported as verified model
  performance. Real-model API tests are subject to the same runtime limitation.
- Browser workflow: updated, unverified. No local Chromium executable was available;
  the attempted Playwright Chromium download returned invalid/truncated archives.
- No local PostgreSQL/S3 deployment test or external photographic model was exercised.
  Existing private S3 adapter tests use a fake storage client.

Final combined claim/OCR/evidence/migration/dashboard/notification/rule/scenario
run: **219 passed, 4 warnings** in 136.70 seconds. The final configured-confidence
and evidence regression subset was also rerun after using the deployment's OCR
confidence threshold.

The earlier full `pytest -q tests` run completed with **690 passed, 6 skipped,
9 failed**. Five failures asserted obsolete repeat-evidence/false-serial behavior;
those assertions were corrected and their full files passed in the 219-test run.
The other four failures are the real GTM initialization/API/evaluation/accuracy
checks, all traced to `ModuleNotFoundError: tensorflowjs`. The full suite is not
reported as an all-pass run. Browser tests remain opt-in/skipped and unverified.

## Remaining limits

The primary no-repeat-purchase-entry workflow is covered by HTTP integration and
component tests. A browser end-to-end pass remains necessary before release.

No trained product/damage-photo model is shipped. The existing GTM model is a
claim-card classifier; using it to claim damage detection would be misleading.
Without an explicitly configured photographic provider, photos receive quality
measurements and a clear manual-assessment warning, not invented damage labels,
brands, components or severity. Model-dependent acceptance criteria remain unverified.

Warranty cards currently extract duration and visible model/serial labels; free-text
coverage terms, exclusions and warranty start/end interpretation remain manual.
Processing remains synchronous with persisted attachment state, not a newly deployed
background queue. Cross-session edits use optimistic concurrency rather than merging
arbitrary simultaneous changes. Existing registration values remain authoritative;
editing them uses the product-management flow.

API payloads, category mapping, setup commands and local verification steps are in
`documentation/claims.md`.
