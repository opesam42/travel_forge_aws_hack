import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.schemas import ChatSource
from app.services.chat_actions import (
    citations_from_retrieval,
    execute_chat_actions,
)


class ChatActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_actions_are_bounded_and_unknown_actions_are_rejected(self):
        user = SimpleNamespace(id=uuid.uuid4())
        retrieval = {
            "context": "Policy context",
            "sources": ["policy.txt"],
            "distances": [0.2],
        }
        proposals = [
            {"name": "search_policy", "arguments": {"query": "visitor funds"}},
            {"name": "unsupported_sql", "arguments": {}},
            {"name": "search_trusted_web", "arguments": {"query": "ignored"}},
        ]
        with patch(
            "app.services.chat_actions.retrieve_context_with_scores",
            new=AsyncMock(return_value=retrieval),
        ) as search:
            results, sources, contexts, pending_audit = await execute_chat_actions(
                proposals,
                application=None,
                user=user,
                session=SimpleNamespace(),
            )

        search.assert_awaited_once_with("visitor funds")
        self.assertEqual([result.status for result in results], ["completed", "failed", "skipped"])
        self.assertEqual(results[-1].status, "skipped")
        self.assertEqual(sources[0].source_name, "policy.txt")
        self.assertTrue(contexts)
        self.assertIsNone(pending_audit)

    async def test_audit_action_requires_selected_application(self):
        results, sources, contexts, pending_audit = await execute_chat_actions(
            [{"name": "evaluate_application", "arguments": {}}],
            application=None,
            user=SimpleNamespace(id=uuid.uuid4()),
            session=SimpleNamespace(),
        )

        self.assertEqual(results[0].status, "failed")
        self.assertIn("Select an owned application", results[0].error)
        self.assertEqual(sources, [])
        self.assertEqual(contexts, [])
        self.assertIsNone(pending_audit)

    async def test_exhausted_turn_budget_skips_model_proposal(self):
        results, sources, contexts, pending_audit = await execute_chat_actions(
            [{"name": "evaluate_application", "arguments": {}}],
            application=None,
            user=SimpleNamespace(id=uuid.uuid4()),
            session=SimpleNamespace(),
            max_actions=0,
        )

        self.assertEqual(results[0].status, "skipped")
        self.assertIn("0 action slots remained", results[0].error)
        self.assertEqual(sources, [])
        self.assertEqual(contexts, [])
        self.assertIsNone(pending_audit)

    def test_pgvector_citation_contains_chunk_and_verification_provenance(self):
        citations = citations_from_retrieval(
            {
                "documents": [
                    {
                        "source_name": "travel_data.txt",
                        "document_id": "document-1",
                        "chunk_id": "chunk-1",
                        "chunk_index": 3,
                        "distance": 0.12,
                        "metadata": {
                            "version_id": "uk-visitor-v1",
                            "knowledge_entry_id": "uk-visitor",
                            "verification_status": "verified",
                            "verified_source": "GOV.UK",
                        },
                    }
                ]
            }
        )

        self.assertEqual(
            citations,
            [
                ChatSource(
                    source_name="travel_data.txt",
                    source_type="pgvector",
                    document_id="document-1",
                    chunk_id="chunk-1",
                    chunk_index=3,
                    distance=0.12,
                    version_id="uk-visitor-v1",
                    knowledge_entry_id="uk-visitor",
                    verification_status="verified",
                    verified_source="GOV.UK",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
