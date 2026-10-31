import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat
from app.core.config import settings
from app.core.database import get_session
from app.core.schemas import (
    ChatActionResult,
    ChatResponse,
    ChatSource,
    ChoiceOption,
    DynamicChatControl,
)
from app.models.accounts import ChatMessage, ChatSession, User
from app.models.applications import Application
from app.services.auth import get_current_user


class DynamicChatControlSchemaTests(unittest.TestCase):
    def test_allowlisted_control_shapes(self):
        # 1. choice_list
        choice = DynamicChatControl(
            type="choice_list",
            title="Suggested Next Steps",
            options=[
                ChoiceOption(label="Run Audit", value="run_audit", action="run_audit"),
                ChoiceOption(label="Ask Question", value="Hello", action="send_message"),
            ],
        )
        self.assertEqual(choice.type, "choice_list")
        self.assertEqual(len(choice.options), 2)
        self.assertEqual(choice.options[0].action, "run_audit")

        # 2. confirmation_card
        confirm = DynamicChatControl(
            type="confirmation_card",
            title="Confirm Applicant Fact",
            field_name="citizenship",
            field_label="Citizenship",
            proposed_value="Nigerian",
        )
        self.assertEqual(confirm.type, "confirmation_card")
        self.assertEqual(confirm.proposed_value, "Nigerian")

        # 3. missing_field_prompt
        missing = DynamicChatControl(
            type="missing_field_prompt",
            title="Missing Fact: Intended Travel Date",
            field_name="intended_travel_date",
            field_label="Intended Travel Date",
            placeholder="YYYY-MM-DD",
        )
        self.assertEqual(missing.type, "missing_field_prompt")
        self.assertEqual(missing.placeholder, "YYYY-MM-DD")

    def test_chat_response_includes_controls_and_sources(self):
        source = ChatSource(
            source_name="UK Home Office Visas",
            source_type="trusted_web",
            url="https://www.gov.uk/skilled-worker-visa",
        )
        control = DynamicChatControl(
            type="confirmation_card",
            field_name="employer",
            field_label="Employer",
            proposed_value="NHS",
        )
        response = ChatResponse(
            session_id="test-session-123",
            reply="Your visa details have been noted.",
            sources=[source],
            controls=[control],
        )
        self.assertEqual(len(response.sources), 1)
        self.assertEqual(len(response.controls), 1)
        self.assertEqual(response.controls[0].field_name, "employer")


class FakeChatDbSession:
    def __init__(self, entities=None):
        self.entities = entities or {}
        self.added = []
        self.committed = False

    def get(self, model_class, primary_key):
        return self.entities.get((model_class, str(primary_key)))

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def exec(self, query):
        class ExecResult:
            def __init__(self, items):
                self.items = items

            def all(self):
                return self.items

            def first(self):
                return self.items[0] if self.items else None

        return ExecResult([])


class ChatEndpointDynamicControlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = User(
            id=uuid.uuid4(),
            email="applicant@example.com",
            name="Test Applicant",
            password_hash="hash",
            user_type="client",
            created_at=datetime.now(timezone.utc),
        )
        self.app_id = uuid.uuid4()
        self.application = Application(
            id=self.app_id,
            user_id=self.user.id,
            visa_type="uk_skilled_worker",
            destination="United Kingdom",
            applicant_data={
                "intake": {},
                "proposed_intake": {},
                "intake_verification": {},
            },
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.chat_session = ChatSession(
            session_id="session-dynamic-test",
            user_id=self.user.id,
            application_id=self.app_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.fake_db = FakeChatDbSession({
            (User, str(self.user.id)): self.user,
            (Application, str(self.app_id)): self.application,
            (ChatSession, "session-dynamic-test"): self.chat_session,
        })

        app = FastAPI()
        app.include_router(chat.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user

        def get_test_db():
            yield self.fake_db

        app.dependency_overrides[get_session] = get_test_db
        self.client = TestClient(app)
        self.headers = {"Origin": settings.allowed_origins[0]}

    def test_chat_history_deserializes_sources_and_controls(self):
        # Prepare a message with metadata
        source_data = {
            "source_name": "UKVI Appendix Skilled Worker",
            "source_type": "pgvector",
            "url": "https://www.gov.uk/guidance/immigration-rules",
        }
        control_data = {
            "type": "confirmation_card",
            "id": "confirm-cit",
            "title": "Confirm Applicant Fact",
            "field_name": "citizenship",
            "field_label": "Citizenship",
            "proposed_value": "British",
            "options": [],
            "input_type": "text",
        }
        msg = ChatMessage(
            id=uuid.uuid4(),
            session_id="session-dynamic-test",
            role="assistant",
            content="I found the relevant guidance.",
            meta={"sources": [source_data], "controls": [control_data]},
            created_at=datetime.now(timezone.utc),
        )

        class ExecResult:
            def all(self):
                return [msg]

        self.fake_db.exec = lambda query: ExecResult()

        response = self.client.get(
            "/api/chat/session-dynamic-test/messages",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        messages = response.json()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "I found the relevant guidance.")
        self.assertEqual(len(messages[0]["sources"]), 1)
        self.assertEqual(messages[0]["sources"][0]["source_name"], "UKVI Appendix Skilled Worker")
        self.assertEqual(len(messages[0]["controls"]), 1)
        self.assertEqual(messages[0]["controls"][0]["type"], "confirmation_card")
        self.assertEqual(messages[0]["controls"][0]["field_name"], "citizenship")
        self.assertEqual(messages[0]["controls"][0]["proposed_value"], "British")


if __name__ == "__main__":
    unittest.main()
