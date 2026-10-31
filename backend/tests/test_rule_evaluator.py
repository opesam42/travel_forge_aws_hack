import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.core.schemas import ActiveRule
from app.services.rule_evaluator import evaluate_active_rule, evaluate_active_rules
from app.services.rule_store import get_active_rules


def build_rule(**overrides) -> ActiveRule:
    data = {
        "rule_id": "uk_skilled_worker_passport_expiry_v1",
        "visa_type": "uk_skilled_worker",
        "category": "timing",
        "condition_summary": "Passport expiry must meet the approved route rule.",
        "target_field": "passport.expiry_date",
        "operator": "date_after_months",
        "threshold_value": 6,
        "confirmed_at": "2026-01-01T00:00:00+00:00",
        "confirmed_by": "test-admin",
        "source_text": "Verified source excerpt for testing.",
        "source_name": "Official source",
        "source_url": "https://example.test/official-guidance",
        "version": 1,
    }
    data.update(overrides)
    return ActiveRule.model_validate(data)


class ActiveRuleSchemaTests(unittest.TestCase):
    def test_rejects_unsupported_operator_and_unsafe_target_path(self):
        with self.assertRaises(ValidationError):
            build_rule(operator="eval", threshold_value="__import__('os')")
        with self.assertRaises(ValidationError):
            build_rule(target_field="passport.__class__")
        with self.assertRaises(ValidationError):
            build_rule(operator=">=", threshold_value="six")

    def test_document_existence_rule_requires_no_threshold(self):
        rule = build_rule(
            rule_id="uk_skilled_worker_passport_document_v1",
            category="document_requirement",
            target_field="passport.passport_number",
            operator="exists",
            threshold_value=None,
        )
        self.assertEqual(rule.operator, "exists")


class RuleEvaluatorTests(unittest.TestCase):
    def test_date_rule_passes_and_preserves_source_provenance(self):
        rule = build_rule()
        result = evaluate_active_rule(
            rule,
            {
                "passport": {
                    "expiry_date": "2030-01-01",
                    "verification_status": "user_reviewed",
                }
            },
            today=date(2026, 1, 1),
        )

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.expected, {"later_than": "2026-07-01"})
        self.assertEqual(result.source_name, "Official source")
        self.assertEqual(result.source_url, "https://example.test/official-guidance")

    def test_unreviewed_document_data_needs_human_review(self):
        result = evaluate_active_rule(
            build_rule(),
            {"passport": {"expiry_date": "2030-01-01"}},
            today=date(2026, 1, 1),
        )
        self.assertEqual(result.status, "needs_review")


    def test_missing_data_does_not_pass_and_optional_rule_is_not_applicable(self):
        required = evaluate_active_rule(build_rule(), {})
        optional = evaluate_active_rule(build_rule(required=False), {})

        self.assertEqual(required.status, "not_provided")
        self.assertEqual(optional.status, "not_applicable")

    def test_numeric_rule_uses_stored_threshold(self):
        rule = build_rule(
            rule_id="uk_skilled_worker_balance_v1",
            category="statutory",
            target_field="bank_statement.closing_balance_ngn",
            operator=">=",
            threshold_value=2_000_000,
        )
        data = {
            "bank_statement": {
                "closing_balance_ngn": "2,500,000",
                "verification_status": "user_reviewed",
            }
        }

        result = evaluate_active_rules([rule], data)[0]
        self.assertEqual(result.status, "passed")

    def test_non_numeric_comparison_needs_review_instead_of_passing(self):
        rule = build_rule(
            rule_id="uk_skilled_worker_balance_v1",
            category="statutory",
            target_field="bank_statement.closing_balance_ngn",
            operator=">=",
            threshold_value=2_000_000,
        )
        result = evaluate_active_rule(
            rule,
            {
                "bank_statement": {
                    "closing_balance_ngn": "unreadable",
                    "verification_status": "user_reviewed",
                }
            },
        )
        self.assertEqual(result.status, "needs_review")


class ActiveRuleLookupTests(unittest.TestCase):
    def test_lookup_filters_by_route_and_validates_persisted_json(self):
        stored_rule = build_rule().model_dump(mode="json")
        record = SimpleNamespace(
            rule_id=stored_rule["rule_id"],
            visa_type="uk_skilled_worker",
            data=stored_rule,
        )
        with patch("app.services.rule_store.Session") as session_factory:
            session = session_factory.return_value.__enter__.return_value
            session.exec.return_value.all.return_value = [record]
            rules = get_active_rules("uk_skilled_worker")

        statement = session.exec.call_args.args[0]
        self.assertIn("active_rules.visa_type", str(statement))
        self.assertEqual([rule.rule_id for rule in rules], [stored_rule["rule_id"]])

    def test_lookup_rejects_mismatched_indexed_visa_type(self):
        stored_rule = build_rule().model_dump(mode="json")
        record = SimpleNamespace(
            rule_id=stored_rule["rule_id"],
            visa_type="uk_standard_visitor",
            data=stored_rule,
        )
        with patch("app.services.rule_store.Session") as session_factory:
            session = session_factory.return_value.__enter__.return_value
            session.exec.return_value.all.return_value = [record]
            with self.assertRaisesRegex(ValueError, "does not match"):
                get_active_rules("uk_skilled_worker")
