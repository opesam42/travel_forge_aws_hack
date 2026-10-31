"""Add staff case assignment and review activity.

Revision ID: 20261031_01
Revises: 20260618_01
"""
from alembic import context, op
import sqlalchemy as sa

revision = "20261031_01"
down_revision = "20260618_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column(
            "applications",
            sa.Column("assigned_staff_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            "fk_applications_assigned_staff_id_users",
            "applications",
            "users",
            ["assigned_staff_id"],
            ["id"],
        )
        op.create_index(
            "ix_applications_assigned_staff_id",
            "applications",
            ["assigned_staff_id"],
        )
        op.create_table(
            "case_activities",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("application_id", sa.Uuid(), nullable=False),
            sa.Column("actor_user_id", sa.Uuid(), nullable=False),
            sa.Column("event_type", sa.String(length=30), nullable=False),
            sa.Column("outcome", sa.String(length=50), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("document_id", sa.Uuid(), nullable=True),
            sa.Column("assigned_staff_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["document_id"], ["application_documents.id"]),
            sa.ForeignKeyConstraint(["assigned_staff_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_case_activities_application_id",
            "case_activities",
            ["application_id"],
        )
        op.create_index(
            "ix_case_activities_actor_user_id",
            "case_activities",
            ["actor_user_id"],
        )
        op.create_index(
            "ix_case_activities_document_id",
            "case_activities",
            ["document_id"],
        )
        op.create_index(
            "ix_case_activities_created_at",
            "case_activities",
            ["created_at"],
        )
        return

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "applications" not in tables or "users" not in tables:
        return

    application_columns = {
        column["name"] for column in inspector.get_columns("applications")
    }
    if "assigned_staff_id" not in application_columns:
        op.add_column(
            "applications",
            sa.Column("assigned_staff_id", sa.Uuid(), nullable=True),
        )
    application_foreign_keys = inspector.get_foreign_keys("applications")
    if not any(
        foreign_key["constrained_columns"] == ["assigned_staff_id"]
        and foreign_key["referred_table"] == "users"
        for foreign_key in application_foreign_keys
    ):
        op.create_foreign_key(
            "fk_applications_assigned_staff_id_users",
            "applications",
            "users",
            ["assigned_staff_id"],
            ["id"],
        )
    application_indexes = {
        index["name"] for index in inspector.get_indexes("applications")
    }
    if "ix_applications_assigned_staff_id" not in application_indexes:
        op.create_index(
            "ix_applications_assigned_staff_id",
            "applications",
            ["assigned_staff_id"],
        )

    if "case_activities" not in tables:
        op.create_table(
            "case_activities",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("application_id", sa.Uuid(), nullable=False),
            sa.Column("actor_user_id", sa.Uuid(), nullable=False),
            sa.Column("event_type", sa.String(length=30), nullable=False),
            sa.Column("outcome", sa.String(length=50), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("document_id", sa.Uuid(), nullable=True),
            sa.Column("assigned_staff_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["document_id"], ["application_documents.id"]),
            sa.ForeignKeyConstraint(["assigned_staff_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in (
            "application_id",
            "actor_user_id",
            "document_id",
            "created_at",
        ):
            op.create_index(
                f"ix_case_activities_{column}",
                "case_activities",
                [column],
            )


def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_index("ix_case_activities_created_at", table_name="case_activities")
        op.drop_index("ix_case_activities_document_id", table_name="case_activities")
        op.drop_index("ix_case_activities_actor_user_id", table_name="case_activities")
        op.drop_index("ix_case_activities_application_id", table_name="case_activities")
        op.drop_table("case_activities")
        op.drop_index("ix_applications_assigned_staff_id", table_name="applications")
        op.drop_constraint(
            "fk_applications_assigned_staff_id_users",
            "applications",
            type_="foreignkey",
        )
        op.drop_column("applications", "assigned_staff_id")
        return

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "case_activities" in tables:
        indexes = {
            index["name"] for index in inspector.get_indexes("case_activities")
        }
        for name in (
            "ix_case_activities_created_at",
            "ix_case_activities_document_id",
            "ix_case_activities_actor_user_id",
            "ix_case_activities_application_id",
        ):
            if name in indexes:
                op.drop_index(name, table_name="case_activities")
        op.drop_table("case_activities")
    if "applications" not in tables:
        return
    columns = {
        column["name"] for column in inspector.get_columns("applications")
    }
    if "assigned_staff_id" not in columns:
        return
    indexes = {
        index["name"] for index in inspector.get_indexes("applications")
    }
    if "ix_applications_assigned_staff_id" in indexes:
        op.drop_index("ix_applications_assigned_staff_id", table_name="applications")
    for foreign_key in inspector.get_foreign_keys("applications"):
        if (
            foreign_key["constrained_columns"] == ["assigned_staff_id"]
            and foreign_key["referred_table"] == "users"
            and foreign_key["name"]
        ):
            op.drop_constraint(
                foreign_key["name"],
                "applications",
                type_="foreignkey",
            )
            break
    op.drop_column("applications", "assigned_staff_id")
