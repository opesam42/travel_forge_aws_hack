import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import applications
from app.api.applications import ApplicationCreate
from app.core.config import settings
from app.core.database import get_session
from app.models.accounts import ChatSession, User
from app.models.applications import Application
from app.services.auth import get_current_user


class FakeSession:
    def __init__(self, application):
        self.application = application
        self.records = []

    def get(self, model, _key):
        return self.application if model is Application else None

    def exec(self, _statement):
        chat_sessions = [
            record for record in self.records if isinstance(record, ChatSession)
        ]
        return SimpleNamespace(
            all=lambda: [],
            first=lambda: chat_sessions[-1] if chat_sessions else None,
        )

    def add(self, record):
        self.records.append(record)

    def commit(self):
        return None

    def rollback(self):
        return None

    def refresh(self, _record):
        return None


class ApplicationIntakeRouteTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=uuid.uuid4())
        self.application = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=self.user.id,
            visa_type="uk_standard_visitor",
            destination="United Kingdom",
            stage="intake",
            applicant_data={
                "proposed_intake": {"destination": "United Kingdom"},
            },
            latest_audit=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.session = FakeSession(self.application)
        app = FastAPI()
        app.include_router(applications.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user

        def get_test_session():
            yield self.session

        app.dependency_overrides[get_session] = get_test_session
        self.client = TestClient(app)
        self.headers = {"Origin": settings.allowed_origins[0]}

    def tearDown(self):
        self.client.close()

    def test_intake_facts_are_proposed_until_explicitly_confirmed(self):
        response = self.client.patch(
            f"/api/applications/{self.application.id}/intake",
            headers=self.headers,
            json={
                "facts": {
                    "destination": "United Kingdom",
                    "employer": "Example Ltd",
                },
                "confirm_fields": ["destination"],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = self.application.applicant_data
        self.assertEqual(data["intake"]["destination"], "United Kingdom")
        self.assertEqual(
            data["intake_verification"]["destination"],
            "user_confirmed",
        )
        self.assertEqual(data["proposed_intake"]["employer"], "Example Ltd")
        self.assertNotIn("destination", data["proposed_intake"])

        followup = self.client.patch(
            f"/api/applications/{self.application.id}/intake",
            headers=self.headers,
            json={"facts": {"destination": "France"}},
        )
        self.assertEqual(followup.status_code, 200, followup.text)
        self.assertEqual(
            self.application.applicant_data["intake"]["destination"],
            "United Kingdom",
        )

    def test_intake_rejects_unapproved_fields_and_confirmations(self):
        response = self.client.patch(
            f"/api/applications/{self.application.id}/intake",
            headers=self.headers,
            json={
                "facts": {"passport_number": "A123"},
                "confirm_fields": ["passport_number"],
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_application_creation_cannot_seed_server_managed_document_state(self):
        with self.assertRaises(ValueError):
            ApplicationCreate.model_validate(
                {
                    "visa_type": "uk_standard_visitor",
                    "applicant_data": {
                        "passport": {
                            "expiry_date": "2034-01-01",
                            "verification_status": "user_reviewed",
                        }
                    },
                }
            )

    def test_application_chat_session_is_created_and_reused(self):
        first = self.client.post(
            f"/api/applications/{self.application.id}/chat-session",
            headers=self.headers,
        )
        self.assertEqual(first.status_code, 200, first.text)
        first_data = first.json()
        self.assertEqual(first_data["application_id"], str(self.application.id))
        self.assertEqual(len(first_data["session_id"]), 36)

        second = self.client.post(
            f"/api/applications/{self.application.id}/chat-session",
            headers=self.headers,
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), first_data)
        self.assertEqual(len(self.session.records), 1)

    def test_application_chat_session_hides_another_users_application(self):
        self.application.user_id = uuid.uuid4()

        response = self.client.post(
            f"/api/applications/{self.application.id}/chat-session",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
