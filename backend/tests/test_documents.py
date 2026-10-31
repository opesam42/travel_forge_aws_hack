import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import documents
from app.core.config import settings
from app.core.database import get_session
from app.models.accounts import User
from app.models.applications import Application, ApplicationDocument
from app.services.auth import get_current_user


class FakeSession:
    def __init__(self, application, document=None):
        self.application = application
        self.document = document
        self.records = []

    def get(self, model, _record_id):
        if model is Application:
            return self.application
        if model is ApplicationDocument:
            return self.document
        return None

    def add(self, record):
        self.records.append(record)

    def commit(self):
        return None

    def rollback(self):
        return None


class DocumentExtractionRouteTests(unittest.TestCase):
    def setUp(self):
        self.user_id = uuid.uuid4()
        self.application_id = uuid.uuid4()
        self.application = SimpleNamespace(
            id=self.application_id,
            user_id=self.user_id,
            applicant_data={},
            updated_at=None,
        )
        self.session = FakeSession(self.application)
        self.user = SimpleNamespace(id=self.user_id)

        app = FastAPI()
        app.include_router(documents.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user

        def get_test_session():
            yield self.session

        app.dependency_overrides[get_session] = get_test_session
        self.client = TestClient(app)
        self.headers = {"Origin": settings.allowed_origins[0]}

    def tearDown(self):
        self.client.close()

    def test_extraction_persists_typed_data_as_unverified_application_state(self):
        extracted = documents.PassportData(
            passport_number="A12345678",
            full_name="A Person",
            expiry_date="2030-01-01",
            issuing_country="NG",
        )
        with patch(
            "app.api.documents._extract_with_gemini",
            return_value=extracted,
        ):
            response = self.client.post(
                "/api/documents/extract",
                data={
                    "application_id": str(self.application_id),
                    "doc_type": "passport",
                },
                files={"file": ("passport.png", b"image-bytes", "image/png")},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["review_status"], "pending_review")
        self.assertEqual(body["extracted_data"]["passport_number"], "A12345678")
        self.assertEqual(
            self.application.applicant_data["passport"]["verification_status"],
            "unverified_ocr",
        )
        self.assertEqual(len(self.session.records), 2)
        self.assertNotIn(b"image-bytes", repr(self.session.records).encode())

    def test_extraction_does_not_process_another_users_application(self):
        self.application.user_id = uuid.uuid4()
        with patch("app.api.documents._extract_with_gemini") as extractor:
            response = self.client.post(
                "/api/documents/extract",
                data={
                    "application_id": str(self.application_id),
                    "doc_type": "passport",
                },
                files={"file": ("passport.png", b"image-bytes", "image/png")},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 404)
        extractor.assert_not_called()

    def test_document_review_confirms_values_and_marks_them_user_reviewed(self):
        document = SimpleNamespace(
            id=uuid.uuid4(),
            application_id=self.application_id,
            user_id=self.user_id,
            doc_type="passport",
            review_status="pending_review",
            extracted_data={
                "passport_number": "A12345678",
                "full_name": "A Person",
                "expiry_date": "2030-01-01",
                "issuing_country": "NG",
            },
            review_notes=None,
            reviewed_at=None,
        )
        self.application.applicant_data["passport"] = {
            **document.extracted_data,
            "document_id": str(document.id),
            "verification_status": "unverified_ocr",
        }
        self.session.document = document
        response = self.client.post(
            f"/api/documents/{document.id}/review",
            headers=self.headers,
            json={"decision": "confirm", "review_notes": "Checked with applicant."},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["review_status"], "reviewed")
        self.assertEqual(document.review_status, "reviewed")
        self.assertEqual(
            self.application.applicant_data["passport"]["verification_status"],
            "user_reviewed",
        )
        self.assertIsNotNone(document.reviewed_at)

    def test_document_review_rejects_document_owned_by_another_user(self):
        document = SimpleNamespace(
            id=uuid.uuid4(),
            application_id=self.application_id,
            user_id=uuid.uuid4(),
            doc_type="passport",
            review_status="pending_review",
            extracted_data={},
        )
        self.session.document = document
        response = self.client.post(
            f"/api/documents/{document.id}/review",
            headers=self.headers,
            json={"decision": "reject"},
        )

        self.assertEqual(response.status_code, 404)

    def test_document_review_saves_corrected_typed_fields(self):
        document = SimpleNamespace(
            id=uuid.uuid4(),
            application_id=self.application_id,
            user_id=self.user_id,
            doc_type="passport",
            review_status="pending_review",
            extracted_data={
                "passport_number": "A12345678",
                "full_name": "A Person",
                "expiry_date": "2030-01-01",
                "issuing_country": "NG",
            },
            review_notes=None,
            reviewed_at=None,
        )
        self.application.applicant_data["passport"] = {
            **document.extracted_data,
            "document_id": str(document.id),
            "verification_status": "unverified_ocr",
        }
        self.session.document = document
        response = self.client.post(
            f"/api/documents/{document.id}/review",
            headers=self.headers,
            json={
                "decision": "correct",
                "corrected_data": {
                    "passport_number": "A87654321",
                    "full_name": "Corrected Person",
                    "expiry_date": "2031-01-01",
                    "issuing_country": "NG",
                },
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["extracted_data"]["passport_number"],
            "A87654321",
        )
        self.assertEqual(
            self.application.applicant_data["passport"]["verification_status"],
            "user_reviewed",
        )

    def test_rejected_current_document_is_removed_from_active_profile(self):
        document = SimpleNamespace(
            id=uuid.uuid4(),
            application_id=self.application_id,
            user_id=self.user_id,
            doc_type="passport",
            review_status="pending_review",
            extracted_data={
                "passport_number": "A12345678",
                "full_name": "A Person",
                "expiry_date": "2030-01-01",
                "issuing_country": "NG",
            },
            review_notes=None,
            reviewed_at=None,
        )
        self.application.applicant_data["passport"] = {
            **document.extracted_data,
            "document_id": str(document.id),
            "verification_status": "unverified_ocr",
        }
        self.session.document = document
        response = self.client.post(
            f"/api/documents/{document.id}/review",
            headers=self.headers,
            json={"decision": "reject"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["review_status"], "rejected")
        self.assertNotIn("passport", self.application.applicant_data)


if __name__ == "__main__":
    unittest.main()
