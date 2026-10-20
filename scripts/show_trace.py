"""Muestra una traza extendida de una peticion (local o en Azure Blob) de forma legible.

Uso:
    uv run python scripts/show_trace.py <request_id | ruta.json> [--blob] [--top 10] [--source document:14]

- Sin --blob busca en TRACE_STORE_DIR (por defecto `traces/`).
- Con --blob lee del contenedor TRACE_STORE_CONTAINER de TRACE_STORE_ACCOUNT_URL usando
  tu sesion de Azure CLI (necesitas el rol Storage Blob Data Reader).
- --source sigue una fuente concreta por todas las etapas para ver donde se pierde.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from internal_assistant.config import get_settings  # noqa: E402


def _load_local(ref: str, base_dir: Path) -> dict:
    path = Path(ref)
    if not path.exists():
        matches = sorted(base_dir.rglob(f"*{ref}*.json"))
        if not matches:
            raise SystemExit(f"No hay ninguna traza con '{ref}' en {base_dir}")
        path = matches[-1]
    print(f"# {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_blob(ref: str) -> dict:
    from azure.identity import AzureCliCredential
    from azure.storage.blob import BlobServiceClient

    settings = get_settings()
    if not settings.trace_store_account_url:
        raise SystemExit("Define TRACE_STORE_ACCOUNT_URL para leer trazas de Azure Blob")
    container = BlobServiceClient(settings.trace_store_account_url, credential=AzureCliCredential()).get_container_client(
        settings.trace_store_container
    )
    names = sorted(blob.name for blob in container.list_blobs() if ref in blob.name)
    if not names:
        raise SystemExit(f"No hay ninguna traza con '{ref}' en el contenedor {settings.trace_store_container}")
    print(f"# {settings.trace_store_container}/{names[-1]}")
    return json.loads(container.download_blob(names[-1]).readall())


def _rows(rows: list[dict], fields: list[str], top: int) -> None:
    for row in rows[:top]:
        values = "  ".join(f"{field}={row.get(field)}" for field in fields if row.get(field) is not None)
        print(f"    #{row['rank']:<3} {row['source']:<14} {str(row.get('title', ''))[:48]:<48} {values}")
    if len(rows) > top:
        print(f"    ... {len(rows) - top} mas")


def _follow(stages: dict, source: str) -> None:
    print(f"\n## Donde esta {source}")
    for stage in ("vector", "bm25", "fused", "reranked", "returned"):
        ranks = [row["rank"] for row in stages.get(stage, []) if row["source"] == source]
        print(f"  {stage:<9} {ranks[:5] if ranks else 'no aparece'}")
    dropped = [row["rank"] for row in stages.get("token_budget", {}).get("dropped", []) if row["source"] == source]
    if dropped:
        print(f"  recortado por presupuesto de tokens: {dropped}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ref", help="request_id (o parte) o ruta al JSON")
    parser.add_argument("--blob", action="store_true", help="Leer de Azure Blob en vez de la carpeta local")
    parser.add_argument("--top", type=int, default=10, help="Filas por etapa")
    parser.add_argument("--source", help="Fuente a seguir por las etapas, p. ej. document:14")
    parser.add_argument("--prompts", action="store_true", help="Imprimir tambien los prompts completos enviados al LLM")
    args = parser.parse_args()

    settings = get_settings()
    document = _load_blob(args.ref) if args.blob else _load_local(args.ref, ROOT / settings.trace_store_dir)

    print(f"request_id: {document['request_id']}  inicio: {document['started_at']}  duracion: {document['duration_ms']} ms")
    print(f"metadata: {json.dumps(document['metadata'], ensure_ascii=False)}")
    print(f"pregunta: {document['inputs'].get('question')}")
    if document.get("error"):
        print(f"ERROR: {document['error']}")

    for run in document["runs"]:
        print(f"\n## {run['name']} ({run['duration_ms']} ms)")
        outputs = run.get("outputs") or {}
        if run["name"] == "rag.plan":
            plan = outputs.get("planner_output", {})
            for key in ("intent", "needs_knowledge_index", "should_ask_clarification_first", "knowledge_index_query", "search_keywords", "retrieval_filters", "filter_reason"):
                print(f"  {key}: {json.dumps(plan.get(key), ensure_ascii=False)}")
            if outputs.get("fallback_used"):
                print("  (plan de respaldo: fallo el LLM planificador)")
        elif run["name"] == "rag.retrieve":
            print(f"  keyword_query: {outputs.get('keyword_query')}")
            print(f"  confianza: {outputs.get('confidence')}  chunks: {outputs.get('retrieved_count')}  cuarentena: {outputs.get('quarantined_chunk_ids')}")
        elif run["name"] == "rag.generate_answer":
            decision = outputs.get("decision", {})
            print(f"  needs_clarification={decision.get('needs_clarification')}  used_chunk_ids={decision.get('used_chunk_ids')}  guard={outputs.get('output_guard')}")
        elif run["name"] == "rag.format_response":
            inputs = run.get("inputs") or {}
            decision = inputs.get("decision") or {}
            print(f"  comportamiento: {inputs.get('actual_behavior')}  politica: {decision.get('policy_decision', '-')}")
            print(f"  respuesta: {outputs.get('answer', '')[:400]}")

    for event in document["events"]:
        name = event["name"]
        if name == "retrieval.bm25":
            print(f"\n## BM25  lexemas={event['lexemes']}  df={event['document_frequency']}  candidatos={event['matched_candidates']}")
        elif name == "retrieval.hybrid_search":
            stages = event["stages"]
            print(f"\n## Busqueda hibrida  filtros={json.dumps(event['filters'], ensure_ascii=False)}  fallback_filtros={event['filter_fallback']}")
            print(f"  reranker: {event['reranker']}")
            print(f"  vector ({len(stages['vector'])}):")
            _rows(stages["vector"], ["score"], args.top)
            print(f"  bm25 ({len(stages['bm25'])}):")
            _rows(stages["bm25"], ["score"], args.top)
            print(f"  fusion RRF ({len(stages['fused'])}):")
            _rows(stages["fused"], ["rrf_score", "vector_rank", "bm25_rank"], args.top)
            if stages["reranked"]:
                print(f"  reranker ({len(stages['reranked'])}):")
                _rows(stages["reranked"], ["rerank_score", "fused_rank"], args.top)
            budget = stages["token_budget"]
            print(f"  presupuesto: {budget['kept_tokens']}/{budget['budget_tokens']} tokens, recortados {len(budget['dropped'])}")
            print(f"  contexto final ({len(stages['returned'])}):")
            _rows(stages["returned"], ["final_score", "tokens"], args.top)
            if args.source:
                _follow(stages, args.source)
        elif name == "evidence_gate":
            assessment = event.get("evidence_assessment", {})
            print(
                f"\n## Filtro de evidencia  confianza={event['confidence']:.3f} umbral={event['confidence_threshold']} "
                f"corte_por_confianza={event['low_confidence_gate']} pre_gate={event['pre_gate_answer']} "
                f"motivo={assessment.get('reason_code')} semantica={assessment.get('semantic_confidence')}"
            )
        elif name == "llm.call":
            print(f"\n## LLM {event['purpose']} ({event['model']}, {event.get('latency_ms')} ms, fin={event.get('finish_reason')})")
            if args.prompts:
                for message in event["messages"]:
                    print(f"  [{message['role']}]\n{message['content']}\n")
            print(f"  salida cruda: {str(event.get('raw_output'))[:1500]}")
        elif name == "input_guard":
            print(f"\n## Guardia de entrada  riesgo={event.get('risk')} categorias={event.get('categories')} intent={event.get('intent')}")

    print("\n## Uso")
    for row in document.get("usage", []):
        print(f"  {row['component']}/{row['purpose']} {row['model']}: in={row['input_tokens']} cached={row['cached_input_tokens']} out={row['output_tokens']} su={row['search_units']}")


if __name__ == "__main__":
    main()
