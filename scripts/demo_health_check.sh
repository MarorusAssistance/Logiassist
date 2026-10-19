#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_demo_common.sh"

TARGET="${1:-local}"
load_demo_context "${TARGET}"

status=0
chat_payload='{"user_id":"u-demo","message":"Como solicito acceso temporal a SafeGate para personal externo?","channel":"demo"}'

check() {
  local label="$1"
  shift
  if "$@"; then
    print_ok "${label}"
  else
    print_fail "${label}"
    status=1
  fi
}

check_endpoint() {
  local url="$1"
  run_curl -fsS "${url}" >/dev/null
}

check_chat() {
  local response
  response="$(run_curl -fsS -X POST "${DEMO_API_BASE_URL}/chat" -H "Content-Type: application/json" -d "${chat_payload}")"
  "${PYTHON_CMD[@]}" -c "import json, sys; payload = json.loads(sys.argv[1]); answer = (payload.get('answer') or '').strip(); sources = payload.get('sources') or []; assert answer, 'Respuesta vacia'; print(f'Chat answer length: {len(answer)}'); print(f'Sources returned: {len(sources)}')" "${response}"
}

print_info "Ejecutando health checks para target '${DEMO_TARGET}'"
print_info "API base: ${DEMO_API_BASE_URL}"

check "App health" check_endpoint "${DEMO_API_BASE_URL}/health"
check "App deep health" check_endpoint "${DEMO_API_BASE_URL}/health/deep"
check "Custom incidents health" check_endpoint "${DEMO_INCIDENTS_BASE_URL}/health"
check "Indexer health" check_endpoint "${DEMO_INDEXER_BASE_URL}/health"
check "Basic chat flow" check_chat

if [[ "${status}" -ne 0 ]]; then
  fail "Uno o mas health checks de demo han fallado"
fi

print_ok "Todos los health checks de demo han pasado"
