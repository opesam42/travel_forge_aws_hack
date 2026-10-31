"""
trusted_sources.py
──────────────────
PostgreSQL models for:
1. TrustedSource: Whitelisted domain boundaries for the AI Agent
2. AuditTicket: Continuous learning & knowledge flywheel review tickets
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import DateTime, Text, Boolean, text


class TrustedSource(SQLModel, table=True):
    __tablename__ = "trusted_sources"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
    )
    domain: str = Field(
        max_length=255,
        index=True,
        sa_column_kwargs={"unique": True, "comment": "Root domain or subdomain, e.g. 'gov.uk'"},
    )
    name: str = Field(max_length=255)
    base_url: str = Field(max_length=500)
    jurisdiction_country: str = Field(
        max_length=10,
        index=True,
        sa_column_kwargs={"comment": "ISO country code, e.g. 'UK', 'US', or 'GLOBAL'"},
    )
    category: str = Field(
        max_length=50,
        sa_column_kwargs={"comment": "'government_statutory', 'embassy', 'booking_partner'"},
    )
    trust_level: str = Field(
        default="gold",
        sa_column_kwargs={"comment": "'gold' (legal authority) vs 'silver' (commercial partner)"},
    )
    can_answer_autonomously: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,
            nullable=False,
            server_default=text("false"),
            comment="If true, AI quotes directly; if false, routes as an audit ticket.",
        ),
    )
    is_active: bool = Field(
        default=True,
        sa_column=Column(
            Boolean,
            nullable=False,
            server_default=text("true"),
            comment="Soft toggle to preserve audit history.",
        ),
    )
    notes: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AuditTicket(SQLModel, table=True):
    __tablename__ = "audit_tickets"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
    )
    ticket_type: str = Field(
        max_length=50,
        sa_column_kwargs={"comment": "'knowledge_gap', 'policy_update', 'ambiguity_review'"},
    )
    title: str = Field(max_length=255)
    description: str = Field(sa_column=Column(Text, nullable=False))
    target_country: str = Field(max_length=10, default="GLOBAL")
    
    # The proposed rule or FAQ synthesized from the whitelisted site
    proposed_content: str = Field(sa_column=Column(Text, nullable=False))
    source_url: Optional[str] = Field(default=None, max_length=500)
    source_domain: Optional[str] = Field(default=None, max_length=255)
    
    status: str = Field(
        default="pending",
        sa_column_kwargs={"comment": "'pending', 'approved', 'rejected'"},
    )
    frequency_count: int = Field(
        default=1,
        sa_column_kwargs={"comment": "Count of how many times users encountered this gap"},
    )
    approved_by: Optional[str] = Field(default=None, max_length=100)
    approved_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
