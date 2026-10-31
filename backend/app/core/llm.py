"""Provider-agnostic LangChain chat-model factory."""

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.core.config import settings

SUPPORTED_PROVIDERS = {
    "google_genai",
    "groq",
}


def _resolve_provider(provider: str | None) -> str:
    selected = (provider or settings.LLM_PROVIDER or settings.PRIMARY_LLM_PROVIDER).lower()
    if selected in {"google", "gemini"}:
        selected = "google_genai"
    elif selected == "grok":
        selected = "groq"
    if selected not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported LLM provider '{selected}'. "
            f"Choose one of: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
        )
    return selected


def _resolve_model(model_name: str | None, provider: str) -> str:
    if model_name:
        return model_name
    if settings.LLM_MODEL:
        return settings.LLM_MODEL
    defaults = {
        "google_genai": settings.GEMMA_MODEL or settings.GEMINI_MODEL,
        "groq": settings.GROQ_MODEL or settings.GEMMA_MODEL,
    }
    return defaults[provider]


def _provider_options(provider: str) -> dict[str, Any]:
    if provider == "google_genai":
        api_key = settings.get_gemini_key
        if not api_key or api_key.startswith("your_"):
            raise RuntimeError(
                "GEMINI_API_KEY or GOOGLE_AI_API_KEY is required for Google AI."
            )
        return {"api_key": api_key}
    if provider == "groq":
        api_key = settings.get_groq_key
        if not api_key or api_key.startswith("your_"):
            raise RuntimeError("GROQ_API_KEY is required for the Groq provider.")
        return {"api_key": api_key}
    return {}


def get_llm(
    model_name: str | None = None,
    provider: str | None = None,
    temperature: float = 0.0,
) -> BaseChatModel:
    """Create a LangChain chat model using validated application settings."""
    selected_provider = _resolve_provider(provider)
    selected_model = _resolve_model(model_name, selected_provider)
    return init_chat_model(
        model=selected_model,
        model_provider=selected_provider,
        temperature=temperature,
        **_provider_options(selected_provider),
    )
