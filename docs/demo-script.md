# Demo script

## Preparacion previa

### Demo local

```bash
./scripts/demo_prep.sh local
./scripts/demo_health_check.sh local
```

### Demo cloud

```bash
./scripts/demo_prep.sh cloud
./scripts/demo_health_check.sh cloud
```

Ten a mano:

- la consola o terminal con los health checks en verde
- una ventana con `/demo`, `/api/chat` o Teams
- un reporte RAG reciente en `evaluation/reports/`
- si es cloud, el paquete de Teams ya cargado

## Version corta (3 minutos)

1. Explica en una frase el problema:
   - documentacion y tickets internos dispersos
2. Abre `/demo`, pulsa `Comprobar` y haz una pregunta conocida con fuentes.
3. Haz una pregunta ambigua para forzar aclaracion.
4. Cierra con el flujo de incidencia no resuelta.
5. Ensena un reporte de evaluacion RAG y una frase sobre hit@k / citation coverage.

## Version extendida (5-7 minutos)

1. Muestra arquitectura resumida.
2. Haz dos preguntas de documentacion.
3. Haz dos preguntas de incidencias resueltas.
4. Fuerza una aclaracion.
5. Completa el registro de una incidencia no resuelta.
6. Marca una respuesta como `no util`.
7. Abre un reporte de evaluacion RAG.
8. Cierra con tradeoffs, costes y pasos siguientes.

## Preguntas preparadas

| # | Pregunta | Objetivo | Comportamiento esperado | Fuentes esperadas | Punto tecnico |
| --- | --- | --- | --- | --- | --- |
| 1 | Como se registra una entrega parcial en ventana critica en LogiCore ERP? | Abrir la demo con una respuesta clara y operativa | `answer_with_sources` | documentos de operaciones | retrieval hibrido y grounding |
| 2 | Como solicito acceso temporal a SafeGate para personal externo? | Mostrar cruce entre seguridad e incidencias | `answer_with_sources` | documento de seguridad + ticket relacionado | mezcla de documentos e incidents |
| 3 | Que pasos de onboarding debo completar en la primera semana? | Demostrar cobertura funcional fuera de operaciones | `answer_with_sources` | documento de onboarding | corpus multi area |
| 4 | Que politica aplica al uso de credenciales compartidas? | Mostrar una politica interna bien delimitada | `answer_with_sources` | documento de politicas | respuestas basadas en evidencia |
| 5 | Que hago si RutaNexo no sincroniza una ruta aprobada? | Enseniar un caso resuelto | `say_incident_resolved` | ticket resuelto + documento operativo | tickets como conocimiento reutilizable |
| 6 | Como se revisa un pedido bloqueado por validacion manual? | Mostrar un caso operativo con trazabilidad | `answer_with_sources` | documento + ticket | fuentes ordenadas y legibles |
| 7 | No puedo entrar | Forzar aclaracion | `ask_clarification` | ninguna al principio | abstencion temprana |
| 8 | El torno principal sigue rechazando el acceso y no aparece ningun caso parecido | Forzar el cierre en registro de incidencia | `abstain_and_offer_incident_registration` | ninguna suficiente | no inventar pasos |
| 9 | no util | Mostrar el loop de feedback | `feedback` | no aplica | trazabilidad de feedback |

## Flujo guiado para el caso no resuelto

1. Pregunta ambigua:
   - `No puedo entrar`
2. Segunda vuelta:
   - `Sigue fallando y no veo un patron claro`
3. Tercera vuelta:
   - `Quiero dejar constancia`
4. Completa el ticket:
   - `si`
   - `titulo: Acceso denegado en SafeGate`
   - `descripcion: El torno principal no deja pasar al turno de noche`
   - `departamento: Seguridad`
   - `categoria: accesos`
   - `sistema: SafeGate`
   - `impacto: bloquea la entrada del turno`
   - `esperado: deberia permitir el acceso`
   - `actual: muestra acceso denegado`
   - `si`

## Flujo de feedback

1. Haz una pregunta resuelta.
2. Envias `no util`.
3. Explicas que el sistema registra feedback sin romper la conversacion.

## Que pantallas conviene ensenar

- README principal
- consola web `/demo`
- conversacion resuelta con fuentes
- caso de aclaracion
- confirmacion de ticket creado
- reporte RAG en Markdown
- si aplica, Teams con la custom app abierta

## Cierre sugerido

- recordar que el corpus es ficticio pero la arquitectura es realista
- remarcar la separacion entre sistema fuente e indice
- mencionar que el proyecto se puede levantar localmente o desplegar en Azure
- cerrar con la evaluacion RAG como prueba de criterio tecnico y no solo de UX
