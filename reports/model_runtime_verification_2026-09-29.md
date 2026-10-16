# Real Model Runtime and SRS Verification

**Date:** 2026-09-29  
**Repository:** `AIENGINEERS-SYS/AssureX-claims`  
**Audit branch:** `audit/real-model-runtime-verification`

## Scope

This verification executed the committed AssureX Python XGBoost model, the committed exported Google Teachable Machine model, the model-comparison engine, and the final combined evaluation path.

The checks were written against the SRS requirements for:

- independent Python and GTM model execution;
- three-class prediction output;
- confidence values for Valid, Invalid, and Manual Review;
- absolute top-class confidence difference;
- Strong Match / Acceptable Match / Weak Match / Model Disagreement / Uncertain Result;
- final recommendation using models plus warranty/evidence rules;
- manual review for incomplete evidence;
- at least 85% accuracy on unseen test claims.

## Runtime verification

GitHub Actions run **36512689127** executed the real committed model artifacts with no injected GTM predictor.

Result:

- **579 passed**
- **3 skipped**
- frontend build: **passed**
- PostgreSQL migration smoke test: **passed**
- backend test suite: **passed**

The real-model tests verified that:

1. the Python XGBoost artifact loads and predicts successfully;
2. the GTM TensorFlowJS model, weights, and metadata load successfully;
3. both outputs contain exactly `valid`, `invalid`, and `manual_review` confidence values;
4. each probability is within 0..1 and the three scores sum to approximately 1;
5. the predicted class owns the top confidence;
6. model versions and prediction rows are persisted;
7. GTM renders and uses a 224x224 RGB Claim Summary Card;
8. both actual model results feed the same comparison engine;
9. the comparison engine stores the absolute top-confidence difference;
10. missing required evidence produces `manual_review_required` in the final combined evaluation.

## Comparison-engine verification

The test suite challenged all five required consistency states:

- Strong Match
- Acceptable Match
- Weak Match
- Model Disagreement
- Uncertain Result

The verified formula is:

```text
confidence_difference =
abs(python_top_confidence - gtm_top_confidence)
```

The comparison result also correctly records whether predicted classes match.

## Python model accuracy evidence

The committed full Python test report contains 360 held-out records and reports:

- accuracy: **92.5%**
- macro precision: **92.81%**
- macro recall: **92.50%**
- macro F1: **92.46%**

The first 30 committed held-out Python prediction rows contain **28 correct predictions out of 30 = 93.33%**.

This is above the SRS 85% unseen-test target.

## GTM accuracy verification

A second verification joined the first 30 committed held-out Python test Claim IDs back to the structured dataset, generated corresponding Claim Summary Cards, and executed the real GTM artifact.

The structured rows were verified as belonging to the dataset's `test` split.

### Result

**13 / 30 correct = 43.33%**

This is below the SRS requirement of **at least 85% accuracy on unseen test claims**.

GitHub Actions run **36513317419** failed only this SRS accuracy assertion:

```text
GTM 30-claim held-out accuracy 0.433 (13/30) is below the SRS 0.85 target
```

The remainder of the suite passed:

- **579 passed**
- **3 skipped**
- **1 failed: GTM SRS accuracy threshold**

## Compliance conclusion

| Requirement | Result |
| --- | --- |
| Python artifact loads and runs | PASS |
| GTM artifact loads and runs | PASS |
| Python returns all 3 class confidences | PASS |
| GTM returns all 3 class confidences | PASS |
| Predictions are normalized and persisted | PASS |
| Claim Summary Card is used for GTM | PASS |
| Confidence-difference formula is correct | PASS |
| Five comparison statuses work | PASS |
| Real model outputs reach comparison engine | PASS |
| Final evaluation uses model + rules + evidence | PASS |
| Missing evidence routes to manual review | PASS |
| Python unseen-test accuracy >= 85% | PASS |
| GTM unseen-test accuracy >= 85% | **FAIL: 43.33% on 30 shared test claims** |
| Same train/validation/test split demonstrably used for both models | NOT VERIFIED; current card generator contains its own split logic |

## Important split concern

The structured dataset contains an explicit `split` field. The SRS requires the same underlying 70/15/15 split to be used by both the Python model and GTM Claim Summary Cards.

The current `dataset_generator/claim_card_generator.py` calculates a separate hash-based 80/10/10 split instead of honoring the dataset's explicit split. Therefore the repository cannot currently prove that the GTM model was trained with the exact same 70/15/15 split as the Python model.

This should be corrected before retraining GTM.

## Required corrective work

1. Change Claim Summary Card dataset generation to use the existing structured dataset `split` column.
2. Enforce 70% train / 15% validation / 15% test.
3. Keep every visual variation of a claim in the same split.
4. Generate at least two visual variations for training claims only.
5. Do not upload validation or test cards to GTM training.
6. Retrain the GTM model on the train cards.
7. Validate tuning using validation cards.
8. Export the retrained GTM model.
9. Run the complete held-out test set.
10. Require >=85% test accuracy before accepting the artifact.
11. Regenerate the required 30-claim Python/GTM comparison report using only held-out claims.

## Additional observation

The Python model currently emits an XGBoost serialization compatibility warning when loaded with the CI version of XGBoost. Inference succeeds, but the model should eventually be re-exported using XGBoost's supported model serialization format to reduce future compatibility risk.

## Final finding

The **inference architecture and comparison engine are functioning correctly**. The project is **not yet fully compliant with the SRS because the current GTM artifact fails the required unseen-test accuracy threshold**, and the repository cannot prove that GTM was trained on the same required split as the Python model.
