# AssureX Warranty Policy Deliverables

This directory contains the configurable warranty-policy files required for the AssureX Claim Engine submission.

## Included policies

| File | Policy code | Main product scope | Coverage |
| --- | --- | --- | ---: |
| `electronics_policy.json` | `electronics` | General electronics, laptops, computers, cameras, televisions | 24 months |
| `mobile_devices_policy.json` | `mobile_devices` | Phones, smartphones, tablets and mobile devices | 12 months |
| `home_appliances_policy.json` | `appliances` | Refrigerators, washing machines, freezers, air conditioners and home appliances | 24 months |

Each file exposes the policy fields required by the project SRS:

- product category and aliases;
- coverage duration;
- warranty start conditions;
- covered faults;
- exclusions;
- claim-reporting period;
- repair conditions;
- authorized service-centre requirements;
- replacement conditions;
- grace period;
- mandatory documents;
- hard-fail rules;
- warning rules; and
- manual-review rules.

## Relationship to the runtime rule engine

The current runtime policy bundle remains `data/warranty_policies.json`, consumed by
`backend/services/warranty_policy.py`. These submission files preserve the runtime-compatible
fields `code`, `categories`, `warranty_months`, `required_documents`,
`optional_documents`, `exclusions`, `authorized_repair_required`, and
`unauthorized_repair_invalidates`, while adding the richer policy metadata required by the SRS.

The public claim workflow uses the upload label `damage_evidence`, while persisted documents use
`fault_evidence`. The files therefore expose both the public/runtime compatibility list and the
canonical persisted mandatory-document list. This distinction should be preserved until the rule
engine and claim-document vocabulary are unified.

## Policy evaluation behavior

The AssureX rule engine independently checks:

1. warranty expiry;
2. serial-number consistency;
3. contradictions in dates, models, and evidence;
4. missing mandatory documents;
5. authorized/unauthorized repair history; and
6. excluded damage.

A missing or invalid category policy does not silently approve a claim. The evaluation service
creates an unconfigured fallback policy and routes the case toward manual review.

Model outputs do not override hard warranty evidence. The final decision also considers Python and
Google Teachable Machine predictions, confidence difference, duplicate signals, missing evidence,
and contradictions.

## Nigeria-specific reference evidence

`data/assurex_nigeria_policy_rules_v2.json` contains project research references for Nigerian
consumer/warranty contexts and selected manufacturer/retailer policies. The three submission
policies above are AssureX application policies designed for the competition workflow; they are
not legal advice and should not be represented as a verbatim warranty promise from any particular
manufacturer.

## Editing a policy

Policy changes should be made in configuration rather than by adding product-specific conditions
to Python code. When a policy change affects live rule behavior, update the runtime policy bundle
and add/adjust tests in `tests/test_fuzzy_rules.py` and `tests/test_evaluation.py`.

Examples of evaluator-safe changes include:

- adding a new exclusion;
- changing a coverage duration;
- changing a reporting deadline;
- adding or removing a required document; or
- adding a manual-review trigger.

All such changes should remain reviewable through version control.
