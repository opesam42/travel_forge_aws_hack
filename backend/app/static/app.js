const API = "/api";
const FACT_LABELS = {
  citizenship: "Citizenship",
  destination: "Destination",
  travel_purpose: "Travel purpose",
  intended_travel_date: "Intended travel date",
  employer: "Employer",
  visa_type: "Visa type",
};

function portalComponent() {
  return {
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
    auth: { name: "", email: "", password: "" },
    authError: "",
    globalError: "",
    formError: "",
    toast: "",
    busy: false,
    sending: false,
    view: "cases",
    chatDraft: "",
    review: { outcome: "needs_further_review", notes: "" },
    assignment: { staffId: "", notes: "" },
    newApplication: {
      visa_type: "uk_standard_visitor",
      destination: "United Kingdom",
    },
    upload: { doc_type: "passport" },
    intakeEdits: {},
    documentEdits: {},

    get isStaff() {
      return this.user?.user_type === "staff" || this.user?.user_type === "admin";
    },

    get pageTitle() {
      if (this.view === "staff") {
        return this.staffCase ? "Review case" : "Agency review queue";
      }
      if (this.view === "create") return "Start an application";
      if (this.view === "case") return this.humanize(this.application?.visa_type);
      return this.isStaff ? "Agency review queue" : "My applications";
    },

    get filteredStaffCases() {
      return this.staffCases.filter((item) => {
        if (this.staffFilter === "unassigned") return !item.assigned_staff_id;
        if (this.staffFilter === "documents") return item.pending_document_count > 0;
        if (this.staffFilter === "audit") {
          return ["NEEDS_REVIEW", "DISCREPANCY_FOUND"].includes(item.audit_status);
        }
        return true;
      });
    },

    get clientTimeline() {
      if (!this.application) return [];
      const events = [
        {
          id: "created",
          label: "Case created",
          date: this.application.created_at,
          detail: this.humanize(this.application.visa_type),
        },
      ];
      for (const document of this.application.documents || []) {
        events.push({
          id: `document-${document.id}`,
          label: `${this.humanize(document.doc_type)} uploaded`,
          date: document.created_at,
          detail: document.original_filename,
        });
        if (document.reviewed_at) {
          events.push({
            id: `review-${document.id}`,
            label: `${this.humanize(document.doc_type)} reviewed`,
            date: document.reviewed_at,
            detail: this.humanize(document.review_status),
          });
        }
      }
      if (this.application.latest_audit?.calculated_metrics?.audit_date) {
        events.push({
          id: "audit",
          label: "Preparation review completed",
          date: this.application.latest_audit.calculated_metrics.audit_date,
          detail: this.humanize(this.application.latest_audit.status),
        });
      }
      for (const [index, message] of this.messages.slice(-5).entries()) {
        if (!message.created_at) continue;
        events.push({
          id: `message-${index}-${message.created_at}`,
          label: message.role === "user" ? "You sent a chat message" : "Case chat replied",
          date: message.created_at,
          detail: "",
        });
      }
      return events
        .filter((event) => event.date)
        .sort((left, right) => new Date(right.date) - new Date(left.date))
        .slice(0, 7);
    },

    humanize(value) {
      return String(value || "intake")
        .replaceAll("_", " ")
        .replace(/\b\w/g, (letter) => letter.toUpperCase());
    },

    formatDate(value) {
      if (!value) return "—";
      const date = new Date(value);
      return Number.isNaN(date.getTime())
        ? String(value)
        : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
    },

    formatTime(value) {
      if (!value) return "";
      const date = new Date(value);
      return Number.isNaN(date.getTime())
        ? ""
        : new Intl.DateTimeFormat(undefined, {
            hour: "numeric",
            minute: "2-digit",
          }).format(date);
    },

    statusClass(value) {
      const status = String(value || "").toLowerCase();
      if (status.includes("discrepancy")) return "status-red";
      if (status.includes("review") || status.includes("await")) return "status-amber";
      if (status && status !== "intake") return "status-green";
      return "";
    },

    async request(path, options = {}) {
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
    },

    notify(message) {
      this.toast = message;
      window.clearTimeout(this.toastTimer);
      this.toastTimer = window.setTimeout(() => {
        this.toast = "";
      }, 3300);
    },

    showError(error) {
      this.globalError = error?.message || String(error);
    },

    clearError() {
      this.globalError = "";
    },

    async init() {
      try {
        const user = await this.request("/auth/me");
        await this.setAuthenticated(user);
      } catch (error) {
        if (error.status !== 401) {
          this.authError = `Could not connect to the service: ${error.message}`;
        }
      }
    },

    async setAuthenticated(user) {
      this.user = user;
      this.authError = "";
      this.globalError = "";
      if (this.isStaff) {
        this.view = "staff";
        await this.loadStaffQueue();
      } else {
        this.view = "cases";
        await this.loadApplications();
      }
    },

    toggleAuthMode() {
      this.authMode = this.authMode === "login" ? "signup" : "login";
      this.authError = "";
      this.auth.password = "";
    },

    async submitAuth() {
      if (this.busy) return;
      this.busy = true;
      this.authError = "";
      try {
        const endpoint =
          this.authMode === "signup" ? "/auth/signup" : "/auth/login";
        const payload =
          this.authMode === "signup"
            ? {
                name: this.auth.name,
                email: this.auth.email,
                password: this.auth.password,
              }
            : {
                email: this.auth.email,
                password: this.auth.password,
              };
        const user = await this.request(endpoint, {
          method: "POST",
          body: payload,
        });
        this.auth.password = "";
        await this.setAuthenticated(user);
      } catch (error) {
        this.authError = error.message;
      } finally {
        this.busy = false;
      }
    },

    async logout() {
      try {
        await this.request("/auth/logout", { method: "POST" });
      } catch (error) {
        if (error.status !== 401) this.notify(error.message);
      } finally {
        this.user = null;
        this.applications = [];
        this.application = null;
        this.staffCase = null;
        this.sessionId = null;
        this.messages = [];
        this.view = "cases";
        this.auth = { name: "", email: "", password: "" };
        this.authError = "";
      }
    },

    async loadApplications() {
      this.clearError();
      this.applications = await this.request("/applications");
      this.application = null;
      this.view = "cases";
    },

    showCreate() {
      this.formError = "";
      this.view = "create";
    },

    async submitCreate() {
      this.busy = true;
      this.formError = "";
      try {
        const payload = { ...this.newApplication };
        if (payload.visa_type === "other") {
          payload.visa_type = window.prompt(
            "Enter the visa route or type “not sure yet”:",
          )?.trim();
          if (!payload.visa_type) return;
        }
        const application = await this.request("/applications", {
          method: "POST",
          body: payload,
        });
        await this.openApplication(application.id);
        this.notify("Application created.");
      } catch (error) {
        this.formError = error.message;
      } finally {
        this.busy = false;
      }
    },

    async openApplication(applicationId) {
      this.clearError();
      try {
        const [application, chatSession] = await Promise.all([
          this.request(`/applications/${encodeURIComponent(applicationId)}`),
          this.request(
            `/applications/${encodeURIComponent(applicationId)}/chat-session`,
            { method: "POST" },
          ),
        ]);
        this.application = application;
        this.sessionId = chatSession.session_id;
        this.messages = await this.request(
          `/chat/${encodeURIComponent(this.sessionId)}/messages`,
        );
        this.documentEdits = Object.fromEntries(
          (application.documents || []).map((document) => [
            document.id,
            { ...(document.extracted_data || {}) },
          ]),
        );
        this.intakeEdits = {
          ...(application.applicant_data?.proposed_intake || {}),
        };
        this.view = "case";
        this.$nextTick(() => {
          if (this.$refs.chatMessages) {
            this.$refs.chatMessages.scrollTop =
              this.$refs.chatMessages.scrollHeight;
          }
        });
      } catch (error) {
        this.showError(error);
      }
    },

    async refreshApplication() {
      if (!this.application) return;
      const applicationId = this.application.id;
      const [application, applications] = await Promise.all([
        this.request(`/applications/${encodeURIComponent(applicationId)}`),
        this.request("/applications"),
      ]);
      this.application = application;
      this.applications = applications;
      this.documentEdits = Object.fromEntries(
        (application.documents || []).map((document) => [
          document.id,
          { ...(document.extracted_data || {}) },
        ]),
      );
      this.intakeEdits = {
        ...(application.applicant_data?.proposed_intake || {}),
      };
    },

    async confirmFact(field) {
      const value = this.intakeEdits[field]?.trim();
      if (!value) {
        this.notify("Add a value before confirming.");
        return;
      }
      this.busy = true;
      try {
        await this.request(
          `/applications/${encodeURIComponent(this.application.id)}/intake`,
          {
            method: "PATCH",
            body: {
              facts: { [field]: value },
              confirm_fields: [field],
            },
          },
        );
        await this.refreshApplication();
        this.notify(`${FACT_LABELS[field] || this.humanize(field)} confirmed.`);
      } catch (error) {
        this.notify(error.message);
      } finally {
        this.busy = false;
      }
    },

    async uploadDocument() {
      if (!this.$refs.documentFile?.files?.length) return;
      this.busy = true;
      const body = new FormData();
      body.append("application_id", this.application.id);
      body.append("doc_type", this.upload.doc_type);
      body.append("file", this.$refs.documentFile.files[0]);
      try {
        await this.request("/documents/extract", { method: "POST", body });
        await this.refreshApplication();
        this.$refs.documentFile.value = "";
        this.notify("Document extracted. Review the suggested values.");
      } catch (error) {
        this.notify(error.message);
      } finally {
        this.busy = false;
      }
    },

    async reviewDocument(document, decision) {
      this.busy = true;
      try {
        let payload = { decision };
        if (decision === "correct") {
          const edits = this.documentEdits[document.id] || {};
          const correctedData = {};
          let changed = false;
          for (const field of Object.keys(document.extracted_data || {})) {
            if (["document_id", "verification_status"].includes(field)) continue;
            const value = edits[field] ?? "";
            correctedData[field] =
              field === "closing_balance_ngn" ||
              field === "statement_period_days"
                ? Number(value)
                : value;
            if (String(document.extracted_data[field] ?? "") !== String(value)) {
              changed = true;
            }
          }
          payload = changed
            ? { decision: "correct", corrected_data: correctedData }
            : { decision: "confirm" };
        }
        await this.request(
          `/documents/${encodeURIComponent(document.id)}/review`,
          { method: "POST", body: payload },
        );
        await this.refreshApplication();
        this.notify(
          decision === "reject"
            ? "Document rejected."
            : "Document review saved.",
        );
      } catch (error) {
        this.notify(error.message);
      } finally {
        this.busy = false;
      }
    },

    async runAudit() {
      this.busy = true;
      try {
        await this.request("/audit/evaluate", {
          method: "POST",
          body: {
            application_id: this.application.id,
            visa_type: this.application.visa_type,
            applicant_data: this.application.applicant_data || {},
          },
        });
        await this.refreshApplication();
        this.notify("Internal case review complete.");
      } catch (error) {
        this.notify(error.message);
      } finally {
        this.busy = false;
      }
    },

    async sendChat() {
      if (this.sending || !this.application || !this.sessionId) return;
      const message = this.chatDraft.trim();
      if (!message) return;
      this.sending = true;
      this.chatDraft = "";
      this.messages.push({
        id: `pending-${Date.now()}`,
        role: "user",
        content: message,
      });
      try {
        const result = await this.request("/chat", {
          method: "POST",
          body: {
            message,
            session_id: this.sessionId,
            application_id: this.application.id,
          },
        });
        this.sessionId = result.session_id;
        this.messages = await this.request(
          `/chat/${encodeURIComponent(this.sessionId)}/messages`,
        );
        await this.refreshApplication();
      } catch (error) {
        this.messages = this.messages.filter(
          (item) => !(item.role === "user" && item.id?.startsWith("pending-")),
        );
        this.notify(`Message could not be sent: ${error.message}`);
      } finally {
        this.sending = false;
        this.$nextTick(() => {
          if (this.$refs.chatMessages) {
            this.$refs.chatMessages.scrollTop =
              this.$refs.chatMessages.scrollHeight;
          }
        });
      }
    },

    async loadStaffQueue() {
      this.clearError();
      this.view = "staff";
      try {
        this.staffCases = await this.request("/staff/cases");
        this.staffUsers =
          this.user?.user_type === "admin"
            ? await this.request("/staff/users")
            : [];
        this.staffCase = null;
      } catch (error) {
        this.showError(error);
      }
    },

    async openStaffCase(applicationId) {
      this.clearError();
      try {
        this.staffCase = await this.request(
          `/staff/cases/${encodeURIComponent(applicationId)}`,
        );
        this.assignment = {
          staffId: this.staffCase.assigned_staff_id || "",
          notes: "",
        };
        this.review = { outcome: "needs_further_review", notes: "" };
      } catch (error) {
        this.showError(error);
      }
    },

    async refreshStaffCase() {
      if (!this.staffCase) return;
      const applicationId = this.staffCase.case.id;
      try {
        [this.staffCase, this.staffCases] = await Promise.all([
          this.request(`/staff/cases/${encodeURIComponent(applicationId)}`),
          this.request("/staff/cases"),
        ]);
        if (this.user?.user_type === "admin") {
          this.staffUsers = await this.request("/staff/users");
        }
        this.assignment.staffId = this.staffCase.assigned_staff_id || "";
      } catch (error) {
        this.showError(error);
      }
    },

    async saveStaffAssignment() {
      if (!this.staffCase) return;
      this.busy = true;
      try {
        await this.request(
          `/staff/cases/${encodeURIComponent(this.staffCase.case.id)}/assignment`,
          {
            method: "PATCH",
            body: {
              assigned_staff_id: this.assignment.staffId || null,
              notes: this.assignment.notes.trim() || null,
            },
          },
        );
        await this.refreshStaffCase();
        this.assignment.notes = "";
        this.notify("Case assignment saved.");
      } catch (error) {
        this.notify(error.message);
      } finally {
        this.busy = false;
      }
    },

    async saveStaffReview() {
      if (!this.staffCase) return;
      this.busy = true;
      try {
        await this.request(
          `/staff/cases/${encodeURIComponent(this.staffCase.case.id)}/reviews`,
          {
            method: "POST",
            body: {
              review_type: "case_review",
              outcome: this.review.outcome,
              notes: this.review.notes.trim() || null,
            },
          },
        );
        await this.refreshStaffCase();
        this.review = { outcome: "needs_further_review", notes: "" };
        this.notify("Agency review saved to case activity.");
      } catch (error) {
        this.notify(error.message);
      } finally {
        this.busy = false;
      }
    },

    async saveStaffDocumentReview(document, outcome) {
      this.busy = true;
      try {
        await this.request(
          `/staff/cases/${encodeURIComponent(this.staffCase.case.id)}/reviews`,
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
        await this.refreshStaffCase();
        this.notify("Agency document review saved.");
      } catch (error) {
        this.notify(error.message);
      } finally {
        this.busy = false;
      }
    },

    goHome() {
      if (this.isStaff) this.loadStaffQueue();
      else this.loadApplications();
    },
  };
}

if (window.Alpine) {
  window.Alpine.data("portal", portalComponent);
} else {
  throw new Error("Alpine.js failed to load; the portal cannot start.");
}
