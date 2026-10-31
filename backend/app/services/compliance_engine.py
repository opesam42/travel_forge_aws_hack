"""
compliance_engine.py
─────────────────────
RAG-backed compliance engine.
Retrieves relevant policy chunks from PostgreSQL pgvector, then asks the LLM to
evaluate the applicant's profile against those retrieved documents.

No hardcoded rules — everything comes from the ingested knowledge base.
"""

from typing import Dict, Any, List

from app.services.rag_service import retrieve_context
from app.services.llm_service import chat_completion, build_audit_prompt


async def evaluate_compliance(
    applicant_profile: Dict[str, Any],
    visa_type: str,
    session_id: str,
) -> Dict[str, Any]:
    """
    Full RAG-powered compliance evaluation.

    1. Build a retrieval query from the visa type + profile
    2. Retrieve relevant policy chunks from PostgreSQL pgvector
    3. Ask the LLM to evaluate all fields against retrieved policy
    4. Return structured compliance result

    Returns:
        {
            "overall_passed": bool,
            "summary": str,
            "issues": [ { rule, passed, detail, severity, source } ],
            "retrieved_policy_chunks": int
        }
    """
    # Step 1: Build a retrieval query
    retrieval_query = (
        f"{visa_type} visa requirements eligibility criteria "
        f"documents needed salary funds maintenance language"
    )

    # Step 2: Retrieve relevant policy context
    rag_context = await retrieve_context(query=retrieval_query)
    chunk_count = len(rag_context.split("\n\n")) if rag_context else 0

    # Step 3: Build prompts and call LLM
    system_prompt, user_message = await build_audit_prompt(
        rag_context=rag_context,
        applicant_profile=applicant_profile,
        visa_type=visa_type,
    )

    result = await chat_completion(
        system_prompt=system_prompt,
        user_message=user_message,
        json_mode=True,
    )

    # Step 4: Normalise and return
    return {
        "overall_passed": result.get("overall_passed", False),
        "summary": result.get("summary", "Compliance evaluation completed."),
        "issues": result.get("issues", []),
        "retrieved_policy_chunks": chunk_count,
    }
