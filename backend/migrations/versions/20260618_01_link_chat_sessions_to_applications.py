"""Link chat sessions to applications.

Revision ID: 20260618_01
Revises:
"""
from alembic import context, op
import sqlalchemy as sa

revision = "20260618_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if context.is_offline_mode():
        op.add_column(
            "chat_sessions",
            sa.Column("application_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            "fk_chat_sessions_application_id_applications",
            "chat_sessions",
            "applications",
            ["application_id"],
            ["id"],
        )
        op.create_index(
            "ix_chat_sessions_application_id",
            "chat_sessions",
            ["application_id"],
            unique=False,
        )
        return
    inspector = sa.inspect(bind)
    if "chat_sessions" not in inspector.get_table_names():
        return

    tables = set(inspector.get_table_names())
    columns = {column["name"] for column in inspector.get_columns("chat_sessions")}
    if "application_id" not in columns and "applications" not in tables:
        raise RuntimeError(
            "Cannot link chat sessions: the applications table does not exist."
        )

    if "application_id" not in columns:
        op.add_column(
            "chat_sessions",
            sa.Column("application_id", sa.Uuid(), nullable=True),
        )

    foreign_keys = inspector.get_foreign_keys("chat_sessions")
    has_application_foreign_key = any(
        foreign_key["constrained_columns"] == ["application_id"]
        and foreign_key["referred_table"] == "applications"
        for foreign_key in foreign_keys
    )
    if not has_application_foreign_key:
        op.create_foreign_key(
            "fk_chat_sessions_application_id_applications",
            "chat_sessions",
            "applications",
            ["application_id"],
            ["id"],
        )

    indexes = {index["name"] for index in inspector.get_indexes("chat_sessions")}
    if "ix_chat_sessions_application_id" not in indexes:
        op.create_index(
            "ix_chat_sessions_application_id",
            "chat_sessions",
            ["application_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if context.is_offline_mode():
        op.drop_index("ix_chat_sessions_application_id", table_name="chat_sessions")
        op.drop_constraint(
            "fk_chat_sessions_application_id_applications",
            "chat_sessions",
            type_="foreignkey",
        )
        op.drop_column("chat_sessions", "application_id")
        return
    inspector = sa.inspect(bind)
    if "chat_sessions" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("chat_sessions")}
    if "application_id" not in columns:
        return
    op.drop_index("ix_chat_sessions_application_id", table_name="chat_sessions")
    foreign_keys = inspector.get_foreign_keys("chat_sessions")
    for foreign_key in foreign_keys:
        if (
            foreign_key["constrained_columns"] == ["application_id"]
            and foreign_key["referred_table"] == "applications"
            and foreign_key["name"]
        ):
            op.drop_constraint(
                foreign_key["name"],
                "chat_sessions",
                type_="foreignkey",
            )
            break
    op.drop_column("chat_sessions", "application_id")
