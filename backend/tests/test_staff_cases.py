import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import staff
from app.core.config import settings
from app.core.database import get_session
from app.models.accounts import User
from app.models.applications import Application, ApplicationDocument, CaseActivity
from app.services.auth import get_current_user


class FakeSession:
    def __init__(self, users, applications, documents=None):
        self.users = {user.id: user for user in users}
        self.applications = {application.id: application for application in applications}
        self.documents = {
            document.id: document for document in (documents or [])
        }
        self.activities = []

    def get(self, model, key):
        if model is User:
            return self.users.get(key)
        if model is Application:
            return self.applications.get(key)
        if model is ApplicationDocument:
            return self.documents.get(key)
        return None

    def exec(self, statement):
        model = statement.column_descriptions[0]["entity"]
        if model is Application:
            rows = list(self.applications.values())
            if statement._where_criteria:
                staff_id = next(
                    (user.id for user in self.users.values() if user.user_type == "staff"),
                    None,
                )
                rows = [
                    application
                    for application in rows
                    if application.assigned_staff_id == staff_id
                ]
        elif model is ApplicationDocument:
            rows = list(self.documents.values())
        elif model is CaseActivity:
            rows = list(self.activities)
            rows.sort(key=lambda activity: activity.created_at, reverse=True)
        elif model is User:
            rows = [
                user
                for user in self.users.values()
                if user.user_type == "staff" and user.is_active
            ]
        else:
            rows = []
        return SimpleNamespace(
            all=lambda: rows,
            first=lambda: rows[0] if rows else None,
        )

    def add(self, record):
        if isinstance(record, CaseActivity):
            self.activities.append(record)
        elif isinstance(record, Application):
            self.applications[record.id] = record

    def commit(self):
        return None

    def rollback(self):
        return None


class StaffCaseRouteTests(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.client_user = SimpleNamespace(
            id=uuid.uuid4(),
            name="Applicant",
            email="applicant@example.com",
            user_type="client",
            is_active=True,
        )
        self.staff_user = SimpleNamespace(
            id=uuid.uuid4(),
            name="Caseworker",
            email="staff@example.com",
            user_type="staff",
            is_active=True,
        )
        self.other_staff = SimpleNamespace(
            id=uuid.uuid4(),
            name="Other worker",
            email="other@example.com",
            user_type="staff",
            is_active=True,
        )
        self.admin = SimpleNamespace(
            id=uuid.uuid4(),
            name="Administrator",
            email="admin@example.com",
            user_type="admin",
            is_active=True,
        )
        self.application = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=self.client_user.id,
            assigned_staff_id=self.staff_user.id,
            visa_type="uk_standard_visitor",
            destination="United Kingdom",
            stage="needs_review",
            applicant_data={},
            latest_audit={"status": "NEEDS_REVIEW"},
            created_at=now,
            updated_at=now,
        )
        self.unassigned_application = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=self.client_user.id,
            assigned_staff_id=None,
            visa_type="uk_skilled_worker",
            destination="United Kingdom",
            stage="intake",
            applicant_data={},
            latest_audit=None,
            created_at=now,
            updated_at=now,
        )
        self.other_assigned_application = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=self.client_user.id,
            assigned_staff_id=self.other_staff.id,
            visa_type="uk_skilled_worker",
            destination="United Kingdom",
            stage="intake",
            applicant_data={},
            latest_audit=None,
            created_at=now,
            updated_at=now,
        )
        self.document = SimpleNamespace(
            id=uuid.uuid4(),
            application_id=self.application.id,
            user_id=self.client_user.id,
            doc_type="passport",
            original_filename="passport.pdf",
            review_status="pending_review",
            extracted_data={"full_name": "Applicant"},
            created_at=now,
            reviewed_at=None,
        )
        self.session = FakeSession(
            [
                self.client_user,
                self.staff_user,
                self.other_staff,
                self.admin,
            ],
            [
                self.application,
                self.unassigned_application,
                self.other_assigned_application,
            ],
            [self.document],
        )
        self.user = self.staff_user
        app = FastAPI()
        app.include_router(staff.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user

        def get_test_session():
            yield self.session

        app.dependency_overrides[get_session] = get_test_session
        self.client = TestClient(app)
        self.headers = {"Origin": settings.allowed_origins[0]}

    def tearDown(self):
        self.client.close()

    def test_clients_cannot_access_staff_queue(self):
        self.user = self.client_user
        response = self.client.get("/api/staff/cases", headers=self.headers)
        self.assertEqual(response.status_code, 403)

    def test_staff_queue_contains_only_assigned_cases(self):
        response = self.client.get("/api/staff/cases", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["application_id"] for item in response.json()],
            [str(self.application.id)],
        )

    def test_staff_queue_hides_cases_assigned_to_other_staff(self):
        response = self.client.get("/api/staff/cases", headers=self.headers)
        self.assertNotIn(
            str(self.other_assigned_application.id),
            {item["application_id"] for item in response.json()},
        )

    def test_admin_can_list_all_cases_and_assignable_staff(self):
        self.user = self.admin
        cases = self.client.get("/api/staff/cases", headers=self.headers)
        staff_users = self.client.get("/api/staff/users", headers=self.headers)
        self.assertEqual(cases.status_code, 200)
        self.assertEqual(len(cases.json()), 3)
        self.assertEqual(
            {item["id"] for item in staff_users.json()},
            {str(self.staff_user.id), str(self.other_staff.id)},
        )

    def test_non_admin_cannot_list_assignable_staff(self):
        response = self.client.get("/api/staff/users", headers=self.headers)
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_open_an_unassigned_case(self):
        response = self.client.get(
            f"/api/staff/cases/{self.unassigned_application.id}",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404)

    def test_admin_assignment_records_activity_and_rejects_client_assignee(self):
        self.user = self.admin
        response = self.client.patch(
            f"/api/staff/cases/{self.unassigned_application.id}/assignment",
            headers=self.headers,
            json={"assigned_staff_id": str(self.other_staff.id), "notes": "New intake"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.unassigned_application.assigned_staff_id,
            self.other_staff.id,
        )
        self.assertEqual(response.json()["activity"][0]["event_type"], "assignment")
        self.assertEqual(response.json()["activity"][0]["actor_name"], "Administrator")

        invalid = self.client.patch(
            f"/api/staff/cases/{self.application.id}/assignment",
            headers=self.headers,
            json={"assigned_staff_id": str(self.client_user.id)},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_case_review_is_persisted_without_overwriting_audit_or_stage(self):
        response = self.client.post(
            f"/api/staff/cases/{self.application.id}/reviews",
            headers=self.headers,
            json={
                "review_type": "case_review",
                "outcome": "needs_changes",
                "notes": "Please clarify the travel purpose.",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["actor_name"], "Caseworker")
        self.assertEqual(self.application.stage, "needs_review")
        self.assertEqual(self.application.latest_audit, {"status": "NEEDS_REVIEW"})
        self.assertEqual(len(self.session.activities), 1)

    def test_staff_document_review_is_separate_from_applicant_confirmation(self):
        response = self.client.post(
            f"/api/staff/cases/{self.application.id}/reviews",
            headers=self.headers,
            json={
                "review_type": "document_review",
                "outcome": "needs_clarification",
                "document_id": str(self.document.id),
                "notes": "Ask the applicant to upload a clearer scan.",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.document.review_status, "pending_review")
        self.assertEqual(response.json()["document_id"], str(self.document.id))

    def test_case_review_rejects_document_review_outcome(self):
        response = self.client.post(
            f"/api/staff/cases/{self.application.id}/reviews",
            headers=self.headers,
            json={
                "review_type": "case_review",
                "outcome": "reviewed",
            },
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
