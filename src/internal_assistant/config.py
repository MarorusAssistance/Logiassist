from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="internal-assistant-mvp", alias="APP_NAME")
    database_url: str = Field(
        default="postgresql+psycopg://assistant:assistant@localhost:5432/assistant",
        alias="DATABASE_URL",
    )
    llm_provider: str = Field(default="auto", alias="LLM_PROVIDER")
    llm_base_url: str = Field(default="", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    embeddings_provider: str = Field(default="", alias="EMBEDDINGS_PROVIDER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    chat_model: str = Field(default="gpt-4o-mini", alias="CHAT_MODEL")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    embedding_dimensions: int = Field(default=512, alias="EMBEDDING_DIMENSIONS")
    llm_timeout_seconds: float = Field(default=120.0, alias="LLM_TIMEOUT_SECONDS")
    admin_api_key: str = Field(default="change-this-admin-key", alias="ADMIN_API_KEY")
    app_shared_secret: str = Field(default="change-this-shared-secret", alias="APP_SHARED_SECRET")
    applicationinsights_connection_string: str = Field(
        default="",
        alias="APPLICATIONINSIGHTS_CONNECTION_STRING",
    )
    langsmith_tracing: bool = Field(default=False, alias="LANGSMITH_TRACING")
    langsmith_api_key: str = Field(default="", alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="internal-assistant-mvp", alias="LANGSMITH_PROJECT")
    langsmith_endpoint: str = Field(default="https://api.smith.langchain.com", alias="LANGSMITH_ENDPOINT")
    allowed_origins: str = Field(default="", alias="ALLOWED_ORIGINS")
    bot_endpoint: str = Field(default="", alias="BOT_ENDPOINT")
    custom_incidents_api_base_url: str = Field(default="http://localhost:7071", alias="CUSTOM_INCIDENTS_API_BASE_URL")
    indexer_api_base_url: str = Field(default="http://localhost:7072", alias="INDEXER_API_BASE_URL")
    microsoft_app_id: str = Field(
        default="",
        validation_alias=AliasChoices("MICROSOFT_APP_ID", "BOT_APP_ID"),
    )
    microsoft_app_password: str = Field(
        default="",
        validation_alias=AliasChoices("MICROSOFT_APP_PASSWORD", "BOT_APP_PASSWORD"),
    )
    microsoft_app_tenant_id: str = Field(default="", alias="MICROSOFT_APP_TENANT_ID")
    teams_app_id: str = Field(default="", alias="TEAMS_APP_ID")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    retrieval_confidence_threshold: float = Field(default=0.58, alias="RETRIEVAL_CONFIDENCE_THRESHOLD")
    reranker_enabled: bool = Field(default=False, alias="RERANKER_ENABLED")
    reranker_provider: str = Field(default="http", alias="RERANKER_PROVIDER")
    reranker_base_url: str = Field(default="http://127.0.0.1:8091", alias="RERANKER_BASE_URL")
    reranker_model: str = Field(default="BAAI/bge-reranker-v2-m3", alias="RERANKER_MODEL")
    reranker_api_key: str = Field(default="", alias="RERANKER_API_KEY")
    reranker_timeout_seconds: float = Field(default=30.0, alias="RERANKER_TIMEOUT_SECONDS")
    reranker_candidates: int = Field(default=30, alias="RERANKER_CANDIDATES")
    eval_max_concurrency: int = Field(default=1, alias="EVAL_MAX_CONCURRENCY")

    @property
    def bot_app_id(self) -> str:
        return self.microsoft_app_id

    @property
    def bot_app_password(self) -> str:
        return self.microsoft_app_password

    @property
    def allowed_origins_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
