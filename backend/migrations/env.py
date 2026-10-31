from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

from app.core.config import settings
from app.models.accounts import ChatMessage, ChatSession, User, UserSession
from app.models.applications import Application, ApplicationDocument, CaseActivity
from app.models.rag_chunk import RAGChunk
from app.models.rules import ActiveRuleRecord, RuleDraftRecord
from app.models.trusted_sources import AuditTicket, TrustedSource

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL.replace(
            "+asyncpg",
            "+psycopg2",
        ).replace(
            "postgresql://",
            "postgresql+psycopg2://",
            1,
        ),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    database_url = settings.DATABASE_URL
    if "+asyncpg" in database_url:
        database_url = database_url.replace("+asyncpg", "+psycopg2")
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+psycopg2://",
            1,
        )
    migration_engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with migration_engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        migration_engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
