import json
from typing import Any

from app.models.applications import Application, ApplicationDocument

MAX_APPLICATION_CONTEXT_CHARS = 8000


def format_application_context(
    application: Application,
    documents: list[ApplicationDocument],
) -> str:
    context: dict[str, Any] = {
        "application": {
            "visa_type": application.visa_type,
            "destination": application.destination,
            "stage": application.stage,
        },
        "applicant_data": application.applicant_data,
        "documents": [
            {
                "doc_type": document.doc_type,
                "review_status": document.review_status,
                "extracted_data": document.extracted_data,
            }
            for document in documents
        ],
    }
    serialized = json.dumps(context, ensure_ascii=False, default=str)
    if len(serialized) <= MAX_APPLICATION_CONTEXT_CHARS:
        return serialized

    context["applicant_data"] = {
        "notice": "Profile omitted because it exceeds the context size limit."
    }
    compact_context = json.dumps(context, ensure_ascii=False, default=str)
    if len(compact_context) <= MAX_APPLICATION_CONTEXT_CHARS:
        return compact_context

    context["documents"] = [
        {
            "doc_type": document["doc_type"],
            "review_status": document["review_status"],
        }
        for document in context["documents"][:10]
    ]
    return json.dumps(context, ensure_ascii=False, default=str)
