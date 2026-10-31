"""Database-backed persistence for pending and confirmed compliance rules."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from app.core.database import engine
from app.core.schemas import ActiveRule, DeterministicRuleDraft, RuleConfirmation
from app.models.rules import ActiveRuleRecord, RuleDraftRecord


class ActiveRuleConflictError(ValueError):
    """Raised when an existing rule ID would overwrite an active-rule version."""


def save_drafts(drafts: List[DeterministicRuleDraft]) -> None:
    """Insert or update extracted rule drafts in PostgreSQL."""
    if not drafts:
        return
    rows = [
        {
            "rule_id": draft.rule_id,
            "visa_type": draft.visa_type,
            "is_complete": draft.is_complete,
            "data": draft.model_dump(mode="json"),
            "updated_at": datetime.now(timezone.utc),
        }
        for draft in drafts
    ]
    statement = pg_insert(RuleDraftRecord.__table__).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[RuleDraftRecord.rule_id],
        set_={
            "visa_type": statement.excluded.visa_type,
            "is_complete": statement.excluded.is_complete,
            "data": statement.excluded.data,
            "updated_at": statement.excluded.updated_at,
        },
    )
    with engine.begin() as connection:
        connection.execute(statement)


def get_draft(rule_id: str) -> Optional[DeterministicRuleDraft]:
    with Session(engine) as session:
        record = session.get(RuleDraftRecord, rule_id)
    return DeterministicRuleDraft(**record.data) if record else None


def list_drafts(needs_input_only: bool = False) -> List[DeterministicRuleDraft]:
    statement = select(RuleDraftRecord)
    if needs_input_only:
        statement = statement.where(RuleDraftRecord.is_complete.is_(False))
    with Session(engine) as session:
        records = session.exec(statement.order_by(RuleDraftRecord.rule_id)).all()
    return [DeterministicRuleDraft(**record.data) for record in records]


def confirm_rule(
    draft: DeterministicRuleDraft,
    confirmation: RuleConfirmation,
    parsed_values: dict,
) -> ActiveRule:
    """Persist an operator-confirmed rule and remove its draft atomically."""
    active = ActiveRule(
        rule_id=draft.rule_id,
        visa_type=draft.visa_type,
        category=draft.category,
        condition_summary=draft.condition_summary,
        target_field=draft.target_field,
        operator=draft.operator,
        threshold_value=(
            confirmation.confirmed_threshold_value
            if confirmation.confirmed_threshold_value is not None
            else parsed_values.get("confirmed_threshold_value", draft.threshold_value)
        ),
        threshold_unit=(
            confirmation.confirmed_threshold_unit
            or parsed_values.get("confirmed_threshold_unit")
            or draft.threshold_unit
        ),
        holding_period_days=(
            confirmation.confirmed_holding_period_days
            if confirmation.confirmed_holding_period_days is not None
            else parsed_values.get(
                "confirmed_holding_period_days",
                draft.holding_period_days,
            )
        ),
        exception_clause=draft.exception_clause,
        is_active=True,
        confirmed_at=datetime.now(timezone.utc).isoformat(),
        confirmed_by=confirmation.confirmed_by,
        operator_notes=confirmation.operator_notes,
        source_text=draft.raw_excerpt,
        source_name=draft.source_name,
        source_url=draft.source_url,
        source_section=draft.source_section,
    )
    data = active.model_dump(mode="json")
    statement = pg_insert(ActiveRuleRecord.__table__).values(
        rule_id=active.rule_id,
        visa_type=active.visa_type,
        is_active=True,
        data=data,
        updated_at=datetime.now(timezone.utc),
    )
    statement = statement.on_conflict_do_nothing(
        index_elements=[ActiveRuleRecord.rule_id]
    )
    with engine.begin() as connection:
        result = connection.execute(statement)
        if not result.rowcount:
            raise ActiveRuleConflictError(
                f"Active rule '{draft.rule_id}' already exists; use a new rule ID for a new version."
            )
        connection.execute(
            RuleDraftRecord.__table__.delete().where(
                RuleDraftRecord.rule_id == draft.rule_id
            )
        )
    return active


def get_active_rules(visa_type: Optional[str] = None) -> List[ActiveRule]:
    statement = select(ActiveRuleRecord).where(ActiveRuleRecord.is_active.is_(True))
    if visa_type:
        statement = statement.where(ActiveRuleRecord.visa_type == visa_type)
    with Session(engine) as session:
        records = session.exec(statement.order_by(ActiveRuleRecord.rule_id)).all()
    rules = []
    for record in records:
        rule = ActiveRule.model_validate(record.data)
        if (
            rule.rule_id != record.rule_id
            or rule.visa_type != record.visa_type
            or not rule.is_active
        ):
            raise ValueError(
                f"Active rule '{record.rule_id}' does not match its indexed database metadata."
            )
        rules.append(rule)
    return rules


def deactivate_rule(rule_id: str) -> bool:
    with Session(engine) as session:
        record = session.get(ActiveRuleRecord, rule_id)
        if not record or not record.is_active:
            return False
        data = dict(record.data)
        data["is_active"] = False
        record.data = data
        record.is_active = False
        record.updated_at = datetime.now(timezone.utc)
        session.add(record)
        session.commit()
    return True


def migrate_legacy_json_rules(
    active_rules: List[dict],
    drafts: List[dict],
) -> dict:
    """Import legacy JSON records without overwriting database values."""
    imported_active = 0
    imported_drafts = 0
    now = datetime.now(timezone.utc)

    with engine.begin() as connection:
        for raw_rule in active_rules:
            rule = ActiveRule(**raw_rule)
            statement = pg_insert(ActiveRuleRecord.__table__).values(
                rule_id=rule.rule_id,
                visa_type=rule.visa_type,
                is_active=rule.is_active,
                data=rule.model_dump(mode="json"),
                updated_at=now,
            ).on_conflict_do_nothing(index_elements=[ActiveRuleRecord.rule_id])
            result = connection.execute(statement)
            imported_active += result.rowcount or 0

        for raw_draft in drafts:
            draft = DeterministicRuleDraft(**raw_draft)
            statement = pg_insert(RuleDraftRecord.__table__).values(
                rule_id=draft.rule_id,
                visa_type=draft.visa_type,
                is_complete=draft.is_complete,
                data=draft.model_dump(mode="json"),
                updated_at=now,
            ).on_conflict_do_nothing(index_elements=[RuleDraftRecord.rule_id])
            result = connection.execute(statement)
            imported_drafts += result.rowcount or 0

    return {"active_rules_imported": imported_active, "drafts_imported": imported_drafts}
