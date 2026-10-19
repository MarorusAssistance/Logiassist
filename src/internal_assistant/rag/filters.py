from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


ALLOWED_SOURCE_TYPES = {"document", "incident"}
ALLOWED_SYSTEMS = {
    "LogiCore ERP",
    "AlmaTrack WMS",
    "RutaNexo TMS",
    "HelpOps",
    "DocuFlow",
    "OnboardHub",
    "SafeGate",
    "QualiTrace QMS",
    "ScanBridge IDP",
    "OpsLake",
}
ALLOWED_DEPARTMENTS = {"Operaciones", "Seguridad", "Onboarding", "Politicas internas"}
ALLOWED_DOCUMENT_TYPES = {
    "procedimiento",
    "guía",
    "política",
    "checklist",
    "guía de diagnóstico",
    "guía de escalado",
    "guía de onboarding",
    "faq operativa",
    "procedimiento de calidad",
    "procedimiento de seguridad",
}
ALLOWED_INCIDENT_STATUSES = {"open", "resolved"}

_SYSTEM_ALIASES = {
    "RutaNexo": "RutaNexo TMS",
}

_DOCUMENT_TYPE_ALIASES = {
    "guia": "guía",
    "guía": "guía",
    "politica": "política",
    "política": "política",
    "procedimiento": "procedimiento",
    "checklist": "checklist",
    "guia de diagnostico": "guía de diagnóstico",
    "guía de diagnóstico": "guía de diagnóstico",
    "guia de escalado": "guía de escalado",
    "guía de escalado": "guía de escalado",
    "guia de onboarding": "guía de onboarding",
    "guía de onboarding": "guía de onboarding",
    "faq operativa": "faq operativa",
    "procedimiento de calidad": "procedimiento de calidad",
    "procedimiento de seguridad": "procedimiento de seguridad",
}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _clean_string_items(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


class RetrievalFilters(BaseModel):
    source_types: list[str] = Field(default_factory=list)
    affected_systems: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)
    incident_statuses: list[str] = Field(default_factory=list)
    is_resolved: bool | None = None
    tags_any: list[str] = Field(default_factory=list)

    @field_validator("source_types", mode="before")
    @classmethod
    def normalize_source_types(cls, value):
        return [item.lower() for item in _clean_string_items(value) if item.lower() in ALLOWED_SOURCE_TYPES]

    @field_validator("affected_systems", mode="before")
    @classmethod
    def normalize_systems(cls, value):
        normalized = []
        for item in _clean_string_items(value):
            canonical = _SYSTEM_ALIASES.get(item, item)
            if canonical in ALLOWED_SYSTEMS:
                normalized.append(canonical)
        return normalized

    @field_validator("departments", mode="before")
    @classmethod
    def normalize_departments(cls, value):
        return [item for item in _clean_string_items(value) if item in ALLOWED_DEPARTMENTS]

    @field_validator("document_types", mode="before")
    @classmethod
    def normalize_document_types(cls, value):
        normalized: list[str] = []
        for item in _clean_string_items(value):
            canonical = _DOCUMENT_TYPE_ALIASES.get(item.lower())
            if canonical in ALLOWED_DOCUMENT_TYPES:
                normalized.append(canonical)
        return normalized

    @field_validator("incident_statuses", mode="before")
    @classmethod
    def normalize_incident_statuses(cls, value):
        return [item.lower() for item in _clean_string_items(value) if item.lower() in ALLOWED_INCIDENT_STATUSES]

    @field_validator("tags_any", mode="before")
    @classmethod
    def normalize_tags(cls, value):
        return _clean_string_items(value)

    def active(self) -> bool:
        return bool(
            self.source_types
            or self.affected_systems
            or self.departments
            or self.document_types
            or self.incident_statuses
            or self.is_resolved is not None
            or self.tags_any
        )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def normalize_retrieval_filters(value: Any) -> RetrievalFilters:
    if isinstance(value, RetrievalFilters):
        return value
    if not value:
        return RetrievalFilters()
    if isinstance(value, dict):
        return RetrievalFilters.model_validate(value)
    return RetrievalFilters()
