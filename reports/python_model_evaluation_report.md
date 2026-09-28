# Python Model Evaluation Report

**Project:** AssureX Claim Engine  
**Final Python model:** XGBoost  
**Evidence:** files under `notebooks/`

## Algorithm comparison

| Model | CV accuracy mean | CV macro F1 mean | Validation accuracy | Validation macro F1 |
| --- | ---: | ---: | ---: | ---: |
| XGBoost | 0.9250 | 0.9244 | 0.9472 | 0.9468 |
| Random Forest | 0.9048 | 0.9043 | 0.9194 | 0.9192 |
| Extra Trees | 0.8679 | 0.8678 | 0.9000 | 0.8990 |
| Logistic Regression | 0.8470 | 0.8465 | 0.8806 | 0.8813 |

XGBoost has the strongest committed cross-validation and validation results and is the selected project model.

## Test-set classification report

The committed classification report contains 360 held-out observations, balanced at 120 records per class.

| Class | Precision | Recall | F1-score | Support |
| --- | ---: | ---: | ---: | ---: |
| Likely Invalid | 0.9640 | 0.8917 | 0.9264 | 120 |
| Likely Valid | 0.8824 | 1.0000 | 0.9375 | 120 |
| Manual Review Required | 0.9381 | 0.8833 | 0.9099 | 120 |
| **Accuracy** |  |  | **0.9250** | **360** |
| **Macro average** | **0.9281** | **0.9250** | **0.9246** | **360** |
| **Weighted average** | **0.9281** | **0.9250** | **0.9246** | **360** |

## Confusion matrix

| Actual \ Predicted | Likely Invalid | Likely Valid | Manual Review Required |
| --- | ---: | ---: | ---: |
| Likely Invalid | 107 | 6 | 7 |
| Likely Valid | 0 | 120 | 0 |
| Manual Review Required | 4 | 10 | 106 |

## Interpretation

The Python classifier exceeds the SRS 85% unseen-test accuracy threshold on the committed test evidence. The largest class-specific weakness is recall for Manual Review Required (0.8833) and Likely Invalid (0.8917), while Likely Valid has perfect recall on this test set but lower precision (0.8824). That pattern matters operationally because some ambiguous/invalid cases are being pulled into the valid class.

For that reason, AssureX does not use the Python prediction as the final decision by itself. The runtime combines it with GTM output, confidence comparison, warranty rules, missing evidence, contradictions and duplicate indicators.

## Feature-importance evidence

The committed top-feature export includes strong signals from:

- misuse;
- IMEI tampering;
- manufacturing defects;
- fault-to-claim delay;
- unauthorized repair;
- product category;
- liquid damage;
- prior-repair authorization;
- power surge;
- reporting delay;
- policy reporting deadline;
- serial/IMEI availability;
- warranty months; and
- document completeness.

Feature importance describes model use of the trained representation; it does not establish causation.

## Model artifact and runtime controls

The selected model is saved under `models/`. Runtime predictions:

- normalize labels into `valid`, `invalid`, `manual_review`;
- require all three probabilities;
- require probabilities to be within 0..1 and approximately sum to one;
- store top confidence;
- retain the model version used for each prediction.

## Limitations

- GTM unseen-test accuracy is not established by this report.
- Accuracy does not measure calibration.
- The project dataset is synthetic/curated competition data rather than a production claims population.
- Hidden evaluator claims may differ from the training distribution.
- Model evidence should be regenerated when the dataset, preprocessing pipeline or model artifact changes.

## Evidence files

- `notebooks/model_comparison.csv`
- `notebooks/classification_report.csv`
- `notebooks/confusion_matrix.csv`
- `notebooks/feature_importance_top25.csv`
- `notebooks/sample_test_predictions_with_confidence.csv`
- `models/assurex_xgboost_final.joblib`
