from __future__ import annotations

from types import SimpleNamespace

from internal_assistant.runtime import build_azure_postgres_url, validate_runtime_settings


def test_build_azure_postgres_url_uses_sslmode_and_server_domain():
    url = build_azure_postgres_url(
        server_name="pg-demo",
        database_name="assistant",
        admin_user="pgadmin",
        password="s3cr3t!",
    )

    assert url.startswith("postgresql+psycopg://pgadmin:")
    assert "@pg-demo.postgres.database.azure.com:5432/assistant" in url
    assert "sslmode=require" in url


def test_validate_runtime_settings_requires_bot_and_openai_in_demo():
    settings = SimpleNamespace(
        app_env="demo",
        database_url="postgresql+psycopg://demo",
        llm_provider="openai",
        llm_base_url="",
        llm_api_key="",
        embeddings_provider="openai",
        openai_api_key="",
        admin_api_key="change-this-admin-key",
        app_shared_secret="change-this-shared-secret",
        microsoft_app_id="",
        microsoft_app_password="",
        microsoft_app_tenant_id="",
        bot_endpoint="",
    )

    errors = validate_runtime_settings(settings)

    assert "OPENAI_API_KEY es obligatorio cuando LLM_PROVIDER=openai" in errors
    assert "ADMIN_API_KEY debe configurarse con un valor no placeholder" in errors
    assert "APP_SHARED_SECRET debe configurarse con un valor no placeholder" in errors
    assert "MICROSOFT_APP_ID es obligatorio en APP_ENV=dev|demo" in errors
    assert "MICROSOFT_APP_TENANT_ID es obligatorio en APP_ENV=dev|demo" in errors


def test_validate_runtime_settings_allows_local_defaults():
    settings = SimpleNamespace(
        app_env="local",
        database_url="postgresql+psycopg://local",
        llm_provider="mock",
        llm_base_url="",
        llm_api_key="",
        embeddings_provider="mock",
        openai_api_key="",
        admin_api_key="change-this-admin-key",
        app_shared_secret="change-this-shared-secret",
        microsoft_app_id="",
        microsoft_app_password="",
        microsoft_app_tenant_id="",
        bot_endpoint="",
    )

    assert validate_runtime_settings(settings) == []


def test_validate_runtime_settings_allows_openai_embeddings_with_local_chat():
    settings = SimpleNamespace(
        app_env="local",
        database_url="postgresql+psycopg://local",
        llm_provider="openai_compatible",
        llm_base_url="http://127.0.0.1:1234/v1",
        llm_api_key="",
        embeddings_provider="openai",
        openai_api_key="sk-real",
        admin_api_key="change-this-admin-key",
        app_shared_secret="change-this-shared-secret",
        microsoft_app_id="",
        microsoft_app_password="",
        microsoft_app_tenant_id="",
        bot_endpoint="",
    )

    assert validate_runtime_settings(settings) == []


def test_validate_runtime_settings_requires_openai_key_for_openai_embeddings():
    settings = SimpleNamespace(
        app_env="local",
        database_url="postgresql+psycopg://local",
        llm_provider="openai_compatible",
        llm_base_url="http://127.0.0.1:1234/v1",
        llm_api_key="",
        embeddings_provider="openai",
        openai_api_key="",
        admin_api_key="change-this-admin-key",
        app_shared_secret="change-this-shared-secret",
        microsoft_app_id="",
        microsoft_app_password="",
        microsoft_app_tenant_id="",
        bot_endpoint="",
    )

    errors = validate_runtime_settings(settings)

    assert "OPENAI_API_KEY es obligatorio cuando EMBEDDINGS_PROVIDER=openai" in errors
