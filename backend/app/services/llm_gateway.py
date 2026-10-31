"""Provider-independent completion gateway with optional provider failover."""

import json
import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.llm import SUPPORTED_PROVIDERS, get_llm

logger = logging.getLogger(__name__)


def clean_json_text(text: str) -> str:
    """Strip optional Markdown fences around a JSON response."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _response_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [
            part if isinstance(part, str) else part.get("text", "")
            for part in content
            if isinstance(part, (str, dict))
        ]
        return "".join(text_parts)
    raise TypeError("The configured LLM returned a non-text response.")


class LLMGateway:
    @staticmethod
    def _has_credentials(provider: str) -> bool:
        credentials = {
            "google_genai": settings.get_gemini_key,
            "groq": settings.get_groq_key,
        }
        key = credentials.get(provider)
        return bool(key and not key.startswith("your_"))

    def _provider_order(self) -> list[str]:
        primary = (
            settings.LLM_PROVIDER or settings.PRIMARY_LLM_PROVIDER or "google_genai"
        ).lower()
        if primary in {"gemini", "google"}:
            primary = "google_genai"
        elif primary == "grok":
            primary = "groq"
        if primary not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported LLM provider '{primary}'. "
                f"Choose one of: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
            )

        fallback = settings.LLM_FALLBACK_PROVIDER
        if fallback:
            fallback = fallback.lower()
            if fallback in {"gemini", "google"}:
                fallback = "google_genai"
            elif fallback == "grok":
                fallback = "groq"
            if fallback not in SUPPORTED_PROVIDERS:
                raise ValueError(
                    f"Unsupported LLM fallback provider '{fallback}'. "
                    f"Choose one of: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
                )
            candidates = [fallback]
        else:
            preferred = {
                "google_genai": "groq",
                "groq": "google_genai",
            }.get(primary)
            candidates = [preferred] if preferred else []
            candidates.extend(
                provider
                for provider in ("google_genai", "groq")
                if provider not in candidates and self._has_credentials(provider)
            )
        return [primary] + [
            provider
            for provider in candidates
            if provider and provider != primary and self._has_credentials(provider)
        ]

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        json_mode: bool = True,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Complete a chat request, trying configured providers in order."""
        normalized_messages: list[dict[str, str]] = []
        if system_instruction:
            normalized_messages.append(
                {"role": "system", "content": system_instruction}
            )
        normalized_messages.extend(messages)
        if json_mode:
            system_message = next(
                (
                    message
                    for message in normalized_messages
                    if message["role"] == "system"
                ),
                None,
            )
            json_instruction = "Respond with valid JSON only, without Markdown fences."
            if system_message is None:
                normalized_messages.insert(
                    0, {"role": "system", "content": json_instruction}
                )
            elif json_instruction not in system_message["content"]:
                system_message["content"] += f"\n\n{json_instruction}"
        last_error: Exception | None = None

        for provider in self._provider_order():
            try:
                model = get_llm(
                    model_name=model_name,
                    provider=provider,
                    temperature=temperature,
                )
                response = await model.ainvoke(normalized_messages)
                raw_text = _response_text(response.content)
                if json_mode:
                    return json.loads(clean_json_text(raw_text))
                return {"text": raw_text}
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "LLM provider %s failed; trying the next configured provider: %s",
                    provider,
                    exc,
                )

        if last_error is not None:
            raise RuntimeError("All configured LLM providers failed.") from last_error
        raise RuntimeError("No LLM provider is configured.")


gateway = LLMGateway()
