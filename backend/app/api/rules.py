"""
rules.py  —  /api/rules
────────────────────────
HITL Rule Extraction & Management API.

POST /api/rules/extract          Feed raw policy text → get back DeterministicRuleDraft list
GET  /api/rules/drafts           List all pending drafts (needing operator input)
POST /api/rules/confirm/{id}     Operator answers the gap question → rule goes active
GET  /api/rules/active           List all confirmed active rules
DELETE /api/rules/active/{id}    Deactivate a rule (soft delete)

Also handles:
POST /api/knowledge/ingest       Ingest raw text into PostgreSQL pgvector (Track A)
POST /api/knowledge/ingest/file  Ingest an uploaded file into PostgreSQL pgvector
GET  /api/knowledge/stats        pgvector table stats
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Optional
import tempfile
import os

from app.core.schemas import (
    ExtractRequest, ExtractResponse,
    RuleConfirmation, ConfirmResponse,
    IngestRequest, IngestResponse,
    DeterministicRuleDraft,
)
from app.services.rule_extractor import extract_rules_from_text, parse_operator_answer
from app.services.rule_store import (
    ActiveRuleConflictError,
    save_drafts, get_draft, list_drafts, confirm_rule,
    get_active_rules, deactivate_rule,
)
from app.services.rag_service import ingest_document, get_collection_stats

router = APIRouter(tags=["Rules & Knowledge"])


# ─────────────────────────────────────────────────────────────────────────────
# TRACK B: HITL Rule Extraction
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/rules/extract", response_model=ExtractResponse)
async def extract_rules(request: ExtractRequest):
    """
    Feed raw policy text through the LLM rule extractor.
    Returns a list of DeterministicRuleDraft — some complete, some with operator questions.
    The drafts are automatically saved to PostgreSQL.
    """
    try:
        drafts = await extract_rules_from_text(
            raw_text=request.raw_text,
            visa_type=request.visa_type,
            source_name=request.source_name,
            source_url=str(request.source_url) if request.source_url else None,
            source_section=request.source_section,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Rule extraction failed: {str(e)}")

    if not drafts:
        raise HTTPException(
            status_code=422,
            detail="No rules could be extracted from the provided text. Try a more specific policy excerpt."
        )

    save_drafts(drafts)

    complete = sum(1 for d in drafts if d.is_complete)

    return ExtractResponse(
        rules=drafts,
        total_extracted=len(drafts),
        complete=complete,
        needs_operator_input=len(drafts) - complete,
    )


@router.get("/rules/drafts")
async def get_drafts(needs_input_only: bool = False):
    """List all rule drafts, optionally filtered to those awaiting operator input."""
    drafts = list_drafts(needs_input_only=needs_input_only)
    return {
        "drafts": drafts,
        "total": len(drafts),
        "needs_input": sum(1 for d in drafts if not d.is_complete),
    }


@router.post("/rules/confirm/{rule_id}", response_model=ConfirmResponse)
async def confirm_rule_endpoint(rule_id: str, confirmation: RuleConfirmation):
    """
    Operator submits their answer to the operator_question.
    The LLM parses the answer into structured values, then the rule is activated.
    """
    # Validate rule_id matches
    if confirmation.rule_id != rule_id:
        raise HTTPException(status_code=400, detail="rule_id in body must match URL parameter")

    draft = get_draft(rule_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft rule '{rule_id}' not found")

    # LLM parses the operator's plain-text answer into structured values
    parsed_values = {}
    if confirmation.operator_answer and not draft.is_complete:
        try:
            parsed_values = await parse_operator_answer(draft, confirmation.operator_answer)
        except Exception as e:
            print(f"[rules] LLM parse of operator answer failed: {e}. Using raw values.")

    try:
        active = confirm_rule(draft, confirmation, parsed_values)
    except ActiveRuleConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to activate rule: {str(e)}")

    return ConfirmResponse(
        rule_id=rule_id,
        status="activated",
        active_rule=active,
        message=f"Rule '{rule_id}' is now active and will be enforced in compliance audits.",
    )


@router.get("/rules/active")
async def list_active_rules(visa_type: Optional[str] = None):
    """List all confirmed, active deterministic rules."""
    rules = get_active_rules(visa_type=visa_type)
    return {
        "rules": rules,
        "total": len(rules),
        "visa_type_filter": visa_type,
    }


@router.delete("/rules/active/{rule_id}")
async def deactivate_rule_endpoint(rule_id: str):
    """Soft-delete an active rule (sets is_active=False, keeps audit trail)."""
    success = deactivate_rule(rule_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Active rule '{rule_id}' not found")
    return {"rule_id": rule_id, "status": "deactivated"}


# ─────────────────────────────────────────────────────────────────────────────
# TRACK A: pgvector Knowledge Ingestion
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/knowledge/ingest", response_model=IngestResponse)
async def ingest_text(request: IngestRequest):
    """
    Ingest raw text directly into pgvector (no file needed).
    Good for pasting policy paragraphs during development.
    """
    if not request.text or not request.text.strip():
        raise HTTPException(status_code=422, detail="text field must not be empty")

    # Write to a temp file so ingest_document can parse it
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(request.text)
        tmp_path = f.name

    try:
        result = await ingest_document(
            file_path=tmp_path,
            source_name=request.source_name,
            doc_type=request.doc_type,
            business_id=request.business_id,
        )
    finally:
        os.unlink(tmp_path)

    return IngestResponse(
        document_id=result["document_id"],
        chunks_created=result["chunks_created"],
        source_name=result["source_name"],
        message=f"Successfully indexed {result['chunks_created']} chunks from '{request.source_name}' into pgvector.",
    )


@router.post("/knowledge/ingest/file", response_model=IngestResponse)
async def ingest_file(
    file: UploadFile = File(...),
    source_name: str = Form(...),
    doc_type: str = Form("policy"),
    business_id: str = Form("default"),
):
    """
    Upload a PDF, DOCX, TXT, or MD file → parse → chunk → embed → store in pgvector.
    """
    allowed_extensions = {".pdf", ".docx", ".txt", ".md"}
    ext = os.path.splitext(file.filename or "")[1].lower()

    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{ext}'. Allowed: {allowed_extensions}"
        )

    # Save upload to temp file
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=ext
    ) as tmp:
        contents = await file.read()
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        result = await ingest_document(
            file_path=tmp_path,
            source_name=source_name or file.filename,
            doc_type=doc_type,
            business_id=business_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")
    finally:
        os.unlink(tmp_path)

    return IngestResponse(
        document_id=result["document_id"],
        chunks_created=result["chunks_created"],
        source_name=result["source_name"],
        message=f"File '{file.filename}' indexed: {result['chunks_created']} chunks in pgvector.",
    )


@router.get("/knowledge/stats")
async def knowledge_stats():
    """pgvector knowledge table statistics."""
    return await get_collection_stats()
