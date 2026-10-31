"""Ingest the verified records from the bundled travel data source."""

import asyncio

from app.core.database import init_db
from app.services.travel_data_ingestion import ingest_verified_travel_data


def main() -> None:
    init_db()
    result = asyncio.run(ingest_verified_travel_data())
    print(
        f"Ingested {result['entries_processed']} verified entries from "
        f"{result['source_name']} ({result['chunks_created']} chunks; "
        f"{result['entries_skipped']} unchanged)."
    )


if __name__ == "__main__":
    main()
