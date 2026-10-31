import os
from pydantic_settings import BaseSettings
from typing import Literal, Optional

class Settings(BaseSettings):
    PROJECT_NAME: str = "TravelForge"
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    ENVIRONMENT: str = "development"
    CORS_ORIGIN: str = "http://localhost:5173"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://travelforge:travelforge@localhost:5432/travelforge"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Session authentication and chat memory
    AUTH_SESSION_TTL_HOURS: int = 168
    AUTH_COOKIE_NAME: str = "travelforge_session"
    CHAT_HISTORY_LIMIT: int = 12
    CHAT_MESSAGE_MAX_LENGTH: int = 10000
    AUTH_COOKIE_SECURE: Optional[bool] = None
    AUTH_COOKIE_SAMESITE: Optional[Literal["lax", "strict", "none"]] = None

    # AI Providers (Gemma 4 via Google AI & Groq)
    GOOGLE_AI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    GROK_LLM_API_KEY: Optional[str] = None

    # Model Configuration
    PRIMARY_LLM_PROVIDER: str = "google_genai"  # "google_genai" primary with "groq" fallback
    LLM_PROVIDER: Optional[str] = None
    LLM_MODEL: Optional[str] = None
    LLM_FALLBACK_PROVIDER: Optional[str] = None
    GEMMA_MODEL: str = "gemma-4-26b-a4b-it"
    GEMINI_MODEL: str = "gemma-4-26b-a4b-it"
    GROQ_MODEL: str = "gemma-4-26b-a4b-it"

    @property
    def get_gemini_key(self) -> Optional[str]:
        return self.GEMINI_API_KEY or self.GOOGLE_AI_API_KEY

    @property
    def get_groq_key(self) -> Optional[str]:
        return self.GROQ_API_KEY or self.GROK_LLM_API_KEY

    # Embeddings
    EMBEDDING_MODEL: str = "models/gemini-embedding-001"
    EMBEDDING_DIMENSIONS: int = 3072

    # PostgreSQL pgvector
    RAG_EMBEDDING_CACHE_DIR: str = "~/.cache/travelforge/all-MiniLM-L6-v2"
    RAG_DISTANCE_THRESHOLD: float = 0.55

    # Qdrant is retained only as the source for the one-time migration command.
    QDRANT_API_KEY: Optional[str] = None
    QDRANT_CLUSTER_ENDPOINT_KEY: Optional[str] = None
    QDRANT_COLLECTION_NAME: str = "business_knowledge"

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGIN.split(",") if origin.strip()]

    @property
    def secure_auth_cookie(self) -> bool:
        if self.AUTH_COOKIE_SECURE is not None:
            return self.AUTH_COOKIE_SECURE
        return self.ENVIRONMENT.lower() == "production"

    @property
    def auth_cookie_samesite(self) -> Literal["lax", "strict", "none"]:
        if self.AUTH_COOKIE_SAMESITE:
            return self.AUTH_COOKIE_SAMESITE
        return "none" if self.secure_auth_cookie else "lax"

    # LLM
    CHAT_MODEL: str = "gemma-4-26b-a4b-it"
    AUDIT_MODEL: str = "gemma-4-26b-a4b-it"
    RAG_TOP_K: int = 5                  # How many chunks to retrieve per query

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
