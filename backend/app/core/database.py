"""
database.py
───────────
PostgreSQL Database connection & session management for TravelForge.
"""

from sqlalchemy import text
from sqlmodel import SQLModel, create_engine, Session, select
from app.core.config import settings
from app.models.trusted_sources import TrustedSource, AuditTicket
from app.models.rag_chunk import RAGChunk
from app.models.accounts import User, UserSession, ChatSession, ChatMessage
from app.models.applications import Application, ApplicationDocument
from app.models.rules import RuleDraftRecord, ActiveRuleRecord

# Convert asyncpg URL to psycopg2 URL for synchronous operations
db_url = settings.DATABASE_URL
if "+asyncpg" in db_url:
    sync_db_url = db_url.replace("+asyncpg", "+psycopg2")
elif "postgresql://" in db_url and "+psycopg2" not in db_url:
    sync_db_url = db_url.replace("postgresql://", "postgresql+psycopg2://")
else:
    sync_db_url = db_url

# Replace localhost with 127.0.0.1 if needed for reliable connection
sync_db_url = sync_db_url.replace("localhost", "127.0.0.1")

engine = create_engine(sync_db_url, echo=False)


def get_session():
    with Session(engine) as session:
        yield session


def init_db():
    """Install pgvector, create application tables, and seed trusted sources."""
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE application_documents "
                "ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_rag_chunks_embedding_hnsw "
                "ON rag_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )
    
    with Session(engine) as session:
        # Check if already seeded
        existing = session.exec(select(TrustedSource)).first()
        if not existing:
            default_sources = [
                TrustedSource(
                    domain="gov.uk",
                    name="UK Home Office - Visas and Immigration (UKVI)",
                    base_url="https://www.gov.uk/browse/visas-immigration",
                    jurisdiction_country="UK",
                    category="government_statutory",
                    trust_level="gold",
                    can_answer_autonomously=True,
                    notes="Primary legal authority for UK visas, financial thresholds, and eligibility."
                ),
                TrustedSource(
                    domain="travel.state.gov",
                    name="US Department of State - Bureau of Consular Affairs",
                    base_url="https://travel.state.gov",
                    jurisdiction_country="US",
                    category="government_statutory",
                    trust_level="gold",
                    can_answer_autonomously=True,
                    notes="Primary statutory authority for US visas (B1/B2, DS-160, wait times)."
                ),
                TrustedSource(
                    domain="vfsglobal.com",
                    name="VFS Global Official Partner",
                    base_url="https://www.vfsglobal.com",
                    jurisdiction_country="GLOBAL",
                    category="embassy",
                    trust_level="silver",
                    can_answer_autonomously=True,
                    notes="Official biometric enrollment and application center partner."
                ),
                TrustedSource(
                    domain="wakanow.com",
                    name="Wakanow Travel Agency Portal",
                    base_url="https://www.wakanow.com",
                    jurisdiction_country="GLOBAL",
                    category="booking_partner",
                    trust_level="silver",
                    can_answer_autonomously=False,
                    notes="Commercial travel partner; fee changes and rules require consultant sign-off."
                ),
            ]
            for src in default_sources:
                session.add(src)
            session.commit()
            print("✅ Seeded default whitelisted trusted_sources in PostgreSQL.")
