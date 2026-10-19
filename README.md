# Logiassist

![Python](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-pytest-green)
![Security](https://img.shields.io/badge/prompt%20injection-defensa%20en%20capas-critical)
![Status](https://img.shields.io/badge/status-MVP%20%2F%20Demo--ready-6f42c1)

Asistente conversacional interno para una empresa ficticia de logistica. Combina RAG hibrido sobre PostgreSQL + pgvector, reranking opcional (BGE local o Cohere), chat en streaming, registro de incidencias con reindexado, defensas en capas frente a prompt injection y un despliegue en Azure con los secretos en Key Vault. La meta no es solo que el asistente "responda", sino que pueda explicarse, evaluarse y mostrarse con criterio tecnico en una demo o portfolio.

## Que demuestra este proyecto

- diseno de un asistente interno con RAG sobre documentacion privada e incidencias
- separacion entre sistema fuente y indice de retrieval
- busqueda hibrida con PostgreSQL full-text + pgvector
- trazabilidad de fuentes y fragmentos usados
- flujo de aclaraciones cuando la evidencia no basta
- registro de incidencias nuevas y reindexado posterior
- planner previo que decide si consultar memoria conversacional, indice o pedir aclaracion
- reranking en segunda fase con un cross-encoder BGE local o Cohere Rerank
- respuestas en streaming (SSE) con fuentes al terminar
- defensa en capas frente a prompt injection directa e indirecta ([docs/security.md](docs/security.md))
- evaluacion tecnica del RAG con metricas, LLM judge y **coste real por ejecucion**
- trazabilidad con LangSmith y Application Insights
- despliegue en Azure con App Service, Functions, PostgreSQL y secretos en Key Vault
- integracion con Teams como custom app (opcional)

## Problema que resuelve

En una operacion interna es habitual que el conocimiento este repartido entre procedimientos, tickets resueltos y experiencia informal del equipo. Este proyecto simula un asistente que concentra ese conocimiento en un unico punto de consulta, pero manteniendo dos limites importantes: solo responde con evidencia recuperada y, cuando no la tiene, pide aclaracion o propone registrar una incidencia nueva.

## Que demuestra tecnicamente

- modelado de un flujo RAG con chunking, embeddings y retrieval hibrido
- persistencia y observabilidad basicas con PostgreSQL
- desacoplo entre sistema fuente de incidencias y capa de indexado
- evaluacion reproducible de retrieval, respuesta, citas y abstencion
- operacion en dos rutas equivalentes:
  - local-first para desarrollo y pruebas baratas
  - Azure + Teams para demo cloud

## Arquitectura resumida

- `app-service/`: FastAPI principal con `/api/chat`, `/api/chat/stream` (SSE), `/api/messages`, health checks, feedback, endpoints de consulta y la UI web en `/demo`.
- `functions/custom-incidents-api-function/`: sistema fuente simulado para incidencias.
- `functions/indexer-function/`: reindexado HTTP y mantenimiento de chunks/embeddings.
- `src/internal_assistant/`: dominio compartido, providers LLM, RAG, reranking, guardias de seguridad, cards, runtime y repositorios.
- `PostgreSQL + pgvector`: persistencia de incidents, documents, chunks, conversations, feedback y retrieval logs.
- `evaluation/`: datasets, judges, runners y reportes para medir calidad RAG.
- `teams-app/`: manifiesto y paquete de Teams para la demo cloud.

Mas detalle en [docs/architecture.md](docs/architecture.md).

## Stack tecnologico

- Python 3.12
- FastAPI
- Azure Functions Python
- PostgreSQL + pgvector
- SQLAlchemy + Alembic
- Pydantic
- pytest
- Docker Compose
- OpenAI API como provider cloud inicial
- provider local `openai_compatible` para LM Studio, Ollama o similar
- Cohere Rerank (Azure AI Foundry) o BGE reranker local, opcionales
- LangSmith y Application Insights para trazas
- Azure App Service, Azure Functions, Azure Key Vault, Azure Bot y PostgreSQL Flexible Server

## Funcionalidades principales

- responder preguntas internas con RAG sobre documentos e incidencias
- citar fuentes y mostrar fragmentos de apoyo
- pedir hasta dos aclaraciones cuando la consulta es ambigua o debil
- ofrecer registrar incidencia si no hay evidencia suficiente
- registrar incidencias nuevas contra un sistema fuente simulado
- reindexar conocimiento nuevo para consultas posteriores
- guardar historial conversacional, feedback y retrieval logs
- evaluar retrieval, respuestas, citas, abstencion y casos adversariales

## Flujo funcional del asistente

1. El usuario pregunta desde `/api/chat` o Teams.
2. El servicio genera embeddings de la consulta y ejecuta retrieval hibrido.
3. El modelo responde usando solo el contexto recuperado.
4. La respuesta se presenta con estructura corta y fuentes consultadas.
5. Si la evidencia no basta, el asistente pide aclaracion.
6. Tras dos aclaraciones fallidas, propone registrar una incidencia no resuelta.
7. Cuando se crea un ticket, el sistema fuente lo guarda y el indexer lo incorpora al indice.

## Como lo pruebo yo ahora

### Ruta local

```bash
python -m uv sync
docker compose up -d postgres
python -m uv run python scripts/init_db.py
python -m uv run python scripts/seed_db.py
python -m uv run python scripts/rebuild_index.py
./scripts/demo_prep.sh local
./scripts/demo_health_check.sh local
python -m uv run uvicorn main:app --app-dir app-service --host 0.0.0.0 --port 8000
```

### Ruta Azure / Teams

```bash
./scripts/deploy_infra.sh
./scripts/configure_app_settings.sh
./scripts/deploy_app_service.sh
./scripts/deploy_functions.sh
./scripts/seed_cloud_db.sh
./scripts/rebuild_cloud_index.sh
./scripts/check_cloud_index.sh
./scripts/demo_prep.sh cloud
./scripts/demo_health_check.sh cloud
./scripts/package_teams_app.sh
```

## Como ejecutarlo en local

1. Copia [`.env.example`](.env.example) a `.env`.
2. Elige uno de estos modos:
   - `mock` para smoke tests baratos
   - `openai` para OpenAI real
   - `openai_compatible` para LM Studio, Ollama o un endpoint OpenAI-compatible
3. Instala dependencias:

```bash
python -m uv sync
```

4. Levanta PostgreSQL:

```bash
docker compose up -d postgres
```

5. Inicializa datos e indice:

```bash
python -m uv run python scripts/init_db.py
python -m uv run python scripts/validate_seed_data.py
python -m uv run python scripts/seed_db.py
python -m uv run python scripts/rebuild_index.py
python -m uv run python scripts/check_index.py
```

Guia ampliada en [docs/deployment-local.md](docs/deployment-local.md).

## Como poblar datos y reconstruir el indice

- validar dataset:

```bash
python -m uv run python scripts/validate_seed_data.py
```

- cargar datos raw:

```bash
python -m uv run python scripts/seed_db.py
```

- reconstruir indice:

```bash
python -m uv run python scripts/rebuild_index.py
```

- verificar indice:

```bash
python -m uv run python scripts/check_index.py
```

## Como probarlo sin Teams

Arranca FastAPI en local:

```bash
python -m uv run uvicorn main:app --app-dir app-service --host 0.0.0.0 --port 8000
```

Abre la consola web:

```text
http://localhost:8000/demo
```

Desde esa pantalla puedes comprobar `health`, hacer preguntas, ver fuentes, enviar feedback y cambiar la `API base` si quieres apuntar a una Web App de Azure. Para probar cloud sin Teams, abre:

```text
https://<webapp>.azurewebsites.net/demo
```

Consulta de ejemplo:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u-demo",
    "message": "Como solicito acceso temporal a SafeGate para personal externo?"
  }'
```

## Como probarlo en Teams

1. Configura `MICROSOFT_APP_ID`, `MICROSOFT_APP_PASSWORD`, `MICROSOFT_APP_TENANT_ID`, `TEAMS_APP_ID` y `BOT_ENDPOINT`.
2. Genera el paquete:

```bash
./scripts/package_teams_app.sh
```

3. Sube `teams-app/build/internal-assistant-demo.zip` como custom app en tu tenant.

Guia completa en [docs/teams-setup.md](docs/teams-setup.md).

## Como ejecutar evaluacion RAG

Smoke test con mock:

```bash
python -m uv run python scripts/run_rag_eval.py --provider mock
```

Evaluacion con OpenAI y judge LLM:

```bash
python -m uv run python scripts/run_rag_eval.py --provider openai --include-adversarial --use-llm-judge
```

Comparacion de configuraciones de retrieval:

```bash
python -m uv run python scripts/compare_retrieval_configs.py
```

Cada reporte incluye una seccion `Cost` con el coste **real** de esa ejecucion. Se calcula a partir del `usage` que devuelven las APIs: tokens de entrada (incluidos los cacheados), tokens de salida, tokens de embeddings y *search units* del reranker. Se desglosa por componente y proposito (planner, respuesta, judge, embeddings, rerank) y se multiplica por la tabla versionada [evaluation/pricing.json](evaluation/pricing.json). Los providers locales registran tokens con coste 0.

Los reportes quedan en `evaluation/reports/` como archivos JSON y Markdown. No se envian a Table Storage ni a otro artifact remoto por defecto.

Mas detalle en [docs/rag-evaluation.md](docs/rag-evaluation.md).

## Trazabilidad RAG

La app puede enviar trazas a dos sistemas con objetivos distintos:

- `Application Insights`: operacion, errores, latencias y metadata segura del flujo `/api/chat`.
- `LangSmith`: depuracion de calidad RAG con pregunta, chunks recuperados, prompt logico y respuesta final.

Para activar LangSmith:

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<langsmith-api-key>
LANGSMITH_PROJECT=internal-assistant-mvp
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

No hay una variable para ocultar contenido en LangSmith: si esta activo, recibe prompts, contexto recuperado y respuestas para poder diagnosticar calidad. App Insights no recibe ese contenido completo.

## Reranker opcional

El retrieval puede reordenar los candidatos con un cross-encoder como segunda fase. Por defecto esta desactivado. Hay dos opciones:

- `RERANKER_PROVIDER=http`: BGE cross-encoder local servido por `scripts/serve_reranker.py` (coste 0).
- `RERANKER_PROVIDER=cohere_azure`: Cohere Rerank v3.5 desplegado en Azure AI Foundry (se factura por *search unit*).

### BGE local

```bash
python -m uv run --extra reranker python scripts/serve_reranker.py
```

Config local:

```env
RERANKER_ENABLED=true
RERANKER_BASE_URL=http://127.0.0.1:8091
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_CANDIDATES=30
```

### Cohere en Azure

```env
RERANKER_ENABLED=true
RERANKER_PROVIDER=cohere_azure
RERANKER_BASE_URL=https://<endpoint>.<region>.models.ai.azure.com
RERANKER_API_KEY=<api-key>
RERANKER_MODEL=Cohere-rerank-v3.5
RERANKER_CANDIDATES=30
```

Despues de aplicar la migracion de metadata de chunks, reconstruye el indice:

```bash
python -m uv run python scripts/init_db.py
python -m uv run python scripts/rebuild_index.py
```

## Como desplegarlo en Azure

1. Copia [`.env.azure.example`](.env.azure.example) a `.env.azure`.
2. Rellena secretos, nombre del proyecto y credenciales del bot.
3. Ejecuta:

```bash
./scripts/azure_login_check.sh
./scripts/deploy_infra.sh
./scripts/sync_keyvault_secrets.sh --dry-run   # lista solo nombres
./scripts/sync_keyvault_secrets.sh
./scripts/configure_app_settings.sh
./scripts/check_keyvault_refs.sh
./scripts/deploy_app_service.sh
./scripts/deploy_functions.sh
./scripts/seed_cloud_db.sh
./scripts/rebuild_cloud_index.sh
./scripts/check_cloud_index.sh
./scripts/smoke_test_cloud.sh
```

### Secretos en Key Vault

Los secretos (`OPENAI_API_KEY`, `DATABASE_URL`, `APP_SHARED_SECRET`, `RERANKER_API_KEY`, `LANGSMITH_API_KEY`...) no se guardan como App Settings en claro:

- `deploy_infra.sh` crea un Key Vault con autorizacion RBAC y activa una identidad administrada en la Web App y en las dos Function Apps, con el rol `Key Vault Secrets User`.
- `sync_keyvault_secrets.sh` lee `.env.azure` y sube cada secreto con `az keyvault secret set --file`, a traves de un fichero temporal `0600` que se borra al terminar. El valor nunca aparece en argv (`ps`), en stdout ni en el historial. El script es idempotente.
- `configure_app_settings.sh` escribe referencias `@Microsoft.KeyVault(VaultName=...;SecretName=...)` en lugar de valores.
- `check_keyvault_refs.sh` comprueba que cada referencia se resuelve y muestra solo nombre y estado.

Para rotar un secreto: cambia `.env.azure` y ejecuta `sync_keyvault_secrets.sh` y despues `check_keyvault_refs.sh --restart`. Para volver al modo anterior: `USE_KEY_VAULT=false`.

Guia ampliada en [docs/deployment-azure.md](docs/deployment-azure.md).

## Estrategia de costes

- desarrollar principalmente en local
- usar cloud para demo, validacion final y Teams
- parar PostgreSQL Flexible Server despues de la demo
- mantener SKUs pequenos
- no activar Azure OpenAI ni extras enterprise en esta fase

Detalle en [docs/cost-control.md](docs/cost-control.md).

## Seguridad frente a prompt injection

Todo texto que no sea el system prompt se trata como dato no confiable: el mensaje del usuario, los chunks, las incidencias registradas por usuarios y la memoria. Las defensas se aplican en capas:

- guardia de entrada que rechaza sin llamar al LLM los intentos de anular instrucciones, cambiar de rol, sacar el prompt o secretos, saltarse controles o inventar contenido. Tambien normaliza el Unicode invisible
- cuarentena de chunks y memorias que contienen instrucciones dirigidas al modelo (inyeccion indirecta)
- spotlighting: los datos van entre delimitadores con un nonce aleatorio que no se puede cerrar desde dentro
- jerarquia de instrucciones explicita y canary token en el system prompt
- guardia de salida que bloquea fugas del prompt o de secretos y elimina imagenes o enlaces externos (exfiltracion). Tambien vigila el streaming
- minimo privilegio: el LLM no tiene herramientas, y las incidencias solo se crean con confirmacion explicita
- aislamiento de conversaciones entre usuarios

Detalle, observabilidad y limitaciones en [docs/security.md](docs/security.md).

## Limitaciones reales del MVP

- no hay SSO ni RBAC real: `user_id` lo envia el cliente
- la deteccion de inyeccion por patrones se puede esquivar con parafrasis creativas; las demas capas no dependen de ella
- la calidad RAG depende del corpus sintetico y del provider de embeddings
- Teams requiere tenant con custom apps o sideloading habilitado
- el flujo prioriza claridad tecnica y demo por encima de hardening enterprise

## Proximos pasos posibles

- SSO con Entra ID y permisos por departamento sobre el retrieval
- clasificador dedicado de prompt injection (por ejemplo Prompt Shields) como capa adicional
- ampliar dataset y cobertura funcional

## Estructura del repositorio

```text
logiassist/
+-- app-service/
+-- functions/
+-- src/internal_assistant/
+-- evaluation/
+-- docs/
+-- scripts/
+-- teams-app/
+-- infra/
+-- data/
`-- tests/
```

## Documentacion clave

- [docs/security.md](docs/security.md)
- [docs/portfolio-summary.md](docs/portfolio-summary.md)
- [docs/tradeoffs-and-decisions.md](docs/tradeoffs-and-decisions.md)
- [docs/demo-script.md](docs/demo-script.md)
- [docs/demo-checklist.md](docs/demo-checklist.md)
- [docs/faq.md](docs/faq.md)
- [docs/project-card.md](docs/project-card.md)

## Licencia / aviso de uso

Proyecto orientado a portfolio tecnico y demo. Todo el dataset es ficticio y se incluye con fines demostrativos. Antes de publicar o reutilizar el codigo en un entorno real conviene revisar seguridad, gobierno de secretos, observabilidad y limites de coste.
