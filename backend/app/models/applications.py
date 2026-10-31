import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import CheckConstraint, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Application(SQLModel, table=True):
    __tablename__ = "applications"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    assigned_staff_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="users.id",
        index=True,
    )
    visa_type: str = Field(max_length=100, index=True)
    destination: str | None = Field(default=None, max_length=100)
    stage: str = Field(
        default="intake",
        sa_column=Column(String(40), nullable=False, server_default="intake"),
    )
    applicant_data: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )
    latest_audit: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CaseActivity(SQLModel, table=True):
    __tablename__ = "case_activities"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    application_id: uuid.UUID = Field(foreign_key="applications.id", index=True)
    actor_user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    event_type: str = Field(max_length=30)
    outcome: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    document_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="application_documents.id",
        index=True,
    )
    assigned_staff_id: uuid.UUID | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class ApplicationDocument(SQLModel, table=True):
    __tablename__ = "application_documents"
    __table_args__ = (
        CheckConstraint(
            "doc_type IN ('passport', 'bank_statement')",
            name="ck_application_documents_doc_type",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    application_id: uuid.UUID = Field(
        foreign_key="applications.id",
        index=True,
    )
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    doc_type: str = Field(max_length=30)
    original_filename: str = Field(max_length=255)
    content_type: str = Field(max_length=100)
    review_status: str = Field(
        default="pending_review",
        sa_column=Column(
            String(30),
            nullable=False,
            server_default="pending_review",
        ),
    )
    extracted_data: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    review_notes: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    reviewed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
