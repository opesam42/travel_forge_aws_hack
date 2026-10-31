"""Bounded, validated actions available to the authenticated chat agent."""

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

from app.core.config import settings
from app.core.schemas import ChatActionResult, ChatSource, ChecklistItem
from app.models.accounts import User
from app.models.applications import Application
from app.services.audit_graph import AuditState, run_audit_graph
from app.services.llm_service import chat_completion
from app.services.rag_service import retrieve_context_with_scores
from app.services.whitelisted_search import resolve_whitelisted_fallback

logger = logging.getLogger(__name__)
MAX_CHAT_ACTIONS = 2


class ChatActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: Literal[
        "search_policy",
        "search_trusted_web",
        "generate_checklist",
        "evaluate_application",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=500)


class ChecklistArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    visa_type: str = Field(min_length=1, max_length=100)


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def citations_from_retrieval(retrieval: dict[str, Any]) -> list[ChatSource]:
    citations: list[ChatSource] = []
    documents = retrieval.get("documents", [])
    if isinstance(documents, list) and documents:
        for document in documents:
            if not isinstance(document, dict):
                continue
            source_name = document.get("source_name")
            if not isinstance(source_name, str) or not source_name:
                continue
            metadata = document.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            citations.append(
                ChatSource(
                    source_name=source_name,
                    source_type="pgvector",
                    document_id=(
                        str(document["document_id"])
                        if document.get("document_id") is not None
                        else None
                    ),
                    chunk_id=(
                        str(document["chunk_id"])
                        if document.get("chunk_id") is not None
                        else None
                    ),
                    chunk_index=(
                        document.get("chunk_index")
                        if isinstance(document.get("chunk_index"), int)
                        else None
                    ),
                    url=(
                        metadata.get("source_url")
                        if isinstance(metadata.get("source_url"), str)
                        else None
                    ),
                    distance=(
                        float(document["distance"])
                        if isinstance(document.get("distance"), (int, float))
                        else None
                    ),
                    version_id=(
                        metadata.get("version_id")
                        if isinstance(metadata.get("version_id"), str)
                        else None
                    ),
                    knowledge_entry_id=(
                        metadata.get("knowledge_entry_id")
                        if isinstance(metadata.get("knowledge_entry_id"), str)
                        else None
                    ),
                    verification_status=(
                        metadata.get("verification_status")
                        if isinstance(metadata.get("verification_status"), str)
                        else None
                    ),
                    verified_source=(
                        metadata.get("verified_source")
                        if isinstance(metadata.get("verified_source"), str)
                        else None
                    ),
                )
            )
        return citations

    # Compatibility for retrieval providers/tests that only return source names.
    sources = retrieval.get("sources", [])
    distances = retrieval.get("distances", [])
    if isinstance(sources, list):
        for index, source_name in enumerate(sources):
            if isinstance(source_name, str) and source_name:
                distance = distances[index] if index < len(distances) else None
                citations.append(
                    ChatSource(
                        source_name=source_name,
                        source_type="pgvector",
                        distance=(
                            float(distance)
                            if isinstance(distance, (int, float))
                            else None
                        ),
                    )
                )
    return citations


def _web_citation(page: dict[str, Any]) -> ChatSource:
    return ChatSource(
        source_name=str(page["title"]),
        source_type="trusted_web",
        url=str(page["url"]),
    )


async def execute_chat_actions(
    proposed_actions: Any,
    *,
    application: Application | None,
    user: User,
    session: Session,
    max_actions: int = MAX_CHAT_ACTIONS,
) -> tuple[list[ChatActionResult], list[ChatSource], list[str], AuditState | None]:
    """Validate and execute at most two approved action proposals."""
    if proposed_actions is None:
        return [], [], [], None
    if not isinstance(proposed_actions, list):
        return [
            ChatActionResult(
                name="invalid_action_proposal",
                status="failed",
                error="The model action proposal must be a list.",
            )
        ], [], [], None

    results: list[ChatActionResult] = []
    citations: list[ChatSource] = []
    contexts: list[str] = []
    pending_audit: AuditState | None = None
    action_limit = max(0, min(max_actions, MAX_CHAT_ACTIONS))
    allowed_actions = proposed_actions[:action_limit]

    for raw_action in allowed_actions:
        if not isinstance(raw_action, dict):
            results.append(
                ChatActionResult(
                    name="invalid_action_proposal",
                    status="failed",
                    error="Action proposal must be an object.",
                )
            )
            continue
        raw_name = raw_action.get("name")
        name = raw_name if isinstance(raw_name, str) else "invalid_action_proposal"
        try:
            action = ChatActionProposal.model_validate(raw_action)
        except ValidationError:
            logger.warning("Rejected invalid chat action proposal: %s", name)
            results.append(
                ChatActionResult(
                    name=name,
                    status="failed",
                    error="Action name or arguments are invalid.",
                )
            )
            continue

        try:
            if action.name == "search_policy":
                arguments = SearchArguments.model_validate(action.arguments)
                retrieval = await retrieve_context_with_scores(arguments.query)
                context = retrieval.get("context", "")
                distances = [
                    distance
                    for distance in retrieval.get("distances", [])
                    if isinstance(distance, (int, float))
                ]
                if (
                    not context
                    or not distances
                    or min(distances) > settings.RAG_DISTANCE_THRESHOLD
                ):
                    results.append(
                        ChatActionResult(
                            name=action.name,
                            status="failed",
                            error="No matching policy content was retrieved.",
                        )
                    )
                    continue
                citations.extend(citations_from_retrieval(retrieval))
                contexts.append(f"Policy search results:\n{context}")
                results.append(
                    ChatActionResult(
                        name=action.name,
                        status="completed",
                        result={
                            "chunks_retrieved": len(
                                citations_from_retrieval(retrieval)
                            )
                        },
                    )
                )
            elif action.name == "search_trusted_web":
                arguments = SearchArguments.model_validate(action.arguments)
                page = await resolve_whitelisted_fallback(arguments.query)
                if not page or not page.get("can_answer_autonomously"):
                    results.append(
                        ChatActionResult(
                            name=action.name,
                            status="failed",
                            error="No autonomously usable page from an approved source was found.",
                        )
                    )
                    continue
                citations.append(_web_citation(page))
                contexts.append(
                    f"Trusted web search result from {page['title']} ({page['url']}):\n"
                    f"{str(page['content'])[:12000]}"
                )
                results.append(
                    ChatActionResult(
                        name=action.name,
                        status="completed",
                        result={"title": page["title"], "url": page["url"]},
                    )
                )
            elif action.name == "generate_checklist":
                arguments = ChecklistArguments.model_validate(action.arguments)
                from app.api.checklist import CHECKLIST_SYSTEM_PROMPT

                checklist_retrieval = await retrieve_context_with_scores(
                    query=(
                        f"{arguments.visa_type} required documents checklist "
                        "mandatory submissions"
                    )
                )
                policy_context = checklist_retrieval.get("context", "")
                if not policy_context:
                    results.append(
                        ChatActionResult(
                            name=action.name,
                            status="failed",
                            error="No policy content was found for this checklist.",
                        )
                    )
                    continue
                citations.extend(citations_from_retrieval(checklist_retrieval))
                trusted_source_names = set(checklist_retrieval.get("sources", []))
                checklist_result = await chat_completion(
                    system_prompt=CHECKLIST_SYSTEM_PROMPT.format(
                        context=policy_context
                    ),
                    user_message=(
                        f"Generate the document checklist for: {arguments.visa_type}"
                    ),
                    json_mode=True,
                )
                items = []
                for raw_item in checklist_result.get("items", []):
                    item = ChecklistItem.model_validate(raw_item)
                    if item.source_policy not in trusted_source_names:
                        item.source_policy = None
                    items.append(item.model_dump(mode="json"))
                result = {
                    "visa_type": arguments.visa_type,
                    "items": items,
                    "generated_from_rag": True,
                }
                contexts.append(
                    f"Checklist tool result for {arguments.visa_type}:\n{result}"
                )
                results.append(
                    ChatActionResult(
                        name=action.name,
                        status="completed",
                        result=result,
                    )
                )
            else:
                NoArguments.model_validate(action.arguments)
                if not application or application.user_id != user.id:
                    results.append(
                        ChatActionResult(
                            name=action.name,
                            status="failed",
                            error="Select an owned application before requesting an audit.",
                        )
                    )
                    continue
                pending_audit = await run_audit_graph(
                    applicant_data=dict(application.applicant_data or {}),
                    visa_type=application.visa_type,
                )
                audit_result = pending_audit.model_dump(mode="json")
                for rule in pending_audit.retrieved_rules:
                    source_name = rule.get("source_name") or rule.get("source")
                    if isinstance(source_name, str) and source_name:
                        citations.append(
                            ChatSource(
                                source_name=source_name,
                                source_type="pgvector",
                                document_id=(
                                    str(rule["document_id"])
                                    if rule.get("document_id")
                                    else None
                                ),
                                chunk_id=(
                                    str(rule["chunk_id"])
                                    if rule.get("chunk_id")
                                    else None
                                ),
                                chunk_index=rule.get("chunk_index"),
                                distance=rule.get("distance"),
                            )
                        )
                contexts.append(f"Application audit result:\n{audit_result}")
                results.append(
                    ChatActionResult(
                        name=action.name,
                        status="completed",
                        result=audit_result,
                    )
                )
        except ValidationError:
            results.append(
                ChatActionResult(
                    name=action.name,
                    status="failed",
                    error="Action arguments failed validation.",
                )
            )
        except Exception:
            logger.exception("Chat action failed: %s", action.name)
            results.append(
                ChatActionResult(
                    name=action.name,
                    status="failed",
                    error="The requested action could not be completed.",
                )
            )

    for raw_action in proposed_actions[action_limit:]:
        raw_name = (
            raw_action.get("name")
            if isinstance(raw_action, dict) and isinstance(raw_action.get("name"), str)
            else "invalid_action_proposal"
        )
        results.append(
            ChatActionResult(
                name=raw_name,
                status="skipped",
                error=(
                    f"At most {MAX_CHAT_ACTIONS} actions may run in one chat turn; "
                    f"{action_limit} action slots remained."
                ),
            )
        )

    return results, citations, contexts, pending_audit
