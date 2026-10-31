import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.chat import get_chat_history, handle_chat
from app.core.schemas import ChatRequest
from app.models.accounts import ChatMessage, ChatSession
from app.models.applications import Application
from app.core.schemas import ChatActionResult, ChatSource
from app.services.whitelisted_search import (
    infer_destination_jurisdiction,
    match_trusted_source,
)


class FakeSession:
    def __init__(self, existing_chat_session=None, application=None):
        self.existing_chat_session = existing_chat_session
        self.application = application
        self.records = []

    def get(self, model, *_args):
        if model is ChatSession:
            return self.existing_chat_session
        if model is Application:
            return self.application
        return None

    def exec(self, *_args):
        return SimpleNamespace(all=lambda: [])

    def add(self, record):
        self.records.append(record)

    def commit(self):
        return None


class ChatWebFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=uuid.uuid4())
        self.http_request = SimpleNamespace(headers={})
        self.db_session = FakeSession()

    async def test_confident_retrieval_skips_web_fallback(self):
        with (
            patch(
                "app.api.chat.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={
                        "context": "Relevant local policy",
                        "distances": [0.2],
                        "sources": ["local-policy.pdf"],
                    }
                ),
            ),
            patch(
                "app.api.chat.resolve_whitelisted_fallback",
                new=AsyncMock(),
            ) as resolve_fallback,
            patch(
                "app.services.chat_graph.chat_completion",
                new=AsyncMock(return_value={"reply": "Local answer", "sources": []}),
            ),
        ):
            response = await handle_chat(
                ChatRequest(message="What is the UK visitor visa?"),
                self.http_request,
                self.user,
                self.db_session,
            )

        resolve_fallback.assert_not_awaited()
        self.assertEqual(response.reply, "Local answer")
        self.assertEqual(
            response.sources,
            [
                ChatSource(
                    source_name="local-policy.pdf",
                    source_type="pgvector",
                    distance=0.2,
                )
            ],
        )
        self.assertEqual(
            [record.role for record in self.db_session.records if isinstance(record, ChatMessage)],
            ["user", "assistant"],
        )

    async def test_case_session_cannot_be_reused_for_another_application(self):
        application = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=self.user.id,
        )
        self.db_session = FakeSession(
            existing_chat_session=SimpleNamespace(
                user_id=self.user.id,
                application_id=uuid.uuid4(),
            ),
            application=application,
        )

        with self.assertRaises(HTTPException) as raised:
            await handle_chat(
                ChatRequest(
                    application_id=application.id,
                    session_id="bound-to-another-case",
                    message="Continue my application.",
                ),
                self.http_request,
                self.user,
                self.db_session,
            )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_legacy_chat_session_is_bound_when_used_for_a_case(self):
        application = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=self.user.id,
            visa_type="uk_standard_visitor",
            destination="United Kingdom",
            stage="intake",
            applicant_data={},
        )
        legacy_session = SimpleNamespace(
            user_id=self.user.id,
            application_id=None,
            updated_at=None,
        )
        self.db_session = FakeSession(
            existing_chat_session=legacy_session,
            application=application,
        )

        with (
            patch(
                "app.api.chat.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={
                        "context": "Relevant local policy",
                        "distances": [0.2],
                        "sources": ["local-policy.pdf"],
                    }
                ),
            ),
            patch(
                "app.services.chat_graph.chat_completion",
                new=AsyncMock(return_value={"reply": "Case answer", "sources": []}),
            ),
        ):
            response = await handle_chat(
                ChatRequest(
                    application_id=application.id,
                    session_id="existing-general-session",
                    message="Continue with my application.",
                ),
                self.http_request,
                self.user,
                self.db_session,
            )

        self.assertEqual(response.session_id, "existing-general-session")
        self.assertEqual(legacy_session.application_id, application.id)

    async def test_case_chat_session_rejects_history_if_application_is_not_owned(self):
        self.db_session = FakeSession(
            existing_chat_session=SimpleNamespace(
                user_id=self.user.id,
                application_id=uuid.uuid4(),
            ),
            application=SimpleNamespace(user_id=uuid.uuid4()),
        )

        with self.assertRaises(HTTPException) as raised:
            get_chat_history(
                "bound-to-another-user",
                self.http_request,
                self.user,
                self.db_session,
            )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_missing_or_weak_retrieval_uses_web_fallback(self):
        page = {
            "title": "Official visitor visa guidance",
            "url": "https://www.gov.uk/standard-visitor/eligibility",
            "domain": "gov.uk",
            "content": "A visitor can stay for up to six months.",
            "can_answer_autonomously": True,
        }
        for distances in ([], [0.8]):
            with self.subTest(distances=distances):
                with (
                    patch(
                        "app.api.chat.retrieve_context_with_scores",
                        new=AsyncMock(
                            return_value={
                                "context": "Weak local evidence" if distances else "",
                                "distances": distances,
                                "sources": [],
                            }
                        ),
                    ),
                    patch(
                        "app.api.chat.resolve_whitelisted_fallback",
                        new=AsyncMock(return_value=page),
                    ) as resolve_fallback,
                    patch(
                        "app.services.chat_graph.chat_completion",
                        new=AsyncMock(return_value={"reply": "Web answer", "sources": []}),
                    ) as completion,
                ):
                    response = await handle_chat(
                        ChatRequest(message="What is the UK visitor visa?"),
                        self.http_request,
                        self.user,
                        self.db_session,
                    )

                fallback_query = resolve_fallback.await_args.args[0]
                self.assertIn("What is the UK visitor visa?", fallback_query)
                self.assertIn("Destination jurisdiction: United Kingdom (UK)", fallback_query)
                self.assertEqual(response.reply, "Web answer")
                self.assertEqual(
                    response.sources,
                    [
                        ChatSource(
                            source_name="Official visitor visa guidance",
                            source_type="trusted_web",
                            url="https://www.gov.uk/standard-visitor/eligibility",
                        )
                    ],
                )
                self.assertIn(
                    "A visitor can stay for up to six months.",
                    completion.await_args.kwargs["system_prompt"],
                )
                self.assertNotIn(
                    "Weak local evidence",
                    completion.await_args.kwargs["system_prompt"],
                )

    async def test_model_invented_sources_are_not_returned(self):
        with (
            patch(
                "app.api.chat.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={
                        "context": "Retrieved official policy",
                        "distances": [0.2],
                        "sources": ["real-policy.txt"],
                    }
                ),
            ),
            patch(
                "app.services.chat_graph.chat_completion",
                new=AsyncMock(
                    return_value={
                        "reply": "The policy says so.",
                        "sources": ["invented-source.gov"],
                    }
                ),
            ),
        ):
            response = await handle_chat(
                ChatRequest(message="What does the policy say?"),
                self.http_request,
                self.user,
                self.db_session,
            )

        self.assertEqual([source.source_name for source in response.sources], ["real-policy.txt"])

    async def test_chat_executes_bounded_actions_and_persists_proposed_facts(self):
        application = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=self.user.id,
            visa_type="uk_standard_visitor",
            destination="United Kingdom",
            stage="intake",
            applicant_data={"intake_verification": {}},
        )
        self.db_session = FakeSession(application=application)
        action_source = ChatSource(
            source_name="official-policy.txt",
            source_type="pgvector",
            document_id="doc-1",
            chunk_id="chunk-1",
        )
        action_result = ChatActionResult(
            name="generate_checklist",
            status="completed",
            result={"items": []},
        )
        with (
            patch(
                "app.api.chat.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={
                        "context": "Retrieved policy",
                        "distances": [0.2],
                        "sources": ["retrieved-policy.txt"],
                    }
                ),
            ),
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
            patch(
                "app.services.chat_graph.chat_completion",
                new=AsyncMock(
                    side_effect=[
                        {
                            "reply": "I will prepare that.",
                            "actions": [
                                {
                                    "name": "generate_checklist",
                                    "arguments": {
                                        "visa_type": "uk_standard_visitor"
                                    },
                                }
                            ],
                            "proposed_intake": {
                                "travel_purpose": "Business meetings"
                            },
                        },
                        {
                            "reply": "Here is the checklist.",
                            "sources": ["model-fabricated.txt"],
                        },
                    ]
                ),
            ) as completion,
        ):
            response = await handle_chat(
                ChatRequest(
                    application_id=application.id,
                    message="I am travelling for business. Prepare the checklist.",
                ),
                self.http_request,
                self.user,
                self.db_session,
            )

        self.assertEqual(execute_actions.await_count, 1)
        self.assertEqual(completion.await_count, 2)
        self.assertIn("BACKEND ACTION RESULTS", completion.await_args.kwargs["system_prompt"])
        created_sessions = [
            record
            for record in self.db_session.records
            if isinstance(record, ChatSession)
            and record.application_id == application.id
        ]
        self.assertEqual(
            {record.session_id for record in created_sessions},
            {response.session_id},
        )
        self.assertTrue(
            all(record.application_id == application.id for record in created_sessions)
        )
        self.assertEqual(
            [action.name for action in response.actions],
            ["search_policy", "generate_checklist"],
        )
        self.assertEqual(response.actions[1], action_result)
        self.assertEqual(response.proposed_intake, {"travel_purpose": "Business meetings"})
        self.assertEqual(
            application.applicant_data["proposed_intake"]["travel_purpose"],
            "Business meetings",
        )
        self.assertEqual(
            [source.source_name for source in response.sources],
            ["retrieved-policy.txt", "official-policy.txt"],
        )

    async def test_route_destination_enriches_retrieval_and_fallback_queries(self):
        page = {
            "title": "UK visitor visa",
            "url": "https://www.gov.uk/standard-visitor/eligibility",
            "domain": "gov.uk",
            "content": "Official UK visitor guidance.",
            "can_answer_autonomously": True,
        }
        message = "I am travelling from Nigeria to London for a holiday. What visa do I need?"
        with (
            patch(
                "app.api.chat.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={"context": "", "distances": [], "sources": []}
                ),
            ) as retrieve,
            patch(
                "app.api.chat.resolve_whitelisted_fallback",
                new=AsyncMock(return_value=page),
            ) as resolve_fallback,
            patch(
                "app.services.chat_graph.chat_completion",
                new=AsyncMock(return_value={"reply": "See official UK visitor guidance."}),
            ) as completion,
        ):
            await handle_chat(
                ChatRequest(message=message),
                self.http_request,
                self.user,
                self.db_session,
            )

        retrieval_query = retrieve.await_args.kwargs["query"]
        self.assertIn(message, retrieval_query)
        self.assertIn("Destination jurisdiction: United Kingdom (UK)", retrieval_query)
        self.assertIn("visa immigration policy requirements", retrieval_query)
        self.assertEqual(
            resolve_fallback.await_args.args[0],
            retrieval_query,
        )
        prompt = completion.await_args.kwargs["system_prompt"]
        self.assertIn("United Kingdom (UK)", prompt)
        self.assertIn("Official UK visitor guidance.", prompt)

    async def test_failed_completion_does_not_persist_partial_turn(self):
        with (
            patch(
                "app.api.chat.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={
                        "context": "Relevant local policy",
                        "distances": [0.2],
                        "sources": [],
                    }
                ),
            ),
            patch(
                "app.services.chat_graph.chat_completion",
                new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                await handle_chat(
                    ChatRequest(message="Question"),
                    self.http_request,
                    self.user,
                    self.db_session,
                )

        self.assertEqual(context.exception.status_code, 503)
        self.assertFalse(
            any(isinstance(record, ChatMessage) for record in self.db_session.records)
        )

    async def test_chat_session_owned_by_another_user_is_not_accessible(self):
        other_user_session = SimpleNamespace(user_id=uuid.uuid4())
        self.db_session = FakeSession(existing_chat_session=other_user_session)

        with self.assertRaises(HTTPException) as context:
            await handle_chat(
                ChatRequest(
                    session_id="another-users-session",
                    message="Continue our previous conversation",
                ),
                self.http_request,
                self.user,
                self.db_session,
            )

        self.assertEqual(context.exception.status_code, 404)

    async def test_chat_includes_only_the_selected_owned_application_context(self):
        application = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=self.user.id,
            visa_type="uk_standard_visitor",
            destination="United Kingdom",
            stage="intake",
            applicant_data={
                "passport": {
                    "passport_number": "A12345678",
                    "verification_status": "unverified_ocr",
                }
            },
        )
        self.db_session = FakeSession(application=application)
        with (
            patch(
                "app.api.chat.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={
                        "context": "Relevant local policy",
                        "distances": [0.2],
                        "sources": [],
                    }
                ),
            ),
            patch(
                "app.services.chat_graph.chat_completion",
                new=AsyncMock(return_value={"reply": "Your passport number is ..."}),
            ) as completion,
        ):
            await handle_chat(
                ChatRequest(
                    application_id=application.id,
                    message="What passport number was extracted?",
                ),
                self.http_request,
                self.user,
                self.db_session,
            )

        system_prompt = completion.await_args.kwargs["system_prompt"]
        self.assertIn("A12345678", system_prompt)
        self.assertIn("unverified_ocr", system_prompt)

    async def test_chat_rejects_an_application_owned_by_another_user(self):
        application = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
        )
        self.db_session = FakeSession(application=application)
        with self.assertRaises(HTTPException) as context:
            await handle_chat(
                ChatRequest(
                    application_id=application.id,
                    message="Read my profile",
                ),
                self.http_request,
                self.user,
                self.db_session,
            )

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(self.db_session.records, [])

    def test_trusted_domain_matching_requires_exact_domain_boundary(self):
        source = SimpleNamespace(domain="gov.uk", is_active=True)
        session = SimpleNamespace(
            __enter__=lambda self: self,
            __exit__=lambda self, *_: None,
        )

        class SessionContext:
            def __enter__(self):
                return session

            def __exit__(self, *_):
                return None

        session.exec = lambda _statement: SimpleNamespace(all=lambda: [source])
        with patch(
            "app.services.whitelisted_search.Session",
            return_value=SessionContext(),
        ):
            self.assertIsNotNone(
                match_trusted_source("https://www.gov.uk/standard-visitor")
            )
            self.assertIsNone(
                match_trusted_source("https://gov.uk.attacker.example/page")
            )
            self.assertIsNone(match_trusted_source("http://www.gov.uk/page"))

    def test_route_inference_uses_destination_not_origin(self):
        self.assertEqual(
            infer_destination_jurisdiction("Nigeria to London for a holiday"),
            "UK",
        )
        self.assertIsNone(
            infer_destination_jurisdiction("London to Lagos for a holiday")
        )
        self.assertEqual(infer_destination_jurisdiction("UK visitor visa"), "UK")
