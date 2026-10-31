"""Ingest verified travel guidance records into the pgvector knowledge base."""

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List

from app.services.rag_service import ingest_text

TRAVEL_DATA_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "raw_data" / "travel_data.txt"
)
_ENTRY_MARKER = re.compile(r"(?m)^(VERIFIED|PENDING VERIFICATION)\tSource:.*$")
_VERSION_ID = re.compile(r"(?m)^Version ID\t([^\r\n]+)\s*$")
_VERIFICATION_STATUS = re.compile(
    r"(?mi)^Verification status\tverified\s*$"
)


@dataclass(frozen=True)
class VerifiedTravelEntry:
    text: str
    source: str
    version_id: str
    entry_id: str


def parse_verified_entries(text: str) -> List[VerifiedTravelEntry]:
    """Return only complete records explicitly marked verified in the source."""
    markers = list(_ENTRY_MARKER.finditer(text))
    entries = []
    for index, marker in enumerate(markers):
        if marker.group(1) != "VERIFIED":
            continue

        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        body = text[marker.start() : end].strip()
        if not _VERIFICATION_STATUS.search(body):
            continue

        marker_match = re.match(
            r"VERIFIED\tSource:\s*(.*?)\tVersion:\s*(.*?)\s*$",
            marker.group(0),
        )
        version_match = _VERSION_ID.search(body)
        if not marker_match or not version_match:
            continue

        source = marker_match.group(1).strip()
        version_id = version_match.group(1).strip()
        marker_version = marker_match.group(2).strip()
        if version_id != marker_version:
            continue

        entry_id_match = re.match(r"^(.+?)-v\d+(?:-|$)", version_id)
        if not entry_id_match:
            continue

        prior_lines = text[: marker.start()].splitlines()
        headings = [
            line.strip()
            for line in prior_lines
            if line.strip() and not line.startswith("Verification status\t")
        ]
        heading_prefix = headings[-1] if headings else ""
        entries.append(
            VerifiedTravelEntry(
                text=f"{heading_prefix}\n{body}" if heading_prefix else body,
                source=source,
                version_id=version_id,
                entry_id=entry_id_match.group(1),
            )
        )
    return entries


async def ingest_verified_travel_data(
    file_path: Path = TRAVEL_DATA_PATH,
) -> dict:
    """Ingest verified entries with stable IDs so startup runs are idempotent."""
    if not file_path.is_file():
        raise FileNotFoundError(f"Travel knowledge source not found: {file_path}")

    entries = parse_verified_entries(file_path.read_text(encoding="utf-8"))
    if not entries:
        raise ValueError(f"No verified travel entries found in {file_path}")

    results = []
    for entry in entries:
        document_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"travelforge:travel-data:{entry.entry_id}")
        )
        results.append(
            await ingest_text(
                text=entry.text,
                source_name=file_path.name,
                doc_type="policy",
                metadata_extra={
                    "knowledge_source": "travel_data",
                    "knowledge_entry_id": entry.entry_id,
                    "version_id": entry.version_id,
                    "verified_source": entry.source,
                    "verification_status": "verified",
                },
                document_id=document_id,
            )
        )

    return {
        "entries_processed": len(entries),
        "chunks_created": sum(result["chunks_created"] for result in results),
        "entries_skipped": sum(bool(result.get("skipped")) for result in results),
        "source_name": file_path.name,
    }
