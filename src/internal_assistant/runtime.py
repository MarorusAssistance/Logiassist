from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.orm import Session

from internal_assistant.config import Settings
from internal_assistant.llm.openai_provider import normalize_provider_name, resolve_provider_name


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {
        "",
        "<your-openai-api-key>",
        "your-openai-api-key",
        "change-me",
        "set-me",
        "change-this-admin-key",
        "change-this-shared-secret",
        "change-this",
    }


def build_azure_postgres_url(*, server_name: str, database_name: str, admin_user: str, password: str) -> str:
    encoded_password = quote_plus(password)
    return (
        f"postgresql+psycopg://{admin_user}:{encoded_password}"
        f"@{server_name}.postgres.database.azure.com:5432/{database_name}?sslmode=require"
    )


def validate_runtime_settings(settings: Settings, *, require_bot: bool | None = None) -> list[str]:
    errors: list[str] = []
    environment = settings.app_env.strip().lower()
    provider = resolve_provider_name(settings)
    embeddings_provider = normalize_provider_name(settings.embeddings_provider or "")
    must_require_bot = environment in {"dev", "demo"} if require_bot is None else require_bot

    if not settings.database_url.strip():
        errors.append("DATABASE_URL es obligatorio")

    if provider == "openai" and _is_placeholder(settings.openai_api_key):
        errors.append("OPENAI_API_KEY es obligatorio cuando LLM_PROVIDER=openai")
    elif provider == "openai_compatible" and not settings.llm_base_url.strip():
        errors.append("LLM_BASE_URL es obligatorio cuando LLM_PROVIDER=openai_compatible")

    if embeddings_provider == "openai" and _is_placeholder(settings.openai_api_key):
        errors.append("OPENAI_API_KEY es obligatorio cuando EMBEDDINGS_PROVIDER=openai")

    if environment in {"dev", "demo"} and provider == "mock":
        errors.append("LLM_PROVIDER=mock no es valido en APP_ENV=dev|demo")

    supported_split_provider = provider == "openai_compatible" and embeddings_provider == "openai"
    if embeddings_provider not in {"", "auto", provider}:
        if not (embeddings_provider == "mock" and provider == "mock") and not supported_split_provider:
            errors.append(
                "EMBEDDINGS_PROVIDER debe coincidir con el proveedor de chat, usar mock completo, "
                "o usar LLM_PROVIDER=openai_compatible con EMBEDDINGS_PROVIDER=openai"
            )

    if environment in {"dev", "demo"}:
        if _is_placeholder(settings.admin_api_key):
            errors.append("ADMIN_API_KEY debe configurarse con un valor no placeholder")
        if _is_placeholder(settings.app_shared_secret):
            errors.append("APP_SHARED_SECRET debe configurarse con un valor no placeholder")

    if must_require_bot:
        if not settings.microsoft_app_id.strip():
            errors.append("MICROSOFT_APP_ID es obligatorio en APP_ENV=dev|demo")
        if not settings.microsoft_app_password.strip():
            errors.append("MICROSOFT_APP_PASSWORD es obligatorio en APP_ENV=dev|demo")
        if not settings.microsoft_app_tenant_id.strip():
            errors.append("MICROSOFT_APP_TENANT_ID es obligatorio en APP_ENV=dev|demo")
        if not settings.bot_endpoint.strip():
            errors.append("BOT_ENDPOINT es obligatorio en APP_ENV=dev|demo")

    return errors


def assert_runtime_settings(settings: Settings, *, require_bot: bool | None = None) -> None:
    errors = validate_runtime_settings(settings, require_bot=require_bot)
    if errors:
        raise RuntimeError("Configuracion invalida: " + "; ".join(errors))


def build_health_report(session: Session, settings: Settings) -> tuple[dict[str, Any], bool]:
    checks: dict[str, Any] = {}
    ok = True

    config_errors = validate_runtime_settings(settings)
    checks["config"] = {
        "ok": not config_errors,
        "errors": config_errors,
        "provider": resolve_provider_name(settings),
    }
    ok = ok and not config_errors

    try:
        session.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}
        ok = False

    try:
        chunks_count = int(session.execute(text("SELECT COUNT(*) FROM chunks")).scalar_one())
        checks["chunks"] = {"ok": chunks_count > 0, "count": chunks_count}
        ok = ok and chunks_count > 0
    except Exception as exc:
        checks["chunks"] = {"ok": False, "error": str(exc)}
        ok = False

    try:
        vector_enabled = bool(
            session.execute(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")).scalar_one()
        )
        checks["vector_extension"] = {"ok": vector_enabled}
        ok = ok and vector_enabled
    except Exception as exc:
        checks["vector_extension"] = {"ok": False, "error": str(exc)}
        ok = False

    return (
        {
            "status": "ok" if ok else "degraded",
            "service": settings.app_name,
            "environment": settings.app_env,
            "checks": checks,
        },
        ok,
    )
