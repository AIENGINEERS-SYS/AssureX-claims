# Phase 23 dashboards

AssureX exposes `/dashboard/customer`, `/dashboard/reviewer`, and `/dashboard/admin` as browser routes. The API service redirects these paths to the standalone React service configured by `FRONTEND_URL`, preserving deep links and query strings. The supplied design informs the dark green navigation, lime actions, light cards, and status chips. The HTML shell is public; **all dashboard data APIs require JWT**. Customers see only their own records, reviewers see unassigned or assigned cases, and administrators can inspect a customer view using `user_id`.

## Start locally

1. Install backend dependencies from `backend/requirements.txt`, or `tests/requirements.txt` for pytest.
2. Configure `DATABASE_URL` and random `JWT_SECRET_KEY` using `config/.env.example`. Production requires PostgreSQL.
3. Run `python -m flask --app backend:create_app db upgrade`.
4. Run `npm --prefix frontend ci` and `npm --prefix frontend run build`.
5. Start Flask and Vite, open `/dashboard` on either service, and sign in with an existing account. Flask redirects browser routes to `FRONTEND_URL`.
6. Schedule `python -m flask --app backend:create_app dashboard-reminders` daily. It creates idempotent warranty notices.

Access and refresh tokens stay in browser memory. A reload requires sign-in. React Query caches each role's data for 15 seconds and refetches every 30 seconds and on focus. API responses use `Cache-Control: no-store`. Production needs a shared Redis limiter; large dashboard reads are limited to 60/minute.

## Metric definitions

| Metric | Persisted source and rule |
| --- | --- |
| Product coverage | One applicable warranty per product, prioritizing active extensions, using the UTC date and `WARRANTY_NEAR_EXPIRY_DAYS`. |
| Claim outcomes | Stored claim status. Lifetime includes drafts; customer counts list drafts separately. |
| Reviewer risk | Highest latest Python/GTM invalid-class confidence; labeled as a proxy, not a calibrated fraud score. Missing predictions yield null. |
| Model disagreement | Latest Python and GTM classes differ or top confidence differs by `MODEL_ACCEPTABLE_MAX_GAP` (default 0.20). Denominator: claims with both outputs. `DASHBOARD_DISAGREEMENT_GAP` remains a legacy default alias. |
| Model confidence | Mean top confidence of latest available outputs for non-draft claims. No missing value is treated as zero. |
| Model performance | Accuracy, precision, recall, and F1 from stored model version metrics, null if absent. |
| Duplicate warnings | SHA-256 exact file matches across separate non-draft claims; reviewer dispositions are retained separately. |
| Fraud detection rate | Distinct non-draft claims with a fraud rule warning/failure divided by non-draft claims. |
| Reviewer duration | Hours between submission and an approve/reject decision. |
| Notifications | Persisted submission, decision, status, reviewer-request, reminder, and warranty-expiry events with per-user read state. |

Required evidence follows the Phase 5 claim policy. Reviewer actions have assignment checks and audit records. Aggregates use SQL window and aggregation queries. The migration adds queue, owner, reviewer, and notification indexes. Lists are paginated (default 10, max 50), and chart code loads lazily.

## API

All paths below begin with `/api`; pass `Authorization: Bearer <access_token>`. Errors follow `{ "error": { "code": "...", "message": "..." } }`.

| Method and path | Roles | Purpose |
| --- | --- | --- |
| `GET /dashboard/customer?page=1&per_page=10` | customer; admin with `user_id` | Scoped coverage, claims, actions, charts, unread count. |
| `GET /dashboard/reviewer?page=1&per_page=10&search=CLM` | reviewer, admin | Queue, model disagreement, missing evidence, exact duplicates, outcomes. |
| `GET /dashboard/admin` | admin | Executive and operational aggregates. |
| `GET /dashboard/analytics` | admin | Same platform aggregates for analytics consumers. |
| `GET /dashboard/analytics/models?page=1&per_page=10` | admin | Paginated model evaluation history. |
| `GET /dashboard/trends?window=30d&interval=day` | customer, reviewer, admin | Windows `7d`, `30d`, `90d`, `12m`; intervals day, week, month. Reviewers see their own decisions. |
| `GET /dashboard/notifications?page=1&per_page=10&unread=true` | authenticated | Own notifications and read state. |
| `PATCH /dashboard/notifications/{id}/read` | authenticated | Mark owned notification read. Other IDs return 404. |
| `GET /dashboard/reviewer/claims/{id}` | reviewer, admin | Scoped case and evidence status. |
| `PATCH /dashboard/reviewer/claims/{id}/assignment` | reviewer, admin | Self-assign; admin sends `{"reviewer_id":7}`. |
| `POST /dashboard/reviewer/claims/{id}/request-documents` | reviewer, admin | `{"document_types":["receipt"]}`, notify the customer. |
| `POST /dashboard/reviewer/claims/{id}/remind` | reviewer, admin | Reminder for an outstanding request (10/hour). |
| `POST /dashboard/reviewer/claims/{id}/resume` | reviewer, admin | Resume manual review after evidence arrives. |
| `POST /dashboard/reviewer/duplicates/decision` | reviewer, admin | `{"claim_id":1,"matching_claim_id":2,"file_hash":"<64 hex chars>","status":"confirmed"}` or `false_positive`. |

### Example responses (selected fields)

`GET /api/dashboard/customer`:

```json
{"products":{"total_products":2,"covered":1,"expired":1,"active_warranties":1,"coverage_percentage":50.0,"expiring_count":1},"claims":{"total":2,"draft":1,"submitted":1,"approved":0,"rejected":0,"under_review":0,"closed":0},"pagination":{"page":1,"per_page":10,"total":2,"pages":1},"notifications_unread":0}
```

`GET /api/dashboard/admin`:

```json
{"disagreement":{"processed":10,"disagreements":2,"rate":20.0,"weekly_change_percentage_points":null},"model_confidence":{"average":0.815,"samples":20,"weekly_change":null},"duplicate_alerts":{"active":1,"confirmed":0,"false_positive":0}}
```

The examples omit other fields and chart rows. A customer can upload requested evidence and confirm OCR on `/claims/{claim_id}`; a reviewer can then resume the case. Employees retain the assigned-claims API.
