import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import RuleEvaluation
from app.services.rag_service import retrieve_context_with_scores
from app.services.rule_evaluator import evaluate_active_rules
from app.services.rule_store import get_active_rules

RECURSION_LIMIT = 4
logger = logging.getLogger(__name__)


class AuditState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    applicant_data: dict[str, Any]
    visa_type: str
    retrieved_rules: list[dict[str, Any]] = Field(default_factory=list)
    rule_results: list[RuleEvaluation] = Field(default_factory=list)
    calculated_metrics: dict[str, Any] = Field(default_factory=dict)
    discrepancies: list[dict[str, Any]] = Field(default_factory=list)
    is_valid: bool = False
    status: Literal[
        "PLANNING",
        "EXECUTING",
        "REFLECTING",
        "AWAITING_APPROVAL",
        "DISCREPANCY_FOUND",
        "NEEDS_REVIEW",
    ] = "PLANNING"
    retry_count: int = 0
    planned_checks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=lambda: [
            "Audit outcomes use active, human-approved rules for the exact visa "
            "route. Missing or unsupported rule logic requires human review.",
            "OCR-extracted document values are unverified until reviewed by a human.",
        ]
    )


def _as_state(state: AuditState | dict[str, Any]) -> AuditState:
    return state if isinstance(state, AuditState) else AuditState.model_validate(state)


def _nested_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


async def planner_node(state: AuditState | dict[str, Any]) -> dict[str, Any]:
    current = _as_state(state)
    checks = [
        f"Load active approved rules for {current.visa_type}",
        "Evaluate applicant data using the supported deterministic operators",
        "Retrieve relevant policy clauses for citations and explanation",
    ]
    return {"planned_checks": checks, "status": "EXECUTING"}


async def tool_executor_node(state: AuditState | dict[str, Any]) -> dict[str, Any]:
    current = _as_state(state)
    active_rules = await asyncio.to_thread(
        get_active_rules, current.visa_type.strip().lower()
    )
    retrieval_status = "completed"
    try:
        retrieval = await retrieve_context_with_scores(
            query=(
                f"{current.visa_type} UK visa route eligibility and requirements"
            )
        )
    except Exception:
        logger.exception("Policy retrieval failed while evaluating active rules")
        retrieval = {"context": "", "distances": [], "sources": [], "documents": []}
        retrieval_status = "unavailable"

    context = retrieval.get("context", "")
    sources = retrieval.get("sources", [])
    distances = retrieval.get("distances", [])
    documents = retrieval.get("documents", [])
    rule_blocks = context.split("\n\n") if context else []
    retrieved_rules: list[dict[str, Any]] = []
    for index, (source, distance) in enumerate(
        zip(sources, distances)
    ):
        document = documents[index] if index < len(documents) else {}
        retrieved_rules.append(
            {
                "source": source,
                "source_name": document.get("source_name", source),
                "distance": distance,
                "content": (
                    document.get("content")
                    or (rule_blocks[index] if index < len(rule_blocks) else "")
                ),
                "document_id": document.get("document_id"),
                "chunk_id": document.get("chunk_id"),
                "chunk_index": document.get("chunk_index"),
            }
        )

    evaluated = evaluate_active_rules(active_rules, current.applicant_data)
    return {
        "retrieved_rules": retrieved_rules,
        "rule_results": [result.model_dump(mode="json") for result in evaluated],
        "calculated_metrics": {
            "audit_date": datetime.now(timezone.utc).date().isoformat(),
            "active_rule_count": len(active_rules),
            "policy_retrieval_status": retrieval_status,
            "unverified_document_fields": [
                f"{document_type}.{field_name}"
                for document_type in ("passport", "bank_statement")
                if _nested_mapping(current.applicant_data, document_type).get(
                    "verification_status"
                )
                != "user_reviewed"
                for field_name in _nested_mapping(
                    current.applicant_data,
                    document_type,
                )
                if field_name not in {"document_id", "verification_status"}
            ],
        },
        "status": "REFLECTING",
    }


async def self_reflection_node(state: AuditState | dict[str, Any]) -> dict[str, Any]:
    current = _as_state(state)
    discrepancies: list[dict[str, Any]] = []
    statuses = {result.status for result in current.rule_results}
    for result in current.rule_results:
        if result.status in {
            "passed",
            "not_applicable",
            "not_yet_effective",
            "expired",
        }:
            continue
        discrepancies.append(
            {
                "rule_id": result.rule_id,
                "field": result.target_field,
                "expected": result.expected,
                "actual": result.actual,
                "severity": result.severity,
                "status": result.status,
                "review_reason": result.review_reason,
                "source_name": result.source_name,
                "source_url": result.source_url,
                "source_section": result.source_section,
            }
        )

    if not current.rule_results:
        discrepancies.append(
            {
                "field": "active_rules",
                "expected": f"At least one approved rule for {current.visa_type}",
                "actual": "No active rules are configured for this visa route.",
                "severity": "warning",
                "status": "needs_review",
            }
        )

    has_failed_rule = "failed" in statuses
    needs_review = bool(
        not current.rule_results
        or statuses.intersection({"not_provided", "needs_review"})
        or not statuses.intersection({"passed", "failed"})
    )
    if needs_review and not discrepancies:
        discrepancies.append(
            {
                "field": "active_rules",
                "expected": f"At least one applicable rule for {current.visa_type}",
                "actual": "No current rule could be evaluated for this application.",
                "severity": "warning",
                "status": "needs_review",
            }
        )
    is_valid = bool(current.rule_results) and not has_failed_rule and not needs_review

    return {
        "discrepancies": discrepancies,
        "is_valid": is_valid,
        "status": (
            "DISCREPANCY_FOUND"
            if has_failed_rule
            else "NEEDS_REVIEW"
            if needs_review
            else "AWAITING_APPROVAL"
        ),
        "retry_count": current.retry_count,
    }


_builder = StateGraph(AuditState)
_builder.add_node("planner_node", planner_node)
_builder.add_node("tool_executor_node", tool_executor_node)
_builder.add_node("self_reflection_node", self_reflection_node)
_builder.set_entry_point("planner_node")
_builder.add_edge("planner_node", "tool_executor_node")
_builder.add_edge("tool_executor_node", "self_reflection_node")
_builder.add_edge("self_reflection_node", END)
audit_graph = _builder.compile()


async def run_audit_graph(applicant_data: dict[str, Any], visa_type: str) -> AuditState:
    initial_state = AuditState(applicant_data=applicant_data, visa_type=visa_type)
    result = await audit_graph.ainvoke(
        initial_state.model_dump(),
        config={"recursion_limit": RECURSION_LIMIT},
    )
    return AuditState.model_validate(result)
