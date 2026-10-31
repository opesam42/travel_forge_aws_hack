"""Deterministic evaluation for validated active rules."""

import calendar
import math
from datetime import date, datetime
from typing import Any

from app.core.schemas import ActiveRule, RuleEvaluation


def _lookup(data: dict[str, Any], field_path: str) -> tuple[bool, Any, Any]:
    parts = field_path.split(".")
    root = data.get(parts[0])
    value: Any = data
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return False, None, root
        value = value[part]
    return True, value, root


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.replace(",", "").strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _field_unit(field_path: str) -> str | None:
    field_name = field_path.rsplit(".", maxsplit=1)[-1].lower()
    for unit in ("ngn", "gbp", "eur"):
        if field_name.endswith(f"_{unit}"):
            return unit.upper()
    if field_name.endswith(("_days", "_days_remaining")):
        return "days"
    if field_name.endswith(("_months", "_months_remaining")):
        return "months"
    if field_name.endswith("_weeks"):
        return "weeks"
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for date_format in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%d %b %Y",
        "%d %B %Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def evaluate_active_rule(
    rule: ActiveRule,
    applicant_data: dict[str, Any],
    *,
    today: date | None = None,
) -> RuleEvaluation:
    """Evaluate one active rule without executing rule-provided code."""
    present, actual, root = _lookup(applicant_data, rule.target_field)
    status: str
    expected: Any = rule.threshold_value
    review_reason: str | None = None
    evaluation_date = today or date.today()
    missing_value = (
        not present
        or actual is None
        or isinstance(actual, (str, list, dict, tuple, set))
        and len(actual) == 0
    )

    if rule.effective_from and evaluation_date < rule.effective_from:
        status = "not_yet_effective"
        expected = {"effective_from": rule.effective_from.isoformat()}
    elif rule.effective_until and evaluation_date > rule.effective_until:
        status = "expired"
        expected = {"effective_until": rule.effective_until.isoformat()}
    elif rule.operator == "not_exists":
        status = "needs_review" if missing_value else "failed"
        if missing_value:
            review_reason = (
                "The profile does not establish that this field is absent."
            )
    elif missing_value:
        status = "not_applicable" if not rule.required else "not_provided"
    elif (
        rule.target_field.split(".", maxsplit=1)[0] in {"passport", "bank_statement"}
        and isinstance(root, dict)
        and root.get("verification_status") != "user_reviewed"
    ):
        status = "needs_review"
        review_reason = "Document data has not been reviewed by a human."
    elif rule.exception_clause:
        status = "needs_review"
        review_reason = (
            "The rule has an exception that is not represented as a supported "
            "structured condition."
        )
    elif rule.holding_period_days is not None:
        status = "needs_review"
        expected = {
            "threshold": rule.threshold_value,
            "unit": rule.threshold_unit,
            "holding_period_days": rule.holding_period_days,
        }
        review_reason = (
            "The applicant data does not establish that the balance was held "
            "continuously for the required period."
        )
    elif rule.operator == "exists":
        status = "passed"
    elif rule.operator in {">=", "<=", ">", "<"}:
        actual_number = _number(actual)
        threshold_number = _number(rule.threshold_value)
        unit_matches = (
            rule.threshold_unit is None
            or _field_unit(rule.target_field) == rule.threshold_unit.lower()
        )
        if (
            actual_number is None
            or threshold_number is None
            or not unit_matches
        ):
            status = "needs_review"
            review_reason = (
                "The value is unreadable or its unit does not match the rule."
            )
        else:
            comparisons = {
                ">=": actual_number >= threshold_number,
                "<=": actual_number <= threshold_number,
                ">": actual_number > threshold_number,
                "<": actual_number < threshold_number,
            }
            status = "passed" if comparisons[rule.operator] else "failed"
    elif rule.operator == "==":
        if isinstance(actual, (dict, list)) or isinstance(rule.threshold_value, list):
            status = "needs_review"
            review_reason = "The value cannot be compared as a scalar."
        else:
            status = "passed" if actual == rule.threshold_value else "failed"
    elif rule.operator == "in":
        allowed_values = rule.threshold_value
        if not isinstance(allowed_values, list) or isinstance(actual, (dict, list)):
            status = "needs_review"
            review_reason = "The value or allowed-value list is not comparable."
        else:
            status = "passed" if actual in allowed_values else "failed"
    elif rule.operator == "contains":
        if not isinstance(actual, str) or not isinstance(rule.threshold_value, str):
            status = "needs_review"
            review_reason = "The value is not readable text."
        else:
            status = (
                "passed"
                if rule.threshold_value.casefold() in actual.casefold()
                else "failed"
            )
    else:
        actual_date = _parse_date(actual)
        threshold_months = rule.threshold_value
        if (
            actual_date is None
            or isinstance(threshold_months, bool)
            or not isinstance(threshold_months, int)
        ):
            status = "needs_review"
            review_reason = "The date or month threshold is not readable."
        else:
            minimum_date = _add_months(evaluation_date, threshold_months)
            expected = {"later_than": minimum_date.isoformat()}
            status = "passed" if actual_date > minimum_date else "failed"

    return RuleEvaluation(
        rule_id=rule.rule_id,
        visa_type=rule.visa_type,
        category=rule.category,
        target_field=rule.target_field,
        operator=rule.operator,
        status=status,
        expected=expected,
        actual=actual if present else None,
        review_reason=review_reason,
        severity=rule.severity,
        source_name=rule.source_name,
        source_url=rule.source_url,
        source_section=rule.source_section,
        source_text=rule.source_text,
    )


def evaluate_active_rules(
    rules: list[ActiveRule],
    applicant_data: dict[str, Any],
    *,
    today: date | None = None,
) -> list[RuleEvaluation]:
    """Evaluate all route rules against one applicant profile."""
    return [
        evaluate_active_rule(rule, applicant_data, today=today)
        for rule in rules
    ]
