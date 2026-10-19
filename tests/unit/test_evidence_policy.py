from __future__ import annotations

from types import SimpleNamespace

from internal_assistant.chat.evidence import assess_evidence, detect_query_signals
from internal_assistant.rag.retrieval import RetrievedChunk


def chunk(
    *,
    chunk_id: int,
    source_type: str,
    source_id: int,
    system: str,
    title: str = "Fuente",
    final_score: float = 0.9,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_type=source_type,
        source_id=source_id,
        content=f"Contenido de {title}",
        metadata={"title": title, "affected_system": system},
        final_score=final_score,
    )


def test_detects_new_issue_signal():
    signals = detect_query_signals("No encuentro solucion para un error nuevo en AlmaTrack WMS")

    assert signals.new_issue is True
    assert signals.mentioned_systems == ["AlmaTrack WMS"]


def test_detects_procedure_request_signal():
    signals = detect_query_signals("Como registro una entrega parcial en LogiCore ERP")

    assert signals.procedure_request is True
    assert signals.mentioned_systems == ["LogiCore ERP"]


def test_detects_dissatisfied_followup_signal():
    signals = detect_query_signals("esa respuesta no me vale, explicamelo mejor")

    assert signals.dissatisfied_followup is True


def test_detects_resolved_unresolved_and_status_signals():
    resolved = detect_query_signals("Como se resolvio una visita tecnica en SafeGate")
    unresolved = detect_query_signals("Hay un caso abierto parecido en AlmaTrack WMS")
    status = detect_query_signals("Esta resuelta o sigue abierta la ruta en RutaNexo")
    definitive = detect_query_signals("Hay una solucion definitiva ya documentada para SafeGate")

    assert resolved.resolved_case_request is True
    assert unresolved.unresolved_case_request is True
    assert status.status_request is True
    assert definitive.unresolved_case_request is True


def test_detects_all_current_system_names_and_aliases():
    cases = {
        "RutaNexo": "RutaNexo TMS",
        "RutaNexo TMS": "RutaNexo TMS",
        "QualiTrace QMS": "QualiTrace QMS",
        "ScanBridge IDP": "ScanBridge IDP",
        "OpsLake": "OpsLake",
        "HelpOps": "HelpOps",
        "DocuFlow": "DocuFlow",
    }

    for text, expected in cases.items():
        signals = detect_query_signals(f"Necesito revisar {text}")
        assert expected in signals.mentioned_systems


def test_rutanexo_alias_matches_tms_source():
    _, assessment = assess_evidence(
        "En RutaNexo una ruta queda congelada. Esta resuelta o sigue abierta",
        [chunk(chunk_id=1, source_type="incident", source_id=10, system="RutaNexo TMS")],
        related_incidents_by_id={10: SimpleNamespace(id=10, is_resolved=True, status="resolved")},
    )

    assert assessment.system_mismatch is False
    assert assessment.should_block_answer is False
    assert assessment.allowed_behavior == "say_incident_resolved"


def test_rutanexo_tms_query_matches_legacy_rutanexo_source():
    _, assessment = assess_evidence(
        "En RutaNexo TMS una ruta queda congelada. Esta resuelta o sigue abierta",
        [chunk(chunk_id=1, source_type="incident", source_id=10, system="RutaNexo")],
        related_incidents_by_id={10: SimpleNamespace(id=10, is_resolved=True, status="resolved")},
    )

    assert assessment.system_mismatch is False
    assert assessment.should_block_answer is False
    assert assessment.allowed_behavior == "say_incident_resolved"


def test_resolved_case_allows_direct_resolved_incident():
    _, assessment = assess_evidence(
        "Como se resolvio una visita tecnica con acceso rechazado en SafeGate",
        [chunk(chunk_id=1, source_type="incident", source_id=10, system="SafeGate")],
        related_incidents_by_id={10: SimpleNamespace(id=10, is_resolved=True, status="resolved")},
    )

    assert assessment.should_block_answer is False
    assert assessment.evidence_mode == "direct_resolved_incident"
    assert assessment.allowed_behavior == "say_incident_resolved"
    assert assessment.direct_chunk_ids == [1]


def test_resolved_case_allows_direct_resolved_incident_with_tms_suffix():
    _, assessment = assess_evidence(
        "Hubo una ruta congelada tras parada prioritaria en RutaNexo TMS. Como quedo resuelta",
        [chunk(chunk_id=1, source_type="incident", source_id=43, system="RutaNexo TMS")],
        related_incidents_by_id={43: SimpleNamespace(id=43, is_resolved=True, status="resolved")},
    )

    assert assessment.system_mismatch is False
    assert assessment.should_block_answer is False
    assert assessment.evidence_mode == "direct_resolved_incident"
    assert assessment.allowed_behavior == "say_incident_resolved"


def test_unresolved_case_allows_direct_open_incident():
    _, assessment = assess_evidence(
        "Hay un caso abierto parecido de ubicacion RF en AlmaTrack WMS",
        [chunk(chunk_id=2, source_type="incident", source_id=11, system="AlmaTrack WMS")],
        related_incidents_by_id={11: SimpleNamespace(id=11, is_resolved=False, status="open")},
    )

    assert assessment.should_block_answer is False
    assert assessment.evidence_mode == "direct_unresolved_incident"
    assert assessment.allowed_behavior == "say_incident_unresolved"
    assert assessment.direct_chunk_ids == [2]


def test_unresolved_case_allows_direct_open_incident_with_tms_suffix():
    _, assessment = assess_evidence(
        "En RutaNexo TMS hay recalculo masivo pendiente por meteorologia. Se conoce solucion",
        [chunk(chunk_id=2, source_type="incident", source_id=93, system="RutaNexo TMS")],
        related_incidents_by_id={93: SimpleNamespace(id=93, is_resolved=False, status="open")},
    )

    assert assessment.system_mismatch is False
    assert assessment.should_block_answer is False
    assert assessment.allowed_behavior == "say_incident_unresolved"


def test_status_request_prefers_open_incident_when_user_asks_if_still_open():
    _, assessment = assess_evidence(
        "En RutaNexo una ruta queda congelada. Esta resuelta o sigue abierta",
        [
            chunk(chunk_id=1, source_type="incident", source_id=10, system="RutaNexo"),
            chunk(chunk_id=2, source_type="incident", source_id=11, system="RutaNexo"),
        ],
        related_incidents_by_id={
            10: SimpleNamespace(id=10, is_resolved=True, status="resolved"),
            11: SimpleNamespace(id=11, is_resolved=False, status="open"),
        },
    )

    assert assessment.should_block_answer is False
    assert assessment.allowed_behavior == "say_incident_unresolved"
    assert assessment.direct_chunk_ids == [2]


def test_marks_related_only_for_only_resolved_incidents():
    _, assessment = assess_evidence(
        "No encuentro solucion para un error nuevo en AlmaTrack WMS",
        [
            chunk(chunk_id=1, source_type="incident", source_id=10, system="AlmaTrack WMS"),
            chunk(chunk_id=2, source_type="incident", source_id=11, system="AlmaTrack WMS"),
        ],
    )

    assert assessment.related_only is True
    assert assessment.should_block_answer is True
    assert assessment.reason_code == "new_issue_only_similar_incidents"


def test_marks_system_mismatch():
    _, assessment = assess_evidence(
        "No encuentro solucion para un error nuevo en AlmaTrack WMS",
        [chunk(chunk_id=1, source_type="document", source_id=10, system="SafeGate")],
    )

    assert assessment.system_mismatch is True
    assert assessment.should_block_answer is True
    assert assessment.reason_code == "source_system_mismatch"


def test_planner_hallucinated_system_does_not_create_hard_mismatch():
    _, assessment = assess_evidence(
        "Soy nuevo en operaciones. Que pasos de onboarding debo completar?",
        [chunk(chunk_id=1, source_type="document", source_id=10, system="OnboardHub")],
        planner_output={"mentioned_systems": ["LogiCore ERP"]},
    )

    assert assessment.system_mismatch is False
    assert assessment.should_block_answer is False
    assert assessment.evidence_mode == "direct_document"
