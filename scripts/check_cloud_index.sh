#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

load_azure_env
azure_login_check
load_bicep_outputs
export DATABASE_URL="$(webapp_appsetting DATABASE_URL)"
export APP_ENV="${APP_ENV:-$(webapp_appsetting APP_ENV)}"
export EMBEDDING_DIMENSIONS="${EMBEDDING_DIMENSIONS:-$(webapp_appsetting EMBEDDING_DIMENSIONS)}"
CHECK_SCRIPT="${ROOT_DIR}/scripts/check_index.py"

run_uv_python_script \
  "${CHECK_SCRIPT}" \
  "DATABASE_URL=${DATABASE_URL}" \
  "APP_ENV=${APP_ENV}" \
  "EMBEDDING_DIMENSIONS=${EMBEDDING_DIMENSIONS}"
