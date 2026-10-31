"""LangGraph orchestration for chat responses and validated tool actions."""

import json
import logging
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from sqlmodel import Session

from app.core.schemas import ChatActionResult, ChatSource
from app.models.accounts import User
from app.models.applications import Application
from app.services.audit_graph import AuditState
from app.services.chat_actions import MAX_CHAT_ACTIONS, execute_chat_actions
from app.services.llm_service import chat_completion

logger = logging.getLogger(__name__)


class ChatWorkflowState(TypedDict, total=False):
    system_prompt: str
    user_message: str
    automatic_actions: list[dict[str, Any]]
    application: Application | None
    user: User
    session: Session
    max_actions: int
    initial_result: dict[str, Any]
    model_action_results: list[ChatActionResult]
    action_sources: list[ChatSource]
    action_contexts: list[str]
    pending_audit: AuditState | None
    action_results: list[dict[str, Any]]
    final_result: dict[str, Any]


class ChatGraphError(RuntimeError):
    def __init__(
        self,
        phase: Literal["initial_response", "final_synthesis"],
        *,
        invalid_output: bool = False,
    ) -> None:
        self.phase = phase
        self.invalid_output = invalid_output
        message = (
            "The initial chat response could not be generated."
            if phase == "initial_response"
            else "The chat response could not be synthesized after actions."
        )
        super().__init__(message)


async def generate_initial_response(
    state: ChatWorkflowState,
) -> dict[str, Any]:
    try:
        result = await chat_completion(
            system_prompt=state["system_prompt"],
            user_message=state["user_message"],
            json_mode=True,
        )
    except Exception as exc:
        logger.exception("Initial chat agent completion failed")
        raise ChatGraphError("initial_response") from exc
    if not isinstance(result, dict):
        raise ChatGraphError("initial_response", invalid_output=True)
    return {"initial_result": result, "final_result": result}


async def execute_proposed_actions(
    state: ChatWorkflowState,
) -> dict[str, Any]:
    model_action_results, action_sources, action_contexts, pending_audit = (
        await execute_chat_actions(
            state["initial_result"].get("actions", []),
            application=state.get("application"),
            user=state["user"],
            session=state["session"],
            max_actions=min(
                state.get("max_actions", MAX_CHAT_ACTIONS),
                MAX_CHAT_ACTIONS,
            ),
        )
    )
    action_results = [
        *state.get("automatic_actions", []),
        *[action.model_dump(mode="json") for action in model_action_results],
    ]
    return {
        "model_action_results": model_action_results,
        "action_sources": action_sources,
        "action_contexts": action_contexts,
        "pending_audit": pending_audit,
        "action_results": action_results,
    }


def _route_after_actions(state: ChatWorkflowState) -> str:
    return "synthesize" if state.get("model_action_results") else END


async def synthesize_after_actions(
    state: ChatWorkflowState,
) -> dict[str, Any]:
    followup_system_prompt = (
        f"{state['system_prompt']}\n\n"
        "BACKEND ACTION RESULTS (only completed actions succeeded):\n"
        f"{json.dumps(state['action_results'], ensure_ascii=False)}\n"
        "Supporting context from successful actions:\n"
        f"{json.dumps(state['action_contexts'], ensure_ascii=False)}\n\n"
        "Write the final answer using these results. Do not propose or claim "
        "any additional action. Mention failed actions plainly when relevant. "
        "Do not create citations; the backend supplies citations separately."
    )
    try:
        result = await chat_completion(
            system_prompt=followup_system_prompt,
            user_message=state["user_message"],
            json_mode=True,
        )
    except Exception as exc:
        logger.exception("Final chat synthesis after actions failed")
        raise ChatGraphError("final_synthesis") from exc
    if not isinstance(result, dict):
        raise ChatGraphError("final_synthesis", invalid_output=True)
    return {"final_result": result}


_builder = StateGraph(ChatWorkflowState)
_builder.add_node("generate_initial_response", generate_initial_response)
_builder.add_node("execute_proposed_actions", execute_proposed_actions)
_builder.add_node("synthesize_after_actions", synthesize_after_actions)
_builder.set_entry_point("generate_initial_response")
_builder.add_edge("generate_initial_response", "execute_proposed_actions")
_builder.add_conditional_edges(
    "execute_proposed_actions",
    _route_after_actions,
    {
        "synthesize": "synthesize_after_actions",
        END: END,
    },
)
_builder.add_edge("synthesize_after_actions", END)
chat_workflow = _builder.compile()


async def run_chat_workflow(
    *,
    system_prompt: str,
    user_message: str,
    automatic_actions: list[dict[str, Any]],
    application: Application | None,
    user: User,
    session: Session,
    max_actions: int = MAX_CHAT_ACTIONS,
) -> ChatWorkflowState:
    """Run the chat agent, validated actions, and optional answer synthesis."""
    return await chat_workflow.ainvoke(
        {
            "system_prompt": system_prompt,
            "user_message": user_message,
            "automatic_actions": automatic_actions,
            "application": application,
            "user": user,
            "session": session,
            "max_actions": max_actions,
        }
    )
