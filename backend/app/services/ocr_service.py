"""
ocr_service.py
──────────────
Hackathon-aligned OCR pipeline for TravelForge.

Strategy (Option 3 from the design doc):
  1. Extract raw text from the uploaded image/PDF using Tesseract (local, free,
     no image-token costs, zero API rate-limits).
  2. Pass the extracted text into Gemma 4 (via the configured LLM provider —
     Groq or Google AI) for structured JSON parsing into the Pydantic schemas.

This keeps 100% of the AI logic inside Gemma, satisfies the Gemma-family
hackathon scoring criteria, and avoids sending raw image bytes to any
third-party vision model.

Fallback: if Tesseract is unavailable or extraction yields nothing useful,
return empty-field schemas so the human review step can fill in the gaps.
"""

from __future__ import annotations

import io
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_SANITIZE_VALUES = frozenset({"null", "none", "unknown", "n/a", ""})
MAX_PDF_OCR_PAGES = 5


def _sanitize(v: Any, *, is_number: bool = False) -> Any:
    """Normalise model output: strip junk null-like strings, clamp negatives."""
    if v is None:
        return 0 if is_number else ""
    s = str(v).strip()
    if s.lower() in _SANITIZE_VALUES:
        return 0 if is_number else ""
    if is_number:
        try:
            num = float(s.replace(",", ""))
            return max(0.0, num)
        except ValueError:
            return 0
    return s


def extract_text_from_bytes(data: bytes, mime_type: str) -> str:
    """
    Run Tesseract OCR on the uploaded document bytes and return the raw text.

    Supports JPEG, PNG, WEBP directly. Searchable PDF text is read directly;
    scanned PDFs are rasterised with pdf2image (requires poppler-utils).
    """
    try:
        if mime_type == "application/pdf":
            try:
                from PyPDF2 import PdfReader

                pdf = PdfReader(io.BytesIO(data))
                page_text = [(page.extract_text() or "").strip() for page in pdf.pages]
                searchable_text = "\n\n".join(text for text in page_text if text)
                if searchable_text:
                    logger.debug(
                        "Extracted searchable text from %d PDF pages (%d chars)",
                        len(page_text),
                        len(searchable_text),
                    )
                    return searchable_text
                page_count = len(page_text)
            except Exception as pdf_text_err:
                logger.info("Searchable PDF text extraction unavailable: %s", pdf_text_err)
                page_count = None

            try:
                import pytesseract
                from pdf2image import convert_from_bytes

                pages = convert_from_bytes(
                    data,
                    dpi=200,
                    first_page=1,
                    last_page=min(page_count or MAX_PDF_OCR_PAGES, MAX_PDF_OCR_PAGES),
                )
                if page_count and page_count > MAX_PDF_OCR_PAGES:
                    pages.extend(
                        convert_from_bytes(
                            data,
                            dpi=200,
                            first_page=page_count,
                            last_page=page_count,
                        )
                    )
                if not pages:
                    return ""
                raw_text = "\n\n".join(
                    pytesseract.image_to_string(image, timeout=20)
                    for image in pages
                )
                logger.debug("Tesseract extracted %d chars from PDF", len(raw_text))
                return raw_text.strip()
            except Exception as pdf_err:
                logger.warning("PDF text/OCR extraction failed: %s", pdf_err)
                return ""
        else:
            try:
                import pytesseract
                from PIL import Image
            except ImportError:
                logger.warning(
                    "pytesseract/Pillow not installed — image OCR skipped"
                )
                return ""
            image = Image.open(io.BytesIO(data))

        raw_text: str = pytesseract.image_to_string(image, timeout=20)
        logger.debug("Tesseract extracted %d chars", len(raw_text))
        return raw_text.strip()
    except Exception as exc:
        logger.warning("Tesseract extraction failed: %s", exc)
        return ""


def _build_passport_prompt(raw_text: str) -> str:
    return (
        "You are a precise document parser. Extract passport fields from the OCR "
        "text below and return ONLY valid JSON with exactly these keys: "
        "passport_number, full_name, expiry_date, issuing_country. "
        "Use empty string \"\" for any field that is not present or unreadable. "
        "Do NOT invent or guess values. Do NOT include any explanation.\n\n"
        f"OCR TEXT:\n{_bounded_document_text(raw_text)}\n\n"
        "Return JSON only."
    )


def _build_bank_statement_prompt(raw_text: str) -> str:
    return (
        "You are a precise document parser. Extract bank statement fields from "
        "the OCR text below and return ONLY valid JSON with exactly these keys: "
        "account_name, bank_name, closing_balance_ngn, statement_period_days. "
        "closing_balance_ngn must be a number (0 if not found). "
        "statement_period_days must be an integer (0 if not found). "
        "Use empty string \"\" for text fields that are missing. "
        "Do NOT invent or guess values. Do NOT include any explanation.\n\n"
        f"OCR TEXT:\n{_bounded_document_text(raw_text)}\n\n"
        "Return JSON only."
    )


def _bounded_document_text(raw_text: str, max_chars: int = 30000) -> str:
    """Keep useful text from both the start and end of long statements."""
    if len(raw_text) <= max_chars:
        return raw_text
    head_chars = max_chars * 3 // 4
    tail_chars = max_chars - head_chars
    return (
        f"{raw_text[:head_chars]}\n\n"
        "[Middle of document omitted]\n\n"
        f"{raw_text[-tail_chars:]}"
    )


def _parse_json_from_llm_response(text: str) -> dict:
    """
    Robustly extract a JSON object from an LLM response that may include
    markdown fences or extra prose.
    """
    # Strip markdown fences
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    # Find the outermost {...}
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return {}


def _fields_from_completion(result: Any, field_names: set[str]) -> dict[str, Any]:
    """Accept the gateway's parsed JSON dict and older text-wrapped responses."""
    if not isinstance(result, dict):
        return {}
    if field_names.intersection(result):
        return result
    for key in ("reply", "text", "content"):
        value = result.get(key)
        if isinstance(value, str):
            parsed = _parse_json_from_llm_response(value)
            if field_names.intersection(parsed):
                return parsed
    return {}


async def extract_passport_fields(data: bytes, mime_type: str) -> dict[str, Any]:
    """
    Full pipeline:
      1. Tesseract → raw text
      2. Gemma 4 → structured JSON
      3. Sanitise → return dict compatible with PassportData
    """
    raw_text = extract_text_from_bytes(data, mime_type)
    if not raw_text:
        logger.info("No OCR text extracted; returning empty passport fields")
        return {
            "passport_number": "",
            "full_name": "",
            "expiry_date": "",
            "issuing_country": "",
        }

    from app.services.llm_service import chat_completion

    prompt = _build_passport_prompt(raw_text)
    try:
        result = await chat_completion(
            system_prompt=(
                "You are a structured data extractor. "
                "Return only valid compact JSON. No prose, no markdown."
            ),
            user_message=prompt,
            json_mode=True,
        )
        raw = _fields_from_completion(
            result,
            {"passport_number", "full_name", "expiry_date", "issuing_country"},
        )
    except Exception as exc:
        logger.warning("Gemma passport parse failed: %s", exc)
        raw = {}

    return {
        "passport_number": _sanitize(raw.get("passport_number")),
        "full_name": _sanitize(raw.get("full_name")),
        "expiry_date": _sanitize(raw.get("expiry_date")),
        "issuing_country": _sanitize(raw.get("issuing_country")),
    }


async def extract_bank_statement_fields(data: bytes, mime_type: str) -> dict[str, Any]:
    """
    Full pipeline:
      1. Tesseract → raw text
      2. Gemma 4 → structured JSON
      3. Sanitise → return dict compatible with BankStatementData
    """
    raw_text = extract_text_from_bytes(data, mime_type)
    if not raw_text:
        logger.info("No OCR text extracted; returning empty bank statement fields")
        return {
            "account_name": "",
            "bank_name": "",
            "closing_balance_ngn": 0.0,
            "statement_period_days": 0,
        }

    from app.services.llm_service import chat_completion

    prompt = _build_bank_statement_prompt(raw_text)
    try:
        result = await chat_completion(
            system_prompt=(
                "You are a structured data extractor. "
                "Return only valid compact JSON. No prose, no markdown."
            ),
            user_message=prompt,
            json_mode=True,
        )
        raw = _fields_from_completion(
            result,
            {"account_name", "bank_name", "closing_balance_ngn", "statement_period_days"},
        )
    except Exception as exc:
        logger.warning("Gemma bank statement parse failed: %s", exc)
        raw = {}

    return {
        "account_name": _sanitize(raw.get("account_name")),
        "bank_name": _sanitize(raw.get("bank_name")),
        "closing_balance_ngn": _sanitize(
            raw.get("closing_balance_ngn"), is_number=True
        ),
        "statement_period_days": int(
            _sanitize(raw.get("statement_period_days"), is_number=True)
        ),
    }
