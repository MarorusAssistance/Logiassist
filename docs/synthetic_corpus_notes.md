# Synthetic Corpus Notes

Este corpus es completamente ficticio y está pensado para una demo técnica de portfolio de un asistente interno de operaciones, logística y supply chain.

## Principios

- Los documentos e incidencias están escritos en español y son originales.
- Los sistemas internos son ficticios: LogiCore ERP, AlmaTrack WMS, RutaNexo TMS, HelpOps, DocuFlow, OnboardHub, SafeGate, QualiTrace QMS, ScanBridge IDP y OpsLake.
- No se han usado nombres de plataformas SaaS reales como sistemas internos.
- No hay URLs externas, emails, personas reales, empresas reales ni credenciales en los seed files.
- El contenido está inspirado en procesos comunes de almacén, transporte, calidad, seguridad, soporte y analítica operativa, pero no copia documentación externa.

## Volumen

- `data/seed_documents.json`: 20 documentos internos.
- `data/seed_tickets.json`: 100 incidencias.
- Distribución de incidencias: 90 resueltas y 10 abiertas.
- Todos los sistemas ficticios aparecen en documentos o tickets, y todos aparecen varias veces en tickets.

## Relación semántica

Los documentos describen procedimientos, guías, políticas, checklists y FAQs internas. Las incidencias reutilizan la misma terminología de sistemas, procesos, síntomas, evidencias y controles para que el retrieval pueda recuperar:

- procedimientos documentados;
- incidencias resueltas como conocimiento reutilizable;
- incidencias abiertas cuando no existe solución validada;
- casos donde el asistente debe pedir aclaración antes de responder.

## Flujo recomendado

Validar y cargar datos raw:

```bash
python scripts/validate_seed_data.py
python scripts/seed_db.py
```

Reconstruir el índice solo cuando el proveedor de embeddings configurado sea el deseado para el entorno:

```bash
python scripts/rebuild_index.py
python scripts/check_index.py
```

Si `EMBEDDINGS_PROVIDER=openai`, `rebuild_index.py` puede llamar a la API externa configurada. Para pruebas sin coste, usa un entorno local/mock compatible con la dimensión de embeddings esperada por la base de datos.

## Regeneración

El script reproducible para reescribir los seed files es:

```bash
python scripts/generate_seed_data.py
python scripts/validate_seed_data.py
```

El esquema de base de datos, modelos SQLAlchemy, migraciones, contratos API y arquitectura RAG no dependen de cambios estructurales para soportar este corpus.
