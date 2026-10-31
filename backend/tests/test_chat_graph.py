import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.schemas import ChatActionResult, ChatSource
from app.services.chat_graph import ChatGraphError, run_chat_workflow


class ChatWorkflowGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_proposed_actions_returns_initial_agent_response(self):
        response = {
            "reply": "I can help with that.",
            "actions": [],
            "proposed_intake": {},
        }
        with (
            patch(
                "app.services.chat_graph.chat_completion",
                new=AsyncMock(return_value=response),
            ) as completion,
            patch(
                "app.services.chat_graph.execute_chat_actions",
                new=AsyncMock(return_value=([], [], [], None)),
            ) as execute_actions,
        ):
            result = await run_chat_workflow(
                system_prompt="system",
                user_message="hello",
                automatic_actions=[],
                application=None,
                user=SimpleNamespace(id=uuid.uuid4()),
                session=SimpleNamespace(),
            )

        self.assertEqual(result["initial_result"], response)
        self.assertEqual(result["final_result"], response)
        self.assertEqual(result["action_results"], [])
        completion.assert_awaited_once()
        execute_actions.assert_awaited_once()

    async def test_proposed_actions_are_executed_then_synthesized(self):
        action_result = ChatActionResult(
            name="generate_checklist",
            status="completed",
            result={"items": []},
        )
        action_source = ChatSource(
            source_name="official-policy",
            source_type="pgvector",
        )
        initial = {
            "reply": "I will prepare that.",
            "actions": [
                {
                    "name": "generate_checklist",
                    "arguments": {"visa_type": "uk_skilled_worker"},
                }
            ],
        }
        final = {"reply": "Here is the checklist.", "actions": []}
        with (
            patch(
                "app.services.chat_graph.chat_completion",
                new=AsyncMock(side_effect=[initial, final]),
            ) as completion,
            patch(
                "app.services.chat_graph.execute_chat_actions",
                new=AsyncMock(
                    return_value=(
                        [action_result],
                        [action_source],
                        ["Checklist result: []"],
                        None,
                    )
                ),
            ) as execute_actions,
        ):
            result = await run_chat_workflow(
                system_prompt="system",
                user_message="Prepare a checklist.",
                automatic_actions=[
                    {
                        "name": "search_policy",
                        "status": "completed",
                        "result": {"chunks_retrieved": 1},
                        "error": None,
                    }
                ],
                application=None,
                user=SimpleNamespace(id=uuid.uuid4()),
                session=SimpleNamespace(),
                max_actions=1,
            )

        self.assertEqual(result["final_result"], final)
        self.assertEqual(result["action_sources"], [action_source])
        self.assertEqual(result["model_action_results"], [action_result])
        self.assertEqual(
            [action["name"] for action in result["action_results"]],
            ["search_policy", "generate_checklist"],
        )
        self.assertEqual(execute_actions.await_count, 1)
        self.assertEqual(completion.await_count, 2)
        self.assertIn(
            "BACKEND ACTION RESULTS",
            completion.await_args.kwargs["system_prompt"],
        )

    async def test_initial_model_failure_preserves_graph_phase(self):
        with patch(
            "app.services.chat_graph.chat_completion",
            new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
        ):
            with self.assertRaises(ChatGraphError) as error:
                await run_chat_workflow(
                    system_prompt="system",
                    user_message="hello",
                    automatic_actions=[],
                    application=None,
                    user=SimpleNamespace(id=uuid.uuid4()),
                    session=SimpleNamespace(),
                )

        self.assertEqual(error.exception.phase, "initial_response")
        self.assertIsInstance(error.exception.__cause__, RuntimeError)


if __name__ == "__main__":
    unittest.main()
