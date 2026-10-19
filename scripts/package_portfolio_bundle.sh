#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

BUNDLE_DIR="${ROOT_DIR}/dist/portfolio-bundle"
ZIP_PATH="${ROOT_DIR}/dist/portfolio-bundle.zip"

rm -rf "${BUNDLE_DIR}" "${ZIP_PATH}"
mkdir -p "${BUNDLE_DIR}/docs" "${BUNDLE_DIR}/evaluation" "${BUNDLE_DIR}/teams-app/build"

copy_if_exists() {
  local source="$1"
  local target_dir="$2"
  if [[ -e "${source}" ]]; then
    cp -R "${source}" "${target_dir}/"
  fi
}

copy_if_exists "${ROOT_DIR}/README.md" "${BUNDLE_DIR}"
copy_if_exists "${ROOT_DIR}/docs/portfolio-summary.md" "${BUNDLE_DIR}/docs"
copy_if_exists "${ROOT_DIR}/docs/tradeoffs-and-decisions.md" "${BUNDLE_DIR}/docs"
copy_if_exists "${ROOT_DIR}/docs/project-card.md" "${BUNDLE_DIR}/docs"
copy_if_exists "${ROOT_DIR}/docs/demo-script.md" "${BUNDLE_DIR}/docs"
copy_if_exists "${ROOT_DIR}/docs/demo-checklist.md" "${BUNDLE_DIR}/docs"
copy_if_exists "${ROOT_DIR}/docs/architecture.md" "${BUNDLE_DIR}/docs"
copy_if_exists "${ROOT_DIR}/docs/rag-evaluation.md" "${BUNDLE_DIR}/docs"
copy_if_exists "${ROOT_DIR}/docs/faq.md" "${BUNDLE_DIR}/docs"
copy_if_exists "${ROOT_DIR}/docs/screenshots" "${BUNDLE_DIR}/docs"
copy_if_exists "${ROOT_DIR}/docs/assets" "${BUNDLE_DIR}/docs"

if [[ -f "${ROOT_DIR}/teams-app/build/internal-assistant-demo.zip" ]]; then
  cp "${ROOT_DIR}/teams-app/build/internal-assistant-demo.zip" "${BUNDLE_DIR}/teams-app/build/"
fi

"${PYTHON_CMD[@]}" - "$(native_path "${ROOT_DIR}")" "$(native_path "${BUNDLE_DIR}")" <<'PY'
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1])
bundle = Path(sys.argv[2])
reports = [path for path in (root / "evaluation" / "reports").rglob("*.json") if path.is_file()]
if reports:
    latest = max(reports, key=lambda path: path.stat().st_mtime)
    target_dir = bundle / "evaluation" / "reports"
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(latest, target_dir / latest.name)
    md_path = latest.with_suffix(".md")
    if md_path.exists():
        shutil.copy2(md_path, target_dir / md_path.name)
PY

cat > "${BUNDLE_DIR}/COMMANDS.md" <<'EOF'
# Comandos utiles

## Arranque local

```bash
python -m uv sync
docker compose up -d postgres
python -m uv run python scripts/init_db.py
python -m uv run python scripts/seed_db.py
python -m uv run python scripts/rebuild_index.py
python -m uv run uvicorn main:app --app-dir app-service --host 0.0.0.0 --port 8000
```

## Demo local

```bash
./scripts/demo_prep.sh local
./scripts/demo_health_check.sh local
```

## Demo cloud

```bash
./scripts/demo_prep.sh cloud
./scripts/demo_health_check.sh cloud
```

## Evaluacion RAG

```bash
python -m uv run python scripts/run_rag_eval.py --provider mock
python -m uv run python scripts/run_rag_eval.py --provider openai --include-adversarial --use-llm-judge
```
EOF

build_zip_from_directory "${BUNDLE_DIR}" "${ZIP_PATH}" >/dev/null
echo "Portfolio bundle generado en:"
echo "  - ${BUNDLE_DIR}"
echo "  - ${ZIP_PATH}"
