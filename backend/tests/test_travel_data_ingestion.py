import hashlib
import uuid
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app.services.rag_service import ingest_text
from app.services.travel_data_ingestion import (
    TRAVEL_DATA_PATH,
    ingest_verified_travel_data,
    parse_verified_entries,
)


class TravelDataParsingTests(unittest.IsolatedAsyncioTestCase):
    def test_only_explicitly_verified_entries_are_parsed(self):
        source = TRAVEL_DATA_PATH.read_text(encoding="utf-8")

        entries = parse_verified_entries(source)

        self.assertEqual(len(entries), 12)
        self.assertEqual(len({entry.version_id for entry in entries}), 12)
        self.assertTrue(all("PENDING VERIFICATION\tSource:" not in entry.text for entry in entries))
        self.assertTrue(
            all("Verification status\tverified" in entry.text for entry in entries)
        )

    async def test_ingestion_attaches_verification_and_version_metadata(self):
        entries = parse_verified_entries(
            TRAVEL_DATA_PATH.read_text(encoding="utf-8")
        )

        async def fake_ingest_text(**kwargs):
            return {"chunks_created": 2, **kwargs}

        with patch(
            "app.services.travel_data_ingestion.ingest_text",
            new=AsyncMock(side_effect=fake_ingest_text),
        ) as ingest:
            result = await ingest_verified_travel_data()

        self.assertEqual(result["entries_processed"], 12)
        self.assertEqual(result["chunks_created"], 24)
        self.assertEqual(ingest.await_count, 12)
        first_call = ingest.await_args_list[0].kwargs
        self.assertEqual(first_call["metadata_extra"]["verification_status"], "verified")
        self.assertEqual(first_call["metadata_extra"]["knowledge_source"], "travel_data")
        self.assertEqual(first_call["source_name"], Path(TRAVEL_DATA_PATH).name)
        self.assertEqual(
            first_call["document_id"],
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"travelforge:travel-data:{entries[0].entry_id}",
                )
            ),
        )


class StableTravelIngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_identical_document_is_skipped_without_embedding(self):
        text = "A verified policy record."
        document_id = str(uuid.uuid4())
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        with (
            patch(
                "app.services.rag_service._get_existing_document",
                return_value=(content_hash, 3),
            ),
            patch("app.services.rag_service.get_embedder") as get_embedder,
            patch("app.services.rag_service._upsert_chunks") as upsert,
        ):
            result = await ingest_text(
                text=text,
                source_name="travel_data.txt",
                document_id=document_id,
            )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["chunks_created"], 3)
        get_embedder.assert_not_called()
        upsert.assert_not_called()

    async def test_changed_document_replaces_previous_chunks(self):
        text = "Updated verified policy record."
        document_id = str(uuid.uuid4())
        embedder = Mock()
        embedder.embed.return_value = [[0.0] * 384]

        with (
            patch(
                "app.services.rag_service._get_existing_document",
                return_value=("old-hash", 2),
            ),
            patch("app.services.rag_service.get_embedder", return_value=embedder),
            patch("app.services.rag_service._upsert_chunks") as upsert,
        ):
            result = await ingest_text(
                text=text,
                source_name="travel_data.txt",
                document_id=document_id,
            )

        self.assertEqual(result["chunks_created"], 1)
        self.assertEqual(upsert.call_args.kwargs["replace_document_id"], document_id)
        metadata = upsert.call_args.args[2][0]
        self.assertEqual(
            metadata["source_document_sha256"],
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
