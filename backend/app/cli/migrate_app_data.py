"""Create account/chat/rule tables and import legacy JSON rule data."""

import json
from pathlib import Path

from app.core.database import init_db
from app.services.rule_store import migrate_legacy_json_rules


def _read_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as source:
        data = json.load(source)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"Expected a JSON array of objects in {path}.")
    return data


def main() -> None:
    init_db()
    rules_dir = Path(__file__).resolve().parents[1] / "data" / "rules"
    result = migrate_legacy_json_rules(
        active_rules=_read_json_list(rules_dir / "active_rules.json"),
        drafts=_read_json_list(rules_dir / "drafts.json"),
    )
    print(
        "Database migration complete. "
        f"Imported {result['active_rules_imported']} active rules and "
        f"{result['drafts_imported']} drafts."
    )


if __name__ == "__main__":
    main()
