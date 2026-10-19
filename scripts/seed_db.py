from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from internal_assistant.db import session_scope
from internal_assistant.models import Document, Incident
from internal_assistant.seed_data import DATA_DIR, load_seed_data, validate_seed_data


@dataclass(slots=True)
class TableSeedSummary:
    created: int = 0
    updated: int = 0
    skipped: int = 0


@dataclass(slots=True)
class SeedSummary:
    incidents: TableSeedSummary
    documents: TableSeedSummary


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _values_differ(current: Any, incoming: Any) -> bool:
    return current != incoming


def _apply_payload(instance: Any, payload: dict[str, Any]) -> bool:
    changed = False
    for field_name, value in payload.items():
        if _values_differ(getattr(instance, field_name), value):
            setattr(instance, field_name, value)
            changed = True
    return changed


def _document_payload(item: dict) -> dict[str, Any]:
    return {
        "title": item["title"],
        "document_type": item["document_type"],
        "department": item["department"],
        "affected_system": item["affected_system"],
        "content": item["content"],
        "tags": item["tags"],
        "source_url": item.get("source_url"),
        "created_at": _parse_datetime(item["created_at"]),
        "updated_at": _parse_datetime(item["updated_at"]),
    }


def _incident_payload(item: dict) -> dict[str, Any]:
    payload = {
        "external_id": item["external_id"],
        "title": item["title"],
        "description": item["description"],
        "department": item["department"],
        "category": item["category"],
        "affected_system": item["affected_system"],
        "priority": item["priority"],
        "status": item["status"],
        "is_resolved": item["is_resolved"],
        "resolution": item.get("resolution"),
        "impact": item.get("impact"),
        "expected_behavior": item.get("expected_behavior"),
        "actual_behavior": item.get("actual_behavior"),
        "tags": item["tags"],
        "created_by": item.get("created_by"),
        "created_at": _parse_datetime(item["created_at"]),
        "resolved_at": _parse_datetime(item.get("resolved_at")),
        "updated_at": _parse_datetime(item.get("updated_at") or item["created_at"]),
        "source": item.get("source", "seed_dataset"),
        "source_url": item.get("source_url"),
    }
    return payload


def _upsert_documents(session, documents: list[dict]) -> TableSeedSummary:
    summary = TableSeedSummary()
    for item in documents:
        payload = _document_payload(item)
        existing = session.get(Document, item["id"])
        if not existing:
            session.add(Document(id=item["id"], **payload))
            summary.created += 1
            continue
        if _apply_payload(existing, payload):
            summary.updated += 1
        else:
            summary.skipped += 1
    return summary


def _upsert_incidents(session, tickets: list[dict]) -> TableSeedSummary:
    summary = TableSeedSummary()
    for item in tickets:
        payload = _incident_payload(item)
        existing = session.get(Incident, item["id"])
        if not existing:
            session.add(Incident(id=item["id"], **payload))
            summary.created += 1
            continue
        if _apply_payload(existing, payload):
            summary.updated += 1
        else:
            summary.skipped += 1
    return summary


def _sync_sequence(session, table_name: str) -> None:
    session.execute(
        text(
            f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), true)"
        )
    )


def seed_database(*, data_dir: Path = DATA_DIR) -> SeedSummary:
    tickets, documents = load_seed_data(data_dir)
    validate_seed_data(tickets, documents)

    with session_scope() as session:
        documents_summary = _upsert_documents(session, documents)
        incidents_summary = _upsert_incidents(session, tickets)
        session.flush()
        _sync_sequence(session, "documents")
        _sync_sequence(session, "incidents")

    return SeedSummary(incidents=incidents_summary, documents=documents_summary)


def main() -> int:
    summary = seed_database()
    print(
        "Incidents: "
        f"created={summary.incidents.created} "
        f"updated={summary.incidents.updated} "
        f"skipped={summary.incidents.skipped}"
    )
    print(
        "Documents: "
        f"created={summary.documents.created} "
        f"updated={summary.documents.updated} "
        f"skipped={summary.documents.skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
