# TravelForge Hackathon Plan

## Project goal

Build a focused UK visa case-preparation portal for a travel agency. The
prototype should show how agency staff and applicants can work through one
case: collect applicant information, organize documents, run guarded AI and
deterministic checks, review issues, and prepare for a manual handoff to the
official application service.

The demo should make the boundary clear:

- **Gemma 4** interprets natural language and explains results.
- **Deterministic Python checks** evaluate facts against approved, structured
  rules.
- **LangGraph** coordinates the workflow and passes validated data between
  specialist nodes.
- **A human reviewer** remains responsible for confirming extracted documents
  and approving policy changes.

The target is a working prototype that can be demonstrated reliably in a
two-minute video. Prioritize a complete, explainable agency workflow over
broad visa coverage or production-grade agency operations.

## Current starting point

This is an integration plan, not a greenfield rebuild. The backend already has:

- PostgreSQL-backed applications, chat history, documents, and rule storage.
- Gemini-based passport and bank-statement OCR with human review.
- A provider factory and a configurable LLM gateway.
- pgvector policy retrieval and a trusted-domain live web fallback.
- LangGraph chat orchestration, audit orchestration, and a deterministic
  active-rule evaluator.
- Rule drafts, approval endpoints, and audit tickets.

The chat graph now sequences the configured model response, validated backend
actions, and optional post-action synthesis. The provider/model remains
environment-configurable; set it to the desired provider/model before the
demo. The backend now also has a lightweight same-origin case workspace for
account access, owned application lists, case-bound chat, document review, and
internal audits. A separate staff/admin queue now supports assigned-case
visibility, administrator assignment, and persisted case/document review
activity. The remaining work is to validate the migrations and complete
applicant-to-staff flow against a real database and configured model. Staff
and admin accounts must be provisioned by a trusted operator; public signup
creates client accounts only. Do not recreate existing services or add
microservices for the demo.

## Frontend and case identity

Use a small browser frontend built with plain HTML, CSS, and JavaScript that
consumes the FastAPI endpoints. Prefer serving the static assets from the
backend for the hackathon so the UI and API can share one origin, session
cookie, and deployment. Use JavaScript for the dynamic chat and case workspace;
Jinja templates are optional for the initial page shell, not a requirement.

The **application ID is the case identity**. The chat session belongs to that
case and provides the conversation identity:

```text
Application (case) 1 ──────── many ChatSessions
       |                              |
       |                              └── ChatMessages
       ├── Applicant profile/intake
       ├── Uploaded documents
       ├── Audit results
       ├── Case status/activity
       └── Submission and travel milestones
```

For every case-bound chat request, the frontend sends both `application_id`
and the active `session_id`. On opening a case it obtains that case's session
and loads its saved conversation. The backend persists the application link
and validates both application and session ownership. Do not treat a client-
supplied application ID alone as authorization.

The initial interface has three simple views:

1. **Case list:** cases owned by the signed-in user, visa route, current stage,
   and next required task.
2. **Case workspace:** applicant facts and confirmations, documents/review
   state, route-rule findings and sources, case milestones/activity, and the
   linked conversation.
3. **Chat panel:** saved conversation history, source citations, and agent
   responses. Typed dynamic chat prompts/actions are a later enhancement.

Chat-generated dynamic UI must use a typed, allowlisted response shape—such as
single-choice buttons, confirmation cards, or a request for one missing field.
Render text as text, never inject model-produced HTML or JavaScript. Selecting
a choice sends a normal validated API request; consequential operations (for
example confirming extracted facts or recording a case milestone) require
explicit user confirmation. Unknown component/action types are displayed as
plain text or rejected; they must never execute arbitrary client or server
code.

## Agentic workflow

```text
Applicant text / supported document
                 |
                 v
       Intake extraction node
    Gemma 4 -> validated applicant facts
                 |
                 v
      Document / route router
          /            \
         v              v
 Passport specialist   Financial specialist
 deterministic checks  deterministic checks
          \            /
           v          v
      Rule result aggregator
                 |
                 v
       Remediation node (Gemma 4)
                 |
                 v
 Chat response with evidence, status, and next steps
```

### End-to-end agency case workflow

```text
New enquiry / case created
          |
          v
Applicant intake, route selection, and consent
          |
          v
Application ID anchors case; linked chat session carries conversation
          |
          v
Document upload -> OCR extraction (unverified)
          |
          v
Specialist checks + route-scoped approved-rule evaluation
          |
          v
Caseworker review -> applicant confirms answers/declarations
          |
          v
Manual handoff to official application service
          |
          v
Appointment completed; applicant/caseworker records receipt/reference details
          |
          v
Await decision; record status updates supplied by the applicant/caseworker
          |
          +---- refused / more information requested -> caseworker follow-up
          |
          +---- granted -> check current travel/status instructions
                              |
                              v
                    Travel preparation and bookings
```

### What the portal automates

- Organize an agency case, applicant profile, task checklist, assigned/review
  status, and case activity timeline.
- Extract facts from text and supported uploads; surface missing fields,
  mismatches, and source-backed rule results for human review.
- Draft applicant-facing questions, preparation instructions, and a grounded
  remediation checklist.
- Track the manual handoff, appointment, reference/receipt information, and
  decision progress when those updates are entered by the applicant or staff.
- After a recorded grant, provide a travel-preparation checklist and help
  compare itinerary, flight, accommodation, and transfer options. Actual
  reservations require explicit user confirmation and an authorized booking
  integration or a handoff to agency staff.

The portal should not assume every applicant receives the same appointment
receipt, reference format, tracker access, or visa document. Store only details
needed for the case and let the user provide them; show official tracker links
only when verified for that process. Post-visa steps should refer travelers to
current official instructions for their specific visa/status rather than
assuming one eVisa or share-code flow.

For the hackathon UI, represent the agency side with a small case workspace:
case stage, assigned/review-needed state, missing items, source-backed audit
results, applicant confirmations, and a chronological activity timeline. Keep
the visa decision and official submission separate from internal checks.

### Submission and account boundary

- TravelForge prepares and tracks a case; the official service remains where
  the application is submitted.
- Do not assume a GOV.UK API exists, scrape its application portal, or automate
  form submission.
- Never ask for, store, or proxy GOV.UK passwords. Credentials are entered only
  on the official service.
- Provide official links, preparation checklists, and manual status steps such
  as `ready_to_submit`, `submitted`, `appointment_booked`,
  `awaiting_decision`, and `decision_recorded`.
- The applicant reviews application answers and gives any required declaration
  or consent. Agency submission support must follow the current official
  representative process and be verified before implementation.
- Distinguish internal checks passing from `ready_to_submit`, actual
  `submitted`, and a visa decision. TravelForge cannot guarantee or grant
  approval.
- Do not say a flight, hotel, appointment, or service is booked unless the
  booking actually completed through an authorized integration or staff
  recorded its confirmation.

### Specialist responsibilities

1. **Intake specialist (Gemma 4)** extracts only facts stated by the applicant
   into a Pydantic-validated structure. It must preserve missing/uncertain
   values rather than inventing them.
2. **Document specialists** consume OCR output for supported document types.
   The passport specialist checks passport-related fields; the financial
   specialist checks financial evidence. These checks are deterministic tools,
   not separate LLMs. OCR data remains unverified until a person reviews it.
3. **Route-rule evaluator** loads the active rules for the exact `visa_type`
   and evaluates supported checks. It returns `passed`, `failed`,
   `not_provided`, or `needs_review`, including the rule and source.
4. **Remediation specialist (Gemma 4)** explains the evaluator's results and
   suggests next steps using only those results and supplied evidence. It
   cannot change rules, mark a document verified, or claim visa approval.
5. **Chat coordinator** presents the result, asks for missing facts, and
   routes permitted actions. Backend validation—not model instructions—decides
   which tools can run.

TB screening and other route checks can join the same rule workflow only when
their applicability and rule data have been verified and represented in the
active rule set.

## Rule and evidence policy

- Keep rules scoped to a specific route, initially `uk_skilled_worker`.
- Fetch all active rules for that `visa_type` in one query; do not make a
  separate database lookup per requirement.
- Store flexible rule data in PostgreSQL JSONB, but validate it through typed
  Pydantic schemas and a deliberately small set of deterministic operators.
- Keep source name, official URL, section, version, effective dates, and
  approver with each approved rule.
- Never infer legal requirements from a demo JSON file or an LLM response.
- Do not seed or activate UK Skilled Worker values until each is checked
  against current official UK guidance and its source is recorded.
- If an active rule is missing, applicant data is missing, OCR data is
  unreviewed, or a rule cannot be safely evaluated, return `NEEDS_REVIEW`.
- RAG and live trusted-site retrieval provide citations and context. They do
  not decide deterministic pass/fail results.
- Scraped changes are proposals only. No scraped content may automatically
  update or activate a rule.

If verified policy data is not ready for the hackathon, use clearly labelled
synthetic fixtures to demonstrate the evaluator. Do not present synthetic
thresholds or outcomes as actual immigration guidance.

## Three-hour execution plan

### Before the sprint

- Confirm API keys and connectivity for the intended Gemma 4 provider/model.
- Confirm the demo can run locally from a clean setup.
- Confirm the FastAPI app can serve the plain HTML/CSS/JavaScript demo and its
  API from one origin; keep the UI to the case list, one case workspace, and
  one chat panel.
- Identify official sources for any policy facts shown as real requirements.
- Prepare two applicant examples: one that produces a review/failure outcome
  and one that passes the demonstrated checks.

### Session 1 — backend and agent workflow (0:00–1:30)

| Time | Work | Completion check |
|---|---|---|
| 0:00–0:15 | Verify Gemma 4 provider/model, environment variables, and a real completion. | A short prompt returns successfully; model/provider are recorded for disclosure. |
| 0:15–0:35 | Finalize typed case/workflow state, intake output, and durable application-to-chat-session association. | Natural-language input becomes validated case facts; every chat request is bound to the selected owned application. |
| 0:35–1:00 | Connect LangGraph routing to passport and financial deterministic tools and route-scoped active-rule lookup. | Specialists return structured results with rule/source IDs; unreviewed OCR stays flagged. |
| 1:00–1:20 | Add result aggregation, grounded remediation, safe structured chat choices, and manual-submission readiness. | UI choices trigger only validated endpoints; portal never claims to submit. |
| 1:20–1:30 | Run focused tests for pass, fail, missing data, unreviewed OCR, and handoff status. | Results and status transitions are deterministic and visible. |

### Break and checkpoint (1:30–1:40)

- Run the end-to-end workflow once.
- If integration is behind, reduce scope: keep text intake and deterministic
  checks; do not add another agent, document type, or provider.

### Session 2 — UI, integration, and freeze (1:40–3:00)

| Time | Work | Completion check |
|---|---|---|
| 1:40–2:10 | Build a plain HTML/CSS/JS case list and workspace consuming the API. | User can open a case and see facts, missing items, review state, and its linked chat. |
| 2:10–2:30 | Render agent choices/confirmations with allowlisted components; show evidence and manual handoff checklist. | Status cards distinguish checks, human review, and submission progress; no model HTML is executed. |
| 2:30–2:45 | End-to-end test failing and passing examples, including applicant confirmation and manual handoff state. | Both paths are clear; no credentials or automated portal submission. |
| 2:45–3:00 | Freeze, verify secrets are excluded, capture screenshots, and record the demo. | Setup notes and the two-minute agency-workflow demo are ready. |

## Demo flow (two minutes)

| Time | Show |
|---|---|
| 0:00–0:20 | Show the case list and open one application; explain that this case has its own linked chat session. |
| 0:20–0:45 | Enter applicant details; show Gemma/OCR extraction and a safe choice/confirmation control in chat. |
| 0:45–1:15 | Run the LangGraph workflow; show passport/financial specialist results, rule sources, and a needs-review or failed item in the case workspace. |
| 1:15–1:40 | Show caseworker review, applicant confirmation, and a grounded remediation checklist. |
| 1:40–2:00 | Show manual official-service handoff and status tracking, then a clearly simulated post-decision travel checklist. State that passwords stay on GOV.UK and disclose Gemma + LangGraph + deterministic checks. |

Use synthetic fixtures if policy values have not been independently verified;
label them as demo data on screen and in the README.

## Acceptance criteria

- A natural-language applicant message can be converted to validated structured
  facts by the configured Gemma 4 model.
- The LangGraph workflow routes supported document/rule checks to specialist
  deterministic functions and aggregates their outputs.
- An agency user can view the case status, findings, missing items, and review
  progress in one workspace.
- Staff can list and open only cases assigned to them; admins can assign and
  reassign cases to active staff accounts.
- Agency case and document reviews are persisted as activity and remain
  separate from applicant confirmation, OCR status, automated audit results,
  and official visa decisions.
- The case workspace sends the selected application ID and its associated chat
  session ID together; the backend verifies ownership and their association.
- Dynamic chat UI uses a typed allowlist of controls and never executes model-
  generated markup or scripts.
- Audit results include rule identifiers and source provenance.
- Missing facts, unreviewed OCR, absent active rules, and unsupported
  conditions never appear as a successful pass.
- Remediation text is grounded in the structured results and does not invent
  legal thresholds, assert that a visa is approved, or alter the database.
- The case can be marked ready for manual official-service handoff, but the
  prototype neither collects official-site passwords nor claims to submit an
  application.
- A recorded positive decision can reveal a travel-preparation checklist; the
  prototype does not purchase or reserve flights, hotels, or transfers.
- One passing and one failing or needs-review flow work end to end.
- A two-minute video and setup/run instructions are ready for submission.

## Scope boundaries

### In scope for the hackathon

- One UK route only.
- An agency-oriented case workspace, not a full agency ERP or CRM.
- Plain HTML/CSS/JavaScript served with or alongside FastAPI, consuming the
  existing APIs; no new frontend framework required for the prototype.
- One active case conversation linked to its application, with a durable
  backend association.
- A small set of typed dynamic chat controls (choice, confirmation, missing-
  field prompt).
- Gemma 4 for intake and remediation language tasks.
- LangGraph orchestration with typed node inputs/outputs.
- Deterministic rule tools for the limited checks available in the route rule
  set.
- The current supported passport/bank-statement path, if it is stable in the
  demo environment.
- A small, clear UI and a reliable end-to-end video.
- Manual handoff and status tracking for official submission.
- A post-decision travel checklist and itinerary-planning view using demo data.

### Not in scope for the three-hour demo

- Multiple deployed services or a broad multi-country product.
- Autonomous legal decisions, automatic document verification, or unrestricted
  web browsing.
- GOV.UK/VFS scraping or automated application submission, credential
  collection, or claims of an official API integration.
- Full appointment booking, payment processing, caseworker workload
  management, or agency billing.
- A generalized agent-generated UI/component framework.
- Live flight/hotel inventory, payment, ticket issuance, or accommodation
  reservation integrations.
- Scraping on every chat turn or automatically changing active rules.
- A full admin dashboard, comprehensive rule version-management UI, or
  automatic rule inheritance across visa routes.
- Adding OCR support for every document type.
- Unverified live policy thresholds presented as authoritative.

## Post-hackathon roadmap

1. **Verified UK Skilled Worker rule set:** verify a small route-specific rule
   set against current official UK sources, record provenance and approval, and
   test expected audit outcomes. This is intentionally deferred until policy
   verification is ready.
2. **Admin review dashboard:** let authorized reviewers inspect drafts and
   source evidence, approve/reject proposals, and manage rule versions while
   preserving audit history.
3. **Trusted-source change monitor:** periodically compare approved official
   pages, create review tickets for detected changes, and optionally draft
   suggested rule updates. A human must approve changes before activation.
4. **Agency operations:** extend the initial staff queue with secure applicant
   messaging, consent records, richer assignment controls, and a fuller
   case timeline.
5. **Travel booking integrations:** after a positive outcome, connect authorized
   flight, lodging, and transfer providers. Show live availability, total price,
   cancellation terms, and require explicit traveler confirmation before any
   purchase. Keep booking references and confirmations in the case timeline.
6. **Broader route coverage:** add more visa routes only after the UK route
   workflow and review process are reliable.

## Rubric alignment

| Rubric area | Evidence in the demo |
|---|---|
| Technology | Gemma 4, LangGraph orchestration, typed Pydantic state, and deterministic rule tools. |
| Design | Focused intake experience, clear status cards, source visibility, and actionable remediation. |
| Completion | End-to-end passing and failing/review flows, setup instructions, and a polished video. |
| Learning | Explicit AI disclosure, tested guardrails, and a clear separation between extraction, evaluation, and advice. |

## Submission checklist

- [ ] README states problem, solution, team/roles, setup, tests, and credits.
- [ ] README discloses the exact Gemma 4 model/provider actually used.
- [ ] Any illustrative policy values are either officially verified with citations
      or visibly labelled synthetic demo data.
- [ ] No API keys or personal applicant documents are committed.
- [ ] Backend tests and both end-to-end demo paths pass.
- [ ] Two-minute demo video is recorded and links to the repository.