"""Document ingestion and vector search backed by PostgreSQL pgvector."""

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from app.core.database import engine
from app.core.config import settings
from app.models.rag_chunk import RAGChunk, RAG_VECTOR_SIZE
from app.services.embeddings import get_embedder


def _chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> List[str]:
    """Split text into overlapping chunks by word count."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        start += chunk_size - overlap
    return [chunk for chunk in chunks if chunk.strip()]


def _parse_pdf(file_path: str) -> str:
    try:
        import PyPDF2
        text = []
        with open(file_path, "rb") as file:
            reader = PyPDF2.PdfReader(file)
            for page in reader.pages:
                text.append(page.extract_text() or "")
        return "\n".join(text)
    except ImportError as exc:
        raise RuntimeError("PyPDF2 is required to ingest PDF files.") from exc


def _parse_docx(file_path: str) -> str:
    try:
        from docx import Document
        document = Document(file_path)
        return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
    except ImportError as exc:
        raise RuntimeError("python-docx is required to ingest DOCX files.") from exc


def _parse_file(file_path: str) -> str:
    extension = Path(file_path).suffix.lower()
    if extension == ".pdf":
        return _parse_pdf(file_path)
    if extension in (".docx", ".doc"):
        return _parse_docx(file_path)
    if extension in (".txt", ".md"):
        return Path(file_path).read_text(encoding="utf-8")
    raise ValueError(f"Unsupported file type: {extension}")


def _upsert_chunks(
    ids: List[uuid.UUID],
    documents: List[str],
    metadatas: List[Dict[str, Any]],
    vectors: List[List[float]],
    replace_document_id: Optional[str] = None,
) -> None:
    if not (len(ids) == len(documents) == len(metadatas) == len(vectors)):
        raise ValueError("Chunk IDs, documents, metadata, and embeddings must have equal lengths.")
    if not vectors:
        return
    if any(len(vector) != RAG_VECTOR_SIZE for vector in vectors):
        raise ValueError(f"Embeddings must be {RAG_VECTOR_SIZE} dimensions.")

    rows = [
        {
            "id": chunk_id,
            "content": document,
            "metadata": metadata,
            "embedding": vector,
            "created_at": datetime.now(timezone.utc),
        }
        for chunk_id, document, metadata, vector in zip(ids, documents, metadatas, vectors)
    ]
    statement = pg_insert(RAGChunk.__table__).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[RAGChunk.id],
        set_={
            "content": statement.excluded.content,
            "metadata": statement.excluded["metadata"],
            "embedding": statement.excluded.embedding,
        },
    )
    with engine.begin() as connection:
        if replace_document_id:
            connection.execute(
                delete(RAGChunk).where(
                    RAGChunk.metadata_json["document_id"].astext == replace_document_id
                )
            )
        connection.execute(statement)


def _get_existing_document(document_id: str) -> tuple[Optional[str], int]:
    with Session(engine) as session:
        content_hash = session.exec(
            select(RAGChunk.metadata_json["source_document_sha256"].astext)
            .where(RAGChunk.metadata_json["document_id"].astext == document_id)
            .limit(1)
        ).first()
        chunk_count = session.exec(
            select(func.count())
            .select_from(RAGChunk)
            .where(RAGChunk.metadata_json["document_id"].astext == document_id)
        ).one()
    return content_hash, chunk_count


async def ingest_text(
    text: str,
    source_name: str,
    doc_type: str = "policy",
    business_id: str = "default",
    metadata_extra: Optional[Dict[str, Any]] = None,
    document_id: Optional[str] = None,
) -> Dict[str, Any]:
    fixed_document_id = str(uuid.UUID(document_id)) if document_id else str(uuid.uuid4())
    chunks = _chunk_text(text)
    if not chunks:
        return {
            "document_id": fixed_document_id,
            "chunks_created": 0,
            "source_name": source_name,
        }

    extra_metadata = metadata_extra or {}
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if document_id:
        existing_hash, existing_count = _get_existing_document(fixed_document_id)
        if existing_hash == content_hash:
            return {
                "document_id": fixed_document_id,
                "chunks_created": existing_count,
                "source_name": source_name,
                "skipped": True,
            }

    ids = [
        uuid.uuid5(uuid.UUID(fixed_document_id), str(index))
        for index in range(len(chunks))
    ]
    metadatas = [
        {
            "document_id": fixed_document_id,
            "source_name": source_name,
            "doc_type": doc_type,
            "business_id": business_id,
            "chunk_index": index,
            **(
                {"source_document_sha256": content_hash}
                if document_id
                else {}
            ),
            **extra_metadata,
        }
        for index in range(len(chunks))
    ]
    vectors = get_embedder().embed(chunks)
    _upsert_chunks(
        ids,
        chunks,
        metadatas,
        vectors,
        replace_document_id=fixed_document_id if document_id else None,
    )
    return {
        "document_id": fixed_document_id,
        "chunks_created": len(chunks),
        "source_name": source_name,
    }


async def ingest_document(
    file_path: str,
    source_name: str,
    doc_type: str = "policy",
    business_id: str = "default",
    metadata_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Parse, chunk, embed, and persist a document into PostgreSQL."""
    return await ingest_text(
        text=_parse_file(file_path),
        source_name=source_name,
        doc_type=doc_type,
        business_id=business_id,
        metadata_extra=metadata_extra,
    )


async def retrieve_context(
    query: str,
    n_results: Optional[int] = None,
    filter_metadata: Optional[Dict[str, str]] = None,
) -> str:
    result = await retrieve_context_with_scores(query, n_results, filter_metadata)
    return result["context"]


async def retrieve_context_with_scores(
    query: str,
    n_results: Optional[int] = None,
    filter_metadata: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Retrieve relevant chunks and cosine distances from pgvector."""
    limit = n_results or settings.RAG_TOP_K
    query_vector = get_embedder().embed([query])[0]
    distance = RAGChunk.embedding.cosine_distance(query_vector).label("distance")
    statement = select(RAGChunk, distance)

    if filter_metadata:
        statement = statement.where(
            *[
                RAGChunk.metadata_json[key].astext == value
                for key, value in filter_metadata.items()
            ]
        )
    statement = statement.order_by(distance).limit(limit)

    with Session(engine) as session:
        results = session.exec(statement).all()

    context_blocks = []
    distances = []
    sources = []
    documents = []
    for index, (chunk, chunk_distance) in enumerate(results, start=1):
        source = chunk.metadata_json.get("source_name", "Unknown")
        context_blocks.append(f"[{index}] Source: {source}\n{chunk.content}")
        sources.append(source)
        distances.append(float(chunk_distance))
        documents.append(
            {
                "source_name": source,
                "chunk_id": str(chunk.id),
                "document_id": chunk.metadata_json.get("document_id"),
                "chunk_index": chunk.metadata_json.get("chunk_index"),
                "distance": float(chunk_distance),
                "metadata": chunk.metadata_json,
                "content": chunk.content,
            }
        )
    return {
        "context": "\n\n".join(context_blocks),
        "distances": distances,
        "sources": sources,
        "documents": documents,
    }


async def get_collection_stats() -> Dict[str, Any]:
    with Session(engine) as session:
        total = session.exec(select(func.count()).select_from(RAGChunk)).one()
    return {
        "collection_name": RAGChunk.__tablename__,
        "total_chunks": total,
        "provider": "pgvector",
    }


def migrate_qdrant_to_pgvector(batch_size: int = 256) -> Dict[str, Any]:
    """Copy existing Qdrant points and vectors into PostgreSQL; safe to rerun."""
    from qdrant_client import QdrantClient

    if not settings.QDRANT_API_KEY or not settings.QDRANT_CLUSTER_ENDPOINT_KEY:
        raise RuntimeError("Set the Qdrant endpoint and API key before running this migration.")

    client = QdrantClient(
        url=settings.QDRANT_CLUSTER_ENDPOINT_KEY,
        api_key=settings.QDRANT_API_KEY,
        timeout=30,
    )
    if not client.collection_exists(settings.QDRANT_COLLECTION_NAME):
        raise RuntimeError("The configured Qdrant collection does not exist.")

    expected_count = client.get_collection(settings.QDRANT_COLLECTION_NAME).points_count or 0
    migrated = 0
    offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=settings.QDRANT_COLLECTION_NAME,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        if not points:
            break

        ids: List[uuid.UUID] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        vectors: List[List[float]] = []
        for point in points:
            payload = point.payload or {}
            document = payload.get("_rag_text")
            vector = point.vector
            if not isinstance(document, str) or not document:
                raise RuntimeError(f"Qdrant point {point.id} is missing its text payload.")
            if not isinstance(vector, list) or len(vector) != RAG_VECTOR_SIZE:
                raise RuntimeError(f"Qdrant point {point.id} has an incompatible vector.")

            try:
                chunk_id = uuid.UUID(str(point.id))
            except ValueError:
                chunk_id = uuid.uuid5(uuid.NAMESPACE_URL, str(point.id))
            ids.append(chunk_id)
            documents.append(document)
            metadatas.append(
                {
                    key: value
                    for key, value in payload.items()
                    if key not in {"_rag_text", "_chroma_id"}
                }
            )
            vectors.append([float(value) for value in vector])

        _upsert_chunks(ids, documents, metadatas, vectors)
        migrated += len(points)
        if next_offset is None:
            break
        offset = next_offset

    with Session(engine) as session:
        stored_count = session.exec(select(func.count()).select_from(RAGChunk)).one()
    if migrated != expected_count:
        raise RuntimeError(
            f"Migration read {migrated} Qdrant points, expected {expected_count}."
        )
    return {
        "source_collection": settings.QDRANT_COLLECTION_NAME,
        "target_table": RAGChunk.__tablename__,
        "points_migrated": migrated,
        "target_total_rows": stored_count,
    }
