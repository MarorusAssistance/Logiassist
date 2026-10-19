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
VALIDATE_SCRIPT="${ROOT_DIR}/scripts/validate_seed_data.py"
SEED_SCRIPT="${ROOT_DIR}/scripts/seed_db.py"

run_uv_python_script "${VALIDATE_SCRIPT}"
run_uv_python_script \
  "${SEED_SCRIPT}" \
  "DATABASE_URL=${DATABASE_URL}" \
  "APP_ENV=${APP_ENV}"
