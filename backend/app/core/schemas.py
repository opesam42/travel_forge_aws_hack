from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date, datetime
import uuid

# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    application_id: Optional[uuid.UUID] = None
    message: str = Field(min_length=1, max_length=10000)

class ChatSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_name: str
    source_type: Literal["pgvector", "trusted_web"]
    document_id: Optional[str] = None
    chunk_id: Optional[str] = None
    chunk_index: Optional[int] = None
    url: Optional[str] = None
    distance: Optional[float] = None
    version_id: Optional[str] = None
    knowledge_entry_id: Optional[str] = None
    verification_status: Optional[str] = None
    verified_source: Optional[str] = None


class ChatActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    status: Literal["completed", "failed", "skipped"]
    result: Any = None
    error: Optional[str] = None


class ChoiceOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    label: str
    value: str
    action: Literal["send_message", "run_audit", "scroll_to_documents"] = "send_message"


class DynamicChatControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal[
        "single_choice",
        "choice_list",
        "confirmation_card",
        "missing_field_prompt",
    ]
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""
    description: Optional[str] = None
    options: List[ChoiceOption] = Field(default_factory=list)
    field_name: Optional[str] = None
    field_label: Optional[str] = None
    proposed_value: Optional[str] = None
    placeholder: Optional[str] = None
    input_type: Optional[str] = "text"


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str
    reply: str
    sources: List[ChatSource] = Field(default_factory=list)
    actions: List[ChatActionResult] = Field(default_factory=list)
    extracted_intent: Dict[str, Any] = Field(default_factory=dict)
    proposed_intake: Dict[str, str] = Field(default_factory=dict)
    controls: List[DynamicChatControl] = Field(default_factory=list)
    stage: str = "intake"


# ── Audit ─────────────────────────────────────────────────────────────────────

class AuditRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    applicant_profile: Dict[str, Any]   # Structured data from intake chat
    visa_type: str                       # e.g. "uk_skilled_worker"

class IssueDetail(BaseModel):
    rule: str
    passed: bool
    detail: str
    severity: str = "critical"           # critical | warning | info
    source: Optional[str] = None         # Which policy doc this came from

class AuditResponse(BaseModel):
    session_id: str
    visa_type: str
    overall_passed: bool
    issues: List[IssueDetail] = []
    summary: str                         # LLM-generated plain-English summary
    retrieved_policy_chunks: int = 0     # How many RAG chunks informed this

# ── Checklist ─────────────────────────────────────────────────────────────────

class ChecklistItem(BaseModel):
    id: str
    label: str
    description: str
    status: str = "pending"              # pending | uploaded | verified | flagged
    required: bool = True
    source_policy: Optional[str] = None  # Which doc this requirement came from

class ChecklistResponse(BaseModel):
    session_id: str
    visa_type: str
    items: List[ChecklistItem]
    generated_from_rag: bool = True

# ── HITL Rule Extraction ──────────────────────────────────────────────────────

RuleOperator = Literal[
    ">=",
    "<=",
    "==",
    ">",
    "<",
    "in",
    "exists",
    "not_exists",
    "contains",
    "date_after_months",
]
RuleScalar = str | int | float | bool
RuleThreshold = RuleScalar | list[RuleScalar]


class DeterministicRuleDraft(BaseModel):
    """
    What the LLM returns after reading a raw policy excerpt.
    is_complete=False means it detected a gap and has a question for the operator.
    """
    rule_id: str = Field(description="Unique snake_case ID, e.g. 'uk_maintenance_funds'")
    visa_type: str = Field(description="e.g. 'uk_standard_visitor', 'us_b1_b2', 'schengen'")
    category: Literal["statutory", "company_policy", "fee", "document_requirement", "timing"] = Field(
        description="Classification of the rule"
    )
    condition_summary: str = Field(description="One-sentence plain-English description of the rule")
    target_field: str = Field(description="Applicant profile field this checks, e.g. 'bank_balance_gbp'")
    operator: RuleOperator = Field(
        description="Comparison operator for deterministic evaluation"
    )
    threshold_value: Optional[RuleThreshold] = Field(
        None,
        description="Typed scalar or allowed-value list used by the selected operator",
    )
    threshold_unit: Optional[str] = Field(None, description="Unit: 'GBP', 'EUR', 'NGN', 'days', 'months', 'weeks'")
    holding_period_days: Optional[int] = Field(None, description="How many days the threshold must be maintained")
    exception_clause: Optional[str] = Field(None, description="Any explicit exceptions stated in the policy")
    # Gap detection
    is_complete: bool = Field(description="True only if ALL fields needed for code execution are present")
    missing_fields: List[str] = Field(default_factory=list, description="Field names that are ambiguous or absent")
    operator_question: Optional[str] = Field(
        None,
        description="Specific question to ask the human operator to resolve the gap. Must be actionable."
    )
    raw_excerpt: str = Field(description="The exact source text this rule was extracted from")
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    source_section: Optional[str] = None


class RuleConfirmation(BaseModel):
    """Operator's response that fills the gap in a DeterministicRuleDraft."""
    rule_id: str
    operator_answer: str               # Free-text answer, e.g. "£1,270 held for 28 days"
    confirmed_threshold_value: Optional[RuleThreshold] = None
    confirmed_threshold_unit: Optional[str] = None
    confirmed_holding_period_days: Optional[int] = None
    operator_notes: Optional[str] = None
    confirmed_by: str = "admin"        # Who approved it


class ActiveRule(BaseModel):
    """A fully confirmed, executable deterministic rule persisted in PostgreSQL."""
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    visa_type: str
    category: Literal[
        "statutory",
        "company_policy",
        "fee",
        "document_requirement",
        "timing",
    ]
    condition_summary: str
    target_field: str = Field(
        min_length=1,
        max_length=150,
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$",
    )
    operator: RuleOperator
    threshold_value: Optional[RuleThreshold] = None
    threshold_unit: Optional[str] = None
    holding_period_days: Optional[int] = None
    exception_clause: Optional[str] = None
    required: bool = True
    severity: Literal["critical", "warning", "info"] = "critical"
    is_active: bool = True
    confirmed_at: str                  # ISO datetime string
    confirmed_by: str
    operator_notes: Optional[str] = None
    source_text: str                   # Original policy excerpt
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    source_section: Optional[str] = None
    version: int = Field(default=1, ge=1)
    effective_from: Optional[date] = None
    effective_until: Optional[date] = None

    @model_validator(mode="after")
    def validate_operator_threshold(self):
        value = self.threshold_value
        if self.category == "document_requirement" and self.operator not in {
            "exists",
            "not_exists",
        }:
            raise ValueError(
                "Document requirement rules must use exists or not_exists."
            )
        if self.operator in {"exists", "not_exists"}:
            if value is not None:
                raise ValueError(f"{self.operator} rules must not have a threshold.")
        elif self.operator in {">=", "<=", ">", "<"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"{self.operator} rules require a numeric threshold."
                )
        elif self.operator == "date_after_months":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
                or not float(value).is_integer()
            ):
                raise ValueError(
                    "date_after_months rules require a non-negative integer threshold."
                )
            self.threshold_value = int(value)
        elif self.operator == "in":
            if not isinstance(value, list) or not value:
                raise ValueError("in rules require a non-empty list threshold.")
        elif self.operator == "contains":
            if not isinstance(value, str) or not value:
                raise ValueError("contains rules require a non-empty string threshold.")
        elif self.operator == "==" and (
            value is None or isinstance(value, list)
        ):
            raise ValueError("== rules require a scalar threshold.")
        if (
            self.effective_from
            and self.effective_until
            and self.effective_from > self.effective_until
        ):
            raise ValueError("effective_from must not be later than effective_until.")
        return self


class RuleEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rule_id: str
    visa_type: str
    category: str
    target_field: str
    operator: RuleOperator
    status: Literal[
        "passed",
        "failed",
        "not_provided",
        "needs_review",
        "not_applicable",
        "not_yet_effective",
        "expired",
    ]
    expected: Any = None
    actual: Any = None
    review_reason: Optional[str] = None
    severity: Literal["critical", "warning", "info"]
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    source_section: Optional[str] = None
    source_text: str


# ── Knowledge Ingestion ───────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    source_name: str                   # Human-readable doc name
    doc_type: str = "policy"           # policy | sop | faq | regulation
    business_id: str = "default"
    text: Optional[str] = None         # Inline text OR use file upload endpoint

class IngestResponse(BaseModel):
    document_id: str
    chunks_created: int
    source_name: str
    message: str


# ── Rule Extract & Confirm ────────────────────────────────────────────────────

class ExtractRequest(BaseModel):
    raw_text: str                      # The raw policy paragraph/section
    visa_type: str                     # Hint to the LLM about context
    source_name: str = "unknown"       # Which doc this came from
    source_url: Optional[AnyHttpUrl] = None
    source_section: Optional[str] = Field(default=None, max_length=300)

class ExtractResponse(BaseModel):
    rules: List[DeterministicRuleDraft]
    total_extracted: int
    complete: int                      # Rules that are immediately usable
    needs_operator_input: int          # Rules that have gaps

class ConfirmResponse(BaseModel):
    rule_id: str
    status: str                        # "activated" | "error"
    active_rule: Optional[ActiveRule] = None
    message: str
