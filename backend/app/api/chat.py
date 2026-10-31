"""
chat.py  —  POST /api/chat
──────────────────────────
Conversational Q&A endpoint.
Retrieves relevant policy context from PostgreSQL pgvector, then calls the LLM
to answer the user's question in context.
"""

import uuid
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request as HttpRequest
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.schemas import (
    ChatActionResult,
    ChatRequest,
    ChatResponse,
    ChatSource,
)
from app.core.database import get_session
from app.models.accounts import ChatMessage, ChatSession, User
from app.models.applications import Application, ApplicationDocument
from app.services.auth import get_current_user, verify_request_origin
from app.services.application_context import format_application_context
from app.services.chat_actions import (
    MAX_CHAT_ACTIONS,
    citations_from_retrieval,
)
from app.services.chat_graph import ChatGraphError, run_chat_workflow
from app.services.rag_service import retrieve_context_with_scores
from app.services.llm_service import build_rag_chat_prompt
from app.services.whitelisted_search import (
    infer_destination_jurisdiction,
    resolve_whitelisted_fallback,
)

router = APIRouter(prefix="/chat", tags=["Chat"])
logger = logging.getLogger(__name__)


class ProposedIntake(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    citizenship: str | None = Field(default=None, max_length=200)
    destination: str | None = Field(default=None, max_length=200)
    travel_purpose: str | None = Field(default=None, max_length=200)
    intended_travel_date: str | None = Field(default=None, max_length=200)
    employer: str | None = Field(default=None, max_length=200)
    visa_type: str | None = Field(default=None, max_length=200)

    def clean_facts(self) -> dict[str, str]:
        facts = {}
        for field_name, value in self.model_dump(exclude_none=True).items():
            if not value.strip():
                continue
            facts[field_name] = value.strip()
        return facts


class ChatMessageResponse(BaseModel):
    role: str
    content: str
    created_at: datetime


@router.post("", response_model=ChatResponse)
async def handle_chat(
    payload: ChatRequest,
    http_request: HttpRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    verify_request_origin(http_request)
    session_id = payload.session_id or str(uuid.uuid4())
    if len(session_id) > 100:
        raise HTTPException(status_code=422, detail="session_id is too long.")

    chat_session = session.get(ChatSession, session_id)
    if chat_session and chat_session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    applicant_context = ""
    application = None
    if payload.application_id:
        application = session.get(Application, payload.application_id)
        if not application or application.user_id != user.id:
            raise HTTPException(status_code=404, detail="Application not found.")
    associated_application_id = (
        getattr(chat_session, "application_id", None) if chat_session else None
    )
    if chat_session and associated_application_id and (
        associated_application_id != payload.application_id
    ):
        raise HTTPException(status_code=404, detail="Chat session not found.")
    if chat_session and payload.application_id and not associated_application_id:
        chat_session.application_id = application.id

    if not chat_session:
        chat_session = ChatSession(
            session_id=session_id,
            user_id=user.id,
            application_id=application.id if application else None,
        )
        associated_application_id = chat_session.application_id
        session.add(chat_session)
        session.commit()

    if application:
        documents = session.exec(
            select(ApplicationDocument)
            .where(
                ApplicationDocument.application_id == application.id,
                ApplicationDocument.user_id == user.id,
            )
            .order_by(ApplicationDocument.created_at.desc())
            .limit(10)
        ).all()
        applicant_context = format_application_context(application, documents)
        if associated_application_id is None:
            session.add(chat_session)
            session.commit()

    previous_messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(settings.CHAT_HISTORY_LIMIT)
    ).all()
    conversation_history = [
        {"role": message.role, "content": message.content}
        for message in reversed(previous_messages)
    ]

    # 1. Retrieve relevant policy chunks for the user's query
    recent_user_messages = [
        message["content"]
        for message in conversation_history
        if message["role"] == "user"
    ][-3:]
    retrieval_query = " ".join([*recent_user_messages, payload.message])[-5000:]
    destination = infer_destination_jurisdiction(retrieval_query)
    route_context = ""
    if destination:
        destination_name = "United Kingdom" if destination == "UK" else "United States"
        route_context = (
            f"Destination jurisdiction inferred from the user's route: "
            f"{destination_name} ({destination})."
        )
        retrieval_query = (
            f"{retrieval_query} Destination jurisdiction: {destination_name} "
            f"({destination}); visa immigration policy requirements."
        )
    retrieval = await retrieve_context_with_scores(query=retrieval_query)
    rag_context = retrieval.get("context", "")
    valid_distances = [
        distance
        for distance in retrieval.get("distances", [])
        if isinstance(distance, (int, float))
    ]
    best_distance = min(valid_distances, default=None)
    web_page = None
    sources: list[ChatSource] = []
    automatic_actions = []
    retrieval_is_confident = (
        bool(rag_context)
        and best_distance is not None
        and best_distance <= settings.RAG_DISTANCE_THRESHOLD
    )

    if not retrieval_is_confident:
        rag_context = ""
        automatic_actions.append(
            {
                "name": "search_policy",
                "status": "failed",
                "result": None,
                "error": "No sufficiently confident policy match was retrieved.",
            }
        )
        try:
            web_page = await resolve_whitelisted_fallback(retrieval_query)
        except Exception:
            logger.exception("Trusted web fallback failed for chat request")
        automatic_actions.append(
            {
                "name": "search_trusted_web",
                "status": (
                    "completed"
                    if web_page and web_page.get("can_answer_autonomously")
                    else "failed"
                ),
                "result": (
                    {
                        "title": web_page["title"],
                        "url": web_page["url"],
                    }
                    if web_page and web_page.get("can_answer_autonomously")
                    else None
                ),
                "error": (
                    None
                    if web_page and web_page.get("can_answer_autonomously")
                    else "No autonomously usable page from an approved source was found."
                ),
            }
        )
    else:
        sources.extend(citations_from_retrieval(retrieval))
        automatic_actions.append(
            {
                "name": "search_policy",
                "status": "completed",
                "result": {
                    "chunks_retrieved": len(citations_from_retrieval(retrieval))
                },
                "error": None,
            }
        )

    web_context = ""
    if web_page and web_page["can_answer_autonomously"]:
        web_context = (
            f"Source: {web_page['title']} ({web_page['url']})\n"
            f"Trusted domain: {web_page['domain']}\n"
            f"Content:\n{web_page['content'][:12000]}"
        )
        sources.append(
            ChatSource(
                source_name=str(web_page["title"]),
                source_type="trusted_web",
                url=str(web_page["url"]),
            )
        )

    # 2. Build RAG-enriched prompt
    system_prompt, user_msg = await build_rag_chat_prompt(
        rag_context=rag_context,
        user_message=payload.message,
        web_context=web_context,
        conversation_history=conversation_history,
        applicant_context=applicant_context,
        route_context=route_context,
    )

    try:
        workflow = await run_chat_workflow(
            system_prompt=system_prompt,
            user_message=user_msg,
            automatic_actions=automatic_actions,
            application=application,
            user=user,
            session=session,
            max_actions=MAX_CHAT_ACTIONS - len(automatic_actions),
        )
    except ChatGraphError as exc:
        if exc.invalid_output:
            raise HTTPException(
                status_code=503,
                detail="The language model returned an invalid structured response.",
            ) from exc
        if exc.phase == "initial_response":
            cause = exc.__cause__
            raise HTTPException(
                status_code=503,
                detail=f"LLM unavailable: {cause or exc}",
            ) from exc
        logger.exception("Chat response synthesis after actions failed")
        raise HTTPException(
            status_code=503,
            detail="The requested action completed, but the response could not be generated.",
        ) from exc

    result = workflow["initial_result"]
    final_result = workflow["final_result"]
    model_action_results = workflow["model_action_results"]
    action_sources = workflow["action_sources"]
    action_contexts = workflow["action_contexts"]
    pending_audit = workflow["pending_audit"]
    action_results = workflow["action_results"]
    sources.extend(action_sources)

    reply = final_result.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise HTTPException(
            status_code=503,
            detail="The language model returned an invalid chat response.",
        )
    try:
        proposed_intake = ProposedIntake.model_validate(
            final_result.get("proposed_intake", result.get("proposed_intake", {}))
        ).clean_facts()
    except ValidationError:
        logger.warning("Discarded invalid proposed intake facts from chat model")
        proposed_intake = {}

    extracted_intent = final_result.get(
        "extracted_intent",
        result.get("extracted_intent", {}),
    )
    if not isinstance(extracted_intent, dict):
        extracted_intent = {}
    response_stage = final_result.get("stage", "intake")
    if not isinstance(response_stage, str):
        response_stage = "intake"

    unique_sources = list(
        {
            (
                source.source_type,
                source.source_name,
                source.document_id,
                source.chunk_id,
                source.url,
            ): source
            for source in sources
        }.values()
    )

    if application and proposed_intake:
        applicant_data = dict(application.applicant_data or {})
        proposed = dict(applicant_data.get("proposed_intake") or {})
        verification = dict(applicant_data.get("intake_verification") or {})
        for field_name, value in proposed_intake.items():
            if verification.get(field_name) != "user_confirmed":
                proposed[field_name] = value
        applicant_data["proposed_intake"] = proposed
        application.applicant_data = applicant_data

    if application and pending_audit:
        application.latest_audit = pending_audit.model_dump(mode="json")
        application.stage = pending_audit.status.lower()

    session.add(
        ChatMessage(session_id=session_id, role="user", content=payload.message)
    )
    session.add(
        ChatMessage(session_id=session_id, role="assistant", content=reply.strip())
    )
    chat_session.updated_at = datetime.now(timezone.utc)
    session.add(chat_session)
    if application and (proposed_intake or pending_audit):
        session.add(application)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("Failed to persist completed chat turn")
        raise HTTPException(
            status_code=500,
            detail="The chat response was generated but could not be saved.",
        ) from exc

    return ChatResponse(
        session_id=session_id,
        reply=reply.strip(),
        sources=unique_sources,
        actions=[
            *[
                ChatActionResult.model_validate(action)
                for action in automatic_actions
            ],
            *model_action_results,
        ],
        extracted_intent=extracted_intent,
        proposed_intake=proposed_intake,
        stage=response_stage,
    )


@router.get("/{session_id}/messages", response_model=list[ChatMessageResponse])
def get_chat_history(
    session_id: str,
    http_request: HttpRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[ChatMessageResponse]:
    verify_request_origin(http_request)
    chat_session = session.get(ChatSession, session_id)
    if not chat_session or chat_session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    application_id = getattr(chat_session, "application_id", None)
    if application_id:
        application = session.get(Application, application_id)
        if not application or application.user_id != user.id:
            raise HTTPException(status_code=404, detail="Chat session not found.")

    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    ).all()
    return [
        ChatMessageResponse(
            role=message.role,
            content=message.content,
            created_at=message.created_at,
        )
        for message in messages
    ]
