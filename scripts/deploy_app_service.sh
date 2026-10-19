#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

build_webapp_package() {
  local staging_dir="${ROOT_DIR}/.artifacts/app-service"
  local requirements_native
  rm -rf "${staging_dir}"
  mkdir -p "${staging_dir}"
  requirements_native="$(native_path "${staging_dir}/requirements.txt")"
  "${PYTHON_CMD[@]}" -m uv export --format requirements-txt --no-hashes --no-dev --no-emit-project -o "${requirements_native}" >/dev/null
  cp -R "${ROOT_DIR}/app-service" "${staging_dir}/app-service"
  cp -R "${ROOT_DIR}/src" "${staging_dir}/src"
  cp "${ROOT_DIR}/pyproject.toml" "${ROOT_DIR}/README.md" "${staging_dir}/"
  build_zip_from_directory "${staging_dir}" "${ROOT_DIR}/.artifacts/app-service.zip" >/dev/null
}

load_azure_env
azure_login_check
require_vars AZURE_RESOURCE_GROUP
load_bicep_outputs
build_webapp_package
PACKAGE_FILE="$(native_path "${ROOT_DIR}/.artifacts/app-service.zip")"

az webapp deployment source config-zip \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --name "${AZURE_WEBAPP_NAME}" \
  --src "${PACKAGE_FILE}" \
  --timeout 1800 \
  --track-status false >/dev/null

echo "App Service desplegado: ${AZURE_WEBAPP_NAME}"
