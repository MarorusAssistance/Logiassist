# Datasource Viability Audit

## Executive summary

Estado: **mostly compatible**.

El modelo actual de datos es suficiente para soportar el corpus sintético planificado de supply chain sin una refactorización estructural de base de datos. Las tablas `incidents`, `documents` y `chunks` ya permiten cargar documentación interna, tickets resueltos/no resueltos, generar embeddings pgvector, hacer full-text search y conservar atribución de fuentes.

No hay un hard blocker de schema. Los principales problemas están en:

- El **seed data actual** es demasiado templado y repetitivo para una demo pública.
- El corpus actual usa solo 6 sistemas y no cubre todavía `HelpOps`, `QualiTrace QMS`, `ScanBridge IDP`, `OpsLake` ni el nombre previsto `RutaNexo TMS`.
- Las listas permitidas de sistemas en validación/filtros están hardcodeadas para el set anterior.
- Hay `source_url` ficticias tipo `https://intranet.logistica.local/...`; no son URLs externas reales, pero si el requisito es "sin URLs en seed content", conviene sustituirlas por referencias internas legibles.
- No existe relación explícita `ticket -> documento`, pero para el MVP no es necesaria: la relación puede ser semántica mediante texto, tags, sistema, categoría y evaluación.

Conclusión: **no haría migraciones ahora**. El mínimo cambio viable es rehacer seed data, ampliar listas de sistemas permitidos y reconstruir el índice.

## Current schema overview

### Tables and SQLAlchemy models

| Table/model | Purpose | Main fields |
|---|---|---|
| `incidents` / `Incident` | Tickets/incidencias usadas como fuente y para registro de nuevos casos | `id`, `external_id`, `title`, `description`, `department`, `category`, `affected_system`, `priority`, `status`, `is_resolved`, `resolution`, `impact`, `expected_behavior`, `actual_behavior`, `tags`, `created_by`, `created_at`, `resolved_at`, `updated_at`, `source`, `source_url` |
| `documents` / `Document` | Documentación interna indexable | `id`, `title`, `document_type`, `department`, `affected_system`, `content`, `tags`, `source_url`, `created_at`, `updated_at` |
| `chunks` / `Chunk` | Índice RAG unificado para documentos e incidencias | `id`, `source_type`, `source_id`, `chunk_index`, `content`, `content_hash`, `embedding`, `full_text_tsvector`, `metadata`, `source_title`, `affected_system`, `department`, `document_type`, `incident_status`, `is_resolved`, `tags`, `created_at` |
| `conversations` / `Conversation` | Estado conversacional | `id`, `channel_id`, `teams_conversation_id`, `user_id`, `state`, `created_at`, `updated_at` |
| `messages` / `Message` | Mensajes de usuario/asistente | `id`, `conversation_id`, `role`, `content`, `intent`, `created_ticket_id`, `created_at` |
| `conversation_memories` / `ConversationMemory` | Memoria conversacional vectorial por mensaje | `id`, `conversation_id`, `message_id`, `role`, `memory_text`, `summary`, `metadata`, `embedding`, `created_at` |
| `feedback` / `Feedback` | Feedback útil/no útil | `id`, `conversation_id`, `message_id`, `user_id`, `feedback_type`, `comment`, `created_at` |
| `retrieval_logs` / `RetrievalLog` | Auditoría del retrieval y respuesta | `id`, `conversation_id`, `message_id`, `query`, `detected_intent`, `retrieved_chunk_ids`, `retrieved_source_ids`, `scores`, `confidence_score`, `was_answered`, `tokens_input_estimated`, `tokens_output_estimated`, `latency_ms`, `created_ticket_id`, `answer`, `created_at` |

### Alembic migrations

| Migration | Effect |
|---|---|
| `0001_initial_schema.py` | Crea tablas principales, `pgvector`, índice ivfflat en `chunks.embedding`, GIN para `full_text_tsvector` |
| `0002_conversation_memories.py` | Añade memoria conversacional vectorial con `conversation_memories.embedding` |
| `0003_chunk_metadata_filters.py` | Añade columnas normalizadas de metadata en `chunks` para filtros por sistema, departamento, tipo documental, estado de incidencia, resolución y tags |

### Current local DB snapshot

Read-only audit run observed:

| Item | Count/value |
|---|---:|
| `incidents` | 100 |
| `documents` | 20 |
| `chunks` | 200 |
| `conversations` | 22 |
| `messages` | 124 |
| `feedback` | 0 |
| `retrieval_logs` | 62 |
| `conversation_memories` | 80 |
| Chunk embedding dimensions | 1024 |
| pgvector enabled | `true` |

## Current incident/ticket fields

| Target field | Current support | Classification | Notes |
|---|---|---|---|
| `id` | Yes | Supported | Stable integer PK used by seed. |
| `external_id` | Yes | Supported | Unique and indexed. |
| `title` | Yes | Supported | Used in chunks and citations. |
| `description` | Yes | Supported | Main body for incident chunks. |
| `category` | Yes | Supported | Current categories are broad. |
| `subcategory` | No | Useful but optional | Can be represented by `tags` or richer `category` for now. Do not add column yet. |
| `priority` | Yes | Supported | Nullable. |
| `status` | Yes | Supported | Current values `open`/`resolved`. |
| `created_at` | Yes | Supported | Preserved by seed. |
| `resolved_at` | Yes | Supported | Null for unresolved tickets. |
| `updated_at` | Yes | Supported | Present in model and seed payload. |
| `department` | Yes | Supported | Indexed. |
| `affected_system` | Yes | Supported | Indexed. |
| `resolution` | Yes | Supported | Null for unresolved. |
| `tags` | Yes | Supported | PostgreSQL array. |
| `source` | Yes | Supported | Current seed uses `seed_dataset`. |
| `is_resolved` | Yes | Supported | Explicit boolean, not just derived. |
| `related_document_ids` | No | Useful but optional | Not needed for current RAG. Can be represented semantically in text/tags or evaluation expected sources. Add only if UI/reporting needs explicit links. |
| Source references/citations | Yes | Supported | `source_url`, `title`, `external_id`, chunk metadata. |

## Current document fields

| Target field | Current support | Classification | Notes |
|---|---|---|---|
| `id` | Yes | Supported | Stable integer PK used by seed. |
| `title` | Yes | Supported | Used in chunk metadata and citations. |
| `body/content` | Yes | Supported | `content`. |
| `document_type` | Yes | Supported | Current values `procedimiento`, `guía`, `política`. |
| `department` | Yes | Supported | Indexed. |
| `category` | No | Can be represented using existing field | Use `document_type`, `department`, `tags` first. No migration needed now. |
| `affected_system/system` | Yes | Supported | Nullable and indexed. |
| `source` | No | Useful but optional | Current model has `source_url` but not `source`. Add only if multiple document origins become relevant. |
| `version` | No | Useful but optional | Can be included in `content`, `tags`, or future citation label. No hard blocker. |
| `created_at` | Yes | Supported | Preserved by seed. |
| `updated_at` | Yes | Supported | Preserved by seed. |
| `tags` | Yes | Supported | PostgreSQL array. |
| `status` | No | Useful but optional | Could be represented by tags like `vigente`/`retirado`. Add only if lifecycle workflows are needed. |
| `owner` | No | Useful but optional | Can be part of content or tags. Not needed for RAG demo. |
| Citation metadata | Partial | Supported enough | `source_url`, `title`, `source_id`, chunk metadata. A separate citation label is optional. |

## Current chunk/indexing model

The chunk model supports the planned corpus well:

- `source_type`: yes, currently `document` or `incident`.
- `source_id`: yes.
- `chunk_index`: yes.
- `chunk_text`: yes, field name `content`.
- `embedding`: yes, pgvector with configurable `EMBEDDING_DIMENSIONS`.
- `fts/search text`: yes, `full_text_tsvector` populated with `to_tsvector('spanish', content)`.
- `metadata`: yes, JSONB.
- `title/source title`: yes, `source_title` and metadata `title`.
- `citation label/reference`: partially supported by `source_type`, `source_id`, `title`, `source_url`; no separate label column needed now.
- `created_at`: yes.
- `updated_at`: no, but not a blocker because rebuild deletes/recreates chunks and raw source rows have timestamps.

### Retrieval/indexing behavior

- Documents and incidents are indexed together into `chunks`.
- Rebuild deletes existing chunks and reindexes from raw `documents` and `incidents`.
- Embeddings are generated by configured provider; current local DB has dimension `1024`.
- pgvector cosine search is used through `embedding <=>`.
- Full-text search uses PostgreSQL Spanish text search.
- Hybrid retrieval combines vector and text scores with default `top_k=5`, `vector_weight=0.70`, `text_weight=0.30`, candidate pools of 15.
- Metadata filters are supported through normalized columns on `chunks`.
- Optional local reranker is supported after hybrid retrieval.
- Source attribution is preserved through chunk metadata and `ChatResponse.sources`.

Potential technical issue to keep in mind: `ChunkRepository.upsert()` uses `content_hash` globally. If two different sources produce identical chunk text, the existing chunk is updated instead of creating a second source-specific chunk. With the current corpus this has not blocked the demo, but with heavily templated data it can collapse sources unexpectedly. This is not an immediate migration blocker; the primary fix is reducing duplicated boilerplate in seed content.

## Current seed data overview

### Counts

| Type | Count |
|---|---:|
| Tickets/incidents | 100 |
| Resolved tickets | 90 |
| Unresolved tickets | 10 |
| Documents | 20 |

### Systems currently used

Tickets:

| System | Count |
|---|---:|
| SafeGate | 20 |
| LogiCore ERP | 20 |
| DocuFlow | 20 |
| AlmaTrack WMS | 19 |
| RutaNexo | 11 |
| OnboardHub | 10 |

Documents:

| System | Count |
|---|---:|
| SafeGate | 6 |
| LogiCore ERP | 4 |
| DocuFlow | 4 |
| RutaNexo | 2 |
| AlmaTrack WMS | 2 |
| OnboardHub | 2 |

### Departments and categories

Departments:

- Tickets: `Operaciones` 50, `Seguridad` 20, `Onboarding` 20, `Politicas internas` 10.
- Documents: `Operaciones` 8, `Seguridad` 6, `Onboarding` 4, `Politicas internas` 2.

Ticket categories:

- `documentacion` 20
- `rutas` 11
- `accesos` 10
- `pedidos` 10
- `onboarding` 10
- `permisos` 10
- `sincronizacion` 10
- `integracion` 10
- `almacen` 9

Document types:

- `procedimiento` 8
- `guía` 8
- `política` 4

### Quality observations

- Language: Spanish.
- Current systems are fictitious; no real SaaS/platform names were detected in seed content by the audit script.
- No emails were detected.
- No obvious credential/secret terms were detected.
- Seed JSON appears valid UTF-8 when read by Python.
- There are 120 `source_url` values pointing to `https://intranet.logistica.local/...`; these are fictitious internal URLs, but still URL strings.
- The phrase `flujo ficticio del entorno de demo` appears 100 times in ticket descriptions. This is safe but poor for demo quality because the assistant can surface that text in answers.
- Tickets are overly repetitive: many incidents are generated from repeated patterns with different IDs.
- Documents are usable structurally but contain repeated control boilerplate.

## Planned corpus compatibility

### Target systems vs current system support

Planned systems:

- `LogiCore ERP`
- `AlmaTrack WMS`
- `RutaNexo TMS`
- `HelpOps`
- `DocuFlow`
- `OnboardHub`
- `SafeGate`
- `QualiTrace QMS`
- `ScanBridge IDP`
- `OpsLake`

Current seed systems:

- `LogiCore ERP`
- `AlmaTrack WMS`
- `RutaNexo`
- `DocuFlow`
- `OnboardHub`
- `SafeGate`

Schema support: the DB stores systems as strings, so the planned systems are compatible without migration.

Code/validation support: `internal_assistant.seed_data.ALLOWED_SYSTEMS` and `internal_assistant.rag.filters.ALLOWED_SYSTEMS` currently only allow the old 6-system set. These constants must be updated when the seed changes. This is not a DB refactor.

### Target shape compatibility

| Requirement | Current compatibility |
|---|---|
| 10-20 internal documents | Compatible. Current has 20. |
| 50-100 incidents/tickets | Compatible. Current has 100. |
| Documents and tickets linked semantically | Compatible via text/tags/system/category. Explicit links are missing but not required. |
| Tickets answerable from documents | Compatible, but current corpus quality is weak. |
| Resolved previous incidents | Supported and present. |
| Unresolved incidents | Supported and present. |
| Spanish content | Supported and present. |
| Realistic supply-chain operations | Partially compatible; needs better seed content. |
| No real SaaS/platform names as internal systems | Current seed appears compliant. |
| No copied external text | No copied-looking vendor docs detected, but this cannot be proven automatically. |
| No external URLs | Current seed has fictitious intranet URLs; remove/null if strict. |

## Gaps

| Gap | Area | Severity | Recommendation | Requires DB migration |
|---|---|---|---|---|
| Planned systems not in allowed constants | Validation/retrieval filters | Medium | Update `ALLOWED_SYSTEMS` in seed validation and retrieval filters when the new corpus is introduced. | No |
| `RutaNexo` vs planned `RutaNexo TMS` naming mismatch | Seed data/taxonomy | Medium | Standardize to `RutaNexo TMS` or consciously keep `RutaNexo`; do not mix both. | No |
| Current seed only covers 6 of 10 planned systems | Seed data | Medium | Rewrite seed data to include `HelpOps`, `QualiTrace QMS`, `ScanBridge IDP`, `OpsLake`. | No |
| Repetitive ticket templates | Seed data | Medium | Rewrite incidents with varied causes, symptoms, resolutions and operational contexts. | No |
| Demo boilerplate appears in every ticket | Seed data | Medium | Remove phrases like `flujo ficticio del entorno de demo` from source content. | No |
| Fake intranet URLs in seed | Seed data/citations | Low/Medium | If strict no URLs, set `source_url=null` and use title/source id as citation. | No |
| No `related_document_ids` | Incident-document linking | Low | Keep semantic linking for now. Add explicit links only if future UI/eval requires them. | No, unless explicit relational queries become required |
| No document `version`, `owner`, `status` columns | Document lifecycle metadata | Low | Represent in content/tags for the portfolio demo. Add columns only for real lifecycle workflows. | No |
| Global `content_hash` uniqueness can collapse identical chunks | Indexing | Low/Medium | Prefer unique content in corpus. If this becomes observable, change upsert key to source tuple in a later targeted patch. | Maybe later |
| No `updated_at` on chunks | Indexing metadata | Low | Not needed while rebuild recreates chunks and source rows have timestamps. | No |
| Document create/update schemas are read-only | API surface | Low | Fine for seed-driven demo. Add admin document APIs only if required later. | No |

## Minimum viable change set

Smallest safe path to support the planned corpus:

1. Rewrite `data/seed_documents.json` and `data/seed_tickets.json` with original fictitious supply-chain content.
2. Use the planned 10-system taxonomy consistently.
3. Update allowed systems in:
   - `src/internal_assistant/seed_data.py`
   - `src/internal_assistant/rag/filters.py`
   - any tests expecting the old set.
4. Keep the existing `incidents`, `documents` and `chunks` schema.
5. Remove or null out fake `source_url` values if the strict requirement is no URLs in seed content.
6. Add richer tags/categories in seed data rather than new columns.
7. Ensure documents and tickets refer to each other semantically in original text.
8. Run existing validation, seed and rebuild flow:
   - `python scripts/validate_seed_data.py`
   - `python scripts/seed_db.py`
   - `python scripts/rebuild_index.py`
   - `python scripts/check_index.py`
9. Re-run RAG evaluation and manual smoke tests.

## Recommended no-change decisions

Do not refactor these yet:

- Do not split documents/incidents into separate vector indexes; unified `chunks` works.
- Do not add `related_document_ids` until a concrete UI/evaluation requirement needs explicit links.
- Do not add document `version`, `owner`, `status` columns for this portfolio iteration.
- Do not migrate from PostgreSQL/pgvector to Azure AI Search for this corpus update.
- Do not change embedding dimensions as part of the corpus rewrite.
- Do not change conversation/memory tables for datasource work.
- Do not redesign API schemas unless new seed fields must be exposed publicly.
- Do not introduce real vendor names or copied vendor documentation.

## Risks for public portfolio demo

Risk indicators found:

- **Demo boilerplate leakage:** ticket descriptions explicitly say they are part of a fictitious demo. The assistant has already surfaced this kind of text in answers. This should be removed from source content.
- **Over-templated incidents:** many tickets have similar structure and repeated operational phrasing. This hurts retrieval quality and makes answers look synthetic.
- **Fake internal URLs:** `https://intranet.logistica.local/...` is not a real external URL, but visible URLs can distract in public demos. Prefer citation labels if the demo should be clean.
- **Limited taxonomy coverage:** current corpus does not yet demonstrate QMS, IDP/OCR, analytics lake or ITSM/helpdesk concepts.
- **No evidence of real brands/secrets/PII in seed:** audit script did not detect real SaaS names, emails or obvious secret terms in seed content.

No legal conclusion is made here; these are technical/content risk indicators only.

## Compatibility with assistant behavior

| Behavior | Current support |
|---|---|
| Answer from documentation | Supported via `documents -> chunks -> retrieval`. |
| Answer from resolved incidents | Supported via `incidents.is_resolved`, `resolution`, chunk metadata and evidence gating. |
| Say an incident is unresolved | Supported via `status`, `is_resolved=false`, unresolved evidence rules. |
| Ask clarification when evidence is insufficient | Supported by evidence gating and LLM decision handling. |
| Propose incident registration | Supported by chat flow and custom incidents API path. |
| Cite sources | Supported by `SourceSnippet`, source metadata, cards and fallback text. |
| Show related tickets | Supported through related incidents from retrieved incident source IDs. |
| Log retrieval details | Supported by `retrieval_logs`. |
| Use conversation context | Supported by `conversation_memories`. |
| Filter/rerank retrieval | Supported by chunk metadata filters and optional local reranker. |

## Suggested next Codex tasks

1. Update seed data only: create a new original corpus with 10-20 documents and 50-100 incidents using the planned 10-system taxonomy.
2. Update allowed-system constants and validation tests to match the new taxonomy.
3. Remove demo boilerplate and fake intranet URLs from seed content unless intentionally used as citation placeholders.
4. Improve semantic links between tickets and documents through text, categories and tags; do not add `related_document_ids` yet.
5. Rebuild index and run RAG evaluation comparing old vs new corpus.
6. If duplicate-content chunk collisions appear after the corpus rewrite, audit `content_hash` upsert behavior as a separate targeted patch.

## Audit script

Added non-invasive script:

```bash
python scripts/audit_datasource_viability.py
```

Behavior:

- Reads seed files.
- Attempts a read-only DB summary if `DATABASE_URL` is configured.
- Does not mutate data.
- Does not call OpenAI or external APIs.
- Returns non-zero only for execution errors, not audit findings.
