"""
whitelisted_search.py
─────────────────────
Domain-Restricted Web Verification & Knowledge Flywheel Service for TravelForge.

Responsibilities:
1. Strict Domain Whitelist Enforcement: Validates URLs against PostgreSQL trusted_sources.
2. Verified Page Fetcher: Safely extracts text from approved official government/partner sites.
3. Knowledge Flywheel Logger: Creates AuditTickets for gaps or when consultant sign-off is required.
"""

import logging
import re
from urllib.parse import urljoin, urlparse
from typing import Optional, Dict, Any, List
from bs4 import BeautifulSoup
import httpx
from sqlmodel import Session, select

from app.core.database import engine
from app.models.trusted_sources import TrustedSource, AuditTicket

logger = logging.getLogger(__name__)
MAX_PAGE_BYTES = 1_000_000
MAX_REDIRECTS = 3

_ROUTE_DESTINATION_RE = re.compile(
    r"\b(?:to|into|moving\s+to|travel(?:ing|ling)?\s+to|destination(?:\s+is)?)\s+"
    r"(?P<destination>[a-z][a-z\s/-]{0,80})",
    re.IGNORECASE,
)
_UK_DESTINATION_RE = re.compile(
    r"\b(uk|united kingdom|britain|england|scotland|wales|ukvi|"
    r"london|manchester|birmingham|edinburgh|cardiff|belfast)\b",
    re.IGNORECASE,
)
_US_DESTINATION_RE = re.compile(
    r"\b(us|usa|united states|america|b1|b2|ds-?160|new york|"
    r"los angeles|chicago|washington(?:,\s*d\.?c\.?)?)\b",
    re.IGNORECASE,
)


def infer_destination_jurisdiction(query: str) -> Optional[str]:
    """Infer a supported destination, preferring the destination in a route."""
    route_match = _ROUTE_DESTINATION_RE.search(query)
    destination = route_match.group("destination") if route_match else query
    mentions_uk = _UK_DESTINATION_RE.search(destination) is not None
    mentions_us = _US_DESTINATION_RE.search(destination) is not None
    if mentions_uk == mentions_us:
        return None
    return "UK" if mentions_uk else "US"


def get_trusted_sources(country: Optional[str] = None, only_active: bool = True) -> List[TrustedSource]:
    """Fetch active trusted sources from PostgreSQL, optionally filtered by country."""
    with Session(engine) as session:
        query = select(TrustedSource)
        if only_active:
            query = query.where(TrustedSource.is_active == True)
        if country and country != "GLOBAL":
            query = query.where(TrustedSource.jurisdiction_country.in_([country, "GLOBAL"]))
        return list(session.exec(query).all())


def match_trusted_source(url: str) -> Optional[TrustedSource]:
    """
    Checks if a URL belongs to an active whitelisted domain in PostgreSQL.
    Returns the TrustedSource record or None.
    """
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    domain = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or not domain
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        return None

    with Session(engine) as session:
        active_sources = session.exec(select(TrustedSource).where(TrustedSource.is_active == True)).all()
        for src in active_sources:
            trusted_domain = src.domain.lower().rstrip(".")
            if domain == trusted_domain or domain.endswith(f".{trusted_domain}"):
                return src
    return None


async def fetch_verified_page_content(url: str) -> Dict[str, Any]:
    """
    Fetches and parses text from a URL ONLY IF it is in trusted_sources.
    Enforces the security boundary: refuses to fetch unapproved blogs/forums.
    """
    headers = {
        "User-Agent": "TravelForge-Compliance-Verifier/2.0 (+https://travelforge.internal)"
    }
    current_url = url
    body = bytearray()
    source = None
    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=False,
        headers=headers,
    ) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            source = match_trusted_source(current_url)
            if not source:
                raise ValueError(
                    f"Security Boundary Violation: Domain for '{current_url}' is not in "
                    "approved trusted_sources."
                )

            async with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location or redirect_count == MAX_REDIRECTS:
                        raise ValueError("Trusted page returned an invalid or excessive redirect.")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise ValueError("Trusted page response was not HTML.")

                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_PAGE_BYTES:
                    raise ValueError("Trusted page exceeded the maximum allowed response size.")

                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_PAGE_BYTES:
                        raise ValueError("Trusted page exceeded the maximum allowed response size.")
                    body.extend(chunk)
                current_url = str(response.url)
                break
        else:
            raise ValueError("Trusted page exceeded the maximum allowed redirect count.")

    # Parse HTML to clean, structured markdown/text
    soup = BeautifulSoup(body.decode("utf-8", errors="replace"), "html.parser")
    
    # Remove navigation, headers, footers, scripts
    for elem in soup(["script", "style", "nav", "footer", "header", "aside"]):
        elem.decompose()

    text_content = soup.get_text(separator="\n", strip=True)
    title = soup.title.string.strip() if soup.title and soup.title.string else source.name

    return {
        "url": current_url,
        "title": title,
        "content": text_content,
        "source_name": source.name,
        "domain": source.domain,
        "trust_level": source.trust_level,
        "can_answer_autonomously": source.can_answer_autonomously,
    }


def create_audit_ticket(
    ticket_type: str,
    title: str,
    description: str,
    proposed_content: str,
    target_country: str = "GLOBAL",
    source_url: Optional[str] = None,
    source_domain: Optional[str] = None,
) -> AuditTicket:
    """
    Creates a review ticket in PostgreSQL for the Knowledge Flywheel.
    Consultants can approve, edit, or reject this from the agency dashboard.
    """
    with Session(engine) as session:
        # Check if identical ticket already exists
        stmt = select(AuditTicket).where(
            AuditTicket.title == title,
            AuditTicket.status == "pending"
        )
        existing = session.exec(stmt).first()
        if existing:
            existing.frequency_count += 1
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        ticket = AuditTicket(
            ticket_type=ticket_type,
            title=title,
            description=description,
            proposed_content=proposed_content,
            target_country=target_country,
            source_url=source_url,
            source_domain=source_domain,
            status="pending",
        )
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
        return ticket


async def resolve_whitelisted_fallback(query: str) -> Optional[Dict[str, Any]]:
    """
    Called when pgvector retrieval has low confidence or no documents.
    Detects jurisdiction from user query, queries PostgreSQL trusted_sources,
    and scrapes live verified official guidance on the spot.
    """
    q_lower = query.lower()
    pasted_urls = re.findall(r"https?://[^\s<>\"']+", query, flags=re.IGNORECASE)

    if pasted_urls:
        target_url = pasted_urls[0].rstrip(".,!?;:)]}")
        source = match_trusted_source(target_url)
        if not source:
            logger.info("Skipped pasted URL because it is not on the trusted-source whitelist")
            return None
    else:
        country = infer_destination_jurisdiction(query)
        if country == "UK":
            preferred_domain = "gov.uk"
        elif country == "US":
            preferred_domain = "travel.state.gov"
        elif re.search(r"\b(vfs|biometric|visa application cent(?:er|re))\b", q_lower):
            country = "GLOBAL"
            preferred_domain = "vfsglobal.com"
        else:
            return None

        if preferred_domain:
            with Session(engine) as session:
                sources = session.exec(
                    select(TrustedSource).where(
                        TrustedSource.is_active == True,
                        TrustedSource.can_answer_autonomously == True,
                        TrustedSource.jurisdiction_country.in_([country, "GLOBAL"]),
                    )
                ).all()
                source = next(
                    (item for item in sources if item.domain.lower() == preferred_domain),
                    None,
                )
        else:
            source = next(
                (
                    item for item in get_trusted_sources(country="GLOBAL")
                    if item.domain.lower() == preferred_domain
                    and item.can_answer_autonomously
                ),
                None,
            )
        if not source:
            return None

        target_url = source.base_url
        if preferred_domain == "gov.uk":
            if any(term in q_lower for term in ("visitor", "visit", "tourist", "holiday")):
                target_url = "https://www.gov.uk/standard-visitor/eligibility"
            elif "skilled worker" in q_lower:
                target_url = "https://www.gov.uk/skilled-worker-visa"
        elif preferred_domain == "travel.state.gov" and any(
            term in q_lower for term in ("visitor", "visit", "tourist", "tourism", "b1", "b2")
        ):
            target_url = (
                "https://travel.state.gov/content/travel/en/us-visas/tourism-visit/"
                "visitor.html"
            )

    try:
        page = await fetch_verified_page_content(target_url)
        if not page["can_answer_autonomously"]:
            create_audit_ticket(
                ticket_type="knowledge_gap",
                title=f"Review web guidance from {page['domain']}",
                description=(
                    "A trusted web page was fetched for a chat fallback but its source "
                    "requires consultant review before its guidance can be used."
                ),
                proposed_content=page["content"][:12000],
                target_country=source.jurisdiction_country,
                source_url=page["url"],
                source_domain=page["domain"],
            )
            return None
        return page
    except Exception:
        logger.exception("Live trusted-source fallback fetch failed")
        return None
