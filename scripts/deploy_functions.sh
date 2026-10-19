#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

build_function_package() {
  local function_dir="$1"
  local artifact_name="$2"
  local staging_dir="${ROOT_DIR}/.artifacts/${artifact_name}"
  local requirements_native
  "${PYTHON_CMD[@]}" - "$(native_path "${staging_dir}")" <<'PY'
import os
import shutil
import stat
import sys
from pathlib import Path

target = Path(sys.argv[1])

def onerror(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass

if target.exists():
    shutil.rmtree(target, onerror=onerror)
target.mkdir(parents=True, exist_ok=True)
PY
  requirements_native="$(native_path "${staging_dir}/requirements.txt")"
  "${PYTHON_CMD[@]}" -m uv export --format requirements-txt --no-hashes --no-dev --no-emit-project -o "${requirements_native}" >/dev/null
  cat > "${staging_dir}/.deployment" <<'EOF'
[config]
SCM_DO_BUILD_DURING_DEPLOYMENT = true
EOF
  cp "${function_dir}/function_app.py" "${staging_dir}/function_app.py"
  cp "${function_dir}/host.json" "${staging_dir}/host.json"
  cp -R "${ROOT_DIR}/src" "${staging_dir}/src"
  build_zip_from_directory "${staging_dir}" "${ROOT_DIR}/.artifacts/${artifact_name}.zip" >/dev/null
}

deploy_zip_via_kudu() {
  local app_name="$1"
  local package_file="$2"
  local package_native
  local kudu_user
  local kudu_password
  package_native="$(native_path "${package_file}")"
  kudu_user="$(az functionapp deployment list-publishing-profiles \
    --resource-group "${AZURE_FUNCTIONS_RESOURCE_GROUP}" \
    --name "${app_name}" \
    --query "[?publishMethod=='MSDeploy'] | [0].userName" \
    -o tsv | tr -d '\r\n')"
  kudu_password="$(az functionapp deployment list-publishing-profiles \
    --resource-group "${AZURE_FUNCTIONS_RESOURCE_GROUP}" \
    --name "${app_name}" \
    --query "[?publishMethod=='MSDeploy'] | [0].userPWD" \
    -o tsv | tr -d '\r\n')"

  "${PYTHON_CMD[@]}" - "${app_name}" "${kudu_user}" "${kudu_password}" "${package_native}" <<'PY'
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

app_name, user_name, password, package_path = sys.argv[1:5]
package_bytes = Path(package_path).read_bytes()
auth = "Basic " + base64.b64encode(f"{user_name}:{password}".encode("utf-8")).decode("ascii")
deploy_url = f"https://{app_name}.scm.azurewebsites.net/api/zipdeploy?isAsync=true"
request = urllib.request.Request(
    deploy_url,
    data=package_bytes,
    headers={
        "Authorization": auth,
        "Content-Type": "application/zip",
    },
    method="POST",
)

with urllib.request.urlopen(request, timeout=180) as response:
    status_url = response.headers.get("Location") or response.headers.get("location")
    if not status_url:
        raise SystemExit("Zip deploy no devolvio URL de estado")

deadline = time.time() + 1800
while True:
    poll = urllib.request.Request(status_url, headers={"Authorization": auth})
    with urllib.request.urlopen(poll, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))

    status = int(payload.get("status", -1))
    if status == 4:
        break
    if status == 3:
        raise SystemExit(f"Zip deployment failed: {payload}")
    if time.time() > deadline:
        raise SystemExit(f"Timeout esperando zip deployment: {payload}")
    time.sleep(10)
PY
}

load_azure_env
azure_login_check
require_vars AZURE_RESOURCE_GROUP
load_bicep_outputs

az functionapp config appsettings set \
  --resource-group "${AZURE_FUNCTIONS_RESOURCE_GROUP}" \
  --name "${AZURE_INDEXER_FUNCTION_NAME}" \
  --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true >/dev/null
az functionapp config appsettings delete \
  --resource-group "${AZURE_FUNCTIONS_RESOURCE_GROUP}" \
  --name "${AZURE_INDEXER_FUNCTION_NAME}" \
  --setting-names WEBSITE_RUN_FROM_PACKAGE >/dev/null 2>&1 || true
az functionapp config appsettings set \
  --resource-group "${AZURE_FUNCTIONS_RESOURCE_GROUP}" \
  --name "${AZURE_INCIDENTS_FUNCTION_NAME}" \
  --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true >/dev/null
az functionapp config appsettings delete \
  --resource-group "${AZURE_FUNCTIONS_RESOURCE_GROUP}" \
  --name "${AZURE_INCIDENTS_FUNCTION_NAME}" \
  --setting-names WEBSITE_RUN_FROM_PACKAGE >/dev/null 2>&1 || true

# Kudu reinicia el contenedor SCM tras cambios de configuracion; espera breve para evitar colision con zipdeploy.
sleep 45

build_function_package "${ROOT_DIR}/functions/indexer-function" "indexer-function"
build_function_package "${ROOT_DIR}/functions/custom-incidents-api-function" "custom-incidents-api-function"
INDEXER_PACKAGE_FILE="$(native_path "${ROOT_DIR}/.artifacts/indexer-function.zip")"
INCIDENTS_PACKAGE_FILE="$(native_path "${ROOT_DIR}/.artifacts/custom-incidents-api-function.zip")"

deploy_zip_via_kudu "${AZURE_INDEXER_FUNCTION_NAME}" "${ROOT_DIR}/.artifacts/indexer-function.zip"
deploy_zip_via_kudu "${AZURE_INCIDENTS_FUNCTION_NAME}" "${ROOT_DIR}/.artifacts/custom-incidents-api-function.zip"

echo "Functions desplegadas: ${AZURE_INDEXER_FUNCTION_NAME}, ${AZURE_INCIDENTS_FUNCTION_NAME}"
