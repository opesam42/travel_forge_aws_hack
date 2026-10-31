"""
check_updates.py
────────────────
CLI / Cron Tool for TravelForge.
Checks trusted official web sources for updates, runs rule extraction,
and creates an AuditTicket in PostgreSQL for consultant sign-off.

Usage:
  python -m app.cli.check_updates --country UK
  python -m app.cli.check_updates --url https://www.gov.uk/standard-visitor/eligibility
"""

import sys
import argparse
import asyncio
from sqlmodel import Session, select

from app.core.database import engine, init_db
from app.models.trusted_sources import TrustedSource, AuditTicket
from app.services.whitelisted_search import (
    fetch_verified_page_content,
    match_trusted_source,
    create_audit_ticket,
    get_trusted_sources,
)
from app.services.rule_extractor import extract_rules_from_text


async def inspect_url(url: str, country: str = "GLOBAL"):
    print(f"🔍 Inspecting URL: {url}")
    source = match_trusted_source(url)
    if not source:
        print(f"❌ Security Boundary Block: Domain '{url}' is NOT in trusted_sources!")
        print("   Add this domain to the PostgreSQL whitelist first to authorize ingestion.")
        return

    print(f"✅ Verified Trusted Source: [{source.trust_level.upper()}] {source.name} ({source.domain})")
    
    try:
        page = await fetch_verified_page_content(url)
        content = page["content"]
        title = page["title"]
        print(f"📄 Retrieved '{title}' ({len(content)} characters)")

        print("🤖 Running HITL Rule Extractor through Dual-LLM Gateway...")
        sample_text = content[:4000]
        drafts = await extract_rules_from_text(
            raw_text=sample_text,
            visa_type=f"{country.lower()}_visa",
            source_name=source.name,
        )

        print(f"📋 Extracted {len(drafts)} potential rules from web page.")

        # Create an AuditTicket for the consultant
        proposed_summary = "\n".join([f"- [{d.rule_id}]: {d.condition_summary}" for d in drafts])
        ticket = create_audit_ticket(
            ticket_type="policy_update",
            title=f"Web Policy Update: {title}",
            description=f"Automated crawler detected policy updates on {source.name} ({url}). Found {len(drafts)} rules requiring consultant review.",
            proposed_content=proposed_summary,
            target_country=source.jurisdiction_country,
            source_url=url,
            source_domain=source.domain,
        )
        print(f"🎟️ Created AuditTicket #{ticket.id} in PostgreSQL [Status: {ticket.status}]")
        print("   Consultant can review and 1-click approve from the Agency Dashboard!")

    except Exception as e:
        print(f"❌ Error inspecting URL: {e}")


def main():
    parser = argparse.ArgumentParser(description="TravelForge Policy Update Scanner")
    parser.add_argument("--url", type=str, help="Specific whitelisted URL to scan")
    parser.add_argument("--country", type=str, default="GLOBAL", help="Filter by country (e.g. UK, US)")
    args = parser.parse_args()

    init_db()

    target_url = args.url
    if not target_url:
        # Default scan URL for UK
        if args.country.upper() == "UK":
            target_url = "https://www.gov.uk/standard-visitor/eligibility"
        else:
            target_url = "https://travel.state.gov"

    asyncio.run(inspect_url(target_url, args.country))


if __name__ == "__main__":
    main()
