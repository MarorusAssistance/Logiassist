from __future__ import annotations

import logging
from typing import Any

from pythonjsonlogger.json import JsonFormatter

from internal_assistant.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler()
    formatter = JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    _configure_langsmith(settings)
    _configure_azure_monitor(settings.applicationinsights_connection_string)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_kv(logger: logging.Logger, message: str, **kwargs: Any) -> None:
    logger.info(message, extra={"event": kwargs})


def _configure_azure_monitor(connection_string: str) -> None:
    if not connection_string.strip():
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except ImportError:
        logging.getLogger(__name__).warning(
            "APPLICATIONINSIGHTS_CONNECTION_STRING configurado, pero azure-monitor-opentelemetry no esta instalado"
        )
        return

    try:
        configure_azure_monitor(connection_string=connection_string, logger_name="")
    except Exception as exc:
        logging.getLogger(__name__).warning("No se pudo inicializar Azure Monitor: %s", exc)


def _configure_langsmith(settings) -> None:
    try:
        from internal_assistant.observability.tracing import configure_langsmith_environment
    except ImportError:
        return
    configure_langsmith_environment(settings)
