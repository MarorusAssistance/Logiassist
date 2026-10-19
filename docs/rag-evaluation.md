# Evaluacion RAG

## Por que evaluar el RAG

El asistente no se da por valido solo porque responda. Hay que comprobar:

- si recupera las fuentes adecuadas
- si responde con el comportamiento esperado
- si cita bien
- si se abstiene cuando debe
- si resiste preguntas ambiguas o maliciosas

## Que metricas se usan

### Retrieval

- `hit_at_1`
- `hit_at_3`
- `hit_at_5`
- `recall_at_5`
- `mrr`
- `expected_source_coverage`
- `source_type_match_rate`

### Respuesta

- `answer_generated`
- `answer_contains_required_terms`
- `answer_avoids_forbidden_terms`
- `answer_mentions_uncertainty_when_needed`
- `answer_uses_only_retrieved_context`
- `answer_expected_behavior_match`

### Fuentes

- `citation_present_rate`
- `citation_source_validity_rate`
- `citation_coverage_rate`
- `unsupported_answer_rate`

### Abstencion

- `abstention_precision`
- `abstention_recall`
- `false_answer_when_should_abstain`
- `unnecessary_abstention_when_answer_exists`
- `clarification_rate_when_required`
- `incident_registration_offer_rate`

## Como ejecutar la evaluacion

### Smoke test barato

```bash
python -m uv run python scripts/run_rag_eval.py --provider mock
```

### OpenAI

```bash
python -m uv run python scripts/run_rag_eval.py --provider openai --include-adversarial
```

### Provider local compatible

```bash
python -m uv run python scripts/run_rag_eval.py --provider openai_compatible --include-adversarial
```

### Judge LLM opcional

```bash
python -m uv run python scripts/run_rag_eval.py --provider openai --use-llm-judge
```

### Ablation de retrieval

```bash
python -m uv run python scripts/compare_retrieval_configs.py
```

## Donde quedan los resultados

Los reportes se guardan en `evaluation/reports/` como archivos JSON y Markdown. No se suben automaticamente a PostgreSQL, Table Storage, Blob Storage ni a artifacts remotos.

## Como interpretar los reportes

- `hit_at_5` bajo suele indicar un problema primario de retrieval
- `citation_coverage_rate` baja indica que la respuesta no esta usando o exponiendo bien las fuentes correctas
- `false_answer_when_should_abstain` alto significa que el asistente esta inventando demasiado
- `clarification_rate_when_required` bajo indica que el flujo ambiguo esta siendo demasiado agresivo

## LLM-as-a-judge

`HeuristicJudge` es el modo por defecto y no usa APIs externas. `LLMJudge` es una segunda senal semantica util para evaluaciones manuales, pero no deberia ser obligatorio en CI.

## Trazabilidad con LangSmith y App Insights

La evaluacion genera reportes offline, pero el runtime tambien puede emitir trazas mientras usas `/demo`, `/api/chat` o Teams.

`Application Insights` se usa para operacion:

- errores de `/api/chat`
- latencias por etapa
- conteos de chunks y fuentes
- flags como `needs_clarification` o `should_offer_incident`
- metadata de provider/modelo

`LangSmith` se usa para diagnosticar calidad:

- pregunta del usuario
- chunks recuperados con contenido
- prompt logico enviado al modelo
- decision estructurada del asistente
- respuesta final

Variables necesarias para LangSmith:

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<langsmith-api-key>
LANGSMITH_PROJECT=internal-assistant-mvp
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

Advertencia: cuando `LANGSMITH_TRACING=true`, LangSmith recibe prompts, chunks y respuestas. Activalo solo con datasets ficticios o con una politica clara de tratamiento de datos.

Consultas Kusto utiles en App Insights:

```kusto
exceptions
| where timestamp > ago(24h)
| where cloud_RoleName has "app" or outerMessage has "/api/chat"
| order by timestamp desc
```

```kusto
dependencies
| where timestamp > ago(24h)
| where name in ("api.chat", "chat.request", "chat.retrieve", "chat.generate_answer", "chat.response")
| summarize count(), avg(duration), percentiles(duration, 50, 95) by name
| order by name asc
```

```kusto
dependencies
| where timestamp > ago(24h)
| where name == "chat.response"
| summarize count() by tostring(customDimensions["needs_clarification"])
```

```kusto
dependencies
| where timestamp > ago(24h)
| where name in ("llm.chat_completion", "llm.embeddings")
| summarize errors=countif(success == false), calls=count() by name, tostring(customDimensions["llm.provider"]), tostring(customDimensions["llm.model"])
```

## Coste de cada evaluacion

Cada reporte (`answer_eval`, `retrieval_eval`, `rag_full_eval` y `ablation_eval`) incluye una seccion `cost` en el JSON y una seccion `## Cost` en el Markdown con el coste real de esa ejecucion.

Que cubre:

- chat LLM separado por proposito: `planner`, `answer` y `judge` (solo si `--use-llm-judge`), con tokens de entrada, entrada cacheada y salida
- embeddings de las consultas (`embedding`)
- reranker (`rerank`): search units facturadas por Cohere o peticiones al reranker local BGE

Como se calcula:

- los providers registran el `usage` que devuelve la API (`prompt_tokens`, `prompt_tokens_details.cached_tokens`, `completion_tokens`; en streaming se pide `stream_options.include_usage`). Cohere devuelve `meta.billed_units.search_units`; si falta se cuenta 1 por peticion
- el registro solo esta activo dentro de la evaluacion (`internal_assistant.observability.usage.metering`); en produccion es un no-op
- los precios estan en `evaluation/pricing.json` (USD por 1M tokens, USD por 1K search units para rerank) con fuente y fecha en `_meta`. Se puede usar otra tabla con `EVAL_PRICING_PATH`
- los tokens cacheados se cobran al precio de `cached_input_per_1m`; `mock`, `openai_compatible` y el reranker `http` local cuestan 0 pero se registran sus tokens/peticiones
- un modelo sin precio no rompe la evaluacion: su fila sale con `usd: null`, `complete: false` y una nota

El reporte muestra total, desglose por componente/proposito, media por turno y por escenario, y en `per_question_results` cada turno lleva `cost_usd`, `chat_input_tokens`, `chat_cached_input_tokens`, `chat_output_tokens`, `embedding_tokens` y `rerank_search_units`. El reporte completo anade `phases_usd` (retrieval, answer, adversarial, ablation).

No incluye costes de infraestructura (PostgreSQL, App Service) ni el indexado previo del corpus.

## Como evitar gastar tokens

- usa `mock` para smoke tests
- activa `LLMJudge` solo en evaluaciones manuales
- en provider local compatible, ajusta timeout y concurrencia con cuidado

## Limitaciones

- las metricas heuristicas no sustituyen una revision humana
- `MockProvider` valida el pipeline, pero no mide calidad semantica real
- un benchmark solo es comparable si el provider de consulta y el indice comparten el mismo espacio de embeddings
