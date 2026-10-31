"""
checklist.py  —  GET /api/checklist
─────────────────────────────────────
Dynamically generates a document checklist for a visa type by:
1. Retrieving relevant policy chunks from PostgreSQL pgvector
2. Asking the LLM to extract required documents from those chunks
"""

from fastapi import APIRouter, HTTPException, Query

from app.core.schemas import ChecklistResponse, ChecklistItem
from app.services.rag_service import retrieve_context
from app.services.llm_service import chat_completion

router = APIRouter(prefix="/checklist", tags=["Checklist"])

CHECKLIST_SYSTEM_PROMPT = """You are a visa documentation specialist.
Based ONLY on the retrieved policy context below, generate a checklist of
required documents for the specified visa type.

RETRIEVED POLICY CONTEXT:
{context}

Return valid JSON only:
{{
  "items": [
    {{
      "id": "<snake_case_id>",
      "label": "<short label>",
      "description": "<one-sentence requirement detail>",
      "status": "pending",
      "required": <true|false>,
      "source_policy": "<source doc name or null>"
    }}
  ]
}}

Only include documents explicitly mentioned in the context. Do not invent requirements.
"""


@router.get("", response_model=ChecklistResponse)
async def get_checklist(
    visa_type: str = Query(..., description="e.g. uk_skilled_worker, us_b1_b2"),
    session_id: str = Query(default="anonymous"),
):
    # 1. Retrieve relevant policy chunks
    query = f"{visa_type} required documents checklist mandatory submissions"
    rag_context = await retrieve_context(query=query)

    if not rag_context:
        raise HTTPException(
            status_code=404,
            detail="No policy documents found for this visa type. Please ingest relevant policies first.",
        )

    # 2. Ask LLM to extract checklist from retrieved context
    try:
        result = await chat_completion(
            system_prompt=CHECKLIST_SYSTEM_PROMPT.format(context=rag_context),
            user_message=f"Generate the document checklist for: {visa_type}",
            json_mode=True,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Checklist generation failed: {str(e)}")

    items = [ChecklistItem(**item) for item in result.get("items", [])]

    return ChecklistResponse(
        session_id=session_id,
        visa_type=visa_type,
        items=items,
        generated_from_rag=True,
    )
