# Infraestructura Azure

Este directorio contiene la infraestructura modular en Bicep para desplegar la demo cloud del MVP.

## Archivos

- `main.bicep`: orquesta recursos y wiring principal.
- `main.parameters.example.json`: ejemplo de parametros.
- `modules/app-service.bicep`: plan Linux y Web App FastAPI.
- `modules/functions.bicep`: plan Consumption Linux y las dos Function Apps.
- `modules/postgres.bicep`: PostgreSQL Flexible Server, base de datos y allowlist de `vector`.
- `modules/storage.bicep`: Storage Account para Functions.
- `modules/app-insights.bicep`: Application Insights workspace-based y Log Analytics.
- `modules/bot.bicep`: Azure Bot y canal de Teams.
- `modules/keyvault.bicep`: Key Vault `standard` con autorizacion RBAC, soft delete (7 dias) y asignaciones de rol `Key Vault Secrets User` para las identidades administradas y `Key Vault Secrets Officer` para quien despliega.

## Uso

El flujo manual previsto es:

```bash
az login
./scripts/deploy_infra.sh
./scripts/sync_keyvault_secrets.sh
./scripts/configure_app_settings.sh
./scripts/check_keyvault_refs.sh
./scripts/deploy_app_service.sh
./scripts/deploy_functions.sh
```

## Notas

- El Resource Group no se crea en `main.bicep`; lo crea `scripts/deploy_infra.sh` si no existe.
- PostgreSQL queda configurado para permitir `vector`, pero la extension sigue creandose desde migraciones o scripts SQL del proyecto.
- Key Vault (`useKeyVault=true`, por defecto): la Web App y las dos Function Apps tienen identidad administrada asignada por el sistema y las App Settings secretas (`DATABASE_URL`, `OPENAI_API_KEY`, `ADMIN_API_KEY`, `APP_SHARED_SECRET`, `MICROSOFT_APP_PASSWORD` y, si existen, `RERANKER_API_KEY` y `LANGSMITH_API_KEY`) son referencias `@Microsoft.KeyVault(VaultName=<kv>;SecretName=<nombre>)`. Los valores se suben con `scripts/sync_keyvault_secrets.sh`, nunca por parametros de Bicep.
- Nombres de secreto: variable en minusculas con guiones (`OPENAI_API_KEY` -> `openai-api-key`).
- `postgresAdminPassword` sigue siendo parametro `@secure()` porque lo necesita el servidor PostgreSQL; `deploy_infra.sh` lo pasa en un fichero de parametros temporal (0600) y no por linea de comandos.
- `APPLICATIONINSIGHTS_CONNECTION_STRING` y `AzureWebJobsStorage` siguen como App Settings normales generadas por Bicep.
- Con `useKeyVault=false` se mantiene el comportamiento anterior (secretos en claro en App Settings).
- El vault tiene soft delete sin purge protection: si borras el Resource Group y vuelves a desplegar con el mismo nombre, purga antes el vault (`az keyvault purge --name <kv>`) o define otro `KEY_VAULT_NAME`.
- `main.json` es el ARM compilado de `main.bicep` y no se usa en los scripts (despliegan `main.bicep`). Si lo necesitas actualizado: `az bicep build --file infra/main.bicep`.
