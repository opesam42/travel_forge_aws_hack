"""Provider-agnostic LangChain chat-model factory."""

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.core.config import settings

SUPPORTED_PROVIDERS = {
    "openai",
    "anthropic",
    "google_genai",
    "groq",
    "ollama",
}


def _resolve_provider(provider: str | None) -> str:
    selected = (provider or settings.LLM_PROVIDER or settings.PRIMARY_LLM_PROVIDER).lower()
    if selected == "google":
        selected = "google_genai"
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
        "openai": settings.CHAT_MODEL,
        "anthropic": settings.ANTHROPIC_MODEL,
        "google_genai": settings.GEMINI_MODEL,
        "groq": settings.GROQ_MODEL,
        "ollama": settings.OLLAMA_MODEL,
    }
    return defaults[provider]


def _provider_options(provider: str) -> dict[str, Any]:
    if provider == "openai":
        if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY.startswith("your_"):
            raise RuntimeError("OPENAI_API_KEY is required for the OpenAI provider.")
        return {"api_key": settings.OPENAI_API_KEY}
    if provider == "anthropic":
        if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY.startswith(
            "your_"
        ):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is required for the Anthropic provider."
            )
        return {"api_key": settings.ANTHROPIC_API_KEY}
    if provider == "google_genai":
        api_key = settings.get_gemini_key
        if not api_key or api_key.startswith("your_"):
            raise RuntimeError(
                "GEMINI_API_KEY or GOOGLE_AI_API_KEY is required for Google Gemini."
            )
        return {"api_key": api_key}
    if provider == "groq":
        api_key = settings.get_groq_key
        if not api_key or api_key.startswith("your_"):
            raise RuntimeError("GROQ_API_KEY is required for the Groq provider.")
        return {"api_key": api_key}
    if provider == "ollama":
        return {"base_url": settings.OLLAMA_BASE_URL}
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
