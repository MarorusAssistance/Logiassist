"""Modelos desplegados en Microsoft Foundry (Azure) a traves de su API v1 compatible con OpenAI.

Sirve para cualquier deployment que Foundry exponga en `/openai/v1/`: modelos de OpenAI
(gpt-*, text-embedding-*) y modelos vendidos directamente por Azure como DeepSeek-V4-Flash.
Con deployments Data Zone Standard (EU) la inferencia queda dentro de la UE.

CHAT_MODEL y EMBEDDING_MODEL son los nombres de los deployments.
"""

from __future__ import annotations

from openai import OpenAI

from internal_assistant.config import get_settings
from internal_assistant.llm.openai_provider import OpenAIProvider


def foundry_openai_base_url(settings) -> str:
    if settings.azure_ai_base_url.strip():
        return settings.azure_ai_base_url.strip().rstrip("/") + "/"
    return f"https://{settings.azure_ai_resource.strip()}.services.ai.azure.com/openai/v1/"


class AzureOpenAIProvider(OpenAIProvider):
    provider_name = "azure_foundry"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.azure_ai_api_key.strip():
            raise ValueError("AZURE_AI_API_KEY es obligatorio cuando LLM_PROVIDER=azure_foundry")
        if not (settings.azure_ai_resource.strip() or settings.azure_ai_base_url.strip()):
            raise ValueError("Define AZURE_AI_RESOURCE o AZURE_AI_BASE_URL para LLM_PROVIDER=azure_foundry")
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.azure_ai_api_key,
            base_url=foundry_openai_base_url(settings),
            # Las cuotas por deployment de Foundry son pequenas (TPM): reintentos con backoff ante 429.
            max_retries=8,
            timeout=settings.llm_timeout_seconds,
        )
