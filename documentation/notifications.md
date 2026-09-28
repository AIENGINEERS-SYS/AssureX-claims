# Phase 24: Notifications System

Phase 24 adds an authenticated, role-aware in-app notification platform for AssureX. Domain workflows publish notification events through NotificationService. The current InAppNotificationChannel persists them immediately, while the channel interface allows email, SMS, and push adapters to be added later without changing claim or warranty workflows.

## Event types

| Type | Default priority | Recipient | Trigger |
| --- | --- | --- | --- |
| WARRANTY_EXPIRY | MEDIUM | Customer | Warranty reaches 90, 60, 30, or 7 days remaining |
| MISSING_DOCUMENTS | HIGH | Customer | Claim submission is blocked by required missing evidence |
| CLAIM_SUBMITTED | MEDIUM | Customer | Claim is submitted successfully |
| INFO_REQUESTED | HIGH | Customer | Reviewer requests evidence or clarification |
| CLAIM_IN_REVIEW | HIGH | Customer, assigned reviewer | Claim enters manual review or is assigned |
| CLAIM_APPROVED | HIGH | Customer | Reviewer approves claim |
| CLAIM_REJECTED | HIGH | Customer | Reviewer rejects claim |

For reviewer rejection endpoints, `notes` remains internal review evidence. An optional `rejection_reason` field (maximum 2,000 characters) is the only reviewer-supplied reason included in the customer notification.

Priorities are LOW, MEDIUM, HIGH, and CRITICAL.

## Persistence

The notifications table stores the public notification identifier, recipient, type, title, message, claim/product references, generic reference type and ID, priority, read state, timestamps, a soft-delete timestamp, and an optional dedupe key.

The notification_preferences table stores one optional row per user. If no row exists, all preferences default to enabled.

Database indexes cover user/read/created ordering, type filtering, priority filtering, and reference lookup. Warranty scheduling loads products with warranties using selectinload and bulk-loads user preferences to avoid N+1 queries.

## Security

Every /api/notifications endpoint requires a valid access JWT. User-facing read/update/delete queries always include the authenticated user ID, so a valid notification ID belonging to another account returns 404.

Reviewers cannot read customer notifications. Customers cannot read reviewer notifications. Administrators do not receive a bypass for individual notification records; they can access aggregate notification analytics.

Notification mutations and preference changes create audit records.

## API

All requests use an Authorization: Bearer <access-token> header.

### List and filter notifications

~~~http
GET /api/notifications?page=1&per_page=20&type=CLAIM_IN_REVIEW&priority=HIGH&is_read=false&search=CLM-2026&from=2026-09-01&to=2026-09-30
~~~

Example response:

~~~json
{
  "items": [
    {
      "id": 42,
      "notification_id": "NTF-...",
      "notification_type": "CLAIM_IN_REVIEW",
      "type": "claim_in_review",
      "title": "Claim moved to review",
      "message": "Claim CLM-2026-000001 has been moved to manual review.",
      "reference_type": "claim",
      "reference_id": "CLM-2026-000001",
      "priority": "HIGH",
      "claim_id": 15,
      "product_id": null,
      "is_read": false,
      "created_at": "2026-09-28T21:30:00+00:00",
      "read_at": null,
      "href": "/claims/15"
    }
  ],
  "page": 1,
  "per_page": 20,
  "total": 1,
  "pages": 1
}
~~~

Supported filters: type, priority, is_read, from, to, search, page, and per_page. Search covers title, message, and reference ID such as a claim ID.

### Unread summary

~~~http
GET /api/notifications/unread?limit=5
~~~

~~~json
{
  "count": 3,
  "items": []
}
~~~

The separate count query avoids loading every unread notification just to render the bell badge.

### Single notification

~~~http
GET /api/notifications/42
~~~

### Mark one read

~~~http
PATCH /api/notifications/42/read
~~~

### Mark all read

~~~http
PATCH /api/notifications/read-all
~~~

~~~json
{
  "updated": 7
}
~~~

This is implemented as a set-based SQL update.

### Delete one notification

~~~http
DELETE /api/notifications/42
~~~

~~~json
{
  "deleted": true
}
~~~

### Preferences

~~~http
GET /api/notifications/preferences
~~~

~~~json
{
  "preferences": {
    "warranty_reminders": true,
    "claim_updates": true,
    "information_requests": true,
    "review_notifications": true
  }
}
~~~

Update only the settings that changed:

~~~http
PATCH /api/notifications/preferences
Content-Type: application/json

{
  "warranty_reminders": false,
  "review_notifications": true
}
~~~

### Admin analytics

~~~http
GET /api/notifications/analytics?days=30
~~~

Administrators only.

~~~json
{
  "period_days": 30,
  "notifications_sent": 120,
  "notifications_read": 96,
  "read_rate": 80.0,
  "average_time_to_read_seconds": 2400.5,
  "most_common_notification_type": "CLAIM_SUBMITTED",
  "by_type": {
    "CLAIM_SUBMITTED": 40,
    "WARRANTY_EXPIRY": 25
  },
  "by_priority": {
    "HIGH": 52,
    "MEDIUM": 68
  }
}
~~~

## Daily warranty reminder job

Configure thresholds with:

~~~text
WARRANTY_NOTIFICATION_THRESHOLDS=90,60,30,7
~~~

Run once per day:

~~~bash
python -m flask --app backend:create_app dashboard-reminders
~~~

The command now uses the Phase 24 notification service. A unique dedupe key combines user, warranty, expiry date, and threshold, so reruns and overlapping scheduler executions do not create duplicate reminders.

On Railway, create a cron service or scheduled job that runs that command daily. Keep the web service start command responsible for migrations and Gunicorn; do not run the daily reminder loop inside a web worker.

## Frontend

The dashboard includes a notification bell, unread badge, recent-notification dropdown, /dashboard/notifications notification center, type/priority/read/date/text filters, mark-one-read, mark-all-read, delete, preference controls, customer recent notifications, reviewer assignment/review notices, and administrator 30-day notification analytics.

React Query refreshes unread state every 15 seconds and invalidates notification/customer dashboard caches after mutations.

## Delivery architecture

Workflow code calls semantic service methods such as:

~~~python
NotificationService().send_claim_submitted(claim)
NotificationService().send_claim_approved(claim)
NotificationService().send_review_notification(claim)
~~~

Those methods construct a NotificationEvent, apply user preferences and dedupe rules, then dispatch through channel adapters. The in-app adapter is active now.

Future adapters implement the NotificationChannel protocol. For external providers, use an outbox/queue worker rather than sending network requests in the claim transaction. This keeps claim submission latency and reliability independent from email, SMS, or push vendors.

## Migration

Apply before deploying application code:

~~~bash
python -m flask --app backend:create_app db upgrade
~~~

The existing Railway start sequence should run migrations before starting the production web server.


## Audit hardening

The Phase 24 audit adds several production safeguards:

- Idempotency keys are normalized to the PostgreSQL `VARCHAR(160)` limit with a stable SHA-256 suffix when needed.
- Dedupe inserts use a database savepoint so a concurrent duplicate notification cannot roll back the surrounding claim transaction.
- First-time preference creation also uses a savepoint, avoiding a full request rollback if two updates race.
- Legacy Phase 23 notification references are backfilled to public claim/product IDs, so search by `CLM-...` or `PRD-...` works for migrated rows.
- Admin analytics computes average read time in SQL on PostgreSQL/SQLite and reports active critical/high-priority unread alerts.
- Global analytics indexes cover created date, type/date, and priority/read/date queries.
- CI runs the complete backend test suite, a frontend production build, SQLite migrations, and a PostgreSQL migration smoke test.

Warranty scheduling uses catch-up semantics. If the job misses an exact threshold day, the next run emits the nearest missed reminder once, keyed to the configured threshold. For example, a warranty first seen at 29 days receives the 30-day reminder with the message reporting the actual 29 days remaining.
