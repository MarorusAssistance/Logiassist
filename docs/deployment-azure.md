# Despliegue Azure

## Objetivo

Publicar una demo reproducible en Azure con App Service, Azure Functions, PostgreSQL Flexible Server y Azure Bot, manteniendo costes contenidos.

## Prerrequisitos

- Azure CLI autenticado
- permisos sobre la suscripcion o el Resource Group objetivo
- Python 3.12 y `uv`
- app registration para el bot
- OpenAI API key valida
- rol `Owner` o `User Access Administrator` sobre el Resource Group (Bicep crea asignaciones de rol RBAC para Key Vault)

## Preparacion

1. Copia `.env.azure.example` a `.env.azure`.
2. Rellena como minimo:

```env
PROJECT_NAME=internal-assistant
ENVIRONMENT_NAME=demo
AZURE_LOCATION=westeurope
AZURE_RESOURCE_GROUP=rg-internal-assistant-demo
AZURE_FUNCTIONS_RESOURCE_GROUP=rg-internal-assistant-demo-functions
AZURE_SUBSCRIPTION_ID=<subscription-id>
POSTGRES_ADMIN_USER=pgadmininternal
POSTGRES_ADMIN_PASSWORD=<password>
POSTGRES_DATABASE_NAME=assistant
OPENAI_API_KEY=<openai-key>
CHAT_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=512
ADMIN_API_KEY=<admin-key>
APP_SHARED_SECRET=<shared-secret>
MICROSOFT_APP_ID=<bot-app-id>
MICROSOFT_APP_PASSWORD=<bot-secret>
MICROSOFT_APP_TENANT_ID=<entra-tenant-id>
TEAMS_APP_ID=<teams-app-id>
USE_KEY_VAULT=true
```

Con `USE_KEY_VAULT=true` (por defecto) los secretos de `.env.azure` se suben a Azure Key Vault y las App Settings solo contienen referencias. Mira [Secretos en Key Vault](#secretos-en-key-vault).

`AZURE_FUNCTIONS_RESOURCE_GROUP` separa las Azure Functions Linux Consumption del App Service Linux. Azure puede bloquear workers Linux dinámicos en un Resource Group que ya contiene workers Linux dedicados, así que esta separación mantiene las Functions en `Y1 Dynamic` sin afectar a la Web App.

Opcional para trazabilidad de calidad RAG:

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<langsmith-api-key>
LANGSMITH_PROJECT=internal-assistant-mvp
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

`Application Insights` no usa API key en la app. La Web App recibe `APPLICATIONINSIGHTS_CONNECTION_STRING` desde la infraestructura y emite spans operativos sin prompts ni chunks completos.

## Despliegue manual

```bash
./scripts/azure_login_check.sh
./scripts/deploy_infra.sh
./scripts/sync_keyvault_secrets.sh
./scripts/configure_app_settings.sh
./scripts/check_keyvault_refs.sh
./scripts/deploy_app_service.sh
./scripts/deploy_functions.sh
./scripts/seed_cloud_db.sh
./scripts/rebuild_cloud_index.sh
./scripts/check_cloud_index.sh
./scripts/smoke_test_cloud.sh
./scripts/demo_prep.sh cloud
./scripts/demo_health_check.sh cloud
```

El orden importa: `deploy_infra.sh` crea el Key Vault, las identidades y los roles; `sync_keyvault_secrets.sh` sube los secretos; `configure_app_settings.sh` escribe las referencias (y reinicia las apps, lo que fuerza la resolucion); `check_keyvault_refs.sh` confirma que todas quedan en `Resolved`.

Si activas LangSmith despues de desplegar infraestructura, vuelve a ejecutar:

```bash
./scripts/sync_keyvault_secrets.sh
./scripts/configure_app_settings.sh
./scripts/check_keyvault_refs.sh
./scripts/deploy_app_service.sh
```

## Secretos en Key Vault

`infra/modules/keyvault.bicep` crea un Key Vault `standard` con autorizacion RBAC (`enableRbacAuthorization: true`) y soft delete de 7 dias. La Web App y las dos Function Apps tienen identidad administrada asignada por el sistema con el rol `Key Vault Secrets User` sobre el vault. Quien ejecuta `deploy_infra.sh` recibe `Key Vault Secrets Officer` (object id obtenido con `az ad signed-in-user show`, o `KEY_VAULT_ADMIN_PRINCIPAL_ID` si despliegas con un service principal).

| Variable en `.env.azure` | Secreto en Key Vault | App Setting |
|---|---|---|
| `DATABASE_URL` (construida desde `POSTGRES_*`) | `database-url` | referencia |
| `OPENAI_API_KEY` | `openai-api-key` | referencia |
| `ADMIN_API_KEY` | `admin-api-key` | referencia |
| `APP_SHARED_SECRET` | `app-shared-secret` | referencia |
| `MICROSOFT_APP_PASSWORD` | `microsoft-app-password` | referencia (solo Web App) |
| `RERANKER_API_KEY` | `reranker-api-key` | referencia si existe, si no vacio |
| `LANGSMITH_API_KEY` | `langsmith-api-key` | referencia si existe, si no vacio (solo Web App) |
| `POSTGRES_ADMIN_PASSWORD` | `postgres-admin-password` | no se expone |

Formato de la referencia: `@Microsoft.KeyVault(VaultName=<kv>;SecretName=<nombre>)`.

`sync_keyvault_secrets.sh` no expone los valores:

- no los pasa nunca como argumento (`--value`), que seria visible con `ps`: los escribe con `printf` (builtin) en un fichero temporal `0600` dentro de un directorio `0700` en `.artifacts/` y usa `az keyvault secret set --file ... --encoding utf-8 -o none`;
- borra el fichero temporal al salir (incluido Ctrl+C);
- solo imprime nombres de variables y secretos; los vacios o placeholders (`change-this-*`, `<...>`) se omiten con un aviso;
- es idempotente: si el valor no cambia no crea una nueva version (`--force` para reescribir);
- `--dry-run` lista nombres y accion prevista sin llamar a Azure.

`check_keyvault_refs.sh` consulta `config/configreferences/appsettings` de cada app y muestra solo nombre de setting, secreto y estado (`Resolved`, `SecretNotFound`, `AccessToKeyVaultDenied`...). Con `--restart` reinicia las apps antes de comprobar. Tras asignar un rol nuevo, la propagacion RBAC puede tardar unos minutos.

`deploy_infra.sh` ya no pasa secretos por linea de comandos: `postgresAdminPassword` (lo necesita el servidor PostgreSQL) va en un fichero de parametros temporal `0600`.

Los scripts que leen `DATABASE_URL` u `OPENAI_API_KEY` de la Web App (`seed_cloud_db.sh`, `rebuild_cloud_index.sh`, `check_cloud_index.sh`, `demo_prep.sh cloud`) resuelven la referencia contra Key Vault con tu usuario, que necesita el rol `Key Vault Secrets Officer` o `Key Vault Secrets User`.

Rotar un secreto: cambia el valor en `.env.azure`, ejecuta `./scripts/sync_keyvault_secrets.sh` y despues `./scripts/check_keyvault_refs.sh --restart` (App Service refresca las referencias sin version cada 24 h o al reiniciar).

Para volver al modelo anterior (secretos en claro en App Settings) define `USE_KEY_VAULT=false` y repite `deploy_infra.sh` y `configure_app_settings.sh`.

Si borras el Resource Group y lo recreas con el mismo nombre, el vault anterior sigue en soft delete 7 dias y bloquea el nombre: purgalo con `az keyvault purge --name <kv>` o define otro `KEY_VAULT_NAME`.

Luego lanza una pregunta desde `/demo` o `/api/chat` y busca un trace `rag.chat` en el proyecto LangSmith configurado.

## Health checks esperados

- `GET /api/health`
- `GET /api/health/deep`
- `GET https://<indexer>.azurewebsites.net/api/health`
- `GET https://<incidents>.azurewebsites.net/api/health`

`/api/health/deep` debe confirmar:

- conectividad con PostgreSQL
- provider configurado
- presencia de chunks
- extension `vector`

## Migraciones y pgvector

Si necesitas ejecutar migraciones manuales contra la base cloud:

```bash
python -m uv run alembic upgrade head
```

El servidor Flexible Server queda preparado para `pgvector`, pero conviene verificarlo siempre con `scripts/check_cloud_index.sh`.

## Prueba funcional

Sin Teams, puedes validar la Web App desde la UI integrada:

```text
https://<webapp>.azurewebsites.net/demo
```

La consola usa la misma API publicada bajo `/api`, muestra fuentes y permite feedback. Es la ruta recomendada para probar cloud cuando no tienes un tenant de Teams preparado.

Tambien puedes llamar la API directamente:

```bash
curl -X POST "https://<webapp>.azurewebsites.net/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u-demo",
    "message": "Como se registra una entrega parcial en ventana critica en LogiCore ERP?"
  }'
```

## Cierre de costes tras la demo

```bash
./scripts/stop_postgres_azure.sh
```

Si quieres coste casi cero, la opcion mas segura es borrar el Resource Group completo cuando acabes.
