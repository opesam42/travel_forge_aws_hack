"""
audit_tickets.py  —  /api/audit-tickets
──────────────────────────────────────
Knowledge Flywheel review ticket management for agency consultants.
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, timezone
import uuid
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.trusted_sources import AuditTicket
from app.services.rag_service import ingest_text

router = APIRouter(prefix="/audit-tickets", tags=["Audit Tickets"])


@router.get("", response_model=List[AuditTicket])
def list_tickets(
    status: Optional[str] = None,
    session: Session = Depends(get_session),
):
    """List all knowledge gap and policy update tickets."""
    query = select(AuditTicket)
    if status:
        query = query.where(AuditTicket.status == status)
    return session.exec(query.order_by(AuditTicket.created_at.desc())).all()


@router.post("/{ticket_id}/approve")
async def approve_ticket(
    ticket_id: str,
    approved_by: str = "consultant",
    session: Session = Depends(get_session),
):
    """
    1-Click Operator Approval:
    Promotes the proposed policy content into the PostgreSQL pgvector knowledge base.
    """
    ticket = session.get(AuditTicket, uuid.UUID(ticket_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    if ticket.status == "approved":
        return {"message": "Ticket is already approved.", "ticket_id": str(ticket.id)}

    await ingest_text(
        text=ticket.proposed_content,
        source_name=ticket.source_domain or "operator_approved_ticket",
        doc_type="approved_policy_faq",
        metadata_extra={
            "ticket_id": str(ticket.id),
            "country": ticket.target_country,
        },
    )

    ticket.status = "approved"
    ticket.approved_by = approved_by
    ticket.approved_at = datetime.now(timezone.utc)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)

    return {
        "status": "success",
        "message": "Ticket approved! Proposed content embedded into the pgvector knowledge base.",
        "ticket_id": str(ticket.id),
    }


@router.post("/{ticket_id}/reject")
def reject_ticket(
    ticket_id: str,
    session: Session = Depends(get_session),
):
    """Dismisses an unverified or obsolete update."""
    ticket = session.get(AuditTicket, uuid.UUID(ticket_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    ticket.status = "rejected"
    session.add(ticket)
    session.commit()
    return {"status": "success", "ticket_id": str(ticket.id)}
