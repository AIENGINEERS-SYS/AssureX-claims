# AssureX Phase 3 API

Phase 4 product/warranty routes and request examples are documented in the [product API reference](products.md#api). The browser interface is available at `/products`.

Base URL for local development: `http://127.0.0.1:5000`. Send `Content-Type: application/json` for JSON bodies and `Authorization: Bearer <access_token>` for protected requests. Refresh takes a refresh token; logout accepts either type. Path `{id}` and product/warranty fields use integer database IDs. Public prefixed IDs are also returned for display.

| Method and path | Access | Body / behavior |
| --- | --- | --- |
| `POST /api/auth/register` | Public | `full_name`, `email`, `password`; creates customer; 201 |
| `POST /api/auth/login` | Public | `email`, `password`; returns token pair and user; 200 |
| `POST /api/auth/refresh` | Refresh token | No body; rotates token pair; 200 |
| `POST /api/auth/logout` | Either current token | No body; revokes login session; 200 |
| `GET /api/auth/me` | Any active user | Current user; 200 |
| `PATCH /api/auth/me` | Any active user | Optional `full_name`, `phone`; 200 |
| `POST /api/auth/password` | Fresh access token | `current_password`, `new_password`; invalidates all tokens; 200 |
| `POST /api/claims` | Customer, admin | `product_id`, `warranty_id`, `fault_date`, `fault_type`, `fault_description`, optional `damage_type`; 201 |
| `GET /api/claims/my` | Customer, admin | Own claims; paginated |
| `GET /api/claims/assigned` | Employee, admin | Assigned claims; admin sees all; paginated |
| `PATCH /api/claims/{id}/assignment` | Admin | `employee_id` of active employee; 200 |
| `PATCH /api/claims/{id}/status` | Assigned employee, admin | `status`; 200 |
| `POST /api/claims/{id}/documents` | Assigned employee, admin | Multipart field `file`; 201 |
| `GET /api/claims/{id}/documents/{document_id}` | Owner, assigned employee, manual reviewer, admin | Private file attachment |
| `GET /api/review/manual` | Reviewer, admin | Claims awaiting manual review; paginated |
| `GET /api/review/{id}/risk` | Reviewer, admin | Existing rule indicators for a claim awaiting review; paginated |
| `POST /api/review/{id}/notes` | Reviewer, admin | `notes`; appends review record; 200 |
| `POST /api/review/{id}/approve` | Reviewer, admin | `notes`; records approval; 200 |
| `POST /api/review/{id}/reject` | Reviewer, admin | `notes`; records rejection; 200 |
| `GET /api/admin/users` | Admin | Users, including inactive accounts; paginated |
| `POST /api/admin/users` | Fresh admin | `full_name`, `email`, `password`, `role`; 201 |
| `PATCH /api/admin/users/{id}` | Fresh admin | Optional `role`, `is_active`; invalidates tokens on changes; 200 |
| `DELETE /api/admin/users/{id}` | Fresh admin | Deactivates account, preserves records; 200 |
| `GET /api/admin/claims` | Admin | All claims; paginated |
| `GET /api/admin/analytics` | Admin | User counts and claim counts by status; 200 |

Roles: `customer`, `employee`, `reviewer`, `admin`. Lists accept `page` (default 1) and `per_page` (default 20, maximum 100) and return `{ "items": [], "page": 1, "per_page": 20, "total": 0 }`. Unknown JSON fields are rejected. Dates use `YYYY-MM-DD`; fault dates cannot be in the future. Notes must be nonblank and at most 10,000 characters.

## Example requests

Registration:

```http
POST /api/auth/register
Content-Type: application/json

{"full_name":"Ada Example","email":"ada@example.com","password":"use-a-long-unique-password"}
```

The response is `{ "message": "Account created.", "user": { ... } }` with status 201. Emails are stripped and lowercased. Duplicate emails return 409. Password requirements are 12+ characters and at most 72 UTF-8 bytes. Supplying `role` to registration returns 400.

Login:

```http
POST /api/auth/login
Content-Type: application/json

{"email":"ada@example.com","password":"use-a-long-unique-password"}
```

```json
{
  "access_token": "<signed access JWT>",
  "refresh_token": "<signed refresh JWT>",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": {
    "id": 1,
    "user_id": "USR-<public id>",
    "full_name": "Ada Example",
    "email": "ada@example.com",
    "phone": null,
    "role": "customer",
    "is_active": true,
    "created_at": "2026-09-26T10:00:00+00:00",
    "updated_at": "2026-09-26T10:00:00+00:00"
  }
}
```

Refresh and logout:

```http
POST /api/auth/refresh
Authorization: Bearer <refresh_token>
```

Store both replacement tokens before making subsequent calls. Refresh tokens are single-use; refreshing does not extend the seven-day login session. Access tokens returned by refresh are not fresh enough for sensitive admin actions; log in again to perform those actions.

```http
POST /api/auth/logout
Authorization: Bearer <access_token>
```

Logout invalidates every token from that login session. The current refresh token can be used for logout after the access token expires. A second logout using the revoked token returns 401.

Claim creation (requires an existing owned product and its warranty):

```http
POST /api/claims
Authorization: Bearer <access_token>
Content-Type: application/json

{"product_id":1,"warranty_id":1,"fault_date":"2026-09-20","fault_type":"power","fault_description":"Device does not turn on."}
```

Claim responses use `{ "claim": { ... } }` with IDs, owner, employee assignment, status, fault details, final decision and manual-review flag. Customer identity is taken from authentication. `user_id`, `assigned_employee_id`, decisions and status cannot be supplied at creation.

## Processing and review transitions

New claims start `submitted`. Employees may move `submitted` to `under_evaluation` or `manual_review`; `under_evaluation` to `additional_information_required` or `manual_review`; and `additional_information_required` back to `under_evaluation` or to `manual_review`. Other transitions return 409; approval/rejection values are not accepted by the employee status schema.

Reviewer notes, risk lookup, approval and rejection require both status `manual_review` and a true manual-review flag. Decisions append a Review record and atomically update the claim. Approval sets `approved` / `likely_valid`; rejection sets `rejected` / `likely_invalid`. Final decisions clear the review flag. Repeated decisions return 409.

## Errors

```json
{"error":{"code":"validation_error","message":"Input validation failed.","details":{"email":["Not a valid email address."]}}}
```

| Status | Meaning |
| --- | --- |
| 400 | Validation failure, invalid transition input or prohibited self-administration |
| 401 | Authentication/token failure, inactive account or fresh login required |
| 403 | Authenticated role lacks permission |
| 404 | Missing or inaccessible record |
| 409 | Duplicate/conflicting record or invalid workflow state |
| 413 | Request exceeds upload/body size limit |
| 415 | Expected JSON content type |
| 429 | Rate limit exceeded; observe retry/rate-limit response headers |
| 500 / 503 | Internal failure or database service unavailable; no credentials or SQL details returned |

See [setup, security design and deployment](authentication.md) for environment variables, migrations, account bootstrap, token lifecycle and operational requirements.
