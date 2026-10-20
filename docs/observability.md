# Trazas extendidas por peticion

LangSmith y Application Insights sirven para ver tiempos y errores, pero recortan o redactan el contenido.
Para entender por que la busqueda acierta o falla hace falta todo el detalle, asi que cada peticion de chat
guarda ademas un JSON completo en un storage.

## Donde se guardan

```
{TRACE_CLIENT}/{TRACE_PROJECT}/{conversacion}/{AAAAMMDDTHHMMSS}_{request_id}.json
{TRACE_CLIENT}/{TRACE_PROJECT}/eval/{escenario}/{AAAAMMDDTHHMMSS}_{request_id}.json   # ejecuciones de la eval
```

| `TRACE_STORE` | Destino |
| --- | --- |
| `local` | carpeta `TRACE_STORE_DIR` (por defecto `traces/`, ignorada por git) |
| `azure_blob` | contenedor `TRACE_STORE_CONTAINER` (`rag-traces`) de `TRACE_STORE_ACCOUNT_URL`; la Web App escribe con su identidad administrada (rol *Storage Blob Data Contributor* sobre el contenedor) |
| `none` | desactivado |

En Azure una regla de ciclo de vida borra las trazas a los 90 dias ([trace-storage.bicep](../infra/modules/trace-storage.bicep)).
La escritura va en segundo plano y nunca rompe una peticion.

El `request_id` vuelve en la respuesta de `/api/chat` y `/api/chat/stream`, y es el mismo id de traza que en LangSmith.

## Que contiene

- `inputs` / `outputs`: pregunta y respuesta final con fuentes.
- `metadata`: conversacion, usuario (hash), proveedor y modelos, intent, escenario de eval.
- `runs`: cada paso con entradas, salidas y duracion: `rag.plan` (plan completo del planificador),
  `rag.memory_retrieve`, `rag.retrieve`, `rag.generate_answer` (contexto enviado, decision, guardia de salida),
  `rag.format_response` (comportamiento final y politica aplicada, p. ej. `low_retrieval_confidence`).
- `events`:
  - `input_guard`: riesgo y categorias del guardia de entrada.
  - `llm.call`: prompt completo y salida cruda del modelo (planner y respuesta), tokens y latencia.
  - `retrieval.bm25`: lexemas, frecuencia documental, candidatos y puntuacion BM25 de cada uno.
  - `retrieval.hybrid_search`: configuracion, filtros (y el intento filtrado si hubo fallback) y las etapas
    `vector`, `bm25`, `fused` (RRF con la posicion en cada lista), `reranked` (puntuacion y posicion previa),
    `token_budget` (recortados) y `returned`.
  - `evidence_gate`: confianza, umbral y decision del filtro de evidencia.
- `usage`: tokens reales de LLM y embeddings y *search units* del reranker de esa peticion.

## Leer una traza

```bash
uv run python scripts/show_trace.py <request_id>                     # local
uv run python scripts/show_trace.py <request_id> --blob              # Azure (requiere Storage Blob Data Reader)
uv run python scripts/show_trace.py <request_id> --source document:14 # donde se pierde una fuente
uv run python scripts/show_trace.py <request_id> --prompts           # prompts completos
```

Los informes de la eval incluyen el `request_id` de cada turno para abrir su traza.
