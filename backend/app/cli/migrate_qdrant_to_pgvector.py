"""Copy the configured Qdrant collection into PostgreSQL pgvector."""

from app.core.database import init_db
from app.services.rag_service import migrate_qdrant_to_pgvector


def main() -> None:
    init_db()
    result = migrate_qdrant_to_pgvector()
    print(
        "Migration complete: "
        f"{result['points_migrated']} points copied from "
        f"'{result['source_collection']}' into '{result['target_table']}'. "
        f"The pgvector table now contains {result['target_total_rows']} rows."
    )


if __name__ == "__main__":
    main()
