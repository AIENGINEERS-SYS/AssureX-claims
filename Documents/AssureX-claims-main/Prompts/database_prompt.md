Act as a senior software engineer and database architect working on the **AssureX Claim Engine** project.

Your task is to **fully implement Phase 2: Design the Database** from the project development roadmap.

Do not only create placeholder models. Inspect the existing repository structure first, understand the current backend stack, conventions, configuration, and coding style, then implement a production-quality database layer that integrates cleanly with the existing project.

Repository context:

* Project: AssureX Claim Engine
* Repository: `AIENGINEERS-SYS/AssureX-claims`
* Phase: Phase 2 — Database Design
* Main requirement: create the core relational data model before page/frontend development begins.

## Primary Objective

Design and implement the database architecture required to support the AssureX warranty claim workflow.

The core entities are:

* users
* products
* warranties
* claims
* documents
* repair_history
* python_predictions
* gtm_predictions
* rule_results
* reviews
* notifications
* audit_logs
* model_versions

The conceptual relationship is:

```text
User
 └── Products
      └── Warranty
           └── Claims
                ├── Documents
                ├── Repairs
                ├── Python Prediction
                ├── GTM Prediction
                ├── Rule Results
                └── Review
```

Each claim must have a unique human-readable Claim ID, and every Python/GTM prediction must reference the exact model version that generated it.

## Step 1 — Inspect the Existing Repository

Before writing code:

1. Inspect the repository structure.
2. Identify:

   * backend framework
   * ORM currently used, if any
   * existing database configuration
   * environment variable conventions
   * existing models or schemas
   * migration tooling
   * test structure
3. Reuse existing patterns where appropriate.
4. Do not introduce duplicate database frameworks or incompatible architectural patterns.

If the repository does not yet contain a database layer, choose a clean implementation appropriate for the existing backend architecture.

Prefer:

* PostgreSQL for production
* SQLAlchemy 2.x if FastAPI/Python is being used
* Alembic for migrations
* SQLite only as an optional development/test fallback if appropriate

## Step 2 — Implement Database Configuration

Create a centralized database configuration layer.

Requirements:

* Load connection details from environment variables.
* Do not hard-code credentials.
* Support clean session management.
* Support dependency injection where appropriate.
* Handle connection lifecycle safely.
* Make configuration testable.
* Include clear development defaults where safe.

Typical variables may include:

```env
DATABASE_URL=
DB_HOST=
DB_PORT=
DB_NAME=
DB_USER=
DB_PASSWORD=
```

Use the repository's existing environment/config system where available.

## Step 3 — Implement Core Tables

### 1. Users

Include fields suitable for later authentication and RBAC.

Suggested fields:

```text
id
user_id
email
password_hash
first_name
last_name
phone
role
is_active
created_at
updated_at
last_login_at
```

Roles should be designed to support:

* customer
* service_center_employee
* reviewer
* administrator

Use a stable enum or equivalent domain constraint where appropriate.

---

### 2. Products

A product belongs to a user.

Suggested fields:

```text
id
product_id
user_id
name
category
brand
model_number
serial_number
purchase_date
purchase_price
retailer
created_at
updated_at
```

Requirements:

* `product_id` must be unique and human-readable.
* Serial numbers should be indexed.
* A user may own multiple products.

---

### 3. Warranties

A warranty belongs to a product.

Suggested fields:

```text
id
warranty_id
product_id
provider
warranty_type
start_date
expiry_date
coverage_duration_months
coverage_conditions
exclusions
extended_warranty
service_center_requirements
created_at
updated_at
```

Design warranty status to be derivable later as:

* active
* near_expiry
* expired
* extended

Do not unnecessarily duplicate calculated values if they can reliably be derived.

---

### 4. Claims

A claim should reference:

* user
* product
* warranty

Suggested fields:

```text
id
claim_id
user_id
product_id
warranty_id
fault_date
fault_type
fault_description
damage_type
submission_date
status
final_decision
manual_review_required
created_at
updated_at
closed_at
```

Requirements:

* `claim_id` must be globally unique.
* Add useful indexes for:

  * claim_id
  * user_id
  * product_id
  * status
  * submission_date
* Design statuses for future workflow:

```text
draft
submitted
under_evaluation
additional_information_required
manual_review
approved
rejected
closed
```

Final decision should later support:

```text
likely_valid
likely_invalid
manual_review_required
```

Do not conflate claim workflow status with ML classification result.

---

### 5. Documents

Documents belong primarily to claims, but design them flexibly enough to support product/warranty evidence where appropriate.

Suggested fields:

```text
id
document_id
claim_id
document_type
original_filename
stored_filename
mime_type
file_size
storage_path
file_hash
ocr_status
ocr_text
uploaded_by
created_at
updated_at
```

Supported document types should account for:

* receipt
* invoice
* warranty_card
* product_image
* serial_number_image
* fault_evidence
* diagnostic_report
* repair_report
* other

Requirements:

* `file_hash` should be indexed for duplicate detection.
* Preserve original filename separately from storage filename.
* Do not store large binary files directly in the database unless the repository already explicitly follows that strategy.

---

### 6. Repair History

Suggested fields:

```text
id
repair_id
product_id
claim_id
repair_date
service_center_name
authorized_service_center
parts_replaced
repair_outcome
repair_cost
notes
created_at
updated_at
```

A product may have many repair records.

A repair may optionally be linked to a claim.

---

### 7. Model Versions

Create a shared model version registry.

Suggested fields:

```text
id
model_version_id
model_type
model_name
version
artifact_path
training_dataset_version
metrics_json
is_active
created_at
retired_at
```

Model type should distinguish at least:

```text
python
gtm
```

Requirements:

* Historical prediction records must never change when a new model version is deployed.
* Predictions must reference the exact model version used at inference time.

---

### 8. Python Predictions

Suggested fields:

```text
id
prediction_id
claim_id
model_version_id
predicted_class
confidence_valid
confidence_invalid
confidence_manual_review
top_confidence
inference_duration_ms
created_at
```

Requirements:

* Foreign key to claim.
* Foreign key to model version.
* Preserve historical predictions.
* Do not overwrite old predictions.

---

### 9. GTM Predictions

Use a structure similar to Python predictions:

```text
id
prediction_id
claim_id
model_version_id
claim_summary_card_path
predicted_class
confidence_valid
confidence_invalid
confidence_manual_review
top_confidence
inference_duration_ms
created_at
```

The GTM result must remain logically independent from the Python model result.

---

### 10. Rule Results

Store warranty rule-engine outcomes separately from ML predictions.

Suggested fields:

```text
id
rule_result_id
claim_id
rule_name
rule_code
rule_category
result
severity
details
policy_version
created_at
```

Example rule categories:

* warranty_expiry
* serial_match
* missing_document
* excluded_damage
* unauthorized_repair
* duplicate_claim
* contradiction
* reporting_deadline

Result could support:

```text
passed
failed
warning
manual_review
```

Do not reduce all rule outcomes into a single boolean.

---

### 11. Reviews

A claim may go through manual review.

Suggested fields:

```text
id
review_id
claim_id
reviewer_user_id
decision
comments
override_applied
previous_decision
reviewed_at
created_at
```

Reviewer decisions should support:

```text
approve
reject
request_information
manual_review_continue
```

Preserve the automated/original result when an override occurs.

---

### 12. Notifications

Suggested fields:

```text
id
notification_id
user_id
claim_id
type
title
message
is_read
read_at
created_at
```

Design notification types for:

* warranty_expiry
* claim_submitted
* missing_document
* additional_information_required
* claim_status_changed
* manual_review
* claim_approved
* claim_rejected

---

### 13. Audit Logs

Create a generic immutable audit log.

Suggested fields:

```text
id
audit_id
user_id
claim_id
action
entity_type
entity_id
old_values
new_values
ip_address
user_agent
created_at
```

Requirements:

* Audit logs should not normally be updated or deleted through ordinary application logic.
* Use JSON-compatible fields for old/new values where supported.
* Record important events later such as:

  * login
  * product registration
  * document upload
  * OCR correction
  * claim submission
  * prediction
  * rule execution
  * review
  * override
  * final decision

## Step 4 — Relationships and Foreign Keys

Implement proper relational constraints.

Expected relationships include:

```text
User 1 → many Products
User 1 → many Claims
User 1 → many Notifications

Product 1 → many Warranties
Product 1 → many Claims
Product 1 → many RepairHistory records

Warranty 1 → many Claims

Claim 1 → many Documents
Claim 1 → many RepairHistory records
Claim 1 → many Python Predictions
Claim 1 → many GTM Predictions
Claim 1 → many Rule Results
Claim 1 → many Reviews
Claim 1 → many Notifications
Claim 1 → many Audit Logs

ModelVersion 1 → many Python Predictions
ModelVersion 1 → many GTM Predictions

Reviewer User 1 → many Reviews
```

Choose delete behavior carefully.

Avoid cascading deletion where it could destroy:

* historical predictions
* reviews
* audit records
* regulatory or evidentiary records

Prefer archival/soft-delete strategies for important historical entities if consistent with the project architecture.

## Step 5 — IDs and Identifiers

Separate internal database IDs from public identifiers.

Use:

* integer or UUID primary keys internally
* human-readable IDs externally where required

Examples:

```text
USR-000001
PRD-000001
WAR-000001
CLM-000001
DOC-000001
RPR-000001
REV-000001
```

Implement ID generation centrally.

Requirements:

* collision-safe
* testable
* transaction-safe
* not based purely on counting existing records in an unsafe way

For Claim ID specifically, uniqueness must be enforced at the database level.

## Step 6 — Constraints and Validation

Add database constraints where they protect data integrity.

Examples:

* unique email
* unique claim_id
* unique product_id
* unique model-version identifiers where appropriate
* purchase dates cannot be structurally invalid
* confidence scores must remain within valid bounds
* nonnegative file sizes
* nonnegative repair costs
* required foreign keys
* appropriate enum constraints

Do not put all data-integrity responsibility in frontend validation.

## Step 7 — Indexing

Create useful indexes for expected query patterns.

At minimum inspect and consider indexing:

```text
users.email
products.product_id
products.serial_number
claims.claim_id
claims.user_id
claims.product_id
claims.status
claims.submission_date
documents.file_hash
documents.claim_id
repair_history.product_id
python_predictions.claim_id
gtm_predictions.claim_id
rule_results.claim_id
reviews.claim_id
notifications.user_id
notifications.is_read
audit_logs.user_id
audit_logs.claim_id
audit_logs.created_at
```

Avoid excessive unnecessary indexes.

## Step 8 — Migration Setup

Use the project's migration framework.

If Alembic is appropriate:

1. Configure Alembic.
2. Make ORM metadata discoverable.
3. Generate an initial migration containing all Phase 2 tables.
4. Review the generated migration manually.
5. Ensure upgrade and downgrade paths work.

Do not rely on `create_all()` as the production migration strategy.

## Step 9 — Seed Data

Create minimal development seed data for:

* one admin
* one customer
* sample product
* sample warranty
* sample claim
* at least one Python model version
* at least one GTM model version

Do not commit plaintext production passwords.

Use obviously development-only credentials where unavoidable and document them clearly.

## Step 10 — Pydantic / Validation Schemas

If the backend uses FastAPI/Pydantic, create clean schemas for:

* create
* update
* read/response

Do not expose:

* password hashes
* internal security fields
* sensitive database implementation details

Use proper date/datetime/decimal types.

## Step 11 — Repository / Service Layer

If consistent with the repository architecture, separate:

```text
API
↓
Service
↓
Repository/Data Access
↓
Database
```

Avoid business logic directly inside ORM models.

The database layer should be usable later by:

* authentication
* product registration
* warranty management
* claim submission
* OCR processing
* Python model inference
* GTM inference
* rule engine
* review workflows
* notifications
* dashboards

## Step 12 — Testing

Add automated tests covering at least:

### Database creation

* tables initialize correctly
* migrations apply

### Relationships

* user → product
* product → warranty
* warranty → claim
* claim → prediction/review/document/rules

### Constraints

* duplicate Claim IDs rejected
* duplicate user emails rejected
* invalid foreign keys rejected
* confidence bounds enforced where implemented

### Model version tracking

Verify that:

```text
Prediction A → Python model v1
Prediction B → Python model v2
```

remain historically distinguishable.

### Deletion behavior

Verify important historical records are not accidentally cascaded away.

### ID generation

Verify unique public IDs.

Use an isolated test database.

## Step 13 — Documentation

Update repository documentation with:

### Database architecture

Include an ER-style relationship description.

### Environment variables

Document required DB variables.

### Migrations

Provide commands such as:

```bash
alembic upgrade head
alembic revision --autogenerate -m "description"
```

Adapt commands to the actual repository.

### Local initialization

Document how a new developer can create and initialize the database.

## Step 14 — Implementation Quality Requirements

Follow these rules:

* Use type hints.
* Use clear names.
* Avoid giant modules.
* Avoid duplicated model definitions.
* Avoid hard-coded secrets.
* Avoid hard-coded warranty business rules in database models.
* Use transactions appropriately.
* Use UTC timestamps where appropriate.
* Keep timestamps consistent.
* Follow the repository's linting/formatting conventions.
* Preserve compatibility with future phases.

Do not unnecessarily refactor unrelated working code.

## Step 15 — Verify the Implementation

Before finishing:

1. Run migrations from an empty database.
2. Run all database tests.
3. Run the existing project tests.
4. Check imports and application startup.
5. Confirm all tables exist.
6. Confirm foreign-key relationships.
7. Confirm unique Claim ID enforcement.
8. Confirm Python/GTM predictions reference model versions.
9. Confirm historical predictions remain immutable/preserved.
10. Confirm migration downgrade works where practical.

Fix any failures introduced by the implementation.

## Expected Deliverables

At completion, the repository should contain a robust Phase 2 implementation including:

* database configuration
* ORM/base configuration
* all required database models
* relationships
* enums/constants
* indexes
* constraints
* model-version tracking
* public-ID generation
* migration configuration
* initial migration
* development seed script
* validation schemas where applicable
* automated database tests
* updated README/database documentation

## Final Response Format

After implementation, provide a concise engineering report containing:

### Files Created

List new files.

### Files Modified

List modified files.

### Database Tables

List every implemented table.

### Key Relationships

Summarize important foreign keys and cardinality.

### Migrations

State what migration was created and how to apply it.

### Tests

Report:

* tests run
* tests passed
* tests failed

### Design Decisions

Explain any significant decisions, especially:

* primary-key strategy
* public Claim ID generation
* delete/cascade policy
* JSON fields
* model-version tracking
* indexes
* PostgreSQL vs development/test database behavior

### Issues / Follow-up

List anything that should be handled in later phases.

Do not claim anything is working unless you actually inspected, implemented, and tested it.

