"use strict";

const API = "/api";
const FACT_LABELS = {
  citizenship: "Citizenship",
  destination: "Destination",
  travel_purpose: "Travel purpose",
  intended_travel_date: "Intended travel date",
  employer: "Employer",
  visa_type: "Visa type",
};

const state = {
  user: null,
  applications: [],
  application: null,
  sessionId: null,
  messages: [],
  staffCases: [],
  staffCase: null,
  staffUsers: [],
  staffFilter: "all",
  authMode: "login",
  busy: false,
  sending: false,
  view: "cases",
  intakeEdits: {},
  documentEdits: {},
  toastTimer: null,
};

const VIEW_IDS = {
  cases: "view-cases",
  create: "view-create",
  case: "view-case",
  staff: "view-staff",
  "staff-case": "view-staff-case",
};

function $(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = $(id);
  if (node) node.textContent = value == null ? "" : String(value);
}

function setHidden(id, hidden) {
  const node = $(id);
  if (node) node.hidden = Boolean(hidden);
}

function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

function cloneTemplate(id) {
  const template = $(id);
  if (!template || !(template instanceof HTMLTemplateElement)) {
    throw new Error(`Missing template: ${id}`);
  }
  return template.content.firstElementChild.cloneNode(true);
}

function field(node, name) {
  return node.querySelector(`[data-field="${name}"]`);
}

function setField(node, name, value) {
  const target = field(node, name);
  if (target) target.textContent = value == null ? "" : String(value);
  return target;
}

function humanize(value) {
  return String(value || "intake")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusClass(value) {
  const status = String(value || "").toLowerCase();
  if (status.includes("discrepancy")) return "status-red";
  if (status.includes("review") || status.includes("await")) return "status-amber";
  if (status && status !== "intake") return "status-green";
  return "";
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : new Intl.DateTimeFormat(undefined, {
        hour: "numeric",
        minute: "2-digit",
      }).format(date);
}

function formatValue(value) {
  if (value == null) return "—";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch (_error) {
      return String(value);
    }
  }
  return String(value);
}

function isStaffUser() {
  return state.user?.user_type === "staff" || state.user?.user_type === "admin";
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const isForm = options.body instanceof FormData;
  if (options.body && !isForm && typeof options.body !== "string") {
    headers.set("Content-Type", "application/json");
    options = { ...options, body: JSON.stringify(options.body) };
  }
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok) {
    const detail =
      typeof data?.detail === "string"
        ? data.detail
        : `Request failed (${response.status}).`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return data;
}

function notify(message) {
  const toast = $("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    toast.classList.remove("visible");
  }, 3300);
}

function showError(error) {
  const message = error?.message || String(error);
  setText("global-error", message);
  setHidden("global-error", false);
}

function clearError() {
  setText("global-error", "");
  setHidden("global-error", true);
}

function setBusy(busy) {
  state.busy = Boolean(busy);
  for (const id of [
    "auth-submit",
    "create-submit",
    "upload-button",
    "run-audit-button",
    "staff-assignment-save",
    "staff-review-save",
  ]) {
    const button = $(id);
    if (button) button.disabled = state.busy || state.sending;
  }
  const sendButton = $("chat-send-button");
  if (sendButton) sendButton.disabled = state.busy || state.sending;
}

function scrollChat() {
  const container = $("chat-messages");
  if (container) container.scrollTop = container.scrollHeight;
}

function updateTopbar() {
  let title = isStaffUser() ? "Agency review queue" : "My applications";
  if (state.view === "create") title = "Start an application";
  if (state.view === "case") title = humanize(state.application?.visa_type);
  if (state.view === "staff-case") title = "Review case";
  setText("page-title", title);
  const newButton = $("new-application-button");
  if (newButton) newButton.hidden = isStaffUser() || state.view === "create";
  setText("nav-applications-label", isStaffUser() ? "Review queue" : "My applications");
  const navCreate = $("nav-new-case");
  if (navCreate) navCreate.hidden = isStaffUser();
  const help = $("sidebar-help");
  if (help) help.hidden = isStaffUser();
}

function showView(view) {
  state.view = view;
  for (const viewId of Object.values(VIEW_IDS)) {
    setHidden(viewId, true);
  }
  setHidden(VIEW_IDS[view], false);
  updateTopbar();
}

function showAuth() {
  setHidden("portal", true);
  setHidden("auth-screen", false);
}

function showPortal() {
  setHidden("auth-screen", true);
  setHidden("portal", false);
}

function setAuthMode(mode) {
  state.authMode = mode;
  const signup = mode === "signup";
  setText("auth-title", signup ? "Create your account" : "Sign in to your account");
  setText(
    "auth-subtitle",
    signup
      ? "Create an account to start and manage your travel cases."
      : "Pick up where you left off with your travel case.",
  );
  setHidden("auth-name-field", !signup);
  $("auth-name").required = signup;
  $("auth-password").minLength = signup ? 12 : 1;
  $("auth-password").autocomplete = signup ? "new-password" : "current-password";
  setHidden("auth-password-help", !signup);
  setText("auth-submit", "Sign in");
  setText("auth-switch-label", signup ? "Already have an account?" : "New to TravelForge?");
  setText("auth-toggle", signup ? "Sign in" : "Create an account");
  setText("auth-error", "");
  showAuth();
}

function renderUser() {
  const user = state.user;
  setText("user-avatar", (user?.name || user?.email || "T").charAt(0).toUpperCase());
  setText("user-name", user?.name || "Traveler");
  setText("user-email", user?.email || "");
  updateTopbar();
}

async function init() {
  wireEvents();
  setAuthMode("login");
  try {
    const user = await request("/auth/me");
    await setAuthenticated(user);
  } catch (error) {
    state.user = null;
    showAuth();
    if (error.status !== 401) {
      setText("auth-error", `Could not connect to the service: ${error.message}`);
    }
  }
}

async function setAuthenticated(user) {
  state.user = user;
  setText("auth-error", "");
  clearError();
  renderUser();
  showPortal();
  if (isStaffUser()) {
    await loadStaffQueue();
  } else {
    await loadApplications();
  }
}

async function submitAuth(event) {
  event.preventDefault();
  if (state.busy) return;
  const signup = state.authMode === "signup";
  const name = $("auth-name").value.trim();
  const email = $("auth-email").value.trim();
  const password = $("auth-password").value;
  if (!email || !password || (signup && !name)) {
    setText("auth-error", "Complete all required fields.");
    return;
  }
  setText("auth-error", "");
  setBusy(true);
  try {
    const endpoint = signup ? "/auth/signup" : "/auth/login";
    const payload = signup ? { name, email, password } : { email, password };
    const user = await request(endpoint, { method: "POST", body: payload });
    $("auth-password").value = "";
    await setAuthenticated(user);
  } catch (error) {
    setText("auth-error", error.message);
  } finally {
    setBusy(false);
  }
}

async function toggleAuthMode() {
  const next = state.authMode === "login" ? "signup" : "login";
  $("auth-password").value = "";
  setAuthMode(next);
}

async function logout() {
  try {
    await request("/auth/logout", { method: "POST" });
  } catch (error) {
    if (error.status !== 401) notify(error.message);
  } finally {
    state.user = null;
    state.applications = [];
    state.application = null;
    state.staffCase = null;
    state.staffCases = [];
    state.sessionId = null;
    state.messages = [];
    state.intakeEdits = {};
    state.documentEdits = {};
    $("auth-name").value = "";
    $("auth-email").value = "";
    $("auth-password").value = "";
    setAuthMode("login");
  }
}

async function loadApplications() {
  clearError();
  try {
    state.applications = await request("/applications");
    state.application = null;
    state.sessionId = null;
    state.messages = [];
    renderCases();
    showView("cases");
  } catch (error) {
    showError(error);
  }
}

function renderCases() {
  const apps = state.applications;
  setHidden("cases-empty", apps.length > 0);
  setHidden("cases-content", apps.length === 0);
  setText("case-count", `${apps.length} ${apps.length === 1 ? "case" : "cases"}`);
  const grid = $("case-grid");
  grid.replaceChildren();
  for (const application of apps) {
    const card = cloneTemplate("tpl-case-card");
    const status = field(card, "status");
    status.textContent = humanize(application.stage);
    status.className = `status-pill ${statusClass(application.stage)}`.trim();
    setField(card, "title", humanize(application.visa_type));
    setField(card, "destination", application.destination || "Destination not set");
    const reviewItems = application.latest_audit?.discrepancies?.length || 0;
    const documentCount = application.documents?.length || 0;
    setField(
      card,
      "meta",
      `${documentCount} documents${reviewItems ? ` · ${reviewItems} review items` : ""}`,
    );
    card.querySelector('[data-action="open"]').addEventListener("click", () => {
      openApplication(application.id);
    });
    grid.append(card);
  }
}

function showCreate() {
  setText("create-error", "");
  showView("create");
  $("create-destination").focus();
}

async function submitCreate(event) {
  event.preventDefault();
  if (state.busy) return;
  let visaType = $("create-visa-type").value;
  if (visaType === "other") {
    visaType = window.prompt("Enter the visa route or type “not sure yet”:")?.trim();
    if (!visaType) return;
  }
  const destination = $("create-destination").value.trim();
  setText("create-error", "");
  setBusy(true);
  try {
    const application = await request("/applications", {
      method: "POST",
      body: { visa_type: visaType, destination: destination || null },
    });
    await openApplication(application.id);
    notify("Application created.");
  } catch (error) {
    setText("create-error", error.message);
  } finally {
    setBusy(false);
  }
}

function syncCaseEdits() {
  const application = state.application;
  state.intakeEdits = {
    ...(application?.applicant_data?.proposed_intake || {}),
  };
  state.documentEdits = Object.fromEntries(
    (application?.documents || []).map((document) => [
      document.id,
      { ...(document.extracted_data || {}) },
    ]),
  );
}

async function openApplication(applicationId) {
  clearError();
  try {
    const [application, chatSession] = await Promise.all([
      request(`/applications/${encodeURIComponent(applicationId)}`),
      request(`/applications/${encodeURIComponent(applicationId)}/chat-session`, {
        method: "POST",
      }),
    ]);
    state.application = application;
    state.sessionId = chatSession.session_id;
    state.messages = await request(
      `/chat/${encodeURIComponent(state.sessionId)}/messages`,
    );
    syncCaseEdits();
    renderCase();
    showView("case");
    scrollChat();
  } catch (error) {
    showError(error);
  }
}

async function refreshApplication() {
  if (!state.application) return;
  const applicationId = state.application.id;
  const [application, applications] = await Promise.all([
    request(`/applications/${encodeURIComponent(applicationId)}`),
    request("/applications"),
  ]);
  state.application = application;
  state.applications = applications;
  syncCaseEdits();
  renderCase();
}

function renderCase() {
  const application = state.application;
  if (!application) return;
  setText("case-title", humanize(application.visa_type));
  setText(
    "case-meta",
    `${application.destination || "Destination not set"} · Updated ${formatDate(application.updated_at)}`,
  );
  const status = $("case-status");
  status.textContent = humanize(application.stage);
  status.className = `status-pill ${statusClass(application.stage)}`.trim();
  setText("case-id", application.id);
  setText("case-stage", humanize(application.stage));
  setText("case-document-count", `${application.documents?.length || 0} on file`);
  setText("case-review-state", humanize(application.latest_audit?.status || "Not run"));

  renderFacts();
  renderClientDocuments();
  renderAudit();
  renderClientTimeline();
  renderChat();
}

function renderFacts() {
  const application = state.application;
  const intake = application?.applicant_data?.intake || {};
  const proposed = application?.applicant_data?.proposed_intake || {};
  const confirmedContainer = $("confirmed-facts");
  const proposedContainer = $("proposed-facts");
  confirmedContainer.replaceChildren();
  proposedContainer.replaceChildren();

  for (const [name, value] of Object.entries(intake)) {
    const row = cloneTemplate("tpl-confirmed-fact");
    setField(row, "label", humanize(name));
    setField(row, "value", value);
    confirmedContainer.append(row);
  }
  for (const [name, value] of Object.entries(proposed)) {
    const row = cloneTemplate("tpl-proposed-fact");
    setField(row, "label", humanize(name));
    const input = field(row, "input");
    input.value = state.intakeEdits[name] ?? value ?? "";
    input.addEventListener("input", () => {
      state.intakeEdits[name] = input.value;
    });
    row.querySelector('[data-action="confirm"]').addEventListener("click", () => {
      confirmFact(name);
    });
    proposedContainer.append(row);
  }
  setHidden(
    "facts-empty",
    Object.keys(intake).length > 0 || Object.keys(proposed).length > 0,
  );
}

async function confirmFact(name) {
  if (state.busy) return;
  const value = String(state.intakeEdits[name] ?? "").trim();
  if (!value) {
    notify("Add a value before confirming.");
    return;
  }
  setBusy(true);
  try {
    await request(`/applications/${encodeURIComponent(state.application.id)}/intake`, {
      method: "PATCH",
      body: { facts: { [name]: value }, confirm_fields: [name] },
    });
    await refreshApplication();
    notify(`${FACT_LABELS[name] || humanize(name)} confirmed.`);
  } catch (error) {
    notify(error.message);
  } finally {
    setBusy(false);
  }
}

function renderDocumentFields(container, doc, editable) {
  container.replaceChildren();
  for (const [name, value] of Object.entries(doc.extracted_data || {})) {
    if (name === "document_id" || name === "verification_status") continue;
    const label = createElement("label");
    label.append(createElement("span", null, humanize(name)));
    const input = createElement("input");
    input.value = state.documentEdits[doc.id]?.[name] ?? value ?? "";
    input.disabled = !editable;
    input.addEventListener("input", () => {
      state.documentEdits[doc.id] = state.documentEdits[doc.id] || {};
      state.documentEdits[doc.id][name] = input.value;
    });
    label.append(input);
    container.append(label);
  }
}

function renderClientDocuments() {
  const application = state.application;
  const documents = application?.documents || [];
  const list = $("client-documents");
  list.replaceChildren();
  setText("document-count-pill", `${documents.length} saved`);
  setHidden("client-documents-empty", documents.length > 0);
  for (const document of documents) {
    const card = cloneTemplate("tpl-client-document");
    setField(card, "type", humanize(document.doc_type));
    setField(card, "filename", document.original_filename);
    setField(card, "created", formatDate(document.created_at));
    const status = field(card, "status");
    status.textContent = humanize(document.review_status);
    status.className =
      "status-pill " +
      (document.review_status === "pending_review"
        ? "status-amber"
        : document.review_status === "reviewed"
          ? "status-green"
          : "");
    const pending = document.review_status === "pending_review";
    renderDocumentFields(field(card, "fields"), document, pending);
    field(card, "actions").hidden = !pending;
    field(card, "help").hidden = pending;
    card.querySelector('[data-action="reject"]').addEventListener("click", () => {
      reviewDocument(document, "reject", card);
    });
    card.querySelector('[data-action="confirm"]').addEventListener("click", () => {
      reviewDocument(document, "confirm", card);
    });
    list.append(card);
  }
}

async function reviewDocument(document, decision, card) {
  if (state.busy) return;
  const buttons = [...card.querySelectorAll("button")];
  const confirmButton = card.querySelector('[data-action="confirm"]');
  const originalLabel = confirmButton.textContent;
  buttons.forEach((button) => (button.disabled = true));
  confirmButton.textContent = decision === "reject" ? "Rejecting…" : "Saving…";
  setBusy(true);
  try {
    let payload = { decision };
    if (decision === "confirm") {
      const edits = state.documentEdits[document.id] || {};
      const correctedData = {};
      let changed = false;
      for (const name of Object.keys(document.extracted_data || {})) {
        if (name === "document_id" || name === "verification_status") continue;
        const value = edits[name] ?? "";
        correctedData[name] =
          name === "closing_balance_ngn" || name === "statement_period_days"
            ? Number(value)
            : value;
        if (String(document.extracted_data[name] ?? "") !== String(value)) {
          changed = true;
        }
      }
      payload = changed
        ? { decision: "correct", corrected_data: correctedData }
        : { decision: "confirm" };
    }
    await request(`/documents/${encodeURIComponent(document.id)}/review`, {
      method: "POST",
      body: payload,
    });
    await refreshApplication();
    notify(decision === "reject" ? "Document rejected." : "Document review saved.");
  } catch (error) {
    notify(error.message);
  } finally {
    buttons.forEach((button) => (button.disabled = false));
    confirmButton.textContent = originalLabel;
    setBusy(false);
  }
}

async function uploadDocument(event) {
  event.preventDefault();
  if (state.busy) return;
  const fileInput = $("upload-file");
  if (!fileInput?.files?.length) return;
  const body = new FormData();
  body.append("application_id", state.application.id);
  body.append("doc_type", $("upload-doc-type").value);
  body.append("file", fileInput.files[0]);
  setBusy(true);
  try {
    await request("/documents/extract", { method: "POST", body });
    await refreshApplication();
    fileInput.value = "";
    notify("Document extracted. Review the suggested values.");
  } catch (error) {
    notify(error.message);
  } finally {
    setBusy(false);
  }
}

function auditItemDetail(item) {
  if (item.review_reason) return item.review_reason;
  if (item.detail) return item.detail;
  if (item.message) return item.message;
  const parts = [];
  if (item.expected != null) parts.push(`Expected: ${formatValue(item.expected)}`);
  if (item.actual != null) parts.push(`Actual: ${formatValue(item.actual)}`);
  return parts.join(" · ");
}

function appendAuditItems(container, audit) {
  const seen = new Set();
  const addItem = (title, detail) => {
    const key = `${title}|${detail}`;
    if (seen.has(key)) return;
    seen.add(key);
    const item = cloneTemplate("tpl-audit-item");
    setField(item, "title", title);
    setField(item, "detail", detail);
    container.append(item);
  };
  for (const item of audit?.discrepancies || []) {
    addItem(
      humanize(item.rule_id || item.field || "Review item"),
      auditItemDetail(item) || "Human review required.",
    );
  }
  for (const item of audit?.rule_results || []) {
    addItem(
      `${humanize(item.rule_id)} · ${humanize(item.status)}`,
      auditItemDetail(item) || item.source_text || "",
    );
  }
}

function renderAudit() {
  const audit = state.application?.latest_audit;
  setHidden("audit-empty", Boolean(audit));
  setHidden("audit-content", !audit);
  setHidden("audit-status", !audit);
  setText("run-audit-button", audit ? "Run review again" : "Run case review");
  if (!audit) return;

  const status = $("audit-status");
  status.textContent = humanize(audit.status);
  status.className = `status-pill ${statusClass(audit.status)}`.trim();
  setText("audit-date", formatDate(audit.calculated_metrics?.audit_date));
  const items = $("audit-items");
  items.replaceChildren();
  appendAuditItems(items, audit);

  const sources = $("audit-sources");
  sources.replaceChildren();
  for (const source of audit.retrieved_rules || []) {
    sources.append(
      createElement(
        "span",
        "source-chip",
        source.source_name || source.source || "Policy source",
      ),
    );
  }

  const assumptions = $("audit-assumptions");
  assumptions.replaceChildren();
  for (const assumption of audit.assumptions || []) {
    assumptions.append(createElement("div", null, assumption));
  }
}

async function runAudit() {
  if (state.busy || !state.application) return;
  setBusy(true);
  try {
    await request("/audit/evaluate", {
      method: "POST",
      body: {
        application_id: state.application.id,
        visa_type: state.application.visa_type,
        applicant_data: state.application.applicant_data || {},
      },
    });
    await refreshApplication();
    notify("Internal case review complete.");
  } catch (error) {
    notify(error.message);
  } finally {
    setBusy(false);
  }
}

function clientTimeline() {
  const application = state.application;
  if (!application) return [];
  const events = [
    {
      label: "Case created",
      date: application.created_at,
      detail: humanize(application.visa_type),
    },
  ];
  for (const document of application.documents || []) {
    events.push({
      label: `${humanize(document.doc_type)} uploaded`,
      date: document.created_at,
      detail: document.original_filename,
    });
    if (document.reviewed_at) {
      events.push({
        label: `${humanize(document.doc_type)} reviewed`,
        date: document.reviewed_at,
        detail: humanize(document.review_status),
      });
    }
  }
  if (application.latest_audit?.calculated_metrics?.audit_date) {
    events.push({
      label: "Preparation review completed",
      date: application.latest_audit.calculated_metrics.audit_date,
      detail: humanize(application.latest_audit.status),
    });
  }
  for (const message of state.messages.slice(-5)) {
    if (!message.created_at) continue;
    events.push({
      label: message.role === "user" ? "You sent a chat message" : "Case chat replied",
      date: message.created_at,
      detail: "",
    });
  }
  return events
    .filter((event) => event.date)
    .sort((left, right) => new Date(right.date) - new Date(left.date))
    .slice(0, 7);
}

function renderClientTimeline() {
  const container = $("client-timeline");
  container.replaceChildren();
  for (const event of clientTimeline()) {
    const item = cloneTemplate("tpl-timeline-item");
    setField(item, "label", event.label);
    const date = field(item, "date");
    date.textContent = formatDate(event.date);
    setField(item, "detail", event.detail ? ` · ${event.detail}` : "");
    container.append(item);
  }
}

function renderSources(container, sources) {
  if (!container || !Array.isArray(sources) || sources.length === 0) {
    if (container) container.hidden = true;
    return;
  }
  container.replaceChildren();
  container.hidden = false;
  for (const source of sources) {
    const item = cloneTemplate("tpl-chat-source");
    const link = field(item, "link");
    const badge = field(item, "badge");
    const name = field(item, "name");

    const isWeb = source.source_type === "trusted_web";
    badge.textContent = isWeb ? "GOV.UK" : "POLICY";
    name.textContent = source.source_name || "Official Policy";

    const url = source.url || "";
    if (url.startsWith("http://") || url.startsWith("https://")) {
      link.href = url;
      link.title = `Visit verified source: ${url}`;
    } else {
      link.removeAttribute("href");
      link.removeAttribute("target");
    }
    container.append(item);
  }
}

function renderControls(container, controls) {
  if (!container || !Array.isArray(controls) || controls.length === 0) {
    if (container) container.hidden = true;
    return;
  }
  container.replaceChildren();
  container.hidden = false;
  const ALLOWED_CONTROL_TYPES = new Set([
    "choice_list",
    "single_choice",
    "confirmation_card",
    "missing_field_prompt",
  ]);

  for (const control of controls) {
    if (!control || typeof control !== "object") continue;
    // PLAN.md: Unknown component/action types are displayed as plain text or rejected; they must never execute arbitrary client or server code.
    if (!ALLOWED_CONTROL_TYPES.has(control.type)) {
      if (control.title) {
        const fallback = createElement("p", "chat-unsupported-control", control.title);
        container.append(fallback);
      }
      continue;
    }

    if (control.type === "choice_list" || control.type === "single_choice") {
      const item = cloneTemplate("tpl-chat-control-choice");
      setField(item, "title", control.title || "Suggested Next Steps");
      const list = field(item, "options");
      const options = Array.isArray(control.options) ? control.options : [];
      for (const opt of options) {
        const btn = createElement("button", "chat-choice-btn", opt.label || opt.value);
        btn.type = "button";
        btn.addEventListener("click", () => handleChatChoice(opt, btn));
        list.append(btn);
      }
      if (options.length > 0) container.append(item);
    } else if (control.type === "confirmation_card") {
      const item = cloneTemplate("tpl-chat-control-confirmation");
      const fieldLabel = control.field_label || humanize(control.field_name);
      setField(item, "label", fieldLabel);
      setField(
        item,
        "desc",
        control.description || "Confirm this extracted fact for your case profile.",
      );
      setField(item, "key", fieldLabel);
      setField(item, "value", control.proposed_value || "—");

      const confirmBtn = item.querySelector('[data-action="confirm"]');
      const dismissBtn = item.querySelector('[data-action="dismiss"]');
      const card = item.querySelector(".confirmation-card");

      confirmBtn.addEventListener("click", () => {
        confirmFactFromChat(
          confirmBtn,
          control.field_name,
          control.proposed_value,
          card,
        );
      });
      dismissBtn.addEventListener("click", () => {
        card.remove();
      });
      container.append(item);
    } else if (control.type === "missing_field_prompt") {
      const item = cloneTemplate("tpl-chat-control-missing-field");
      const fieldLabel = control.field_label || humanize(control.field_name);
      setField(item, "label", fieldLabel);
      setField(
        item,
        "desc",
        control.description ||
          `Please provide your ${fieldLabel.toLowerCase()} for route evaluation.`,
      );
      const input = field(item, "input");
      if (control.placeholder) input.placeholder = control.placeholder;

      const submitBtn = item.querySelector('[data-action="submit"]');
      const card = item.querySelector(".missing-field-card");

      const doSubmit = () => {
        submitMissingFactFromChat(submitBtn, control.field_name, input, card);
      };

      submitBtn.addEventListener("click", doSubmit);
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          doSubmit();
        }
      });
      container.append(item);
    }
  }
}

async function handleChatChoice(option, button) {
  if (!option) return;
  if (option.action === "run_audit") {
    if (button) button.disabled = true;
    await runAudit();
    if (button) button.disabled = false;
  } else if (option.action === "scroll_to_documents") {
    const docSection =
      $("client-documents-panel") ||
      $("upload-form") ||
      $("client-documents");
    if (docSection) {
      docSection.scrollIntoView({ behavior: "smooth", block: "center" });
      docSection.classList.add("highlight-pulse");
      setTimeout(() => docSection.classList.remove("highlight-pulse"), 2500);
    }
    const fileInput = $("upload-file");
    if (fileInput) {
      fileInput.focus();
      try {
        fileInput.click();
      } catch (err) {
        // Ignored if browser restricts synthetic click
      }
    }
  } else {
    const input = $("chat-input");
    if (input) {
      input.value = option.value || option.label || "";
      await sendChat();
    }
  }
}

async function confirmFactFromChat(button, fieldName, value, card) {
  if (!state.application || !fieldName) return;
  button.disabled = true;
  button.textContent = "Confirming...";
  try {
    await request(`/applications/${encodeURIComponent(state.application.id)}/intake`, {
      method: "PATCH",
      body: {
        facts: { [fieldName]: value },
        confirm_fields: [fieldName],
      },
    });
    button.textContent = "Confirmed ✓";
    button.classList.add("confirmed");
    if (card) {
      card.classList.add("card-confirmed");
      const dismissBtn = card.querySelector('[data-action="dismiss"]');
      if (dismissBtn) dismissBtn.remove();
    }
    notify(`Confirmed ${humanize(fieldName)}: ${value}`);
    await refreshApplication();
  } catch (err) {
    button.disabled = false;
    button.textContent = "Confirm Fact ✓";
    showError(err);
  }
}

async function submitMissingFactFromChat(button, fieldName, input, card) {
  if (!state.application || !fieldName || !input) return;
  const value = input.value.trim();
  if (!value) {
    notify("Please enter a value before submitting.");
    input.focus();
    return;
  }
  button.disabled = true;
  input.disabled = true;
  button.textContent = "Saving...";
  try {
    await request(`/applications/${encodeURIComponent(state.application.id)}/intake`, {
      method: "PATCH",
      body: {
        facts: { [fieldName]: value },
        confirm_fields: [fieldName],
      },
    });
    button.textContent = "Saved ✓";
    if (card) card.classList.add("card-confirmed");
    notify(`Saved ${humanize(fieldName)}: ${value}`);
    await refreshApplication();
  } catch (err) {
    button.disabled = false;
    input.disabled = false;
    button.textContent = "Submit Fact";
    showError(err);
  }
}

function renderChat() {
  const container = $("chat-messages");
  if (!container) return;
  const welcome = $("chat-welcome");
  const sending = $("chat-sending");
  for (const node of container.querySelectorAll(".message")) node.remove();
  welcome.hidden = state.messages.length > 0;
  sending.hidden = !state.sending;
  const fragment = document.createDocumentFragment();
  for (const message of state.messages) {
    const item = cloneTemplate("tpl-chat-message");
    item.classList.add(message.role === "user" ? "user" : "assistant");
    setField(item, "content", message.content);
    const time = field(item, "time");
    const timeText = formatTime(message.created_at);
    time.textContent = timeText;
    time.hidden = !timeText;

    if (message.role === "assistant") {
      const sourcesContainer = field(item, "sources");
      renderSources(sourcesContainer, message.sources);

      const controlsContainer = field(item, "controls");
      renderControls(controlsContainer, message.controls);
    }
    fragment.append(item);
  }
  container.insertBefore(fragment, sending);
}

async function sendChat(event) {
  if (event) event.preventDefault();
  if (state.sending || !state.application || !state.sessionId) return;
  const input = $("chat-input");
  const message = input.value.trim();
  if (!message) return;
  state.sending = true;
  input.value = "";
  state.messages.push({
    id: `pending-${Date.now()}`,
    role: "user",
    content: message,
  });
  renderChat();
  scrollChat();
  setBusy(state.busy);
  try {
    const result = await request("/chat", {
      method: "POST",
      body: {
        message,
        session_id: state.sessionId,
        application_id: state.application.id,
      },
    });
    state.sessionId = result.session_id;
    state.messages = await request(
      `/chat/${encodeURIComponent(state.sessionId)}/messages`,
    );
    await refreshApplication();
  } catch (error) {
    state.messages = state.messages.filter(
      (item) => !(item.role === "user" && item.id?.startsWith("pending-")),
    );
    notify(`Message could not be sent: ${error.message}`);
  } finally {
    state.sending = false;
    renderChat();
    setBusy(state.busy);
    scrollChat();
  }
}

function filteredStaffCases() {
  return state.staffCases.filter((item) => {
    if (state.staffFilter === "unassigned") return !item.assigned_staff_id;
    if (state.staffFilter === "documents") return item.pending_document_count > 0;
    if (state.staffFilter === "audit") {
      return ["NEEDS_REVIEW", "DISCREPANCY_FOUND"].includes(item.audit_status);
    }
    return true;
  });
}

async function loadStaffQueue() {
  clearError();
  showView("staff");
  try {
    state.staffCases = await request("/staff/cases");
    state.staffUsers =
      state.user?.user_type === "admin" ? await request("/staff/users") : [];
    state.staffCase = null;
    renderStaffQueue();
  } catch (error) {
    showError(error);
  }
}

function renderStaffQueue() {
  const admin = state.user?.user_type === "admin";
  const cases = filteredStaffCases();
  setText(
    "staff-queue-title",
    admin ? "All agency cases" : "Cases assigned to you",
  );
  setText("staff-count", `${cases.length} cases`);
  setHidden("staff-filter-unassigned", !admin);
  setHidden("staff-assigned-header", !admin);
  setHidden("staff-table-wrap", cases.length === 0);
  setHidden("staff-empty", cases.length > 0);
  setText("staff-empty-title", admin ? "No cases match this filter" : "No cases assigned yet");
  setText(
    "staff-empty-description",
    admin
      ? "Try another queue filter or assign a case to a staff member."
      : "An administrator can assign cases to your staff account.",
  );

  const tbody = $("staff-tbody");
  tbody.replaceChildren();
  for (const item of cases) {
    const row = cloneTemplate("tpl-staff-row");
    setField(row, "applicant", item.applicant_name);
    setField(row, "route", `${item.applicant_email} · ${humanize(item.visa_type)}`);
    const stage = field(row, "stage");
    stage.textContent = humanize(item.stage);
    stage.className = `status-pill ${statusClass(item.stage)}`.trim();
    const pending = field(row, "pending");
    pending.hidden = !item.pending_document_count;
    pending.textContent = `${item.pending_document_count} pending`;
    field(row, "up-to-date").hidden = Boolean(item.pending_document_count);
    setField(
      row,
      "review",
      humanize(item.latest_review_outcome || item.audit_status || "Not reviewed"),
    );
    const assignedCell = field(row, "assigned-cell");
    assignedCell.hidden = !admin;
    assignedCell.textContent = item.assigned_staff_name || "Unassigned";
    row.querySelector('[data-action="open"]').addEventListener("click", () => {
      openStaffCase(item.application_id);
    });
    tbody.append(row);
  }
}

async function openStaffCase(applicationId) {
  clearError();
  try {
    state.staffCase = await request(
      `/staff/cases/${encodeURIComponent(applicationId)}`,
    );
    $("staff-assignment-notes").value = "";
    $("staff-review-outcome").value = "needs_further_review";
    $("staff-review-notes").value = "";
    renderStaffDetail();
    showView("staff-case");
  } catch (error) {
    showError(error);
  }
}

function renderStaffDetail() {
  const detail = state.staffCase;
  if (!detail) return;
  const application = detail.case;
  setText("staff-case-title", `${detail.applicant_name} · ${humanize(application.visa_type)}`);
  setText(
    "staff-case-meta",
    `${detail.applicant_email} · ${application.destination || "Destination not set"}`,
  );
  const status = $("staff-case-status");
  status.textContent = humanize(application.stage);
  status.className = `status-pill ${statusClass(application.stage)}`.trim();

  setHidden("staff-assignment-panel", state.user?.user_type !== "admin");
  renderStaffAssignment();
  renderStaffFacts();
  renderStaffDocuments();
  renderStaffAudit();
  renderStaffActivity();
}

function renderStaffAssignment() {
  const select = $("staff-assignment-select");
  select.replaceChildren();
  select.append(new Option("Unassigned", ""));
  for (const staffUser of state.staffUsers) {
    select.append(new Option(staffUser.name, staffUser.id));
  }
  select.value = state.staffCase?.assigned_staff_id || "";
}

function renderStaffFacts() {
  const container = $("staff-facts");
  container.replaceChildren();
  const data = state.staffCase?.case?.applicant_data || {};
  const intake = data.intake || {};
  const proposed = data.proposed_intake || {};
  const add = (name, value, label, className) => {
    const row = cloneTemplate("tpl-staff-fact");
    setField(row, "label", humanize(name));
    setField(row, "value", value);
    const status = field(row, "status");
    status.textContent = label;
    status.className = `status-pill ${className}`.trim();
    container.append(row);
  };
  for (const [name, value] of Object.entries(intake)) {
    add(name, value, "Applicant confirmed", "status-green");
  }
  for (const [name, value] of Object.entries(proposed)) {
    add(name, value, "Proposed", "status-amber");
  }
  setHidden("staff-facts-empty", Object.keys(intake).length + Object.keys(proposed).length > 0);
}

function renderStaffDocuments() {
  const container = $("staff-documents");
  container.replaceChildren();
  const documents = state.staffCase?.case?.documents || [];
  setHidden("staff-documents-empty", documents.length > 0);
  for (const document of documents) {
    const card = cloneTemplate("tpl-staff-document");
    setField(card, "type", humanize(document.doc_type));
    setField(card, "filename", document.original_filename);
    setField(card, "review-state", humanize(document.review_status));
    const status = field(card, "status");
    status.textContent = humanize(document.review_status);
    status.className = `status-pill ${
      document.review_status === "pending_review" ? "status-amber" : "status-green"
    }`.trim();
    renderDocumentFields(field(card, "fields"), document, false);
    const review = field(card, "review");
    review.hidden = document.review_status !== "pending_review";
    card.querySelector('[data-action="clarify"]').addEventListener("click", () => {
      saveStaffDocumentReview(document, "needs_clarification");
    });
    card.querySelector('[data-action="reviewed"]').addEventListener("click", () => {
      saveStaffDocumentReview(document, "reviewed");
    });
    const activities = state.staffCase?.activity || [];
    const activityContainer = field(card, "activities");
    for (const activity of activities.filter((item) => item.document_id === document.id)) {
      activityContainer.append(
        createElement(
          "p",
          "field-help",
          `Agency review: ${humanize(activity.outcome)}${
            activity.notes ? ` — ${activity.notes}` : ""
          }`,
        ),
      );
    }
    container.append(card);
  }
}

function renderStaffAudit() {
  const audit = state.staffCase?.case?.latest_audit;
  setHidden("staff-audit", !audit);
  setHidden("staff-audit-empty", Boolean(audit));
  if (!audit) return;
  const status = $("staff-audit-status");
  status.textContent = humanize(audit.status);
  status.className = `status-pill ${statusClass(audit.status)}`.trim();
  const items = $("staff-audit-items");
  items.replaceChildren();
  appendAuditItems(items, audit);
  const sources = $("staff-audit-sources");
  sources.replaceChildren();
  for (const source of audit.retrieved_rules || []) {
    sources.append(
      createElement(
        "span",
        "source-chip",
        source.source_name || source.source || "Policy source",
      ),
    );
  }
}

function renderStaffActivity() {
  const container = $("staff-activity");
  container.replaceChildren();
  const activities = state.staffCase?.activity || [];
  setHidden("staff-activity-empty", activities.length > 0);
  for (const activity of activities) {
    const item = cloneTemplate("tpl-timeline-item");
    setField(
      item,
      "label",
      `${humanize(activity.event_type)}: ${humanize(activity.outcome)}`,
    );
    setField(item, "date", formatDate(activity.created_at));
    setField(
      item,
      "detail",
      ` · ${activity.actor_name}${
        activity.assigned_staff_name ? ` · Assigned to ${activity.assigned_staff_name}` : ""
      }${activity.notes ? ` — ${activity.notes}` : ""}`,
    );
    container.append(item);
  }
}

async function refreshStaffCase() {
  if (!state.staffCase) return;
  const applicationId = state.staffCase.case.id;
  try {
    const [detail, cases] = await Promise.all([
      request(`/staff/cases/${encodeURIComponent(applicationId)}`),
      request("/staff/cases"),
    ]);
    state.staffCase = detail;
    state.staffCases = cases;
    if (state.user?.user_type === "admin") {
      state.staffUsers = await request("/staff/users");
    }
    renderStaffDetail();
    renderStaffQueue();
  } catch (error) {
    showError(error);
  }
}

async function saveStaffAssignment(event) {
  event.preventDefault();
  if (state.busy || !state.staffCase) return;
  const staffId = $("staff-assignment-select").value;
  const notes = $("staff-assignment-notes").value.trim();
  setBusy(true);
  try {
    await request(
      `/staff/cases/${encodeURIComponent(state.staffCase.case.id)}/assignment`,
      {
        method: "PATCH",
        body: { assigned_staff_id: staffId || null, notes: notes || null },
      },
    );
    await refreshStaffCase();
    $("staff-assignment-notes").value = "";
    notify("Case assignment saved.");
  } catch (error) {
    notify(error.message);
  } finally {
    setBusy(false);
  }
}

async function saveStaffReview(event) {
  event.preventDefault();
  if (state.busy || !state.staffCase) return;
  const notes = $("staff-review-notes").value.trim();
  setBusy(true);
  try {
    await request(
      `/staff/cases/${encodeURIComponent(state.staffCase.case.id)}/reviews`,
      {
        method: "POST",
        body: {
          review_type: "case_review",
          outcome: $("staff-review-outcome").value,
          notes: notes || null,
        },
      },
    );
    await refreshStaffCase();
    $("staff-review-outcome").value = "needs_further_review";
    $("staff-review-notes").value = "";
    notify("Agency review saved to case activity.");
  } catch (error) {
    notify(error.message);
  } finally {
    setBusy(false);
  }
}

async function saveStaffDocumentReview(document, outcome) {
  if (state.busy || !state.staffCase) return;
  setBusy(true);
  try {
    await request(
      `/staff/cases/${encodeURIComponent(state.staffCase.case.id)}/reviews`,
      {
        method: "POST",
        body: {
          review_type: "document_review",
          outcome,
          document_id: document.id,
          notes:
            outcome === "needs_clarification"
              ? "Applicant clarification requested by agency reviewer."
              : null,
        },
      },
    );
    await refreshStaffCase();
    notify("Agency document review saved.");
  } catch (error) {
    notify(error.message);
  } finally {
    setBusy(false);
  }
}

function goHome() {
  if (isStaffUser()) {
    loadStaffQueue();
  } else {
    loadApplications();
  }
}

function wireEvents() {
  $("auth-form").addEventListener("submit", submitAuth);
  $("auth-toggle").addEventListener("click", toggleAuthMode);
  $("logout-button").addEventListener("click", logout);
  $("nav-home").addEventListener("click", goHome);
  $("nav-new-case").addEventListener("click", showCreate);
  $("new-application-button").addEventListener("click", showCreate);
  $("empty-create-button").addEventListener("click", showCreate);
  $("create-form").addEventListener("submit", submitCreate);
  $("create-cancel").addEventListener("click", loadApplications);
  $("case-back-button").addEventListener("click", loadApplications);
  $("staff-back-button").addEventListener("click", loadStaffQueue);
  $("staff-review-form").addEventListener("submit", saveStaffReview);
  $("staff-assignment-form").addEventListener("submit", saveStaffAssignment);
  $("upload-form").addEventListener("submit", uploadDocument);
  $("run-audit-button").addEventListener("click", runAudit);
  $("chat-form").addEventListener("submit", sendChat);
  $("chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendChat(event);
    }
  });
  $("staff-filter").addEventListener("change", (event) => {
    state.staffFilter = event.target.value;
    renderStaffQueue();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
