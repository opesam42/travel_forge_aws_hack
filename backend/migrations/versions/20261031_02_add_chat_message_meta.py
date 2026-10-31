"""Add meta column to chat_messages.

Revision ID: 20261031_02
Revises: 20261031_01
"""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261031_02"
down_revision = "20261031_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column(
            "chat_messages",
            sa.Column(
                "meta",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
        )
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "chat_messages" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("chat_messages")}
    if "meta" not in columns:
        op.add_column(
            "chat_messages",
            sa.Column(
                "meta",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_column("chat_messages", "meta")
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "chat_messages" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("chat_messages")}
    if "meta" in columns:
        op.drop_column("chat_messages", "meta")
