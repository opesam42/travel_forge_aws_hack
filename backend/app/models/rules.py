from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class RuleDraftRecord(SQLModel, table=True):
    __tablename__ = "rule_drafts"

    rule_id: str = Field(primary_key=True, max_length=150)
    visa_type: str = Field(index=True, max_length=100)
    is_complete: bool = Field(default=False, index=True)
    data: Dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class ActiveRuleRecord(SQLModel, table=True):
    __tablename__ = "active_rules"

    rule_id: str = Field(primary_key=True, max_length=150)
    visa_type: str = Field(index=True, max_length=100)
    is_active: bool = Field(default=True, index=True)
    data: Dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
