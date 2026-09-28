# Model Prediction and Confidence Comparison Report

**Project:** AssureX Claim Engine  
**Report version:** 1.0  
**Evidence source:** committed repository artifacts  
**Required unseen claims:** 30 minimum

## 1. Purpose

This report implements the structure required for comparing the independently trained Python classification model and Google Teachable Machine (GTM) model.

The application uses three canonical outcome classes:

- Likely Valid / `valid`
- Likely Invalid / `invalid`
- Manual Review Required / `manual_review`

The top-class confidence difference is:

```text
confidence_difference =
abs(python_top_confidence - gtm_top_confidence)
```

The runtime comparison service assigns one of:

- Strong Match
- Acceptable Match
- Weak Match
- Model Disagreement
- Uncertain Result

## 2. Evidence status

### Available in the repository

- Python unseen-test prediction file: `notebooks/sample_test_predictions_with_confidence.csv`
- Python test classification report: `notebooks/classification_report.csv`
- Python confusion matrix: `notebooks/confusion_matrix.csv`
- Python model comparison: `notebooks/model_comparison.csv`
- GTM exported model: `gtm_model/model.json`
- GTM weights: `gtm_model/weights.bin`
- GTM metadata: `gtm_model/metadata.json`
- runtime comparison implementation: `backend/services/model_comparison.py`
- combined evaluation implementation: `backend/services/evaluation.py`

### Missing final-submission evidence

The repository does **not** currently contain a committed unseen-test result file with GTM predictions and all three GTM confidence values for at least 30 test claims. Therefore this report does not invent GTM confidence values, comparison statuses, rule results, or final decisions for the 30 rows below.

To complete this SRS deliverable, generate the same 30 Claim Summary Cards from the held-out test split, run the exported GTM model, and populate the GTM and combined-decision fields.

## 3. Required fields and project mapping

| SRS field | Project source/status |
| --- | --- |
| Claim ID | Python unseen-test file |
| Actual claim class | Python unseen-test file |
| Python predicted class | Python unseen-test file |
| Python confidence: Valid | Python unseen-test file |
| Python confidence: Invalid | Python unseen-test file |
| Python confidence: Manual Review | Python unseen-test file |
| Claim Summary Card filename | must be generated from the same held-out Claim ID |
| GTM predicted class | pending committed unseen-test GTM run |
| GTM confidence: Valid | pending |
| GTM confidence: Invalid | pending |
| GTM confidence: Manual Review | pending |
| Predicted-class match | computed by `ModelComparisonService` once both outputs exist |
| Top-class confidence difference | computed by `ModelComparisonService` |
| Model consistency status | computed by `ModelComparisonService` |
| Warranty-rule result | stored rule/evaluation output |
| Missing documents | stored rule/evaluation output |
| Contradictions | stored evaluation output |
| Duplicate indicators | stored evaluation output |
| Final application decision | stored evaluation output |
| Major disagreement explanation | generated from stored comparison/rule evidence |

## 4. Thirty unseen Python baseline claims

The following records are taken directly from the committed held-out Python prediction evidence. They establish the Python half of the required 30-claim comparison.

| # | Claim ID | Actual class | Python predicted | P(Invalid) | P(Valid) | P(Manual Review) | Python result |
| ---: | --- | --- | --- | ---: | ---: | ---: | --- |
| 1 | NG-WC-01945 | Likely Valid | Likely Valid | 0.002917 | 0.990578 | 0.006505 | Correct |
| 2 | NG-WC-00780 | Likely Invalid | Likely Invalid | 0.962855 | 0.005974 | 0.031171 | Correct |
| 3 | NG-WC-01268 | Manual Review Required | Manual Review Required | 0.003303 | 0.000125 | 0.996572 | Correct |
| 4 | NG-WC-02325 | Likely Valid | Likely Valid | 0.006609 | 0.970173 | 0.023218 | Correct |
| 5 | NG-WC-00582 | Likely Invalid | Likely Invalid | 0.998435 | 0.000171 | 0.001394 | Correct |
| 6 | NG-WC-01363 | Likely Valid | Likely Valid | 0.003425 | 0.978776 | 0.017799 | Correct |
| 7 | NG-WC-01869 | Likely Valid | Likely Valid | 0.038124 | 0.931129 | 0.030747 | Correct |
| 8 | NG-WC-01379 | Likely Valid | Likely Valid | 0.018201 | 0.956977 | 0.024822 | Correct |
| 9 | NG-WC-00846 | Manual Review Required | Manual Review Required | 0.039501 | 0.113528 | 0.846971 | Correct |
| 10 | NG-WC-00394 | Likely Valid | Likely Valid | 0.005101 | 0.968465 | 0.026434 | Correct |
| 11 | NG-WC-01321 | Manual Review Required | Manual Review Required | 0.023206 | 0.001866 | 0.974928 | Correct |
| 12 | NG-WC-00081 | Manual Review Required | Likely Valid | 0.007378 | 0.964960 | 0.027662 | Incorrect |
| 13 | NG-WC-00902 | Likely Invalid | Likely Invalid | 0.978237 | 0.000703 | 0.021059 | Correct |
| 14 | NG-WC-00605 | Manual Review Required | Manual Review Required | 0.024727 | 0.209068 | 0.766205 | Correct |
| 15 | NG-WC-01812 | Likely Valid | Likely Valid | 0.015778 | 0.881615 | 0.102607 | Correct |
| 16 | NG-WC-01741 | Likely Valid | Likely Valid | 0.032668 | 0.942210 | 0.025122 | Correct |
| 17 | NG-WC-00968 | Likely Invalid | Likely Invalid | 0.991747 | 0.002175 | 0.006078 | Correct |
| 18 | NG-WC-01960 | Likely Valid | Likely Valid | 0.008963 | 0.982493 | 0.008544 | Correct |
| 19 | NG-WC-00009 | Likely Valid | Likely Valid | 0.009303 | 0.956029 | 0.034668 | Correct |
| 20 | NG-WC-01776 | Likely Valid | Likely Valid | 0.001643 | 0.985818 | 0.012539 | Correct |
| 21 | NG-WC-00369 | Manual Review Required | Manual Review Required | 0.040063 | 0.001327 | 0.958610 | Correct |
| 22 | NG-WC-01510 | Manual Review Required | Manual Review Required | 0.011839 | 0.000203 | 0.987959 | Correct |
| 23 | NG-WC-00713 | Manual Review Required | Manual Review Required | 0.020208 | 0.019858 | 0.959934 | Correct |
| 24 | NG-WC-01513 | Manual Review Required | Manual Review Required | 0.118463 | 0.003805 | 0.877732 | Correct |
| 25 | NG-WC-02029 | Likely Valid | Likely Valid | 0.006857 | 0.991940 | 0.001204 | Correct |
| 26 | NG-WC-00736 | Manual Review Required | Manual Review Required | 0.022030 | 0.000570 | 0.977400 | Correct |
| 27 | NG-WC-01720 | Likely Valid | Likely Valid | 0.003251 | 0.991871 | 0.004878 | Correct |
| 28 | NG-WC-01497 | Manual Review Required | Manual Review Required | 0.008709 | 0.007669 | 0.983622 | Correct |
| 29 | NG-WC-00551 | Likely Invalid | Likely Invalid | 0.981050 | 0.000116 | 0.018834 | Correct |
| 30 | NG-WC-00033 | Likely Invalid | Likely Valid | 0.027794 | 0.943683 | 0.028524 | Incorrect |

Python baseline accuracy for these 30 selected rows is **28/30 (93.33%)**. This is a subset summary only and is not a replacement for the full committed 360-record Python test evaluation.

## 5. GTM metadata

The committed GTM metadata declares:

- image size: 224;
- labels: `valid_ claim`, `invalid_claim`, `manual_review`;
- TensorFlowJS/Teachable Machine export metadata.

Runtime normalization maps these labels to the same canonical classes used by the Python model.

## 6. Runtime comparison rules

The current comparison implementation behaves as follows:

1. if either model output is unavailable: **Uncertain Result**;
2. if either top confidence is below `MODEL_MINIMUM_CONFIDENCE`: **Uncertain Result**;
3. if classes differ: **Model Disagreement**;
4. if classes match and both confidences/gap meet strong thresholds: **Strong Match**;
5. if classes match and both confidences/gap meet acceptable thresholds: **Acceptable Match**;
6. otherwise: **Weak Match**.

The service also computes a distribution-distance value across the full three-class probability vectors, although the SRS only requires the top-class confidence difference.

## 7. Manual-review routing

The application decision engine can route a claim to manual review when:

- one model is unavailable;
- predictions disagree;
- confidence is below the minimum;
- confidence differences are too large for reliable agreement;
- required evidence is missing;
- contradictions are detected;
- duplicate indicators are material;
- a policy is missing/unconfigured; or
- rule violations require human judgment.

## 8. Major disagreement handling

When Python and GTM disagree, the system must not choose whichever model has the larger confidence as an automatic winner. Instead, the comparison status is `Model Disagreement`, and the final decision also considers policy rules, evidence completeness, contradictions and duplicate findings.

This preserves model independence and prevents confidence magnitude from silently overriding business rules.

## 9. Steps to complete the final 30-claim report

1. Use the final 70/15/15 held-out split.
2. Select at least 30 test claims that were never used for either model's training.
3. Generate exactly one standardized test Claim Summary Card per selected claim.
4. Run the Python model and save all three class probabilities.
5. Run the exported GTM model on the corresponding card and save all three class probabilities.
6. Run the AssureX comparison service to compute class match, confidence difference and consistency status.
7. Run warranty rules, missing-document, contradiction and duplicate checks.
8. Store/record the final decision.
9. Add a short explanation for every disagreement, low-confidence, rule-failure or manual-review case.
10. Export the completed table as CSV and retain this Markdown summary.

## 10. Conclusion

The repository contains enough evidence to verify the Python side of the required comparison and the actual comparison logic used by the application. The remaining evidence gap is the **paired GTM unseen-test output for at least 30 claims**. Until that output is committed, any completed GTM columns would be fabricated and should not be submitted as model evidence.
