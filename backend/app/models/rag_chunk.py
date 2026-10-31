import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

RAG_VECTOR_SIZE = 384


class RAGChunk(SQLModel, table=True):
    __tablename__ = "rag_chunks"
    __table_args__ = (
        Index(
            "ix_rag_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    content: str = Field(sa_column=Column(Text, nullable=False))
    metadata_json: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False),
    )
    embedding: List[float] = Field(
        sa_column=Column(Vector(RAG_VECTOR_SIZE), nullable=False)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
