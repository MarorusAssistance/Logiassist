from __future__ import annotations

from dataclasses import asdict, dataclass, field
import unicodedata
from typing import Any


SYSTEM_ALIASES = {
    "LogiCore ERP": ("LogiCore ERP", "LogiCore"),
    "AlmaTrack WMS": ("AlmaTrack WMS", "AlmaTrack"),
    "RutaNexo TMS": ("RutaNexo TMS", "RutaNexo"),
    "HelpOps": ("HelpOps",),
    "DocuFlow": ("DocuFlow",),
    "OnboardHub": ("OnboardHub",),
    "SafeGate": ("SafeGate",),
    "QualiTrace QMS": ("QualiTrace QMS", "QualiTrace"),
    "ScanBridge IDP": ("ScanBridge IDP", "ScanBridge"),
    "OpsLake": ("OpsLake",),
}

SYSTEM_NAMES = tuple(SYSTEM_ALIASES.keys())

NEW_ISSUE_TERMS = (
    "error nuevo",
    "nuevo error",
    "no encuentro solucion",
    "sigue fallando",
    "sigue igual",
    "no se resuelve",
)

PROCEDURE_TERMS = (
    "como ",
    "pasos",
    "procedimiento",
    "registrar",
    "completar",
    "hacer",
    "onboarding",
    "iniciacion",
)

DISSATISFIED_FOLLOW_UP_TERMS = (
    "esa respuesta no me vale",
    "no me vale",
    "explicamelo mejor",
    "indicame mejor",
    "teniendo en cuenta",
    "lo anterior",
    "como te dije",
)

KNOWN_CASE_TERMS = (
    "caso conocido",
    "incidencia conocida",
    "caso parecido",
    "hubo una",
)

RESOLVED_CASE_TERMS = (
    "como se resolvio",
    "como quedo resuelta",
    "como quedo resuelto",
    "que correccion se hizo",
    "que arreglo",
    "se corrigio",
    "resuelta",
    "resuelto",
)

UNRESOLVED_CASE_TERMS = (
    "caso abierto",
    "caso pendiente",
    "incidencia abierta",
    "incidencia pendiente",
    "pendiente por",
    "queda pendiente",
    "esta pendiente",
    "sigue pendiente",
    "sigue abierta",
    "sigue abierto",
    "sigue sin resolver",
    "no resuelta",
    "no resuelto",
    "sin solucion",
    "solucion definitiva",
)

STATUS_TERMS = (
    "esta resuelta",
    "esta resuelto",
    "sigue abierta",
    "sigue abierto",
    "estado",
    "se conoce solucion",
    "hay solucion",
    "existe solucion",
    "solucion definitiva",
)

POLICY_TERMS = (
    "politica",
    "se puede",
    "esta permitido",
    "permitido",
    "atajo",
    "manual no documentado",
)

TROUBLESHOOTING_TERMS = (
    "que reviso primero",
    "que revisar primero",
    "que hago si",
    "que debo revisar",
    "por donde empiezo",
)


@dataclass(slots=True)
class QuerySignals:
    new_issue: bool = False
    procedure_request: bool = False
    dissatisfied_followup: bool = False
    known_case_request: bool = False
    resolved_case_request: bool = False
    unresolved_case_request: bool = False
    status_request: bool = False
    policy_question: bool = False
    troubleshooting_request: bool = False
    mentioned_systems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class EvidenceAssessment:
    should_block_answer: bool
    requires_direct_evidence: bool
    reason_code: str
    clarification_question: str
    document_count: int
    incident_count: int
    source_types: list[str]
    source_systems: list[str]
    related_only: bool = False
    system_mismatch: bool = False
    has_matching_document: bool = False
    has_matching_source: bool = False
    direct_chunk_ids: list[int] = field(default_factory=list)
    semantic_confidence: float = 0.0
    evidence_mode: str = "insufficient_evidence"
    allowed_behavior: str = "ask_clarification"
    resolved_incident_count: int = 0
    unresolved_incident_count: int = 0
    direct_resolved_incident_ids: list[int] = field(default_factory=list)
    direct_unresolved_incident_ids: list[int] = field(default_factory=list)
    direct_document_ids: list[int] = field(default_factory=list)
    blocking_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def detect_query_signals(message: str) -> QuerySignals:
    normalized = _normalize(message)
    mentioned_systems = [
        system
        for system, aliases in SYSTEM_ALIASES.items()
        if any(_normalize(alias) in normalized for alias in aliases)
    ]
    status_request = any(term in normalized for term in (_normalize(item) for item in STATUS_TERMS)) and (
        "resuelt" in normalized
        or "abiert" in normalized
        or "estado" in normalized
        or "solucion" in normalized
    )
    return QuerySignals(
        new_issue=any(term in normalized for term in (_normalize(item) for item in NEW_ISSUE_TERMS)),
        procedure_request=any(term in normalized for term in (_normalize(item) for item in PROCEDURE_TERMS)),
        dissatisfied_followup=any(term in normalized for term in (_normalize(item) for item in DISSATISFIED_FOLLOW_UP_TERMS)),
        known_case_request=any(term in normalized for term in (_normalize(item) for item in KNOWN_CASE_TERMS)),
        resolved_case_request=any(term in normalized for term in (_normalize(item) for item in RESOLVED_CASE_TERMS)),
        unresolved_case_request=any(term in normalized for term in (_normalize(item) for item in UNRESOLVED_CASE_TERMS)),
        status_request=status_request,
        policy_question=any(term in normalized for term in (_normalize(item) for item in POLICY_TERMS)),
        troubleshooting_request=any(term in normalized for term in (_normalize(item) for item in TROUBLESHOOTING_TERMS)),
        mentioned_systems=mentioned_systems,
    )


def assess_evidence(
    message: str,
    retrieved: list,
    *,
    planner_output: dict[str, Any] | None = None,
    memory_results: list | None = None,
    related_incidents_by_id: dict[int, Any] | None = None,
) -> tuple[QuerySignals, EvidenceAssessment]:
    signals = detect_query_signals(message)
    related_incidents_by_id = related_incidents_by_id or {}
    memory_results = memory_results or []

    document_count = sum(1 for item in retrieved if getattr(item, "source_type", "") == "document")
    incident_count = sum(1 for item in retrieved if getattr(item, "source_type", "") == "incident")
    source_types = _unique([str(getattr(item, "source_type", "")) for item in retrieved if getattr(item, "source_type", "")])
    source_systems = _unique([_source_system(item) for item in retrieved if _source_system(item)])
    related_only = bool(retrieved) and incident_count == len(retrieved)

    matching_sources = _matching_sources(retrieved, signals.mentioned_systems)
    matching_documents = [item for item in matching_sources if getattr(item, "source_type", "") == "document"]
    matching_incidents = [item for item in matching_sources if getattr(item, "source_type", "") == "incident"]
    resolved_incidents = [item for item in matching_incidents if _is_resolved_incident(item, related_incidents_by_id)]
    unresolved_incidents = [item for item in matching_incidents if _is_unresolved_incident(item, related_incidents_by_id)]
    system_mismatch = bool(signals.mentioned_systems and retrieved and not matching_sources)

    direct_documents = matching_documents
    direct_chunks: list = []
    should_block = False
    reason_code = "sufficient_evidence"
    blocking_reason = ""
    clarification_question = ""
    evidence_mode = "insufficient_evidence"
    allowed_behavior = "ask_clarification"
    requires_direct_evidence = bool(
        signals.new_issue
        or signals.procedure_request
        or signals.dissatisfied_followup
        or signals.mentioned_systems
        or signals.known_case_request
        or signals.resolved_case_request
        or signals.unresolved_case_request
        or signals.status_request
        or signals.policy_question
        or signals.troubleshooting_request
    )

    if system_mismatch:
        should_block, reason_code, blocking_reason, clarification_question = _block(
            "source_system_mismatch",
            "Los fragmentos recuperados no pertenecen al sistema que mencionas. Confirma el sistema y el proceso afectado para buscar una respuesta fiable.",
        )
    elif signals.status_request:
        if signals.unresolved_case_request and unresolved_incidents:
            direct_chunks = unresolved_incidents
            evidence_mode = "direct_incident_status"
            allowed_behavior = "say_incident_unresolved"
        elif signals.resolved_case_request and not signals.unresolved_case_request and resolved_incidents:
            direct_chunks = resolved_incidents
            evidence_mode = "direct_incident_status"
            allowed_behavior = "say_incident_resolved"
        elif resolved_incidents and unresolved_incidents:
            should_block, reason_code, blocking_reason, clarification_question = _block(
                "ambiguous_status_multiple_incidents",
                "He encontrado casos resueltos y abiertos parecidos. Indica el identificador del caso o el detalle operativo para confirmar el estado correcto.",
            )
        elif resolved_incidents:
            direct_chunks = resolved_incidents
            evidence_mode = "direct_incident_status"
            allowed_behavior = "say_incident_resolved"
        elif unresolved_incidents:
            direct_chunks = unresolved_incidents
            evidence_mode = "direct_incident_status"
            allowed_behavior = "say_incident_unresolved"
        elif matching_incidents:
            direct_chunks = matching_incidents
            evidence_mode = "direct_incident_status"
            allowed_behavior = "answer_with_sources"
        else:
            should_block, reason_code, blocking_reason, clarification_question = _block(
                "status_without_incident",
                "No he recuperado una incidencia concreta para confirmar si esta resuelta o abierta. Indica el caso, sistema o sintoma exacto.",
            )
    elif signals.resolved_case_request:
        if resolved_incidents:
            direct_chunks = resolved_incidents
            evidence_mode = "direct_resolved_incident"
            allowed_behavior = "say_incident_resolved"
        else:
            should_block, reason_code, blocking_reason, clarification_question = _block(
                "resolved_case_without_resolved_incident",
                "No he recuperado una incidencia resuelta directa. Indica el caso conocido o el sintoma exacto para no inventar la correccion.",
            )
    elif signals.unresolved_case_request:
        if unresolved_incidents:
            direct_chunks = unresolved_incidents
            evidence_mode = "direct_unresolved_incident"
            allowed_behavior = "say_incident_unresolved"
        else:
            should_block, reason_code, blocking_reason, clarification_question = _block(
                "unresolved_case_without_open_incident",
                "No he recuperado una incidencia abierta directa. Indica el caso pendiente o el sintoma exacto para confirmar el estado.",
            )
    elif signals.known_case_request:
        if resolved_incidents:
            direct_chunks = resolved_incidents
            evidence_mode = "direct_resolved_incident"
            allowed_behavior = "say_incident_resolved"
        elif unresolved_incidents:
            direct_chunks = unresolved_incidents
            evidence_mode = "direct_unresolved_incident"
            allowed_behavior = "say_incident_unresolved"
        elif matching_incidents:
            direct_chunks = matching_incidents
            evidence_mode = "direct_known_incident"
            allowed_behavior = "answer_with_sources"
        else:
            should_block, reason_code, blocking_reason, clarification_question = _block(
                "known_case_without_incident",
                "No he recuperado un caso conocido directo. Indica sistema, sintoma o referencia para buscar el caso correcto.",
            )
    elif signals.new_issue and related_only:
        should_block, reason_code, blocking_reason, clarification_question = _block(
            "new_issue_only_similar_incidents",
            "Al ser un error nuevo, necesito el error exacto, pantalla o paso donde falla, proceso afectado y referencia operativa como pedido, ruta o ubicacion.",
        )
    elif signals.policy_question:
        if direct_documents:
            direct_chunks = direct_documents
            evidence_mode = "direct_policy_document"
            allowed_behavior = "answer_with_sources"
        else:
            should_block, reason_code, blocking_reason, clarification_question = _block(
                "policy_without_document",
                "No he recuperado una politica documentada suficiente. Indica la politica, sistema o contexto para evitar responder con casos aislados.",
            )
    elif signals.procedure_request and direct_documents:
        direct_chunks = direct_documents
        evidence_mode = "direct_document"
        allowed_behavior = "answer_with_sources"
    elif signals.procedure_request and signals.troubleshooting_request and resolved_incidents:
        direct_chunks = resolved_incidents
        evidence_mode = "direct_troubleshooting_incident"
        allowed_behavior = "answer_with_sources"
    elif signals.procedure_request:
        should_block, reason_code, blocking_reason, clarification_question = _block(
            "procedure_without_document",
            "No he recuperado un procedimiento documentado suficiente. Indica sistema, proceso y objetivo para evitar darte pasos inventados.",
        )
    elif signals.dissatisfied_followup and memory_results:
        direct_chunks = direct_documents or matching_incidents
        evidence_mode = "conversation_supported"
        allowed_behavior = "answer_with_sources"
        requires_direct_evidence = bool(direct_chunks)
    elif signals.dissatisfied_followup and not memory_results:
        should_block, reason_code, blocking_reason, clarification_question = _block(
            "follow_up_without_conversation_memory",
            "Para corregir la respuesta necesito recuperar el contexto anterior o que concretes el sistema y proceso exacto.",
        )
    elif direct_documents:
        direct_chunks = direct_documents
        evidence_mode = "direct_document"
        allowed_behavior = "answer_with_sources"
    elif matching_incidents:
        direct_chunks = matching_incidents
        evidence_mode = "direct_known_incident"
        allowed_behavior = "answer_with_sources"
    else:
        should_block, reason_code, blocking_reason, clarification_question = _block(
            "insufficient_evidence",
            "Necesito un dato mas para responder con seguridad. Indica sistema, proceso, error exacto o contexto operativo.",
        )

    if should_block:
        evidence_mode = evidence_mode if evidence_mode != "insufficient_evidence" else reason_code
        allowed_behavior = "ask_clarification"
        direct_chunks = []

    semantic_confidence = _semantic_confidence(
        should_block=should_block,
        direct_chunks=direct_chunks,
        evidence_mode=evidence_mode,
        retrieved=retrieved,
        memory_results=memory_results,
    )

    return signals, EvidenceAssessment(
        should_block_answer=should_block,
        requires_direct_evidence=requires_direct_evidence,
        reason_code=reason_code,
        clarification_question=clarification_question,
        document_count=document_count,
        incident_count=incident_count,
        source_types=source_types,
        source_systems=source_systems,
        related_only=related_only,
        system_mismatch=system_mismatch,
        has_matching_document=bool(matching_documents),
        has_matching_source=bool(matching_sources),
        direct_chunk_ids=[int(getattr(item, "chunk_id")) for item in direct_chunks],
        semantic_confidence=semantic_confidence,
        evidence_mode=evidence_mode,
        allowed_behavior=allowed_behavior,
        resolved_incident_count=len(resolved_incidents),
        unresolved_incident_count=len(unresolved_incidents),
        direct_resolved_incident_ids=[int(getattr(item, "source_id")) for item in resolved_incidents],
        direct_unresolved_incident_ids=[int(getattr(item, "source_id")) for item in unresolved_incidents],
        direct_document_ids=[int(getattr(item, "source_id")) for item in direct_documents],
        blocking_reason=blocking_reason,
    )


def build_policy_clarification_answer(assessment: EvidenceAssessment, clarification_attempts: int) -> str:
    question = assessment.clarification_question or (
        "Necesito un dato mas para responder con seguridad. Indica sistema, proceso, error exacto o contexto operativo."
    )
    return (
        "No tengo evidencia directa suficiente para darte una solucion fiable.\n\n"
        f"{question}\n\n"
        "Responde con ese dato para continuar."
    )


def _block(reason_code: str, clarification_question: str) -> tuple[bool, str, str, str]:
    return True, reason_code, reason_code, clarification_question


def _matching_sources(retrieved: list, systems: list[str]) -> list:
    if not systems:
        return list(retrieved)
    wanted = {_canonical_system_key(system) for system in systems if _canonical_system_key(system)}
    return [item for item in retrieved if _canonical_system_key(_source_system(item)) in wanted]


def _source_system(item) -> str:
    metadata = getattr(item, "metadata", {}) or {}
    return str(metadata.get("affected_system") or "")


def _canonical_system_key(value: str) -> str:
    normalized_value = _normalize(value)
    if not normalized_value:
        return ""
    for canonical, aliases in SYSTEM_ALIASES.items():
        if normalized_value == _normalize(canonical):
            return _system_key(canonical)
        for alias in aliases:
            if normalized_value == _normalize(alias):
                return _system_key(canonical)
    return _system_key(value)


def _is_resolved_incident(item, related_incidents_by_id: dict[int, Any]) -> bool:
    if getattr(item, "source_type", "") != "incident":
        return False
    incident = related_incidents_by_id.get(int(getattr(item, "source_id")))
    if incident is not None:
        return bool(getattr(incident, "is_resolved", False) or _normalize(str(getattr(incident, "status", ""))) == "resolved")
    metadata = getattr(item, "metadata", {}) or {}
    return bool(metadata.get("is_resolved") or _normalize(str(metadata.get("status", ""))) == "resolved")


def _is_unresolved_incident(item, related_incidents_by_id: dict[int, Any]) -> bool:
    if getattr(item, "source_type", "") != "incident":
        return False
    incident = related_incidents_by_id.get(int(getattr(item, "source_id")))
    if incident is not None:
        status = _normalize(str(getattr(incident, "status", "")))
        return bool(getattr(incident, "is_resolved", None) is False or status in {"open", "abierta", "abierto"})
    metadata = getattr(item, "metadata", {}) or {}
    status = _normalize(str(metadata.get("status", "")))
    return bool(metadata.get("is_resolved") is False or status in {"open", "abierta", "abierto"})


def _semantic_confidence(
    *,
    should_block: bool,
    direct_chunks: list,
    evidence_mode: str,
    retrieved: list,
    memory_results: list,
) -> float:
    if should_block:
        return 0.2 if retrieved else 0.0
    if not direct_chunks:
        return 0.35
    top_score = max(float(getattr(item, "final_score", 0.0) or 0.0) for item in direct_chunks)
    mode_bonus = {
        "direct_document": 0.20,
        "direct_policy_document": 0.20,
        "direct_resolved_incident": 0.18,
        "direct_unresolved_incident": 0.18,
        "direct_incident_status": 0.18,
        "direct_troubleshooting_incident": 0.12,
        "conversation_supported": 0.10,
    }.get(evidence_mode, 0.08)
    memory_bonus = 0.05 if memory_results else 0.0
    return round(min(1.0, max(0.55, top_score + mode_bonus + memory_bonus)), 4)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _system_key(value: str) -> str:
    return "_".join(_normalize(value).split())
