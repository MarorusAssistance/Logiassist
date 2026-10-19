from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SEED_TICKETS = DATA_DIR / "seed_tickets.json"
SEED_DOCUMENTS = DATA_DIR / "seed_documents.json"
PLANNED_SYSTEMS = {
    "LogiCore ERP",
    "AlmaTrack WMS",
    "RutaNexo TMS",
    "HelpOps",
    "DocuFlow",
    "OnboardHub",
    "SafeGate",
    "QualiTrace QMS",
    "ScanBridge IDP",
    "OpsLake",
}
REAL_BRAND_TERMS = [
    "SAP",
    "Dynamics",
    "Microsoft",
    "Oracle",
    "Salesforce",
    "ServiceNow",
    "Jira",
    "Confluence",
    "NetSuite",
    "Odoo",
    "Manhattan",
    "Blue Yonder",
    "Infor",
    "Sage",
    "SharePoint",
    "Teams",
    "Azure",
    "OpenAI",
    "Cohere",
    "Workday",
    "Slack",
    "HubSpot",
    "Zendesk",
]


def _load_json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _counter(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(Counter(str(row.get(field)) for row in rows if row.get(field) is not None).most_common())


def _detect_quality_indicators(tickets: list[dict[str, Any]], documents: list[dict[str, Any]]) -> dict[str, Any]:
    all_rows = tickets + documents
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in all_rows)
    lower = text.lower()
    urls = re.findall(r"https?://[^\"\s]+|www\.[^\"\s]+", text)
    emails = re.findall(r"[\w.%-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    brand_hits = {
        term: len(re.findall(r"\b" + re.escape(term) + r"\b", text, flags=re.IGNORECASE))
        for term in REAL_BRAND_TERMS
    }
    brand_hits = {term: count for term, count in brand_hits.items() if count}
    secret_terms = {
        term: lower.count(term)
        for term in (
            "password",
            "contraseña",
            "contrasena",
            "api key",
            "apikey",
            "token",
            "secret",
            "client secret",
            "connection string",
        )
    }
    secret_terms = {term: count for term, count in secret_terms.items() if count}
    boilerplate = lower.count("flujo ficticio del entorno de demo")
    mojibake_markers = sum(lower.count(marker.lower()) for marker in ("Ã", "Â", "â"))
    return {
        "urls": len(urls),
        "url_samples": urls[:5],
        "emails": len(emails),
        "brand_hits": brand_hits,
        "secret_term_hits": secret_terms,
        "demo_boilerplate_hits": boilerplate,
        "mojibake_marker_hits": mojibake_markers,
    }


def _print_seed_summary() -> None:
    tickets = _load_json(SEED_TICKETS)
    documents = _load_json(SEED_DOCUMENTS)
    resolved = sum(1 for item in tickets if item.get("is_resolved") is True)
    unresolved = sum(1 for item in tickets if item.get("is_resolved") is False)
    systems = set(_counter(tickets + documents, "affected_system"))
    missing_planned = sorted(PLANNED_SYSTEMS - systems)
    extra_systems = sorted(systems - PLANNED_SYSTEMS)

    print("Seed files")
    print(f"- tickets: {len(tickets)}")
    print(f"- resolved tickets: {resolved}")
    print(f"- unresolved tickets: {unresolved}")
    print(f"- documents: {len(documents)}")
    print(f"- ticket systems: {_counter(tickets, 'affected_system')}")
    print(f"- document systems: {_counter(documents, 'affected_system')}")
    print(f"- ticket departments: {_counter(tickets, 'department')}")
    print(f"- document departments: {_counter(documents, 'department')}")
    print(f"- ticket categories: {_counter(tickets, 'category')}")
    print(f"- document types: {_counter(documents, 'document_type')}")
    print(f"- planned systems missing from seed: {missing_planned}")
    print(f"- systems not in planned list: {extra_systems}")
    print(f"- quality indicators: {_detect_quality_indicators(tickets, documents)}")


def _load_env_file() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _print_db_summary() -> None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("Database")
        print("- skipped: DATABASE_URL is not configured")
        return

    sys.path.insert(0, str(ROOT / "src"))
    try:
        from sqlalchemy import create_engine, text
    except Exception as exc:
        print("Database")
        print(f"- skipped: SQLAlchemy import failed: {type(exc).__name__}")
        return

    print("Database")
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            for table in (
                "incidents",
                "documents",
                "chunks",
                "conversations",
                "messages",
                "feedback",
                "retrieval_logs",
                "conversation_memories",
            ):
                try:
                    count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                    print(f"- {table}: {count}")
                except Exception as exc:
                    print(f"- {table}: unavailable ({type(exc).__name__})")
            try:
                dims = conn.execute(
                    text("SELECT COALESCE(MAX(vector_dims(embedding)), 0) FROM chunks WHERE embedding IS NOT NULL")
                ).scalar_one()
                print(f"- chunk embedding dimensions: {dims}")
            except Exception as exc:
                print(f"- chunk embedding dimensions: unavailable ({type(exc).__name__})")
            try:
                vector_enabled = conn.execute(
                    text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                ).scalar_one()
                print(f"- pgvector enabled: {vector_enabled}")
            except Exception as exc:
                print(f"- pgvector enabled: unavailable ({type(exc).__name__})")
    except Exception as exc:
        print(f"- skipped: database connection failed ({type(exc).__name__})")
        print("- seed-file audit still completed; no data was mutated")


def main() -> int:
    try:
        _load_env_file()
        _print_seed_summary()
        _print_db_summary()
        return 0
    except Exception as exc:
        print(f"Datasource audit execution error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
