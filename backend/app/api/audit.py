"""
audit.py  —  POST /api/audit
──────────────────────────────
Full eligibility evaluation endpoint.
Runs the RAG-backed compliance engine against an applicant's profile.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session

from app.core.schemas import AuditRequest, AuditResponse, IssueDetail
from app.core.database import get_session
from app.models.accounts import ChatSession, User
from app.models.applications import Application
from app.services.auth import get_current_user, verify_request_origin
from app.services.audit_graph import AuditState, run_audit_graph
from app.services.compliance_engine import evaluate_compliance

router = APIRouter(prefix="/audit", tags=["Audit"])
logger = logging.getLogger(__name__)


class AuditEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    applicant_data: dict[str, Any] = Field(default_factory=dict)
    visa_type: str = Field(min_length=1, max_length=100)
    application_id: uuid.UUID | None = Field(default=None, strict=False)

    @field_validator("visa_type")
    @classmethod
    def normalize_visa_type(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("visa_type cannot be empty.")
        return value


@router.post("", response_model=AuditResponse)
async def run_audit(
    request: AuditRequest,
    http_request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    verify_request_origin(http_request)
    chat_session = session.get(ChatSession, request.session_id)
    if not chat_session or chat_session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Chat session not found.")

    try:
        result = await evaluate_compliance(
            applicant_profile=request.applicant_profile,
            visa_type=request.visa_type,
            session_id=request.session_id,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Audit failed: {str(e)}")

    issues = [IssueDetail(**issue) for issue in result.get("issues", [])]

    return AuditResponse(
        session_id=request.session_id,
        visa_type=request.visa_type,
        overall_passed=result["overall_passed"],
        issues=issues,
        summary=result["summary"],
        retrieved_policy_chunks=result.get("retrieved_policy_chunks", 0),
    )


@router.post("/evaluate", response_model=AuditState)
async def evaluate_application(
    payload: AuditEvaluateRequest,
    http_request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> AuditState:
    verify_request_origin(http_request)
    applicant_data = dict(payload.applicant_data)
    application = None

    if payload.application_id:
        application = session.get(Application, payload.application_id)
        if not application or application.user_id != user.id:
            raise HTTPException(status_code=404, detail="Application not found.")
        if payload.visa_type.lower() != application.visa_type.strip().lower():
            raise HTTPException(
                status_code=422,
                detail="visa_type must match the selected application.",
            )
        # Persisted OCR/profile data takes precedence over client-supplied values.
        applicant_data = {**applicant_data, **(application.applicant_data or {})}

    try:
        result = await run_audit_graph(
            applicant_data=applicant_data,
            visa_type=payload.visa_type,
        )
    except Exception as exc:
        logger.exception("Graph-based audit evaluation failed")
        raise HTTPException(
            status_code=503,
            detail="The audit evaluation could not be completed.",
        ) from exc

    if application:
        application.latest_audit = result.model_dump(mode="json")
        application.stage = result.status.lower()
        session.add(application)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception("Failed to persist application audit result")
            raise HTTPException(
                status_code=500,
                detail="The audit completed but its result could not be saved.",
            ) from exc

    return result
