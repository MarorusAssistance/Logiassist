from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from internal_assistant.seed_data import DATA_DIR, SeedDataValidationError, validate_seed_files


def run_validation(data_dir: Path = DATA_DIR) -> int:
    summary = validate_seed_files(data_dir)
    print(f"Tickets: {summary.tickets}")
    print(f"Tickets resueltos: {summary.resolved_tickets}")
    print(f"Tickets no resueltos: {summary.unresolved_tickets}")
    print(f"Documentos: {summary.documents}")
    print(f"Distribucion documentos: {summary.document_distribution}")
    print("Validacion: OK")
    return 0


def main() -> int:
    try:
        return run_validation()
    except SeedDataValidationError as exc:
        print(f"Validacion: ERROR - {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
