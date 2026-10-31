import unittest
import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import audit
from app.api.documents import BankStatementData, PassportData
from app.core.config import settings
from app.core.schemas import ActiveRule
from app.core.database import get_session
from app.models.accounts import User
from app.models.applications import Application
from app.services.auth import get_current_user
from app.services.audit_graph import run_audit_graph


class AuditGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_flags_insufficient_funds_and_expiring_passport(self):
        rules = [
            active_rule(
                "funds",
                "bank_statement.closing_balance_ngn",
                ">=",
                20_000_000,
                category="statutory",
            ),
            active_rule(
                "passport-expiry",
                "passport.expiry_date",
                "date_after_months",
                6,
                category="timing",
            ),
        ]
        with (
            patch("app.services.audit_graph.get_active_rules", return_value=rules),
            patch(
                "app.services.audit_graph.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={
                        "context": "[1] Source: policy\nUKVI visitor guidance",
                        "distances": [0.2],
                        "sources": ["ukvi-policy.pdf"],
                    }
                ),
            ),
        ):
            result = await run_audit_graph(
                applicant_data={
                    "bank_statement": {
                        "closing_balance_ngn": 12_000_000.0,
                        "verification_status": "user_reviewed",
                    },
                    "passport": {
                        "expiry_date": (date.today() + timedelta(days=60)).isoformat(),
                        "verification_status": "user_reviewed",
                    },
                },
                visa_type="uk_skilled_worker",
            )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, "DISCREPANCY_FOUND")
        self.assertEqual(
            {item["field"] for item in result.discrepancies},
            {
                "bank_statement.closing_balance_ngn",
                "passport.expiry_date",
            },
        )
        self.assertEqual(result.retrieved_rules[0]["source"], "ukvi-policy.pdf")
        self.assertEqual(
            {item.status for item in result.rule_results},
            {"failed"},
        )

    async def test_graph_returns_awaiting_approval_when_checks_pass(self):
        rules = [
            active_rule(
                "funds",
                "bank_statement.closing_balance_ngn",
                ">=",
                20_000_000,
                category="statutory",
            ),
            active_rule(
                "passport-expiry",
                "passport.expiry_date",
                "date_after_months",
                6,
                category="timing",
            ),
        ]
        with (
            patch("app.services.audit_graph.get_active_rules", return_value=rules),
            patch(
                "app.services.audit_graph.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={"context": "", "distances": [], "sources": []}
                ),
            ),
        ):
            result = await run_audit_graph(
                applicant_data={
                    "bank_statement": {
                        "closing_balance_ngn": 40_000_000.0,
                        "verification_status": "user_reviewed",
                    },
                    "passport": {
                        "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
                        "verification_status": "user_reviewed",
                    },
                },
                visa_type="uk_skilled_worker",
            )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.status, "AWAITING_APPROVAL")
        self.assertEqual(result.discrepancies, [])
        self.assertEqual(
            {item.status for item in result.rule_results},
            {"passed"},
        )

    async def test_graph_exposes_unreviewed_ocr_fields_in_metrics(self):
        rules = [
            active_rule(
                "passport-expiry",
                "passport.expiry_date",
                "date_after_months",
                6,
                category="timing",
            ),
        ]
        with (
            patch("app.services.audit_graph.get_active_rules", return_value=rules),
            patch(
                "app.services.audit_graph.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={"context": "", "distances": [], "sources": []}
                ),
            ),
        ):
            result = await run_audit_graph(
                applicant_data={
                    "passport": {
                        "expiry_date": "2035-01-01",
                        "document_id": "passport-doc",
                        "verification_status": "unverified_ocr",
                    },
                    "bank_statement": {
                        "closing_balance_ngn": 40_000_000.0,
                        "verification_status": "user_reviewed",
                    },
                },
                visa_type="uk_standard_visitor",
            )

        self.assertEqual(
            result.calculated_metrics["unverified_document_fields"],
            ["passport.expiry_date"],
        )
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertEqual(result.rule_results[0].status, "needs_review")

    async def test_graph_does_not_pass_when_route_has_no_active_rules(self):
        with (
            patch("app.services.audit_graph.get_active_rules", return_value=[]),
            patch(
                "app.services.audit_graph.retrieve_context_with_scores",
                new=AsyncMock(
                    return_value={"context": "", "distances": [], "sources": []}
                ),
            ),
        ):
            result = await run_audit_graph({}, visa_type="uk_skilled_worker")

        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertEqual(result.discrepancies[0]["field"], "active_rules")

    def test_document_extraction_schemas_accept_valid_fields(self):
        # Extra fields are silently ignored (extra="forbid" removed to avoid
        # Gemini API schema conflicts). Valid construction must succeed.
        p = PassportData(
            passport_number="A12345678",
            full_name="A Person",
            expiry_date="2030-01-01",
            issuing_country="NG",
        )
        self.assertEqual(p.passport_number, "A12345678")

        b = BankStatementData(
            account_name="A Person",
            bank_name="Example Bank",
            closing_balance_ngn="1000",
            statement_period_days=90,
        )
        self.assertAlmostEqual(b.closing_balance_ngn, 1000.0)

    def test_document_extraction_schemas_sanitize_null_strings(self):
        # Field validators must strip null-like sentinel strings.
        p = PassportData(
            passport_number="null",
            full_name="none",
            expiry_date="N/A",
            issuing_country="unknown",
        )
        self.assertEqual(p.passport_number, "")
        self.assertEqual(p.full_name, "")
        self.assertEqual(p.expiry_date, "")
        self.assertEqual(p.issuing_country, "")


class AuditEvaluationRouteTests(unittest.TestCase):
    def setUp(self):
        self.user_id = uuid.uuid4()
        self.application_id = uuid.uuid4()
        self.application = SimpleNamespace(
            id=self.application_id,
            user_id=self.user_id,
            visa_type="uk_standard_visitor",
            applicant_data={"passport": {"expiry_date": "2032-01-01"}},
            latest_audit=None,
            stage="intake",
        )
        self.session = SimpleNamespace(
            get=lambda model, _key: self.application if model is Application else None,
            add=lambda _record: None,
            commit=lambda: None,
            rollback=lambda: None,
        )
        self.user = SimpleNamespace(id=self.user_id)
        app = FastAPI()
        app.include_router(audit.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user

        def get_test_session():
            yield self.session

        app.dependency_overrides[get_session] = get_test_session
        self.client = TestClient(app)
        self.headers = {"Origin": settings.allowed_origins[0]}

    def tearDown(self):
        self.client.close()

    def test_evaluate_uses_persisted_profile_and_saves_result(self):
        result = {
            "applicant_data": self.application.applicant_data,
            "visa_type": self.application.visa_type,
            "retrieved_rules": [],
            "calculated_metrics": {},
            "discrepancies": [],
            "is_valid": True,
            "status": "AWAITING_APPROVAL",
            "retry_count": 0,
            "planned_checks": [],
            "assumptions": [],
        }
        with patch(
            "app.api.audit.run_audit_graph",
            new=AsyncMock(return_value=run_audit_graph_state(result)),
        ) as run_graph:
            response = self.client.post(
                "/api/audit/evaluate",
                headers=self.headers,
                json={
                    "application_id": str(self.application_id),
                    "visa_type": self.application.visa_type,
                    "applicant_data": {
                        "passport": {"expiry_date": "1999-01-01"},
                        "bank_statement": {"closing_balance_ngn": 1000},
                    },
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            run_graph.await_args.kwargs["applicant_data"]["passport"]["expiry_date"],
            "2032-01-01",
        )
        self.assertEqual(self.application.stage, "awaiting_approval")
        self.assertEqual(self.application.latest_audit["status"], "AWAITING_APPROVAL")

    def test_evaluate_rejects_visa_type_mismatch_for_application(self):
        with patch("app.api.audit.run_audit_graph") as run_graph:
            response = self.client.post(
                "/api/audit/evaluate",
                headers=self.headers,
                json={
                    "application_id": str(self.application_id),
                    "visa_type": "us_b1_b2",
                    "applicant_data": {},
                },
            )

        self.assertEqual(response.status_code, 422)
        run_graph.assert_not_called()


def run_audit_graph_state(data):
    from app.services.audit_graph import AuditState

    return AuditState.model_validate(data)


def active_rule(
    rule_id: str,
    target_field: str,
    operator: str,
    threshold_value: int | float,
    *,
    category: str,
) -> ActiveRule:
    return ActiveRule(
        rule_id=rule_id,
        visa_type="uk_skilled_worker",
        category=category,
        condition_summary=f"Test rule for {target_field}",
        target_field=target_field,
        operator=operator,
        threshold_value=threshold_value,
        confirmed_at="2026-01-01T00:00:00+00:00",
        confirmed_by="test",
        source_text="Test-only policy excerpt.",
        source_name="Test source",
        source_url="https://example.test/policy",
    )


if __name__ == "__main__":
    unittest.main()
