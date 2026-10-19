#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

load_azure_env
azure_login_check
load_bicep_outputs

WEBAPP_URL="https://${AZURE_WEBAPP_HOSTNAME}"
INDEXER_URL="https://${AZURE_INDEXER_FUNCTION_HOSTNAME}/api"
INCIDENTS_URL="https://${AZURE_INCIDENTS_FUNCTION_HOSTNAME}/api"

echo "Health:"
curl -fsS "${WEBAPP_URL}/api/health" >/dev/null
echo "  /api/health OK"

echo "Deep health:"
curl -fsS "${WEBAPP_URL}/api/health/deep" >/dev/null
echo "  /api/health/deep OK"

echo "Chat smoke test:"
PAYLOAD_FILE="${ROOT_DIR}/.artifacts/smoke-chat.json"
mkdir -p "${ROOT_DIR}/.artifacts"
PAYLOAD_FILE_NATIVE="$(native_path "${PAYLOAD_FILE}")"
"${PYTHON_CMD[@]}" - "${PAYLOAD_FILE_NATIVE}" <<'PY'
from pathlib import Path
import json
import sys

payload = {
    "user_id": "u-cloud-smoke",
    "message": "Como se registra una entrega parcial en ventana critica en LogiCore ERP?",
    "channel": "local",
}
Path(sys.argv[1]).write_text(json.dumps(payload), encoding="utf-8")
PY
curl -fsS -X POST "${WEBAPP_URL}/api/chat" -H "Content-Type: application/json" --data-binary "@${PAYLOAD_FILE}" >/dev/null
echo "  /api/chat OK"

echo "Functions health:"
curl -fsS "${INDEXER_URL}/health" >/dev/null
echo "  indexer health OK"
curl -fsS "${INCIDENTS_URL}/health" >/dev/null
echo "  custom incidents health OK"

"${ROOT_DIR}/scripts/check_cloud_index.sh"
echo "Smoke test cloud: OK"
