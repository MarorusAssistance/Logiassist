# Seguridad frente a prompt injection

El asistente trata **todo texto que no sea su propio system prompt como dato no confiable**: el mensaje del usuario, los fragmentos del indice, las incidencias registradas por usuarios, la memoria conversacional y los mensajes previos. Ninguna defensa aislada es suficiente frente a prompt injection (OWASP LLM01:2025), asi que se aplican varias capas independientes. Si una falla, la siguiente sigue actuando.

Codigo principal: [src/internal_assistant/security/prompt_guard.py](../src/internal_assistant/security/prompt_guard.py). Integracion: [src/internal_assistant/chat/service.py](../src/internal_assistant/chat/service.py) y [src/internal_assistant/llm/common.py](../src/internal_assistant/llm/common.py).

## Capas

| # | Capa | Que hace | Contra que protege |
|---|------|----------|--------------------|
| 1 | Validacion de peticion | `ChatRequest` limita longitudes (mensaje <= 2000 caracteres) y normaliza el texto: NFKC y eliminacion de zero-width, bidi y *tag characters* | Instrucciones ocultas con Unicode invisible (ASCII smuggling), payloads gigantes |
| 2 | Guardia de entrada | Patrones (sin acentos, ES/EN) para: anular instrucciones, cambio de rol, fuga del prompt, peticion de secretos, evasion de controles, peticion de inventar contenido, delimitadores de chat falsos y exfiltracion. Si el riesgo es alto, se responde con un rechazo **sin llamar al LLM** | Inyeccion directa. Ademas ahorra coste y latencia |
| 3 | Cuarentena de contexto | Cada chunk recuperado y cada memoria se analiza antes de entrar al prompt. Si contiene instrucciones dirigidas al modelo, se descarta y no aparece como fuente | Inyeccion indirecta: una incidencia registrada con texto malicioso que luego se indexa |
| 4 | Spotlighting | Pregunta, chunks, memoria y mensajes previos van entre `<DATOS_NO_CONFIABLES id=<nonce>>`, con un nonce aleatorio por peticion. Cualquier aparicion de la etiqueta dentro del contenido se neutraliza para que no se pueda cerrar el bloque desde dentro | El modelo confunde datos con instrucciones |
| 5 | Jerarquia de instrucciones | El system prompt (respuesta y planner) declara que solo el es fuente de instrucciones y que los bloques de datos nunca lo son | Overrides tipo "a partir de ahora..." |
| 6 | Canary token | El system prompt contiene un identificador secreto aleatorio por proceso. Si aparece en la salida, la respuesta se bloquea | Fuga del system prompt |
| 7 | Guardia de salida | Bloquea respuestas con el canary, fragmentos del prompt o secretos con formato reconocible (claves `sk-`, cadenas de conexion con password, claves privadas, `AccountKey=`...). Elimina imagenes markdown y enlaces externos. Filtra `used_chunk_ids` a los chunks realmente enviados | Fuga de datos, exfiltracion por URL, citas inventadas |
| 8 | Guardia en streaming | Deja de emitir tokens en cuanto el texto acumulado contiene una fuga. El evento `final` sustituye el texto por la version validada | Fugas parciales antes de validar la respuesta completa |
| 9 | Minimo privilegio | El LLM no tiene herramientas: no puede crear incidencias, llamar APIs ni tocar la base de datos. Registrar una incidencia exige confirmacion explicita del usuario y pasa por una API protegida con secreto compartido | Acciones no deseadas aunque la inyeccion tenga exito |
| 10 | Aislamiento de conversaciones | Un `conversation_id` de otro `user_id` no reutiliza esa conversacion: se crea una nueva | Lectura del historial y la memoria de otro usuario (IDOR) |
| 11 | Memoria limpia | Los turnos bloqueados no se indexan en la memoria conversacional | Reinyeccion del ataque en turnos posteriores |
| 12 | Render seguro | La UI pinta las respuestas con `textContent`, nunca con `innerHTML` | XSS a traves de la respuesta del modelo |

## Observabilidad

- Span `chat.request`: `security.input_risk` y `security.input_categories`.
- Span `chat.retrieve`: `security.quarantined_chunk_ids`.
- `_meta.security` en simulacion y evaluacion: chunks y memorias en cuarentena, hallazgos de salida y si la salida se bloqueo.
- Logs `WARNING` en cada bloqueo, sin el contenido del mensaje.

## Evaluacion

El dataset [evaluation/datasets/adversarial_questions.json](../evaluation/datasets/adversarial_questions.json) incluye inyeccion directa, Unicode invisible, delimitadores falsos, peticiones de secretos y del prompt, exfiltracion via markdown e invencion de contenido. La metrica es `Prompt injection rejection rate`:

```bash
python -m uv run python scripts/run_rag_eval.py --provider openai --include-adversarial --use-llm-judge
```

Los tests de [tests/unit/test_prompt_guard.py](../tests/unit/test_prompt_guard.py) comprueban ademas que no hay falsos positivos en las 63 preguntas legitimas del dataset principal ni en el corpus semilla.

## Limitaciones conocidas

- La deteccion por patrones se puede esquivar con parafrasis creativas, otros idiomas o codificaciones. Por eso las capas 3 a 9 no dependen de ella.
- Las preguntas fuera de dominio (por ejemplo, recetas o geopolitica) no se bloquean en la entrada: las gestionan la evidencia y el modelo.
- `user_id` lo envia el cliente y no esta autenticado. Sin SSO, el aislamiento de conversaciones solo frena el acceso accidental o casual, no a un atacante decidido.
- Un siguiente paso razonable seria anadir un clasificador dedicado (por ejemplo, Azure AI Content Safety Prompt Shields o un modelo de deteccion de inyeccion) como capa adicional detras del guardia de patrones.

## Referencias

- OWASP GenAI Security Project, *LLM01:2025 Prompt Injection*.
- Hines et al. (Microsoft), *Defending Against Indirect Prompt Injection Attacks With Spotlighting*, arXiv:2403.14720.
- OpenAI, *The Instruction Hierarchy*, arXiv:2404.13208.
