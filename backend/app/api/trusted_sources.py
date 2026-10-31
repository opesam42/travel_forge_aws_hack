"""
trusted_sources.py  —  /api/trusted-sources
────────────────────────────────────────────
Endpoints for managing domain whitelist boundaries.
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.trusted_sources import TrustedSource

router = APIRouter(prefix="/trusted-sources", tags=["Trusted Sources"])


class TrustedSourceCreate(BaseModel):
    domain: str
    name: str
    base_url: str
    jurisdiction_country: str = "GLOBAL"
    category: str = "government_statutory"
    trust_level: str = "gold"
    can_answer_autonomously: bool = False
    notes: Optional[str] = None


@router.get("", response_model=List[TrustedSource])
def list_trusted_sources(
    country: Optional[str] = None,
    only_active: bool = True,
    session: Session = Depends(get_session),
):
    """Retrieve all whitelisted domains for AI web search & verification."""
    query = select(TrustedSource)
    if only_active:
        query = query.where(TrustedSource.is_active == True)
    if country:
        query = query.where(TrustedSource.jurisdiction_country.in_([country, "GLOBAL"]))
    return session.exec(query).all()


@router.post("", response_model=TrustedSource)
def create_trusted_source(
    payload: TrustedSourceCreate,
    session: Session = Depends(get_session),
):
    """Add a new verified domain to the whitelist."""
    existing = session.exec(select(TrustedSource).where(TrustedSource.domain == payload.domain.lower())).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Domain '{payload.domain}' is already whitelisted.")

    new_source = TrustedSource(
        domain=payload.domain.lower().strip(),
        name=payload.name,
        base_url=payload.base_url,
        jurisdiction_country=payload.jurisdiction_country.upper(),
        category=payload.category,
        trust_level=payload.trust_level,
        can_answer_autonomously=payload.can_answer_autonomously,
        notes=payload.notes,
        is_active=True,
    )
    session.add(new_source)
    session.commit()
    session.refresh(new_source)
    return new_source


@router.patch("/{source_id}/toggle-active")
def toggle_source_active(
    source_id: str,
    session: Session = Depends(get_session),
):
    """Toggle domain active status (enables or blocks the domain immediately)."""
    import uuid
    source = session.get(TrustedSource, uuid.UUID(source_id))
    if not source:
        raise HTTPException(status_code=404, detail="Trusted source not found.")
    source.is_active = not source.is_active
    session.add(source)
    session.commit()
    session.refresh(source)
    return {"id": str(source.id), "domain": source.domain, "is_active": source.is_active}
