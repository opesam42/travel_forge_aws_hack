# Frontend Integration Guide

This guide documents the API routes currently registered by the backend and
how a browser frontend should call them. The running API's OpenAPI document at
`/openapi.json` and interactive docs at `/docs` are the final authority if a
schema changes.

## Base URL and setup

The backend serves the Alpine.js component-based HTML/CSS/JavaScript case
workspace from `/` and its assets, including a locally vendored Alpine runtime,
from `/static/`. With this built-in portal, the API is same-origin
and no separate frontend server or API URL configuration is needed. A
separately hosted frontend can set its API base URL to the backend origin only
(no `/api` suffix):

From the repository root, start the local portal with:

```bash
cd backend
venv/bin/alembic upgrade head
venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8765
```

Then open `http://127.0.0.1:8765/`. Keep the Uvicorn terminal running while
using the page.

```env
VITE_API_URL=http://localhost:8000
```

For production, use the deployed backend's HTTPS origin. All application
routes below are prefixed with `/api`; health is available at both `/health`
and `/api/health`.

Configure the backend `CORS_ORIGIN` with the frontend's exact origin(s),
comma-separated, for example:

```env
CORS_ORIGIN=http://localhost:5173,https://travel.example.com
ENVIRONMENT=production
AUTH_SESSION_TTL_HOURS=168
CHAT_HISTORY_LIMIT=12
```

An origin includes scheme, hostname, and port, but no path or trailing slash.
The browser's `Origin` must match one of the configured values. Credentialed
CORS responses cannot use `Access-Control-Allow-Origin: *`.

## Browser client and cookie authentication

Authentication uses an opaque, HTTP-only session cookie named
`travelforge_session` by default. The backend stores only a hash of the token.
Frontend JavaScript must not read or set this cookie. The browser receives it
from successful signup/login responses and sends it back on later requests.

**Every request that needs the cookie, including signup and login responses
that set it, must use credentials.** With Axios:

```js
import axios from "axios";

export const api = axios.create({
  baseURL: `${import.meta.env.VITE_API_URL || ""}/api`,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});
```

With `fetch`, use `credentials: "include"`:

```js
const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
  credentials: "include",
});
```

For multipart uploads, let the browser set the `Content-Type` and boundary:

```js
const form = new FormData();
form.append("application_id", applicationId);
form.append("doc_type", "passport");
form.append("file", file);
const response = await api.post("/documents/extract", form, {
  headers: { "Content-Type": undefined },
});
```

Do not force a JSON content-type on `FormData`; if using a shared client that
sets JSON by default, remove that header for the upload or use `fetch`.

### Important: local frontend calling a deployed API

`http://localhost:5173` and a `*.vercel.app` API are different sites. Even with
`withCredentials: true`, matching CORS settings, and the production
`SameSite=None; Secure` cookie, browser privacy settings may block third-party
cookies. Prefer a same-site deployment for frontend and API, or proxy API
requests through the frontend's same-site origin. CORS permission by itself
does not override browser third-party-cookie restrictions.

In production, use HTTPS. The backend defaults to `Secure; HttpOnly;
SameSite=None` cookies in production and non-secure `SameSite=Lax` cookies
outside production. The session lifetime defaults to 168 hours.

## What the frontend should support now

Build the client experience around an authenticated account and an application
dossier. The currently implemented workflow is:

1. **Account access:** sign up, sign in, restore the session on reload, and
   sign out.
2. **Application intake:** create an application with visa type and
   destination, list the signed-in user's applications, and reopen an
   application's saved profile and document summaries.
3. **Document intake:** upload a passport or bank statement for OCR, show the
   returned structured fields as **unverified** and **pending review**, and
   let the user inspect the saved application.
4. **Guidance chat:** ask a question grounded in pgvector policy data, scope
   it to an owned application when relevant, and reuse the returned chat
   session ID on later turns.
5. **Application audit:** explicitly run the graph-based audit and display
   its status, discrepancies, metrics, assumptions, and retrieved rules.
6. **Document checklist:** request a policy-grounded checklist for a visa
   type.
7. **Review state:** allow the user to confirm, correct, or reject OCR values;
   confirmed/corrected values are marked user-reviewed but do not prove
   physical document authenticity.

Authorized operational screens can manage policy rule drafts, active rules,
trusted sources, audit tickets, and knowledge-store ingestion/statistics.
These are staff functions and must not be shown in the client portal.

The uploaded binary is sent to the OCR provider in memory and is not stored by
this backend. Extracted JSON and basic document metadata are stored in
PostgreSQL. There is currently no document download endpoint, application
edit/delete endpoint. The review endpoint described below persists corrections
and review state. Do not imply that OCR establishes physical document
authenticity; review means the user confirmed the extracted fields.

The bundled travel corpus has 200 records, but only its 12 explicitly verified
entries are indexed. Pending-verification entries are excluded and must not be
presented as confirmed guidance. Chat and checklist APIs return ordinary JSON;
they do not stream SSE/WebSocket events or tokens.

## Authentication endpoints

All endpoints are under `/api/auth`.

### `POST /auth/signup`

Create a client account and start a session. JSON body:

```json
{
  "email": "person@example.com",
  "name": "A Person",
  "password": "at-least-12-characters"
}
```

Email is trimmed and lowercased; name is trimmed. Password length is 12–128
characters. New accounts are always `client`; do not send `user_type`.
Returns `201` and the user object:

```json
{
  "id": "user-uuid",
  "email": "person@example.com",
  "name": "A Person",
  "user_type": "client"
}
```

An existing email returns `409`.

### `POST /auth/login`

JSON body:

```json
{ "email": "person@example.com", "password": "your-password" }
```

Returns the same user object and sets a new session cookie. Invalid
credentials or an inactive account return `401`.

### `GET /auth/me`

Restore the signed-in account when the app starts or after a page reload.
Returns the user object; missing, expired, invalid, or inactive sessions
return `401`.

### `POST /auth/logout`

Revoke the current session and expire its cookie. Returns `204` with no body.
Treat a `204` as success without trying to parse JSON.

Suggested app behavior: call `/auth/me` at startup; use `401` there to show a
signed-out state; after signup/login, use the returned user data or reload
`/auth/me`; after logout clear account-scoped frontend state. Never store the
cookie or treat a chat `session_id` as an authentication credential.

## Applications and document processing

These routes require a signed-in user and an allowed request origin. Records
are scoped to the current account; another user's application is returned as
`404`.

### Applications

| Method and route | Purpose |
|---|---|
| `POST /api/applications` | Create an application; returns `201` |
| `GET /api/applications` | List the current user's applications, newest updated first |
| `GET /api/applications/{application_id}` | Get one application and its documents |
| `POST /api/applications/{application_id}/chat-session` | Get or create the case's persistent chat session |
| `PATCH /api/applications/{application_id}/intake` | Save proposed intake facts and explicitly confirm selected fields |

Create request:

```json
{
  "visa_type": "uk_skilled_worker",
  "destination": "United Kingdom",
  "applicant_data": {}
}
```

`visa_type` is required (1–100 characters); `destination` is optional
(maximum 100 characters); `applicant_data` is an optional JSON object containing only basic intake facts
(`citizenship`, `destination`, `travel_purpose`, `intended_travel_date`, and
`employer`). These values are saved as proposed, not confirmed. Document
fields and verification state are server-managed. The
response contains `id`, `visa_type`, `destination`, `stage`,
`applicant_data`, `latest_audit`, `documents`, `created_at`, and `updated_at`.
Each document summary contains `id`, `doc_type`, `original_filename`,
`review_status`, `extracted_data`, `created_at`, and `reviewed_at`. There is
currently no general application edit or delete route.

### Suggested application and document workflow

1. At app startup call `GET /api/auth/me`; if it returns `401`, show the
   signed-out experience. After login/signup, load `GET /api/applications`.
2. Create the application with `POST /api/applications`. Save its returned
   `id` as the dossier key. `visa_type` is required; use an empty
   `applicant_data` object when documents will provide initial profile data.
3. Offer separate passport and bank-statement upload actions. Send each as a
   separate `POST /api/documents/extract` request with the selected
   `application_id`, matching `doc_type`, and file.
4. Display returned values as OCR suggestions requiring human review; a
   successful extraction does not verify a document.
5. Allow the user to explicitly confirm, correct, or reject extracted fields
   via `POST /api/documents/{document_id}/review`.
6. After navigation or reload, use
   `GET /api/applications/{application_id}` for persisted profile, documents,
   stage, and latest audit.
7. When requested by the user, run
   `POST /api/audit/evaluate` with the same application ID and matching visa
   type. Then reload the application if you need its persisted stage and audit.
8. If the user confirms an intake fact detected in chat, send it to
   `PATCH /api/applications/{application_id}/intake` with that field in both
   `facts` and `confirm_fields`. Other submitted facts remain proposed.

Example `fetch` helper for passport/bank-statement OCR:

```js
async function extractDocument({ applicationId, docType, file }) {
  const form = new FormData();
  form.append("application_id", applicationId);
  form.append("doc_type", docType); // "passport" | "bank_statement"
  form.append("file", file);

  const response = await fetch(`${API_BASE_URL}/api/documents/extract`, {
    method: "POST",
    credentials: "include",
    body: form, // Let the browser set multipart Content-Type and its boundary.
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      body?.detail || `Document extraction failed (${response.status})`,
    );
  }
  return body;
}
```

Passport OCR returns `passport_number`, `full_name`, `expiry_date`, and
`issuing_country`. Bank-statement OCR returns `account_name`, `bank_name`,
`closing_balance_ngn`, and `statement_period_days`. For both, retain and
display `review_status: "pending_review"` and treat all extracted fields as
unverified. The current audit reads the saved passport expiry and bank
closing balance.

### `POST /api/documents/{document_id}/review`

Requires an authenticated owner and an allowed origin. Reviews only documents
whose current status is `pending_review`.

Confirm the OCR values as-is:

```json
{ "decision": "confirm", "review_notes": "Checked with the applicant." }
```

Correct values with the exact document-type schema:

```json
{
  "decision": "correct",
  "corrected_data": {
    "passport_number": "A12345678",
    "full_name": "A Person",
    "expiry_date": "2030-01-01",
    "issuing_country": "NG"
  }
}
```

Or reject:

```json
{ "decision": "reject", "review_notes": "Wrong applicant document." }
```

The response includes `review_status` (`reviewed` or `rejected`),
`extracted_data`, `review_notes`, and `reviewed_at`. Confirmed/corrected data
is marked `user_reviewed` in application context and becomes eligible for the
current audit's normal field checks. This records the user's review; it does
not certify physical authenticity. A rejected document is removed from the
active passport/bank-statement profile when it is the current document.
Errors include `404` for a document not owned by the user, `409` if it is no
longer pending review, and `422` for invalid or mismatched document fields.

### `PATCH /api/applications/{application_id}/intake`

Saves candidate facts and, optionally, marks the fields the user explicitly
confirmed. Allowed keys are `citizenship`, `destination`, `travel_purpose`,
`intended_travel_date`, `employer`, and `visa_type`; each value must be a
non-empty string up to 200 characters.

```json
{
  "facts": {
    "destination": "United Kingdom",
    "travel_purpose": "Business meetings",
    "employer": "Example Ltd"
  },
  "confirm_fields": ["destination", "travel_purpose"]
}
```

Fields in `confirm_fields` are copied to `applicant_data.intake` and marked
`user_confirmed` in `applicant_data.intake_verification`; other submitted
fields remain in `applicant_data.proposed_intake`. A confirmed value is not
overwritten by a later unconfirmed proposal. The response is the updated
application including documents.

### `POST /api/documents/extract`

Extract passport or bank-statement fields for an owned application. Send
`multipart/form-data` with:

| Field | Type | Values |
|---|---|---|
| `application_id` | form text | Application UUID |
| `doc_type` | form text | `passport` or `bank_statement` |
| `file` | file | PDF, JPEG, PNG, or WEBP; maximum 10 MB |

The filename extension and MIME type must match. Successful response:

```json
{
  "document_id": "document-uuid",
  "application_id": "application-uuid",
  "doc_type": "passport",
  "review_status": "pending_review",
  "extracted_data": {
    "passport_number": "123456789",
    "full_name": "A Person",
    "expiry_date": "2030-01-01",
    "issuing_country": "NG"
  }
}
```

For a bank statement, `extracted_data` instead contains `account_name`,
`bank_name`, `closing_balance_ngn`, and `statement_period_days`. Extraction
uses an external Google AI provider; the uploaded binary is not returned.
Extracted values are **unverified OCR** and need human review; do not present
them as verified facts. The backend also adds the extracted fields to the
application's `applicant_data` and adds a document summary.

Common failures: `404` application not found; `413` file exceeds 10 MB; `415`
unsupported extension/MIME combination; `422` empty or invalid form; `503`
OCR is unavailable/provider failure.

## Chat and checklist

### `POST /api/chat`

Requires a signed-in user. Body:

```json
{
  "message": "What documents do I need for a visitor visa?",
  "session_id": null,
  "application_id": null
}
```

Only `message` is required (1–10,000 characters). `session_id` and
`application_id` are optional. The first request can omit `session_id`; save
the returned `session_id` and send it on later messages. A chat session belongs
to the signed-in user. The optional application must also belong to that user.
When both IDs are supplied, the backend binds the chat session to that
application; a session already bound to another application cannot be reused.
For case UI, call `POST /api/applications/{application_id}/chat-session` when
opening the case and retain its returned `session_id`. That endpoint is
idempotent for a case and returns the same session on subsequent calls. Include
both IDs on every case chat request and use the returned session ID to load
`GET /api/chat/{session_id}/messages`.

Response:

```json
{
  "session_id": "chat-session-id",
  "reply": "You will generally need ...",
  "sources": [
    {
      "source_name": "travel_data.txt",
      "source_type": "pgvector",
      "document_id": "document-uuid",
      "chunk_id": "chunk-uuid",
      "chunk_index": 2,
      "distance": 0.18,
      "version_id": "uk-standard-visitor-v1-2026-10",
      "knowledge_entry_id": "uk-standard-visitor",
      "verification_status": "verified",
      "verified_source": "UK Government (GOV.UK)"
    }
  ],
  "actions": [
    {
      "name": "generate_checklist",
      "status": "completed",
      "result": { "visa_type": "uk_standard_visitor", "items": [] },
      "error": null
    }
  ],
  "extracted_intent": {},
  "proposed_intake": {
    "destination": "United Kingdom",
    "travel_purpose": "Business meetings"
  },
  "stage": "intake"
}
```

The model's own source suggestions are discarded. `sources` contains only
backend-returned pgvector chunks or successfully fetched approved trusted
pages. Citation fields such as `document_id`, `chunk_id`, `version_id`, and
verification metadata are optional when the source does not provide them.
Render the source type and verification status accurately; an absent
verification status does not mean verified.

The backend permits at most two action executions per turn, counting its
automatic pgvector search and any low-confidence trusted-web fallback. It also
accepts at most one additional model-proposed action, which may be marked
`skipped` if the automatic operations consumed the budget. Available action names are:
`search_policy`, `search_trusted_web`, `generate_checklist`, and
`evaluate_application`. Action results explicitly report `completed`, `failed`,
or `skipped`; never treat a failed action as successful. Audits require an
owned application selected in the chat request. OCR is not a chat action:
upload files through `/api/documents/extract`.

`proposed_intake` contains only candidate facts the model extracted from the
user's message. When an application is selected, unconfirmed facts are saved
under `applicant_data.proposed_intake`; they are not trusted audit inputs. Ask
the user to confirm a fact before sending it to the intake PATCH endpoint.

The backend persists user/assistant turns and uses up to
`CHAT_HISTORY_LIMIT` previous messages (12 by default) as context. It retrieves
policy context and may use an approved web fallback. If the LLM is unavailable,
the endpoint returns `503`; failed LLM turns are not saved. Do not display a
locally fabricated successful chat response on HTTP errors.

For case-specific chat, send `application_id` on each turn. The backend checks
ownership and injects that application's saved profile and recent document
summaries into model context. User profile data stays in relational storage
and is not added to the shared vector knowledge base. Keep `session_id` and
`application_id` in account/application-scoped UI state; clear them on logout
or account switch. Chat replies are JSON request/response, not streamed tokens.
Existing chat sessions remain general-purpose until first used with an
application. Their history is retained; the first valid case chat binds that
session to the selected application. A case-bound session cannot be used as
general chat or attached to a different case.

### `GET /api/chat/{session_id}/messages`

Requires a signed-in user. Returns the session's messages oldest first:

```json
[
  {
    "role": "user",
    "content": "What documents do I need?",
    "created_at": "2026-10-24T02:30:00Z"
  },
  {
    "role": "assistant",
    "content": "You will generally need ...",
    "created_at": "2026-10-24T02:30:03Z"
  }
]
```

Returns `404` if the session does not exist or belongs to someone else.
Persist conversation IDs in account-scoped UI state and clear them on logout
or account switch.

### `GET /api/checklist`

Public endpoint (no session cookie required). Query parameters:

| Parameter | Required | Meaning |
|---|---|---|
| `visa_type` | yes | Visa identifier, e.g. `uk_skilled_worker` |
| `session_id` | no | Echoed in response; defaults to `anonymous` |

Returns `{ "session_id": "...", "visa_type": "...", "items": [...],
"generated_from_rag": true }`. Each item has `id`, `label`, `description`,
`status` (initially `pending`), `required`, and optional `source_policy`.
`404` means no relevant policy documents were found; `503` means checklist
generation failed. The optional `session_id` is not an authentication token
and does not create a chat session.

## Audit endpoints

Both audit routes require authentication and an allowed origin.

### `POST /api/audit`

Legacy/chat-session audit. The `session_id` must identify a chat session owned
by the current user:

```json
{
  "session_id": "chat-session-id",
  "applicant_profile": {},
  "visa_type": "uk_skilled_worker"
}
```

Returns `session_id`, `visa_type`, `overall_passed`, `issues`, `summary`, and
`retrieved_policy_chunks`. Each issue contains `rule`, `passed`, `detail`,
`severity`, and optional `source`. Returns `404` for an unavailable/foreign
chat session and `503` if audit evaluation fails.

### `POST /api/audit/evaluate`

Graph-based application audit. Body:

```json
{
  "applicant_data": {},
  "visa_type": "uk_skilled_worker",
  "application_id": "application-uuid"
}
```

`applicant_data` defaults to `{}`; `visa_type` is required (1–100 characters);
`application_id` is optional. When supplied, the application must belong to
the current user, and its saved applicant data takes precedence over
same-named client-supplied fields. The response is an audit-state object with
`applicant_data`, `visa_type`, `retrieved_rules`, `calculated_metrics`,
`discrepancies`, `is_valid`, `status`, `retry_count`, `planned_checks`, and
`assumptions`. When an application is supplied, the result is saved as
`latest_audit` and its stage is set to `awaiting_approval` or
`discrepancy_found`.

**Important product limitation:** the current graph contains fixed demo
assumptions for an NGN/GBP exchange rate and a London funds threshold. Its
response explicitly includes this caveat. Do not present this audit as
authoritative immigration/legal advice. OCR data is also unverified until a
human reviews it. There is no separate review approval endpoint or
configurable per-visa threshold service yet. Surface the returned
`assumptions` and label the audit as a human-review aid, not a visa decision.

## Staff and operational routes

Account signup always creates a `client`. Provision `staff` and `admin` roles
through a trusted operator/database process; never expose role changes through
public signup or the client portal. The portal routes signed-in staff/admin
accounts to the agency review queue and clients to their own application list.

The backend applies a staff/admin role check to the `/api/staff/*`,
`/api/rules/*`, and
`/api/knowledge/*` routes. A staff account is required; normal clients receive
`403`. Do not expose these controls in a client portal.

Agency case review endpoints:

| Method and route | Purpose / input |
|---|---|
| `GET /api/staff/cases` | Staff see assigned cases; admins see all cases |
| `GET /api/staff/cases/{application_id}` | Get a case only if assigned to the staff member; admins can access all cases |
| `GET /api/staff/users` | Admin-only list of active staff available for assignment |
| `PATCH /api/staff/cases/{application_id}/assignment` | Admin-only JSON `{ "assigned_staff_id": "<staff-uuid-or-null>", "notes": "optional" }` |
| `POST /api/staff/cases/{application_id}/reviews` | Save case or document review activity |

Case-review body:

```json
{
  "review_type": "case_review",
  "outcome": "needs_changes",
  "notes": "Please clarify the travel purpose."
}
```

Case outcomes are `needs_changes`, `ready_for_agency_handoff`, and
`needs_further_review`. Document review uses `review_type: "document_review"`,
`document_id`, and outcome `reviewed` or `needs_clarification`. Staff review
creates a separate persisted activity; it does not change applicant OCR
confirmation, document `review_status`, the automated audit, or the visa
decision. A staff member can access only cases assigned to them; admins can
assign/unassign cases and access all cases. Assignment and review events are
returned in the case activity list.

| Method and route | Purpose / input |
|---|---|
| `POST /api/rules/extract` | JSON `{ "raw_text": "...", "visa_type": "...", "source_name": "..." }`; extracts and saves rule drafts |
| `GET /api/rules/drafts` | Optional `needs_input_only=true`; returns drafts, total, and needs_input |
| `POST /api/rules/confirm/{rule_id}` | JSON confirmation; URL and body `rule_id` must match |
| `GET /api/rules/active` | Optional `visa_type` filter; returns rules, total, and filter |
| `DELETE /api/rules/active/{rule_id}` | Deactivates a rule; returns status |
| `POST /api/knowledge/ingest` | JSON `{ "source_name": "...", "doc_type": "policy", "business_id": "default", "text": "..." }` |
| `POST /api/knowledge/ingest/file` | Multipart: `file` (PDF/DOCX/TXT/MD), `source_name`, optional `doc_type`, `business_id` |
| `GET /api/knowledge/stats` | Returns vector-store statistics |

`POST /api/rules/extract` responds with `rules`, `total_extracted`,
`complete`, and `needs_operator_input`. Drafts contain fields such as
`rule_id`, `visa_type`, `category`, `condition_summary`, `target_field`,
`operator`, threshold/period fields, `is_complete`, `missing_fields`,
`operator_question`, and `raw_excerpt`. Confirmation accepts `rule_id`,
`operator_answer`, optional confirmed threshold/unit/holding-period values,
`operator_notes`, and `confirmed_by`; its response contains `rule_id`,
`status`, `active_rule`, and `message`.

The `/api/trusted-sources/*` and `/api/audit-tickets/*` routes are protected by
the backend's staff/admin dependency, as are `/api/rules/*` and
`/api/knowledge/*`. A client account receives `403`. Keep these controls in
authorized staff/admin screens only; hiding buttons in the browser is not an
access-control boundary. Their routes are:

| Method and route | Purpose / input |
|---|---|
| `GET /api/trusted-sources` | Optional `country`, `only_active` (defaults true) |
| `POST /api/trusted-sources` | Create a source using `domain`, `name`, `base_url`, optional jurisdiction/category/trust settings |
| `PATCH /api/trusted-sources/{source_id}/toggle-active` | Toggle source active state |
| `GET /api/audit-tickets` | Optional `status` filter |
| `POST /api/audit-tickets/{ticket_id}/approve` | Approve and ingest ticket; optional `approved_by` query |
| `POST /api/audit-tickets/{ticket_id}/reject` | Reject ticket |

`GET /api/health` (also `/health`) is an operational health check and does not
require login. It returns service/environment and vector-store status. A
healthy response does not guarantee every external provider or authenticated
feature is available.

There is no staff event stream or WebSocket currently registered. Use
request/response loading states and refresh the relevant list after each
successful staff mutation.

## Errors and frontend behavior

FastAPI validation errors use `422` with a `detail` value (often a list of
field errors). Other common statuses:

| Status | Typical meaning | Frontend response |
|---|---|---|
| `400` | Invalid operation (e.g. duplicate trusted domain or mismatched rule ID) | Show a useful message; do not retry unchanged |
| `401` | Missing/invalid/expired session, inactive account, or invalid login | On protected calls, clear stale signed-in state and ask user to sign in |
| `403` | Disallowed `Origin` or insufficient staff role | Show access/configuration error; do not retry unchanged |
| `404` | Missing resource, resource not owned by this user, or no checklist policy | Explain what is unavailable without leaking another user's data |
| `409` | Signup email already exists | Offer sign-in instead |
| `413` / `415` | File too large / unsupported file type or MIME | Explain upload constraints |
| `422` | Invalid body, query, or form fields | Display field-level validation where possible |
| `500` / `503` | Persistence, provider, LLM, or service failure | Show retryable error where appropriate; do not create success-shaped fallback data |

Most API failures return JSON such as `{ "detail": "..." }`; CORS or browser
cookie-policy failures may prevent JavaScript from reading any response. Log
diagnostic status and request ID if available, but never log passwords,
session cookies, or uploaded document contents.

## Integration smoke test

1. Check `/api/health`.
2. Sign up or log in with credentials enabled.
3. In browser developer tools, confirm the login response sets
   `travelforge_session`, then call `/api/auth/me` and confirm the user is
   returned.
4. Create an application; upload an allowed document and confirm the returned
   extracted values are labelled unverified/pending review.
5. Send chat without a `session_id`, retain the returned ID, send another
   message with that ID, and load `/api/chat/{session_id}/messages`.
6. Call an audit endpoint, then load the application and verify its latest
   audit if the graph-based endpoint was given an `application_id`.
7. Log out; `/api/auth/me` and protected endpoints should now return `401`.

If login returns `200` but the next protected call returns `401`, inspect the
browser Network panel: verify the login response's `Set-Cookie`, cookie
storage, and the next request's `Cookie` header. Confirm
`withCredentials: true`/`credentials: "include"`, the exact deployed
`CORS_ORIGIN`, HTTPS, and browser third-party-cookie restrictions.
