from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_MODEL = "qwen/qwen3-30b-a3b-2507"
DEFAULT_CONTEXT_LENGTH = 16384


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def request_json(url: str, payload: dict | None = None, timeout: int = 20) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def lms_path() -> str | None:
    found = shutil.which("lms") or shutil.which("lms.exe")
    if found:
        return found
    candidate = Path.home() / ".lmstudio" / "bin" / "lms.exe"
    return str(candidate) if candidate.exists() else None


def lmstudio_app_path(env: dict[str, str]) -> str | None:
    configured = env.get("LMSTUDIO_APP_PATH") or os.environ.get("LMSTUDIO_APP_PATH")
    if configured and Path(configured).exists():
        return configured
    if sys.platform.startswith("win"):
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LM Studio" / "LM Studio.exe"
        return str(candidate) if candidate.exists() else None
    return None


def run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout)


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        result = run(["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid} -ErrorAction SilentlyContinue"], timeout=10)
        return result.returncode == 0 and bool(result.stdout.strip())
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stale_lock_hint() -> str:
    lock = Path.home() / ".lmstudio" / ".internal" / "llmster-pid.lock"
    if not lock.exists():
        return ""
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
    except ValueError:
        return f"LM Studio lock no numerico detectado: {lock}"
    if not pid_exists(pid):
        return f"LM Studio parece tener un lock stale: {lock} apunta a PID inexistente {pid}"
    return ""


def wait_for_api(base_url: str, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            status, _ = request_json(f"{base_url.rstrip('/')}/models", timeout=5)
            if status == 200:
                return True
        except Exception:
            time.sleep(2)
    return False


def port_is_open(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(3)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def start_lmstudio_app(env: dict[str, str]) -> None:
    app_path = lmstudio_app_path(env)
    if not app_path:
        print("LM Studio app no encontrada; no puedo arrancar la UI automaticamente.", file=sys.stderr)
        return
    subprocess.Popen([app_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_server(lms: str, base_url: str, bind: str, start_app: bool, env: dict[str, str]) -> None:
    parsed = urlparse(base_url)
    port = parsed.port or 1234
    host = parsed.hostname or "127.0.0.1"
    if wait_for_api(base_url, timeout_seconds=5):
        return

    result = run([lms, "server", "start", "--port", str(port), "--bind", bind], timeout=90)
    if result.returncode == 0 and wait_for_api(base_url, timeout_seconds=20):
        return

    if start_app:
        start_lmstudio_app(env)
        time.sleep(15)
        result = run([lms, "server", "start", "--port", str(port), "--bind", bind], timeout=90)
        if result.returncode == 0 and wait_for_api(base_url, timeout_seconds=30):
            return

    hint = stale_lock_hint()
    tcp = "open" if port_is_open(host, port) else "closed"
    message = [
        f"No se pudo arrancar LM Studio server en {base_url}.",
        f"TCP {host}:{port} esta {tcp}.",
    ]
    if result.stderr.strip():
        message.append(result.stderr.strip())
    if hint:
        message.append(hint)
    raise RuntimeError("\n".join(message))


def loaded_models(base_url: str) -> list[str]:
    _, payload = request_json(f"{base_url.rstrip('/')}/models", timeout=20)
    return [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]


def ensure_model_loaded(lms: str, base_url: str, model: str, context_length: int, identifier: str | None) -> None:
    models = loaded_models(base_url)
    if model in models or (identifier and identifier in models):
        return

    command = [lms, "load", model, "--context-length", str(context_length), "--parallel", "1", "--yes"]
    if identifier:
        command.extend(["--identifier", identifier])
    result = run(command, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"No se pudo cargar el modelo {model}")

    deadline = time.time() + 60
    while time.time() < deadline:
        models = loaded_models(base_url)
        if model in models or (identifier and identifier in models):
            return
        time.sleep(2)
    raise RuntimeError(f"El modelo {model} se cargo, pero no aparece en /v1/models")


def smoke_chat(base_url: str, model: str) -> None:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Responde solo OK"}],
        "temperature": 0,
        "max_tokens": 8,
    }
    _, response = request_json(f"{base_url.rstrip('/')}/chat/completions", payload=payload, timeout=120)
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("LM Studio respondio sin contenido en /chat/completions")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Arranca LM Studio server y carga un modelo local para desarrollo.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--context-length", type=int, default=0)
    parser.add_argument("--bind", default="")
    parser.add_argument("--identifier", default="")
    parser.add_argument("--skip-chat-test", action="store_true")
    parser.add_argument("--no-start-app", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = load_env_file(Path(args.env_file))
    base_url = args.base_url or env.get("LLM_BASE_URL") or os.environ.get("LLM_BASE_URL") or "http://127.0.0.1:1234/v1"
    model = args.model or env.get("CHAT_MODEL") or os.environ.get("CHAT_MODEL") or DEFAULT_MODEL
    context_length = args.context_length or int(env.get("LMSTUDIO_CONTEXT_LENGTH") or os.environ.get("LMSTUDIO_CONTEXT_LENGTH") or DEFAULT_CONTEXT_LENGTH)
    bind = args.bind or env.get("LMSTUDIO_BIND") or os.environ.get("LMSTUDIO_BIND") or "0.0.0.0"
    identifier = args.identifier or model
    lms = lms_path()
    if not lms:
        print("ERROR: No encuentro el CLI de LM Studio (`lms`).", file=sys.stderr)
        return 1

    try:
        ensure_server(lms, base_url, bind, start_app=not args.no_start_app, env=env)
        ensure_model_loaded(lms, base_url, model, context_length, identifier)
        if not args.skip_chat_test:
            smoke_chat(base_url, identifier)
    except Exception as exc:
        print(f"LM Studio ensure: ERROR - {exc}", file=sys.stderr)
        return 1

    print(f"LM Studio OK: model={identifier} base_url={base_url} context_length={context_length}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
