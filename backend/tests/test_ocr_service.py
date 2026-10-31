import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.ocr_service import (
    _build_bank_statement_prompt,
    extract_bank_statement_fields,
    extract_text_from_bytes,
)


class OCRServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_bank_statement_uses_parsed_gateway_fields(self):
        extracted = {
            "account_name": "Example Applicant",
            "bank_name": "Example Bank",
            "closing_balance_ngn": 1250000,
            "statement_period_days": 180,
        }
        with (
            patch(
                "app.services.ocr_service.extract_text_from_bytes",
                return_value="Bank statement text",
            ),
            patch(
                "app.services.llm_service.chat_completion",
                new=AsyncMock(return_value=extracted),
            ) as completion,
        ):
            result = await extract_bank_statement_fields(b"pdf", "application/pdf")

        self.assertEqual(result, extracted)
        self.assertTrue(completion.await_args.kwargs["json_mode"])

    async def test_bank_statement_accepts_legacy_json_reply_wrapper(self):
        wrapped = {
            "reply": (
                '{"account_name":"Example Applicant","bank_name":"Example Bank",'
                '"closing_balance_ngn":1250000,"statement_period_days":180}'
            )
        }
        with (
            patch(
                "app.services.ocr_service.extract_text_from_bytes",
                return_value="Bank statement text",
            ),
            patch(
                "app.services.llm_service.chat_completion",
                new=AsyncMock(return_value=wrapped),
            ),
        ):
            result = await extract_bank_statement_fields(b"pdf", "application/pdf")

        self.assertEqual(result["account_name"], "Example Applicant")
        self.assertEqual(result["bank_name"], "Example Bank")
        self.assertEqual(result["closing_balance_ngn"], 1250000)
        self.assertEqual(result["statement_period_days"], 180)

    def test_searchable_pdf_text_does_not_require_image_ocr_packages(self):
        pages = [
            SimpleNamespace(extract_text=lambda: "first page contents"),
            SimpleNamespace(extract_text=lambda: "final page contents"),
        ]
        with patch("PyPDF2.PdfReader", return_value=SimpleNamespace(pages=pages)):
            result = extract_text_from_bytes(b"pdf-bytes", "application/pdf")

        self.assertIn("first page contents", result)
        self.assertIn("final page contents", result)

    def test_long_statement_prompt_keeps_document_end(self):
        text = "A" * 40000 + "BALANCE AT END OF PERIOD" + "B" * 1000
        prompt = _build_bank_statement_prompt(text)

        self.assertIn("[Middle of document omitted]", prompt)
        self.assertIn("BALANCE AT END OF PERIOD", prompt)
        self.assertLess(len(prompt), 31000)


if __name__ == "__main__":
    unittest.main()
