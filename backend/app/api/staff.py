from datetime import datetime, timezone
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session, select

from app.api.applications import (
    ApplicationResponse,
    _application_response,
    update_application_timestamp,
)
from app.core.database import get_session
from app.models.accounts import User
from app.models.applications import (
    Application,
    ApplicationDocument,
    CaseActivity,
)
from app.services.auth import require_staff_user, verify_request_origin

router = APIRouter(prefix="/staff", tags=["Agency staff"])
logger = logging.getLogger(__name__)


class StaffCaseSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    application_id: uuid.UUID
    applicant_name: str
    applicant_email: str
    visa_type: str
    destination: str | None
    stage: str
    assigned_staff_id: uuid.UUID | None
    assigned_staff_name: str | None
    updated_at: datetime
    pending_document_count: int
    audit_status: str | None
    latest_review_outcome: str | None


class CaseActivityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: uuid.UUID
    event_type: str
    outcome: str | None
    notes: str | None
    document_id: uuid.UUID | None
    actor_name: str
    assigned_staff_name: str | None
    created_at: datetime


class StaffCaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    case: ApplicationResponse
    applicant_name: str
    applicant_email: str
    assigned_staff_id: uuid.UUID | None
    assigned_staff_name: str | None
    activity: list[CaseActivityResponse]


class AssignableStaffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    name: str


class AssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    assigned_staff_id: uuid.UUID | None = Field(..., strict=False)
    notes: str | None = Field(default=None, max_length=2000)


class StaffReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    review_type: str = Field(pattern=r"^(case_review|document_review)$")
    outcome: str = Field(
        pattern=r"^(needs_changes|ready_for_agency_handoff|needs_further_review|reviewed|needs_clarification)$"
    )
    document_id: uuid.UUID | None = Field(default=None, strict=False)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_review_scope(self):
        case_outcomes = {
            "needs_changes",
            "ready_for_agency_handoff",
            "needs_further_review",
        }
        document_outcomes = {"reviewed", "needs_clarification"}
        if self.review_type == "case_review":
            if self.outcome not in case_outcomes or self.document_id:
                raise ValueError(
                    "Case reviews require a case outcome and cannot reference a document."
                )
        elif self.outcome not in document_outcomes or not self.document_id:
            raise ValueError(
                "Document reviews require a document and a document review outcome."
            )
        return self


def _require_assigned_case(
    application_id: uuid.UUID,
    user: User,
    session: Session,
) -> Application:
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Case not found.")
    if user.user_type == "staff" and application.assigned_staff_id != user.id:
        raise HTTPException(status_code=404, detail="Case not found.")
    return application


def _latest_case_review(session: Session, application_id: uuid.UUID) -> str | None:
    reviews = session.exec(
        select(CaseActivity)
        .where(
            CaseActivity.application_id == application_id,
            CaseActivity.event_type == "case_review",
        )
        .order_by(CaseActivity.created_at.desc())
    ).all()
    return reviews[0].outcome if reviews else None


def _case_response(
    application: Application,
    session: Session,
) -> StaffCaseResponse:
    applicant = session.get(User, application.user_id)
    assigned_staff = (
        session.get(User, application.assigned_staff_id)
        if application.assigned_staff_id
        else None
    )
    documents = session.exec(
        select(ApplicationDocument)
        .where(ApplicationDocument.application_id == application.id)
        .order_by(ApplicationDocument.created_at.desc())
    ).all()
    activities = session.exec(
        select(CaseActivity)
        .where(CaseActivity.application_id == application.id)
        .order_by(CaseActivity.created_at.desc())
    ).all()
    activity_response = []
    for activity in activities:
        actor = session.get(User, activity.actor_user_id)
        assigned_activity_user = (
            session.get(User, activity.assigned_staff_id)
            if activity.assigned_staff_id
            else None
        )
        activity_response.append(
            CaseActivityResponse(
                id=activity.id,
                event_type=activity.event_type,
                outcome=activity.outcome,
                notes=activity.notes,
                document_id=activity.document_id,
                actor_name=actor.name if actor else "Former user",
                assigned_staff_name=(
                    assigned_activity_user.name if assigned_activity_user else None
                ),
                created_at=activity.created_at,
            )
        )
    return StaffCaseResponse(
        case=_application_response(application, documents),
        applicant_name=applicant.name if applicant else "Applicant",
        applicant_email=applicant.email if applicant else "",
        assigned_staff_id=application.assigned_staff_id,
        assigned_staff_name=assigned_staff.name if assigned_staff else None,
        activity=activity_response,
    )


@router.get("/cases", response_model=list[StaffCaseSummary])
def list_staff_cases(
    request: Request,
    user: User = Depends(require_staff_user),
    session: Session = Depends(get_session),
) -> list[StaffCaseSummary]:
    verify_request_origin(request)
    statement = select(Application).order_by(Application.updated_at.desc())
    if user.user_type == "staff":
        statement = statement.where(Application.assigned_staff_id == user.id)
    applications = session.exec(statement).all()

    summaries = []
    for application in applications:
        applicant = session.get(User, application.user_id)
        assigned_staff = (
            session.get(User, application.assigned_staff_id)
            if application.assigned_staff_id
            else None
        )
        documents = session.exec(
            select(ApplicationDocument).where(
                ApplicationDocument.application_id == application.id
            )
        ).all()
        summaries.append(
            StaffCaseSummary(
                application_id=application.id,
                applicant_name=applicant.name if applicant else "Applicant",
                applicant_email=applicant.email if applicant else "",
                visa_type=application.visa_type,
                destination=application.destination,
                stage=application.stage,
                assigned_staff_id=application.assigned_staff_id,
                assigned_staff_name=assigned_staff.name if assigned_staff else None,
                updated_at=application.updated_at,
                pending_document_count=sum(
                    document.review_status == "pending_review"
                    for document in documents
                ),
                audit_status=(application.latest_audit or {}).get("status"),
                latest_review_outcome=_latest_case_review(session, application.id),
            )
        )
    return summaries


@router.get("/users", response_model=list[AssignableStaffResponse])
def list_assignable_staff(
    request: Request,
    user: User = Depends(require_staff_user),
    session: Session = Depends(get_session),
) -> list[AssignableStaffResponse]:
    verify_request_origin(request)
    if user.user_type != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    staff_users = session.exec(
        select(User)
        .where(User.user_type == "staff", User.is_active.is_(True))
        .order_by(User.name)
    ).all()
    return [
        AssignableStaffResponse(id=str(staff_user.id), name=staff_user.name)
        for staff_user in staff_users
    ]


@router.get("/cases/{application_id}", response_model=StaffCaseResponse)
def get_staff_case(
    application_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_staff_user),
    session: Session = Depends(get_session),
) -> StaffCaseResponse:
    verify_request_origin(request)
    application = _require_assigned_case(application_id, user, session)
    return _case_response(application, session)


@router.patch(
    "/cases/{application_id}/assignment",
    response_model=StaffCaseResponse,
)
def assign_staff_case(
    application_id: uuid.UUID,
    payload: AssignmentRequest,
    request: Request,
    user: User = Depends(require_staff_user),
    session: Session = Depends(get_session),
) -> StaffCaseResponse:
    verify_request_origin(request)
    if user.user_type != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Case not found.")
    if payload.assigned_staff_id:
        assignee = session.get(User, payload.assigned_staff_id)
        if not assignee or not assignee.is_active or assignee.user_type != "staff":
            raise HTTPException(
                status_code=422,
                detail="Cases can only be assigned to active staff accounts.",
            )
    if application.assigned_staff_id != payload.assigned_staff_id:
        previous_assignee = (
            session.get(User, application.assigned_staff_id)
            if application.assigned_staff_id
            else None
        )
        new_assignee = (
            session.get(User, payload.assigned_staff_id)
            if payload.assigned_staff_id
            else None
        )
        if payload.notes and payload.notes.strip():
            activity_notes = payload.notes.strip()
        elif previous_assignee and new_assignee:
            activity_notes = (
                f"Reassigned from {previous_assignee.name} to {new_assignee.name}."
            )
        elif new_assignee:
            activity_notes = f"Assigned to {new_assignee.name}."
        elif previous_assignee:
            activity_notes = f"Unassigned from {previous_assignee.name}."
        else:
            activity_notes = None
        application.assigned_staff_id = payload.assigned_staff_id
        update_application_timestamp(application)
        session.add(application)
        session.add(
            CaseActivity(
                application_id=application.id,
                actor_user_id=user.id,
                event_type="assignment",
                outcome=(
                    "reassigned"
                    if previous_assignee and new_assignee
                    else "assigned"
                    if new_assignee
                    else "unassigned"
                ),
                assigned_staff_id=payload.assigned_staff_id,
                notes=activity_notes,
            )
        )
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception("Failed to assign agency case")
            raise HTTPException(
                status_code=500,
                detail="Case assignment could not be saved.",
            ) from exc
    return _case_response(application, session)


@router.post(
    "/cases/{application_id}/reviews",
    response_model=CaseActivityResponse,
    status_code=201,
)
def create_case_review(
    application_id: uuid.UUID,
    payload: StaffReviewRequest,
    request: Request,
    user: User = Depends(require_staff_user),
    session: Session = Depends(get_session),
) -> CaseActivityResponse:
    verify_request_origin(request)
    application = _require_assigned_case(application_id, user, session)
    if payload.document_id:
        document = session.get(ApplicationDocument, payload.document_id)
        if (
            not document
            or document.application_id != application.id
            or document.user_id != application.user_id
        ):
            raise HTTPException(status_code=404, detail="Document not found.")

    activity = CaseActivity(
        application_id=application.id,
        actor_user_id=user.id,
        event_type=payload.review_type,
        outcome=payload.outcome,
        notes=payload.notes.strip() if payload.notes else None,
        document_id=payload.document_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(activity)
    update_application_timestamp(application)
    session.add(application)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("Failed to persist agency case review")
        raise HTTPException(
            status_code=500,
            detail="Agency review could not be saved.",
        ) from exc
    return CaseActivityResponse(
        id=activity.id,
        event_type=activity.event_type,
        outcome=activity.outcome,
        notes=activity.notes,
        document_id=activity.document_id,
        actor_name=user.name,
        assigned_staff_name=None,
        created_at=activity.created_at,
    )
