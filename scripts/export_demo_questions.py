from __future__ import annotations

import argparse
import json
from pathlib import Path


DEMO_QUESTIONS = [
    {
        "id": "demo-01",
        "category": "Documentación operativa",
        "question": "Cómo cierro una expedición pendiente en AlmaTrack WMS?",
        "goal": "Mostrar una respuesta de procedimiento de almacén con fuentes documentales.",
        "expected_behavior": "answer_with_sources",
        "expected_sources": ["document:1"],
        "technical_point": "Búsqueda híbrida, fuentes visibles y respuesta grounded.",
    },
    {
        "id": "demo-02",
        "category": "Documentación operativa",
        "question": "Cómo se libera un pedido retenido por validación manual en LogiCore ERP?",
        "goal": "Demostrar consulta de pedidos y bloqueos operativos.",
        "expected_behavior": "answer_with_sources",
        "expected_sources": ["document:4"],
        "technical_point": "Uso de documentación interna para pasos operativos.",
    },
    {
        "id": "demo-03",
        "category": "Documentación operativa",
        "question": "Qué reviso si RutaNexo TMS no publica una nueva secuencia válida?",
        "goal": "Mostrar troubleshooting guiado en transporte.",
        "expected_behavior": "answer_with_sources",
        "expected_sources": ["document:3"],
        "technical_point": "Retrieval por sistema y proceso.",
    },
    {
        "id": "demo-04",
        "category": "Seguridad y accesos",
        "question": "Cómo se gestiona un permiso temporal en SafeGate?",
        "goal": "Demostrar política de accesos para personal externo.",
        "expected_behavior": "answer_with_sources",
        "expected_sources": ["document:14"],
        "technical_point": "Respuesta basada en política, no en casos aislados.",
    },
    {
        "id": "demo-05",
        "category": "Onboarding",
        "question": "Qué pasos debe completar un coordinador nuevo en OnboardHub?",
        "goal": "Mostrar cobertura de onboarding y formación operativa.",
        "expected_behavior": "answer_with_sources",
        "expected_sources": ["document:7"],
        "technical_point": "Cobertura multiárea del corpus.",
    },
    {
        "id": "demo-06",
        "category": "Calidad, documentos y KPIs",
        "question": "Cómo se trata una no conformidad en QualiTrace QMS sin dictamen final?",
        "goal": "Mostrar gestión de calidad y abstención si falta dictamen.",
        "expected_behavior": "answer_with_sources",
        "expected_sources": ["document:10"],
        "technical_point": "Diferenciar procedimiento documentado de resolución definitiva.",
    },
    {
        "id": "demo-07",
        "category": "Incidencias conocidas",
        "question": "Cómo se resolvió la ruta congelada tras parada prioritaria en RutaNexo TMS?",
        "goal": "Enseñar una incidencia resuelta reutilizada como conocimiento.",
        "expected_behavior": "say_incident_resolved",
        "expected_sources": ["incident:43"],
        "technical_point": "Uso de tickets históricos como fuente RAG.",
    },
    {
        "id": "demo-08",
        "category": "Incidencias conocidas",
        "question": "Hay una ubicación RF inconsistente en AlmaTrack WMS, existe solución definitiva?",
        "goal": "Mostrar caso abierto/no resuelto y evitar inventar una solución.",
        "expected_behavior": "say_incident_unresolved",
        "expected_sources": ["incident:91"],
        "technical_point": "Abstención controlada ante conocimiento incompleto.",
    },
    {
        "id": "demo-09",
        "category": "Flujo de demo",
        "question": "No puedo cerrar la operación, qué debo revisar?",
        "goal": "Forzar una aclaración útil cuando la consulta es ambigua.",
        "expected_behavior": "ask_clarification",
        "expected_sources": [],
        "technical_point": "Gating de evidencia antes de responder.",
    },
    {
        "id": "demo-10",
        "category": "Flujo de demo",
        "question": "No útil, la respuesta no me sirve.",
        "goal": "Enseñar el loop de feedback.",
        "expected_behavior": "feedback",
        "expected_sources": [],
        "technical_point": "Registro de feedback y trazabilidad.",
    },
]


def render_markdown() -> str:
    lines = [
        "# Demo Questions",
        "",
        "| id | categoría | pregunta | objetivo | comportamiento esperado | fuentes esperadas | punto técnico |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in DEMO_QUESTIONS:
        lines.append(
            "| {id} | {category} | {question} | {goal} | {expected_behavior} | {expected_sources} | {technical_point} |".format(
                id=item["id"],
                category=item["category"].replace("|", "/"),
                question=item["question"].replace("|", "/"),
                goal=item["goal"].replace("|", "/"),
                expected_behavior=item["expected_behavior"],
                expected_sources=", ".join(item["expected_sources"]) or "-",
                technical_point=item["technical_point"].replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta las preguntas de demo para portfolio.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    content = json.dumps(DEMO_QUESTIONS, ensure_ascii=False, indent=2) if args.format == "json" else render_markdown()
    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
