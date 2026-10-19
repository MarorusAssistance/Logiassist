#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_azure_common.sh"

load_azure_env

if [[ -n "${AZURE_RESOURCE_GROUP:-}" ]]; then
  azure_login_check
  load_bicep_outputs || true
fi

BOT_ENDPOINT="${BOT_ENDPOINT:-${AZURE_WEBAPP_HOSTNAME:+https://${AZURE_WEBAPP_HOSTNAME}/api/messages}}"
require_vars MICROSOFT_APP_ID TEAMS_APP_ID BOT_ENDPOINT

[[ -f "${ROOT_DIR}/teams-app/color.png" ]] || fail "No existe teams-app/color.png"
[[ -f "${ROOT_DIR}/teams-app/outline.png" ]] || fail "No existe teams-app/outline.png"

BUILD_DIR="${ROOT_DIR}/teams-app/build"
mkdir -p "${BUILD_DIR}"
TEMPLATE_FILE="$(native_path "${ROOT_DIR}/teams-app/manifest.template.json")"
MANIFEST_FILE="$(native_path "${BUILD_DIR}/manifest.json")"

"${PYTHON_CMD[@]}" - "${TEMPLATE_FILE}" "${MANIFEST_FILE}" "${TEAMS_APP_ID}" "${MICROSOFT_APP_ID}" "${BOT_ENDPOINT}" <<'PY'
from pathlib import Path
import json
import sys
from urllib.parse import urlparse

template_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
teams_app_id = sys.argv[3]
microsoft_app_id = sys.argv[4]
bot_endpoint = sys.argv[5]

template = json.loads(template_path.read_text(encoding="utf-8"))
parsed = urlparse(bot_endpoint)
if not parsed.scheme or not parsed.netloc:
    raise ValueError("BOT_ENDPOINT debe ser una URL absoluta valida")

manifest = json.loads(json.dumps(template))
manifest["id"] = teams_app_id
for bot in manifest.get("bots", []):
    bot["botId"] = microsoft_app_id

valid_domains = list(manifest.get("validDomains", []))
if parsed.hostname and parsed.hostname not in valid_domains:
    valid_domains.append(parsed.hostname)
manifest["validDomains"] = valid_domains
output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
PY

cp "${ROOT_DIR}/teams-app/color.png" "${BUILD_DIR}/color.png"
cp "${ROOT_DIR}/teams-app/outline.png" "${BUILD_DIR}/outline.png"
build_zip_from_directory "${BUILD_DIR}" "${BUILD_DIR}/internal-assistant-demo.zip" >/dev/null
echo "Teams app generada: ${BUILD_DIR}/internal-assistant-demo.zip"
