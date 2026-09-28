# AssureX Claim Engine — Detailed Development Roadmap

## Phase 1: Project Setup and Architecture

Define the system structure and responsibilities.

### Recommended Stack
- Frontend: React
- Backend: FastAPI
- Database: PostgreSQL
- Machine Learning: Scikit-learn / XGBoost
- Document Processing: OpenCV + OCR
- Secondary Model: Google Teachable Machine image classifier
- Testing: pytest
- Deployment: Render, Railway, or similar

### Suggested Repository Structure

```text
assurex/
├── frontend/
├── backend/
├── data/
├── dataset_generator/
├── notebooks/
├── models/
├── gtm_model/
├── policies/
├── document_processing/
├── tests/
├── reports/
├── screenshots/
├── documentation/
├── config/
├── README.md
├── AI_USAGE.md
└── requirements.txt
```

---

## Phase 2: Design the Database

Create the data model before building pages.

### Core Tables
- users
- products
- warranties
- claims
- documents
- repair_history
- python_predictions
- gtm_predictions
- rule_results
- reviews
- notifications
- audit_logs
- model_versions

### Important Relationships

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

Each claim should have a unique Claim ID, and each prediction should be linked to the model version used.

---

## Phase 3: Implement Authentication and Roles

Build:
- Registration
- Login/logout
- Password hashing
- Authentication tokens or sessions
- Role-based access control

### Roles
- Customer
- Service-center employee
- Claim reviewer
- Administrator

Example access:

```text
Customer → create/view own claims
Reviewer → inspect manual-review claims
Admin → policies, models, analytics, users
```

---

## Phase 4: Product and Warranty Management

Implement product registration with:
- Product name
- Brand
- Category
- Model
- Serial number
- Purchase date
- Price
- Retailer
- Warranty duration

Implement warranty records:
- Start date
- Expiry date
- Provider
- Coverage
- Exclusions
- Extended warranty
- Service-center conditions

Automatically calculate:

```text
product_age
warranty_remaining
warranty_status
```

Possible statuses:

```text
Active
Near Expiry
Expired
Extended Warranty
```

---

## Phase 5: Claim Submission Workflow

Build a multi-step claim form.

### Step 1
Select a registered product.

### Step 2
Enter:
- Fault date
- Fault type
- Description
- Damage category
- Repair history
- Previous replacement
- Submission date

### Step 3
Upload:
- Receipt
- Warranty card
- Product image
- Serial-number image
- Damage evidence
- Diagnostic report
- Repair report

### Step 4
Validate information before submission.

Each submitted claim receives a unique Claim ID.

---

## Phase 6: Document Upload and OCR

Support:
- PDF
- JPG
- JPEG
- PNG

Validate:
- Extension
- MIME type
- Size
- Corruption
- Duplicate files

Use OCR to extract:
- Purchase date
- Invoice number
- Product name
- Model number
- Serial number
- Retailer
- Purchase price
- Warranty duration

Allow users to review and correct extracted information before submission.

---

## Phase 7: Build the Claim Dataset

Create at least **1,500 unique claim records**:

```text
500 Valid
500 Invalid
500 Manual Review
```

Include:
- Expired warranties
- Missing receipts
- Covered damage
- Excluded damage
- Serial mismatch
- Unauthorized repair
- Duplicate claim
- Contradictory dates
- Incomplete evidence
- Borderline expiry dates

---

## Phase 8: Split the Dataset Correctly

Perform the split before generating images.

```text
Training    70% = 1,050
Validation  15% = 225
Testing     15% = 225
```

Never allow the same claim to appear in more than one split.

This applies to both:
- CSV/tabular representation
- Claim Summary Card representation

---

## Phase 9: Data Preprocessing

Create a reusable preprocessing pipeline.

Handle:
- Missing values
- Invalid dates
- Categorical values
- Numerical normalization
- Feature engineering

Useful engineered features:

```text
product_age_days
days_until_expiry
days_between_fault_and_claim
repair_count
unauthorized_repair_count
missing_document_count
serial_match
receipt_available
warranty_active
previous_claim_count
```

---

## Phase 10: Train the Python Models

Train at least three classifiers.

Suggested starting set:

```text
Logistic Regression
Random Forest
XGBoost
```

Other options:
- Gradient Boosting
- SVM
- CatBoost

Evaluate using:
- Accuracy
- Precision
- Recall
- F1-score
- Confusion matrix
- Class-wise performance
- Cross-validation

Save:
- claim_model.pkl
- preprocessor.pkl
- label_encoder.pkl
- model_metadata.json

---

## Phase 11: Generate Claim Summary Cards

Convert each claim into a standardized image.

Example:

```text
CLAIM #CLM-1024

Product Age:          14 months
Warranty Remaining:   10 months
Fault:                Power Failure
Receipt:              Available
Serial Match:         Yes
Previous Repairs:     1
Missing Documents:    0
Unauthorized Repair:  No
```

Do not include:
- Python prediction
- Python confidence
- Final claim decision

---

## Phase 12: Generate GTM Training Images

For every training claim, create at least two visual variations.

```text
1,050 × 2 = minimum 2,100 images
```

Variations may change:
- Font size
- Spacing
- Card layout
- Background
- Date formatting
- Minor image quality

Do not change the claim meaning or label.

---

## Phase 13: Train Google Teachable Machine

Create three GTM classes:

```text
Valid Claim
Invalid Claim
Manual Review
```

Train only on training images.

Export:
- GTM model
- Labels
- Metadata

Integrate the exported model into the application.

---

## Phase 14: Build Prediction APIs

Create separate prediction services:

```text
POST /predict/python
POST /predict/gtm
```

Example response:

```json
{
  "prediction": "Manual Review",
  "confidence": {
    "valid": 0.18,
    "invalid": 0.24,
    "manual_review": 0.58
  }
}
```

The models must operate independently.

---

## Phase 15: Build the Model Comparison Engine

Compare:
- Predicted classes
- Confidence values
- Top-class confidence difference

Formula:

```text
confidence_difference =
abs(
    python_top_confidence
    -
    gtm_top_confidence
)
```

Assign a consistency status:

```text
Strong Match
Acceptable Match
Weak Match
Model Disagreement
Uncertain Result
```

---

## Phase 16: Build the Warranty Rule Engine

Store warranty rules in JSON, YAML, CSV, or the database rather than scattering rules throughout Python code.

Example:

```yaml
electronics:
  warranty_months: 24

  required_documents:
    - receipt
    - serial_photo

  exclusions:
    - water_damage
    - intentional_damage

  authorized_repair_required: true
```

Create at least three configurable warranty policies.

---

## Phase 17: Implement Rule Checks

### Warranty Expiry

```text
claim_date <= warranty_expiry
```

### Serial Number Verification
Compare serial numbers from:
- Entered data
- Receipt
- Warranty card
- Product image
- Repair records

### Contradiction Detection

Detect examples such as:

```text
purchase_date > claim_date
repair_date < purchase_date
fault_date > claim_date
different serial numbers
different product models
```

### Missing Documents
Check mandatory evidence.

### Unauthorized Repairs
Check repair-center authorization.

### Excluded Damage
Compare damage against policy exclusions.

---

## Phase 18: Duplicate Detection

### Document Duplicate
Hash uploaded documents:

```text
SHA256(file)
```

Compare hashes against existing files.

### Claim Duplicate
Compare:
- Serial numbers
- Invoice numbers
- Claimant
- Product
- Fault description
- Dates
- Document hashes

---

## Phase 19: Build the Final Decision Engine

Combine:

```text
Python prediction
+
GTM prediction
+
Confidence comparison
+
Warranty rules
+
Missing documents
+
Contradictions
+
Duplicate indicators
```

Final outputs:

```text
Likely Valid
Likely Invalid
Manual Review Required
```

Keep the decision logic transparent and explainable.

---

## Phase 20: Build Decision Explanations

Example:

```text
Decision: Manual Review Required

Supporting evidence:
✓ Warranty active
✓ Receipt available
✓ Fault is normally covered

Problems:
✗ GTM disagrees with Python
✗ Product serial differs from receipt
✗ Previous unauthorized repair detected

Required action:
Reviewer verification required.
```

---

## Phase 21: Manual Review System

Build a reviewer queue.

Reviewers should see:
- Documents
- Original claim information
- Python prediction
- GTM prediction
- Confidence values
- Rules passed/failed
- Contradictions
- Duplicate warnings

Reviewer actions:
- Approve
- Reject
- Request More Information
- Override Automated Recommendation
- Add Comment

Never overwrite the original machine results.

---

## Phase 22: Claim Status Workflow

Implement:

```text
Draft
↓
Submitted
↓
Under Evaluation
↓
Additional Information Required
↓
Manual Review
↓
Approved / Rejected
↓
Closed
```

---

## Phase 23: Build Dashboards

### Customer Dashboard
Show:
- Registered products
- Active warranties
- Expiring warranties
- Claims
- Statuses
- Missing actions
- Notifications

### Reviewer Dashboard
Show:
- Manual-review queue
- Model disagreement claims
- Missing-document cases
- Duplicate warnings

### Admin Dashboard
Show:
- Total claims
- Valid/invalid/manual-review counts
- Model disagreement rate
- Average confidence
- Duplicate alerts
- Trends

---

## Phase 24: Notifications

Implement notifications for:
- Warranty nearing expiry
- Missing documents
- Claim submitted
- Additional information requested
- Claim moved to review
- Approved claim
- Rejected claim

---

## Phase 25: Search and Filtering

Support filters for:
- Claim ID
- Product ID
- Serial Number
- Product Category
- Warranty Status
- Claim Status
- Confidence
- Reviewer
- Date Range

---

## Phase 26: Reports and Exports

Generate downloadable claim reports containing:
- Claim details
- Evidence
- Warranty status
- Python prediction
- GTM prediction
- Confidence comparison
- Policy result
- Contradictions
- Final recommendation
- Reviewer comments

Also support CSV or Excel-compatible exports.

---

## Phase 27: Audit Logging

Record:
- Login
- Product registration
- Document upload
- OCR correction
- Claim submission
- Prediction
- Rule execution
- Status change
- Review
- Override
- Final decision

---

## Phase 28: Testing

Build automated tests for:
- Authentication
- Claims
- Database
- OCR
- Preprocessing
- Python ML
- GTM integration
- Model comparison
- Policy engine
- Contradictions
- Missing documents
- Duplicate claims
- Serial mismatches
- Low confidence
- Model disagreement

---

## Phase 29: Hidden-Test Preparation

Create difficult test scenarios such as:

```text
Warranty expires today
Fault occurs on final warranty day
Missing receipt
Two different serial numbers
Duplicate invoice
Unauthorized repair
Python says valid while GTM says invalid
Both models low confidence
Claim before purchase
Repair before purchase
```

---

## Phase 30: Performance Optimization

The application should normally produce both model predictions within approximately five seconds.

Optimize:
- Model loading
- OCR
- Database queries
- Claim Summary Card generation
- GTM inference

Load models once into memory rather than on every request.

---

## Phase 31: Security

Implement:
- Secure password hashing
- Authentication
- Authorization
- File validation
- Safe filenames
- Upload-size limits
- Protected documents
- Input validation
- SQL injection protection
- Audit logs
- Environment variables
- Secret management

Do not store real confidential customer information in the public repository.

---

## Phase 32: Deployment

Suggested architecture:

```text
React Frontend
      ↓
FastAPI Backend
      ↓
PostgreSQL
      ↓
Model / OCR Services
```

Prepare:
- Public deployment URL
- Evaluator credentials
- Administrator credentials
- Sample claims
- Testing instructions

---

## Phase 33: Documentation

Complete:
- README.md
- Installation instructions
- Execution instructions
- Architecture documentation
- Database design
- API documentation
- Model methodology
- Model results
- Limitations
- Warranty policy documentation
- Test results

---

## Phase 34: Competition Deliverables

Prepare:

1. Project report
2. Public GitHub repository
3. Complete source code
4. 1,500+ claim dataset
5. Claim Summary Card dataset
6. Python model
7. Google Teachable Machine model
8. Three or more warranty policies
9. Model-comparison report
10. Test cases and results
11. Installation instructions
12. Execution instructions
13. Deployment
14. Demonstration video
15. 2,000+ word technical blog
16. AI_USAGE.md
17. Team contribution record

---

# Recommended Implementation Order

```text
1. Architecture + Git
2. Database
3. Authentication
4. Product/Warranty CRUD
5. Claim workflow
6. Document upload + OCR
7. Dataset generation
8. Preprocessing
9. Train 3+ Python models
10. Claim Summary Cards
11. Train GTM
12. Model APIs
13. Warranty rule engine
14. Duplicate/contradiction detection
15. Final decision engine
16. Manual review
17. Dashboards
18. Reports/exports
19. Testing
20. Deployment
21. Documentation
22. Demo + final submission
```

This order prioritizes the highest-risk areas early: dataset quality, machine learning, Google Teachable Machine integration, OCR, and final decision logic.
