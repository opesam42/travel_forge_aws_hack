from datetime import datetime, timezone
import logging
from typing import Any, Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.accounts import ChatSession, User
from app.models.applications import Application, ApplicationDocument
from app.services.auth import get_current_user, verify_request_origin

router = APIRouter(prefix="/applications", tags=["Applications"])
logger = logging.getLogger(__name__)


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    visa_type: str = Field(min_length=1, max_length=100)
    destination: str | None = Field(default=None, max_length=100)
    applicant_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("visa_type")
    @classmethod
    def normalize_visa_type(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("visa_type cannot be empty.")
        return value

    @field_validator("destination")
    @classmethod
    def normalize_destination(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None

    @field_validator("applicant_data")
    @classmethod
    def validate_initial_applicant_data(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        allowed_fields = {
            "citizenship",
            "destination",
            "travel_purpose",
            "intended_travel_date",
            "employer",
        }
        if not set(value).issubset(allowed_fields):
            raise ValueError(
                "Initial applicant_data may contain only basic intake facts. "
                "Documents and verification state are server-managed."
            )
        proposed = {}
        for field_name, fact in value.items():
            if not isinstance(fact, str) or not fact.strip() or len(fact.strip()) > 200:
                raise ValueError(
                    f"Initial intake fact '{field_name}' must contain 1 to 200 characters."
                )
            proposed[field_name] = fact.strip()
        return {"proposed_intake": proposed} if proposed else {}


class ApplicationIntakeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    facts: dict[
        Literal[
            "citizenship",
            "destination",
            "travel_purpose",
            "intended_travel_date",
            "employer",
            "visa_type",
        ],
        str,
    ] = Field(default_factory=dict, max_length=6)
    confirm_fields: list[
        Literal[
            "citizenship",
            "destination",
            "travel_purpose",
            "intended_travel_date",
            "employer",
            "visa_type",
        ]
    ] = Field(default_factory=list)

    @field_validator("facts")
    @classmethod
    def validate_fact_values(cls, value: dict[str, str]) -> dict[str, str]:
        normalized = {}
        for key, fact in value.items():
            fact = fact.strip()
            if not fact or len(fact) > 200:
                raise ValueError(f"{key} must contain between 1 and 200 characters.")
            normalized[key] = fact
        return normalized

    @field_validator("confirm_fields")
    @classmethod
    def validate_confirm_fields(cls, value: list[str], info) -> list[str]:
        facts = info.data.get("facts", {})
        if not set(value).issubset(facts):
            raise ValueError("Each confirmed field must also be included in facts.")
        return value


class DocumentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: uuid.UUID
    doc_type: str
    original_filename: str
    review_status: str
    extracted_data: dict[str, Any]
    created_at: datetime
    reviewed_at: datetime | None


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: uuid.UUID
    visa_type: str
    destination: str | None
    stage: str
    applicant_data: dict[str, Any]
    latest_audit: dict[str, Any] | None
    documents: list[DocumentSummary]
    created_at: datetime
    updated_at: datetime


class ApplicationChatSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    application_id: uuid.UUID
    session_id: str


def _application_response(
    application: Application,
    documents: list[ApplicationDocument],
) -> ApplicationResponse:
    return ApplicationResponse(
        id=application.id,
        visa_type=application.visa_type,
        destination=application.destination,
        stage=application.stage,
        applicant_data=application.applicant_data,
        latest_audit=application.latest_audit,
        documents=[
            DocumentSummary(
                id=document.id,
                doc_type=document.doc_type,
                original_filename=document.original_filename,
                review_status=document.review_status,
                extracted_data=document.extracted_data,
                created_at=document.created_at,
                reviewed_at=document.reviewed_at,
            )
            for document in documents
        ],
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


@router.post("", response_model=ApplicationResponse, status_code=201)
def create_application(
    payload: ApplicationCreate,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ApplicationResponse:
    verify_request_origin(request)
    application = Application(
        user_id=user.id,
        visa_type=payload.visa_type,
        destination=payload.destination,
        applicant_data=payload.applicant_data,
    )
    session.add(application)
    session.commit()
    session.refresh(application)
    return _application_response(application, [])


@router.post(
    "/{application_id}/chat-session",
    response_model=ApplicationChatSessionResponse,
)
def get_or_create_application_chat_session(
    application_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ApplicationChatSessionResponse:
    """Return a chat session durably associated with this user's case."""
    verify_request_origin(request)
    application = require_owned_application(application_id, user, session)
    chat_session = session.exec(
        select(ChatSession)
        .where(
            ChatSession.application_id == application.id,
            ChatSession.user_id == user.id,
        )
        .order_by(ChatSession.updated_at.desc())
    ).first()
    if not chat_session:
        chat_session = ChatSession(
            session_id=str(uuid.uuid4()),
            user_id=user.id,
            application_id=application.id,
        )
        session.add(chat_session)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception("Failed to create application chat session")
            raise HTTPException(
                status_code=500,
                detail="A chat session could not be created for this application.",
            ) from exc
    return ApplicationChatSessionResponse(
        application_id=application.id,
        session_id=chat_session.session_id,
    )


@router.get("", response_model=list[ApplicationResponse])
def list_applications(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[ApplicationResponse]:
    verify_request_origin(request)
    applications = session.exec(
        select(Application)
        .where(Application.user_id == user.id)
        .order_by(Application.updated_at.desc())
    ).all()
    return [
        _application_response(
            application,
            session.exec(
                select(ApplicationDocument)
                .where(
                    ApplicationDocument.application_id == application.id,
                    ApplicationDocument.user_id == user.id,
                )
                .order_by(ApplicationDocument.created_at.desc())
            ).all(),
        )
        for application in applications
    ]


@router.get("/{application_id}", response_model=ApplicationResponse)
def get_application(
    application_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ApplicationResponse:
    verify_request_origin(request)
    application = session.get(Application, application_id)
    if not application or application.user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found.")
    documents = session.exec(
        select(ApplicationDocument)
        .where(
            ApplicationDocument.application_id == application.id,
            ApplicationDocument.user_id == user.id,
        )
        .order_by(ApplicationDocument.created_at.desc())
    ).all()
    return _application_response(application, documents)


@router.patch("/{application_id}/intake", response_model=ApplicationResponse)
def update_application_intake(
    application_id: uuid.UUID,
    payload: ApplicationIntakeUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ApplicationResponse:
    verify_request_origin(request)
    application = require_owned_application(application_id, user, session)
    applicant_data = dict(application.applicant_data or {})
    proposed = dict(applicant_data.get("proposed_intake") or {})
    intake = dict(applicant_data.get("intake") or {})
    verification = dict(applicant_data.get("intake_verification") or {})

    for field_name, fact in payload.facts.items():
        if field_name in payload.confirm_fields:
            intake[field_name] = fact
            verification[field_name] = "user_confirmed"
            proposed.pop(field_name, None)
        elif verification.get(field_name) != "user_confirmed":
            proposed[field_name] = fact

    applicant_data["proposed_intake"] = proposed
    applicant_data["intake"] = intake
    applicant_data["intake_verification"] = verification
    application.applicant_data = applicant_data
    update_application_timestamp(application)
    session.add(application)
    try:
        session.commit()
        session.refresh(application)
    except Exception as exc:
        session.rollback()
        logger.exception("Failed to persist application intake facts")
        raise HTTPException(
            status_code=500,
            detail="Application intake changes could not be saved.",
        ) from exc
    documents = session.exec(
        select(ApplicationDocument)
        .where(
            ApplicationDocument.application_id == application.id,
            ApplicationDocument.user_id == user.id,
        )
        .order_by(ApplicationDocument.created_at.desc())
    ).all()
    return _application_response(application, documents)


def require_owned_application(
    application_id: uuid.UUID,
    user: User,
    session: Session,
) -> Application:
    application = session.get(Application, application_id)
    if not application or application.user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found.")
    return application


def update_application_timestamp(application: Application) -> None:
    application.updated_at = datetime.now(timezone.utc)
