#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

print_ok() {
  echo "[OK] $*"
}

print_fail() {
  echo "[FAIL] $*" >&2
}

print_info() {
  echo "[INFO] $*"
}

curl_cmd() {
  if command -v curl.exe >/dev/null 2>&1; then
    echo "curl.exe"
    return
  fi
  echo "curl"
}

run_curl() {
  local curl_bin
  curl_bin="$(curl_cmd)"
  if [[ "${curl_bin}" == "curl.exe" ]]; then
    "${curl_bin}" --ssl-no-revoke "$@"
    return
  fi
  "${curl_bin}" "$@"
}

load_demo_context() {
  local target="${1:-local}"
  case "${target}" in
    local)
      local env_file="${ENV_FILE:-${ROOT_DIR}/.env}"
      [[ -f "${env_file}" ]] || fail "No existe el archivo de entorno local: ${env_file}"
      set -a
      # shellcheck disable=SC1090
      source "${env_file}"
      set +a
      export DEMO_TARGET="local"
      export DEMO_API_BASE_URL="${DEMO_API_BASE_URL:-http://127.0.0.1:8000/api}"
      export DEMO_INCIDENTS_BASE_URL="${DEMO_INCIDENTS_BASE_URL:-${CUSTOM_INCIDENTS_API_BASE_URL:-http://127.0.0.1:7071}}"
      export DEMO_INDEXER_BASE_URL="${DEMO_INDEXER_BASE_URL:-${INDEXER_API_BASE_URL:-http://127.0.0.1:7072}}"
      ;;
    cloud|azure)
      load_azure_env
      azure_login_check
      load_bicep_outputs
      export DEMO_TARGET="cloud"
      export DATABASE_URL="${DATABASE_URL:-$(webapp_appsetting DATABASE_URL)}"
      export BOT_ENDPOINT="${BOT_ENDPOINT:-https://${AZURE_WEBAPP_HOSTNAME}/api/messages}"
      export DEMO_API_BASE_URL="${DEMO_API_BASE_URL:-https://${AZURE_WEBAPP_HOSTNAME}/api}"
      export DEMO_INCIDENTS_BASE_URL="${DEMO_INCIDENTS_BASE_URL:-https://${AZURE_INCIDENTS_FUNCTION_HOSTNAME}/api}"
      export DEMO_INDEXER_BASE_URL="${DEMO_INDEXER_BASE_URL:-https://${AZURE_INDEXER_FUNCTION_HOSTNAME}/api}"
      ;;
    *)
      fail "Target de demo no soportado: ${target}. Usa 'local' o 'cloud'."
      ;;
  esac
}

resolved_provider_name() {
  local provider="${LLM_PROVIDER:-auto}"
  provider="$(echo "${provider}" | tr '[:upper:]' '[:lower:]')"
  if [[ "${provider}" == "auto" || -z "${provider}" ]]; then
    if [[ -n "${LLM_BASE_URL:-}" ]]; then
      echo "openai_compatible"
      return
    fi
    if [[ -n "${OPENAI_API_KEY:-}" ]]; then
      echo "openai"
      return
    fi
    echo "mock"
    return
  fi
  if [[ "${provider}" == "openai-compatible" || "${provider}" == "local" ]]; then
    echo "openai_compatible"
    return
  fi
  echo "${provider}"
}

check_provider_requirements() {
  local provider
  provider="$(resolved_provider_name)"
  case "${provider}" in
    mock)
      return
      ;;
    openai)
      [[ -n "${OPENAI_API_KEY:-}" ]] || fail "LLM_PROVIDER=openai requiere OPENAI_API_KEY"
      [[ -n "${CHAT_MODEL:-}" ]] || fail "LLM_PROVIDER=openai requiere CHAT_MODEL"
      [[ -n "${EMBEDDING_MODEL:-}" ]] || fail "LLM_PROVIDER=openai requiere EMBEDDING_MODEL"
      ;;
    openai_compatible)
      [[ -n "${LLM_BASE_URL:-}" ]] || fail "LLM_PROVIDER=openai_compatible requiere LLM_BASE_URL"
      [[ -n "${CHAT_MODEL:-}" ]] || fail "LLM_PROVIDER=openai_compatible requiere CHAT_MODEL"
      [[ -n "${EMBEDDING_MODEL:-}" ]] || fail "LLM_PROVIDER=openai_compatible requiere EMBEDDING_MODEL"
      ;;
    *)
      fail "LLM_PROVIDER no soportado para demo: ${provider}"
      ;;
  esac
}

run_uv_python_inline() {
  local script_content="$1"
  shift || true
  "${PYTHON_CMD[@]}" -m uv run python - "$@" <<PY
${script_content}
PY
}

run_uv_python_inline_env() {
  local script_content="$1"
  shift || true
  local tmp_script
  mkdir -p "${ROOT_DIR}/.artifacts"
  tmp_script="$(mktemp "${ROOT_DIR}/.artifacts/demo-inline-XXXXXX.py")"
  printf '%s\n' "${script_content}" >"${tmp_script}"
  run_uv_python_script "${tmp_script}" "$@"
  local status=$?
  rm -f "${tmp_script}"
  return "${status}"
}

http_json_check() {
  local label="$1"
  local url="$2"
  local method="${3:-GET}"
  local body="${4:-}"
  local tmp
  tmp="$(mktemp)"
  if [[ "${method}" == "POST" ]]; then
    if run_curl -fsS -X POST "${url}" -H "Content-Type: application/json" -d "${body}" >"${tmp}" 2>&1; then
      print_ok "${label}"
      cat "${tmp}"
      rm -f "${tmp}"
      return 0
    fi
  else
    if run_curl -fsS "${url}" >"${tmp}" 2>&1; then
      print_ok "${label}"
      cat "${tmp}"
      rm -f "${tmp}"
      return 0
    fi
  fi
  print_fail "${label}"
  cat "${tmp}" >&2 || true
  rm -f "${tmp}"
  return 1
}
