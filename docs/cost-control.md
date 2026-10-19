# Control de costes

## Recursos que generan coste

- Azure App Service Plan Linux
- Azure Functions
- Azure Database for PostgreSQL Flexible Server
- Storage Account
- Application Insights y Log Analytics si se dejan activos
- Azure Key Vault `standard` (coste despreciable, ver abajo)

## Estrategia recomendada para demo

1. Desarrollar casi todo en local.
2. Levantar cloud solo para validacion final o demo.
3. Arrancar PostgreSQL antes de la sesion.
4. Ejecutar health checks.
5. Hacer la demo.
6. Parar PostgreSQL al terminar.

## Comandos utiles

```bash
./scripts/start_postgres_azure.sh
./scripts/smoke_test_cloud.sh
./scripts/stop_postgres_azure.sh
```

Azure CLI equivalente:

```bash
az postgres flexible-server start --resource-group <rg> --name <server>
az postgres flexible-server stop --resource-group <rg> --name <server>
```

## Notas practicas

- parar PostgreSQL reduce compute, pero storage y backups pueden seguir generando coste
- Flexible Server puede permanecer parado hasta 7 dias antes de que Azure lo reactive
- si quieres coste cero total, borra el Resource Group
- en esta fase no compensa activar Azure OpenAI ni SKUs superiores
- Key Vault `standard` no tiene coste fijo: se paga por operacion (~0,03 USD por cada 10.000 operaciones de secretos). App Service cachea las referencias y las refresca cada 24 h o al reiniciar, asi que el coste de la demo es practicamente cero. Soft delete no anade coste; HSM/Premium no es necesario

## SKUs razonables para demo

- App Service Plan `B1`
- PostgreSQL `Standard_B1ms`
- Storage `Standard_LRS`
- Functions en Consumption `Y1 Dynamic`, preferiblemente en un Resource Group separado si el RG principal ya contiene App Service Linux

## Recomendaciones

- crea budget alerts
- evita alta disponibilidad y private endpoints en esta fase
- reconstruye el indice solo cuando cambie el corpus o registres nuevas incidencias
