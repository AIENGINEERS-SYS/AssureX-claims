# Claim assessment and role architecture

Implementation checklist: baseline recorded; registered route inventory generated;
shared upload, audit and schema services extracted; one submission-to-assessment pipeline;
durable retry/recovery; frozen evaluation attempts; immutable report versions;
claim-specific customer access; assignment-only staff scope; explicit administrator
permissions; customer/staff workspaces; regression and browser verification.

The [implementation and verification report](../reports/assessment-implementation-2026-10-21.md)
records root causes, the final role matrix, exact verification commands/results and
the remaining Windows GTM runtime blocker.

The baseline on branch `CJ` had a clean working tree. The selected authentication,
submission, evaluation, reporting, evidence and migration suites passed 154 tests.
Existing warnings concern pypdf deprecation and the committed model's sklearn /
XGBoost serialization versions. Baseline failures: none.

## Authoritative lifecycle

`Claim.status` remains the existing lowercase database state. Customer workflow
serialization remains uppercase. Drafts submit as `submitted`; assessments may
move them to `under_evaluation` or `manual_review`. Additional evidence uses
`additional_information_required`. Only an authorized review records `approved`
or `rejected` and writes `final_decision`. Automated recommendations are immutable
`EvaluationResult.recommendation` values. They are never approvals or rejections.

Queue progress is separate: `queued`, `running`, `retry_wait`, `completed`,
`failed`, `superseded`. Report progress is `pending`, `running`, `available`,
`failed`. An assessment can be complete while report generation has failed.
Partial or unavailable required models cannot produce a completed report.

Submission and queue insertion commit together. The HTTP response does not run
model inference. The existing staff evaluate endpoint retains its 201 evaluation
response and calls the same pipeline as the worker; duplicate requests reuse
the current assessment for identical evidence and model/policy configuration.
Returning to earlier input values after a later assessment creates a new version;
it does not revive a historical report as though it were the latest assessment.

`assessment_jobs` stores exact claim, product, warranty, document hashes, OCR and
confirmed observations, repair and configuration snapshots. `assessment_attempts`
preserves every completed or partial evaluation attempt, rules and policy, linked
to the immutable evaluation row. `assessment_reports` stores durable customer-safe
JSON linked to the exact claim, assessment version, evidence digest and evaluation.
Reports are append-only. Later evidence and human decisions never rewrite them.
The report API returns the current authorized human decision separately from the
decision, if any, captured in the historical report.

## Worker and recovery

Apply migrations before starting the updated API. In local development run:

```powershell
.venv\Scripts\python.exe -m flask --app backend:create_app db upgrade
.venv\Scripts\python.exe -m flask --app backend:create_app assessment-worker
# Process at most one ready job, also recovering abandoned leases:
.venv\Scripts\python.exe -m flask --app backend:create_app assessment-worker --once
```

Railway starts and supervises both assessment and existing export workers in
`scripts/railpack-start.sh`. Set `ASSESSMENT_WORKER_ENABLED=false` only when a
separate supervised worker uses the same database and document storage. Configure
shared durable document storage for separate processes/replicas. Model inference
loads models lazily in the assessment process and needs its own memory allowance.

| Setting | Default | Meaning |
| --- | --- | --- |
| `ASSESSMENT_MAX_ATTEMPTS` | 3 | Bounded automatic attempts per assessment/report stage |
| `ASSESSMENT_RETRY_SECONDS` | 30 | Initial backoff, doubled for successive failures |
| `ASSESSMENT_LEASE_SECONDS` | 900 | Worker lease; abandoned jobs are recovered |

PostgreSQL workers claim rows using `SKIP LOCKED`; conditional updates and a
unique active-job index prevent duplicate processing. Lease tokens fence obsolete
workers. Model evaluation and report creation have separate transaction boundaries.
Report failures reuse the successful evaluation, without repeating inference.
Failures expose safe codes/messages; logs contain IDs, phase, duration and exception
type, not raw model errors. An explicit authorized retry starts another bounded
retry cycle for the same assessment. Changed evidence creates another version.
SQLite remains a local-development database; use one worker there.

## Customer contracts

All claim-specific endpoints enforce ownership for customers and assignment for
staff. Administrators are explicitly authorized. Other users' IDs return 404.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/claims/{id}/assessment` | Assessment/report progress and current human decision |
| POST | `/api/claims/{id}/assessment/retry` | Queue an eligible retry; JSON `{}`; HTTP 202 |
| GET | `/api/claims/{id}/assessment/reports` | Paginated historical report versions |
| GET | `/api/claims/{id}/assessment/reports/{report_id}` | Frozen customer-safe report and separate current human decision |
| GET | `/api/claims/{id}/assessment/reports/{report_id}/download/{format}` | PDF, CSV or Excel from that exact stored report |

Customers can retry a failed/superseded assessment or submit requested additional
evidence for reassessment after validation. Requested document categories are stored
on the claim, displayed to the customer and checked against new uploads before
reassessment. They cannot invoke privileged model
endpoints or rerun arbitrary successful assessments. Refresh never resubmits a
claim. Earlier claims without a job show an explicit unscheduled state; staff can
queue them through the assessment retry endpoint.

Reports distinguish registered facts, OCR observations, customer confirmations,
model predictions, policy rules, recommendations and human decisions. GTM uses a
structured summary card and does not directly verify damage photographs. Missing
facts remain absent; model confidences are shown only when persisted predictions
exist. Customer reports omit model errors, other-claim identifiers, internal rule
evidence, reviewer notes and storage paths. Existing aggregate exports retain
their private-job ownership, expiry, manifest checks and export formats.

## Authorization and frontend communication

`role_required` authenticates and authorizes only explicitly named database roles;
administrator access is declared on each intended handler. `services/access.py`
defines shared resource scope. Customers own resources; employees and reviewers
must be assigned. Unassigned review cases are visible to administrators, who assign
reviewers. Reviewer self-assignment is no longer permitted. Duplicate investigations
require both claims in the reviewer's assignment scope, or administrator permission.

Customer claims stay at `/claims`; progress and report viewing appear on the claim
detail page. Employees use `/dashboard/employee`; administrative assignment,
account creation and failed-job recovery use `/dashboard/operations`. Existing
reviewer and administrator dashboards remain. A central frontend route policy and
access-denied screen prevent inappropriate workspaces rendering. Backend checks
remain authoritative for direct requests. All screens reuse the same Axios client
and in-memory session. Refresh completion cannot restore a signed-out session or
replay an old account's request under a different signed-in account.

Generate the registered route matrix and frontend API call inventory with:

```powershell
.venv\Scripts\python.exe scripts/route_inventory.py --output documentation/route-inventory.md
```

The migration preserves former automated `final_decision` values in audit events
before clearing them on undecided claims without a recorded human decision.
Downgrade removes the new assessment tables; it never restores misleading final
decisions or removes their audit history.
