"""
rule_extractor.py
─────────────────
HITL (Human-in-the-Loop) Rule Extraction Pipeline.

Workflow:
  raw policy text
      → LLM reads and attempts to extract structured deterministic rules
      → For each rule: populates fields OR flags missing parameters
      → Returns List[DeterministicRuleDraft] with operator questions where incomplete
      → Operator answers questions → rule_store.confirm_rule() activates the rule
"""

import json
from typing import List
from app.core.config import settings
from app.core.schemas import DeterministicRuleDraft


# ── Extraction Prompt ─────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """You are an expert immigration policy compiler and legal analyst.

Your job is to read raw policy text scraped from a travel/visa agency website and extract
every distinct deterministic rule hidden inside it. A "deterministic rule" is any requirement,
threshold, condition, or restriction that can be evaluated as TRUE or FALSE against
an applicant's profile.

For each rule you find, populate this schema:
{
  "rule_id": "snake_case unique ID, e.g. 'uk_passport_validity'",
  "visa_type": "inferred from context, e.g. 'uk_standard_visitor'",
  "category": "one of: statutory | company_policy | fee | document_requirement | timing",
  "condition_summary": "one clear sentence, e.g. 'Passport must be valid for 6 months beyond travel dates'",
  "target_field": "applicant profile field to check, e.g. 'passport_expiry_months_remaining'",
  "operator": "one of: >= | <= | == | in | exists | not_exists | contains | date_after_months",
  "threshold_value": <number, string, boolean, scalar list, or null as appropriate for the operator>,
  "threshold_unit": "GBP | EUR | NGN | days | months | weeks | null",
  "holding_period_days": <number of days threshold must be maintained, or null>,
  "exception_clause": "any stated exceptions, or null",
  "is_complete": <true if ALL fields for code execution are present, false if ANY are vague/missing>,
  "missing_fields": ["list of field names that are ambiguous or absent"],
  "operator_question": "ONLY if is_complete=false: a specific, actionable question for the human operator to resolve the gap",
  "raw_excerpt": "the exact source sentence(s) that generated this rule",
  "source_name": "the official source name when provided, otherwise null",
  "source_url": "the exact official source URL when provided, otherwise null",
  "source_section": "the relevant page section when provided, otherwise null"
}

CRITICAL RULES:
- Set is_complete=false whenever the text uses vague language like:
  "sufficient funds", "no fixed amount", "reasonable", "if applicable",
  "adequate", "proof of funds", "strong ties", "relevant documents"
- For insurance requirements, the minimum coverage amount IS extractable (e.g. €30,000)
- For processing times given as a range (e.g. "3-6 weeks"), set threshold_value to the MAXIMUM
- Extract MULTIPLE rules from a single text block if it contains multiple requirements
- Use date_after_months only when the policy explicitly defines a number of months.
- The operator_question must be specific — not "what is the amount?" but
  "The policy says 'sufficient funds' without a number. What minimum GBP balance should we require for a UK Standard Visitor visa?"

Return ONLY a valid JSON array of rule objects. No explanation, no markdown, just the JSON array."""


# ── Confirmation Prompt (LLM interprets operator's plain-text answer) ──────────

CONFIRMATION_SYSTEM_PROMPT = """You are helping activate a deterministic compliance rule.
An operator has answered a clarification question about an incomplete policy rule.

Given the original draft rule and the operator's plain-text answer, extract the specific values:
- confirmed_threshold_value: numeric value (e.g. 1270.0)
- confirmed_threshold_unit: unit string (e.g. "GBP")
- confirmed_holding_period_days: number of days (e.g. 28)

Return ONLY valid JSON:
{
  "confirmed_threshold_value": <number or null>,
  "confirmed_threshold_unit": "<string or null>",
  "confirmed_holding_period_days": <number or null>
}"""


# ── Main extraction function ──────────────────────────────────────────────────

async def extract_rules_from_text(
    raw_text: str,
    visa_type: str,
    source_name: str,
    source_url: str | None = None,
    source_section: str | None = None,
) -> List[DeterministicRuleDraft]:
    """
    Feed raw policy text through the LLM rule extractor.
    Returns a list of DeterministicRuleDraft — some complete, some with operator_question.
    """
    from app.services.llm_gateway import gateway

    user_message = f"""Visa type context: {visa_type}
Source document: {source_name}

Policy text to analyze:
\"\"\"
{raw_text}
\"\"\"

Extract all deterministic rules from this text. Return a JSON object with a 'rules' list or a JSON array of rules."""

    parsed = await gateway.chat_completion(
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
        json_mode=True,
    )

    # Handle both {"rules": [...]} and [...]
    raw_rules = parsed if isinstance(parsed, list) else parsed.get("rules", [])

    drafts = []
    for rule_data in raw_rules:
        if not isinstance(rule_data, dict):
            continue
        # Inject source context if LLM didn't fill it
        if not rule_data.get("visa_type"):
            rule_data["visa_type"] = visa_type
        if not rule_data.get("raw_excerpt"):
            rule_data["raw_excerpt"] = raw_text[:500]
        rule_data["source_name"] = source_name
        rule_data["source_url"] = source_url
        rule_data["source_section"] = source_section
        try:
            drafts.append(DeterministicRuleDraft(**rule_data))
        except Exception as e:
            print(f"[rule_extractor] Skipping malformed rule: {e} | data: {rule_data}")

    return drafts


async def parse_operator_answer(
    draft: DeterministicRuleDraft,
    operator_answer: str,
) -> dict:
    """
    LLM interprets operator's plain-text answer and extracts structured values.
    e.g. "£1,270 held for 28 consecutive days"
         → { confirmed_threshold_value: 1270.0, confirmed_threshold_unit: "GBP", confirmed_holding_period_days: 28 }
    """
    from app.services.llm_gateway import gateway

    user_message = f"""Original rule: {draft.condition_summary}
Question asked: {draft.operator_question}
Operator's answer: "{operator_answer}"

Parse the operator's answer and extract the numeric values."""

    return await gateway.chat_completion(
        messages=[
            {"role": "system", "content": CONFIRMATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.0,
        json_mode=True,
    )
