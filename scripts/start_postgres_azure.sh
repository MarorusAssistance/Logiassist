#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

load_azure_env
azure_login_check
load_bicep_outputs

az postgres flexible-server start \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --name "${AZURE_POSTGRES_SERVER_NAME}" >/dev/null

echo "PostgreSQL arrancado: ${AZURE_POSTGRES_SERVER_NAME}"
