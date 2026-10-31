import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.config import settings
from app.core.llm import get_llm
from app.services.llm_gateway import LLMGateway


class LLMFactoryTests(unittest.TestCase):
    def test_selects_provider_model_and_credentials(self):
        with (
            patch.object(settings, "LLM_MODEL", None),
            patch.object(settings, "GROQ_API_KEY", "test-groq-key"),
            patch("app.core.llm.init_chat_model") as init_chat_model,
        ):
            model = get_llm(provider="groq")

        self.assertIs(model, init_chat_model.return_value)
        init_chat_model.assert_called_once_with(
            model="gemma2-9b-it",
            model_provider="groq",
            temperature=0.0,
            api_key="test-groq-key",
        )

    def test_google_alias_uses_google_genai_provider(self):
        with (
            patch.object(settings, "GEMINI_API_KEY", "test-google-key"),
            patch.object(settings, "GOOGLE_AI_API_KEY", None),
            patch("app.core.llm.init_chat_model") as init_chat_model,
        ):
            get_llm(provider="google")

        self.assertEqual(
            init_chat_model.call_args.kwargs["model_provider"], "google_genai"
        )
        self.assertEqual(init_chat_model.call_args.kwargs["api_key"], "test-google-key")

    def test_missing_credentials_and_unknown_provider_are_explicit(self):
        with patch.object(settings, "GROQ_API_KEY", None):
            with self.assertRaisesRegex(RuntimeError, "GROQ_API_KEY is required"):
                get_llm(provider="groq")
        with self.assertRaisesRegex(ValueError, "Unsupported LLM provider"):
            get_llm(provider="unknown")


class LLMGatewayTests(unittest.IsolatedAsyncioTestCase):
    def test_auto_fallback_preserves_available_groq_and_google_genai_providers(self):
        gateway = LLMGateway()
        with (
            patch.object(settings, "LLM_PROVIDER", None),
            patch.object(settings, "PRIMARY_LLM_PROVIDER", "groq"),
            patch.object(settings, "LLM_FALLBACK_PROVIDER", None),
            patch.object(settings, "GROQ_API_KEY", "test-groq-key"),
            patch.object(settings, "GEMINI_API_KEY", "test-gemini-key"),
        ):
            self.assertEqual(
                gateway._provider_order(), ["groq", "google_genai"]
            )

    async def test_falls_back_to_next_provider_and_parses_json(self):
        primary = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("busy")))
        fallback = SimpleNamespace(
            ainvoke=AsyncMock(return_value=SimpleNamespace(content='{"reply":"ok"}'))
        )
        gateway = LLMGateway()

        with (
            patch.object(gateway, "_provider_order", return_value=["groq", "google_genai"]),
            patch("app.services.llm_gateway.get_llm", side_effect=[primary, fallback]),
        ):
            result = await gateway.chat_completion(
                [{"role": "user", "content": "hello"}]
            )

        self.assertEqual(result, {"reply": "ok"})
        primary.ainvoke.assert_awaited_once()
        fallback.ainvoke.assert_awaited_once()

    async def test_non_json_completion_returns_text(self):
        model = SimpleNamespace(
            ainvoke=AsyncMock(return_value=SimpleNamespace(content="hello there"))
        )
        gateway = LLMGateway()

        with (
            patch.object(gateway, "_provider_order", return_value=["groq"]),
            patch("app.services.llm_gateway.get_llm", return_value=model),
        ):
            result = await gateway.chat_completion(
                [{"role": "user", "content": "hello"}], json_mode=False
            )

        self.assertEqual(result, {"text": "hello there"})
