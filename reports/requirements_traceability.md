# SRS Deliverable Traceability Report

This report maps the policy/reporting deliverables from the AssureX SRS to repository evidence.

| Requirement | Repository evidence | Status |
| --- | --- | --- |
| Project report | `reports/project_report.md` | Added |
| Problem/background/solution/scope/constraints | project report | Added |
| Architecture and module descriptions | project report + `documentation/` | Added |
| Database design/data dictionary | project report + `documentation/database.md` | Added |
| DFD, use case, activity, sequence, decision flow | Mermaid diagrams in project report | Added |
| Rule-engine design | project report + `backend/services/claim_rules.py` | Added |
| Python model design/evaluation | `reports/python_model_evaluation_report.md` + notebooks | Added |
| Confusion matrix/accuracy/precision/recall/F1 | Python model evaluation report | Added |
| GTM exported model | `gtm_model/` | Present |
| Model comparison implementation | `backend/services/model_comparison.py` | Present |
| Separate 30-claim model comparison report | `reports/model_prediction_confidence_comparison_report.md` | Structure + Python baseline added; paired GTM evidence pending |
| At least 3 configurable warranty-policy files | `policies/electronics_policy.json`, `mobile_devices_policy.json`, `home_appliances_policy.json` | Added |
| Policy coverage duration | all 3 policy files | Added |
| Warranty start conditions | all 3 policy files | Added |
| Covered faults | all 3 policy files | Added |
| Exclusions | all 3 policy files | Added |
| Claim reporting period | all 3 policy files | Added |
| Repair conditions | all 3 policy files | Added |
| Authorized service-centre requirements | all 3 policy files | Added |
| Replacement conditions | all 3 policy files | Added |
| Grace periods | all 3 policy files | Added |
| Mandatory documents | all 3 policy files | Added |
| Hard-fail rules | all 3 policy files | Added |
| Warning rules | all 3 policy files | Added |
| Manual-review rules | all 3 policy files | Added |
| Rules stored outside scattered Python code | JSON policy/configuration files + policy service | Present |
| Functional/integration/boundary/security/model/rule tests | `tests/` and `fuzzy.txt` | Present |
| Report/export runtime feature | README currently states not implemented | Pending |
| 70/15/15 card split | generator currently implements 80/10/10 | Needs correction |
| GTM >=85% unseen-test evidence | no committed comparable test metrics found | Pending |

## Priority items before final submission

1. Run the final held-out GTM test set and populate at least 30 paired Python/GTM rows.
2. Correct `dataset_generator/claim_card_generator.py` to the SRS-required 70/15/15 split and regenerate the GTM card dataset.
3. Add GTM training screenshots/configuration, class image counts, incorrect samples and retraining notes.
4. Implement the access-controlled downloadable claim report and CSV/Excel-compatible export feature required by the functional specification.
5. Retain test results from the final repository state and deployment.
