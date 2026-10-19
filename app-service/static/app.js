const starterActions = [
  {
    title: "Consultar procedimiento",
    detail: "Documentación operativa con fuentes",
    question: "Cómo cierro una expedición pendiente en AlmaTrack WMS?",
  },
  {
    title: "Revisar incidencia conocida",
    detail: "Caso resuelto o abierto",
    question: "Cómo se resolvió la ruta congelada tras parada prioritaria en RutaNexo TMS?",
  },
  {
    title: "Registrar caso no resuelto",
    detail: "Aclaración y trazabilidad",
    question: "No encuentro solución para un error nuevo en AlmaTrack WMS.",
  },
];

const systems = [
  {
    name: "LogiCore ERP",
    area: "Pedidos, compras, stock, proveedores y facturación.",
    example: "Cómo se libera un pedido retenido por validación manual en LogiCore ERP?",
  },
  {
    name: "AlmaTrack WMS",
    area: "Almacén, ubicaciones, picking, packing y movimientos de stock.",
    example: "Cómo cierro una expedición pendiente en AlmaTrack WMS?",
  },
  {
    name: "RutaNexo TMS",
    area: "Transporte, rutas, transportistas, entregas y POD/CMR.",
    example: "Qué reviso si RutaNexo TMS no publica una nueva secuencia válida?",
  },
  {
    name: "HelpOps",
    area: "Tickets internos, escalado y coordinación de soporte.",
    example: "Cómo se escala una incidencia crítica en HelpOps?",
  },
  {
    name: "DocuFlow",
    area: "Procedimientos, políticas, versiones y fuentes internas.",
    example: "Debe el asistente presentar un caso parecido como solución definitiva?",
  },
  {
    name: "OnboardHub",
    area: "Onboarding, formación y alta de personal operativo.",
    example: "Qué pasos debe completar un coordinador nuevo en OnboardHub?",
  },
  {
    name: "SafeGate",
    area: "Accesos, seguridad física, permisos y credenciales temporales.",
    example: "Cómo se gestiona un permiso temporal en SafeGate?",
  },
  {
    name: "QualiTrace QMS",
    area: "Calidad, auditorías, no conformidades y lotes bloqueados.",
    example: "Cómo se trata una no conformidad en QualiTrace QMS sin dictamen final?",
  },
  {
    name: "ScanBridge IDP",
    area: "OCR, extracción documental y validación de documentos.",
    example: "Qué hago si ScanBridge IDP marca un CMR como ilegible?",
  },
  {
    name: "OpsLake",
    area: "KPIs operativos, datos consolidados y cuadros de seguimiento.",
    example: "Cómo se revisa una diferencia de KPI en OpsLake?",
  },
];

const sampleGroups = [
  {
    title: "Documentación operativa",
    description: "Procedimientos de almacén, pedidos y transporte.",
    questions: [
      "Cómo cierro una expedición pendiente en AlmaTrack WMS?",
      "Cómo se libera un pedido retenido por validación manual en LogiCore ERP?",
      "Qué reviso si RutaNexo TMS no publica una nueva secuencia válida?",
    ],
  },
  {
    title: "Seguridad y accesos",
    description: "Permisos temporales, credenciales y accesos físicos.",
    questions: [
      "Cómo se gestiona un permiso temporal en SafeGate?",
      "Cuándo hay que revocar una credencial temporal en SafeGate?",
    ],
  },
  {
    title: "Onboarding",
    description: "Primeros pasos de coordinadores y personal operativo.",
    questions: [
      "Qué pasos debe completar un coordinador nuevo en OnboardHub?",
      "Soy nuevo en operaciones. Qué tareas de iniciación debo completar esta semana?",
    ],
  },
  {
    title: "Calidad, documentos y KPIs",
    description: "Calidad, OCR, políticas internas y datos consolidados.",
    questions: [
      "Cómo se trata una no conformidad en QualiTrace QMS sin dictamen final?",
      "Qué hago si ScanBridge IDP marca un CMR como ilegible?",
      "Cómo se revisa una diferencia de KPI en OpsLake?",
      "Debe el asistente presentar un caso parecido como solución definitiva?",
    ],
  },
  {
    title: "Incidencias conocidas",
    description: "Casos resueltos, abiertos y errores nuevos sin evidencia directa.",
    questions: [
      "Cómo se resolvió la ruta congelada tras parada prioritaria en RutaNexo TMS?",
      "Hay una ubicación RF inconsistente en AlmaTrack WMS, existe solución definitiva?",
      "No encuentro solución para un error nuevo en AlmaTrack WMS.",
    ],
  },
  {
    title: "Flujo de demo",
    description: "Aclaraciones, registro de incidencia y feedback.",
    questions: [
      "No puedo cerrar la operación, qué debo revisar?",
      "El torno principal sigue rechazando el acceso y no aparece ningún caso parecido.",
      "No útil, la respuesta no me sirve.",
    ],
  },
];

const recommendedQuestions = sampleGroups
  .flatMap((group) => group.questions)
  .filter((question) => !question.toLowerCase().startsWith("no útil"));

const pendingStatuses = ["Preparando contexto...", "Consultando conocimiento interno...", "Generando respuesta..."];

const state = {
  apiBase: "",
  userId: "",
  conversationId: null,
  lastMessageId: null,
  busy: false,
  recommendationIndex: 0,
  statusTimer: null,
};

const elements = {
  assistantState: document.querySelector("#assistantState"),
  messages: document.querySelector("#messages"),
  chatForm: document.querySelector("#chatForm"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  starterActions: document.querySelector("#starterActions"),
  systems: document.querySelector("#systems"),
  samples: document.querySelector("#samples"),
  surpriseButton: document.querySelector("#surpriseButton"),
  resetButton: document.querySelector("#resetButton"),
  sources: document.querySelector("#sources"),
  sourceCount: document.querySelector("#sourceCount"),
  usefulButton: document.querySelector("#usefulButton"),
  notUsefulButton: document.querySelector("#notUsefulButton"),
  feedbackStatus: document.querySelector("#feedbackStatus"),
};

function defaultApiBase() {
  if (window.location.protocol.startsWith("http")) {
    return `${window.location.origin}/api`;
  }
  return "http://127.0.0.1:8000/api";
}

function normalizeBase(value) {
  return value.trim().replace(/\/+$/, "");
}

function loadState() {
  state.apiBase = normalizeBase(localStorage.getItem("logiassist.apiBase") || defaultApiBase());
  state.userId =
    localStorage.getItem("logiassist.userId") || `web-demo-${Math.random().toString(16).slice(2, 8)}`;
}

function setAssistantState(text, kind = "idle") {
  elements.assistantState.className = `chat-status ${kind}`;
  elements.assistantState.innerHTML = `<span class="status-dot"></span>${text}`;
}

function startPendingStatus() {
  clearPendingStatus();
  let index = 0;
  setAssistantState(pendingStatuses[index], "busy");
  state.statusTimer = window.setInterval(() => {
    index = Math.min(index + 1, pendingStatuses.length - 1);
    setAssistantState(pendingStatuses[index], "busy");
  }, 1300);
}

function clearPendingStatus(finalText = "Listo para consultar conocimiento interno.") {
  if (state.statusTimer) {
    window.clearInterval(state.statusTimer);
    state.statusTimer = null;
  }
  setAssistantState(finalText, "idle");
}

function appendMessage(role, text, kind = role) {
  const item = document.createElement("article");
  item.className = `message ${kind}`;

  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  if (role === "assistant") {
    const img = document.createElement("img");
    img.src = "/static/brand-logo.png";
    img.alt = "";
    avatar.append(img);
  } else {
    avatar.textContent = role === "user" ? "TU" : "!";
  }

  const content = document.createElement("div");
  content.className = "message-content";

  const meta = document.createElement("span");
  meta.className = "message-label";
  meta.textContent = role === "user" ? "Usuario" : role === "error" ? "Error" : "LogiAssist";

  const body = document.createElement("span");
  body.className = "message-body";
  body.textContent = text;

  const inlineSources = document.createElement("div");
  inlineSources.className = "inline-sources";

  content.append(meta, body, inlineSources);
  item.append(avatar, content);
  elements.messages.append(item);
  scrollMessages();
  return { item, body, inlineSources };
}

function scrollMessages() {
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function truncate(value, maxLength = 230) {
  const text = String(value || "").trim();
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1).trim()}...`;
}

function fillComposer(question) {
  elements.messageInput.value = question;
  elements.messageInput.focus();
}

function sourceKind(source) {
  return source.source_type === "document" ? "Documento" : "Incidencia";
}

function createSourceCard(source, compact = false) {
  const item = document.createElement("div");
  item.className = compact ? "source-chip-card" : "source-item";

  const title = document.createElement("span");
  title.className = "source-title";
  title.textContent = source.title || `${source.source_type}:${source.source_id}`;

  const meta = document.createElement("span");
  meta.className = "source-meta";
  meta.textContent = `${sourceKind(source)} ${source.source_id} · chunk ${source.chunk_id}`;

  const excerpt = document.createElement("span");
  excerpt.className = "source-excerpt";
  excerpt.textContent = truncate(source.source_url || source.excerpt, compact ? 120 : 230);

  item.append(title, meta, excerpt);
  return item;
}

function renderInlineSources(container, sources = [], relatedIncidents = []) {
  container.innerHTML = "";
  if (!sources.length && !relatedIncidents.length) {
    const empty = document.createElement("p");
    empty.className = "inline-source-empty";
    empty.textContent = "Esta respuesta no incluye fuentes porque es una aclaración o un flujo de registro.";
    container.append(empty);
    return;
  }

  const title = document.createElement("span");
  title.className = "inline-source-title";
  title.textContent = "Fuentes usadas";
  container.append(title);

  for (const source of sources.slice(0, 3)) {
    container.append(createSourceCard(source, true));
  }
}

function renderSources(sources = [], relatedIncidents = []) {
  elements.sourceCount.textContent = String(sources.length);
  elements.sources.innerHTML = "";

  if (!sources.length && !relatedIncidents.length) {
    elements.sources.className = "source-list empty";
    elements.sources.textContent = "Esta respuesta no incluye fuentes porque es una aclaración o un flujo de registro.";
    return;
  }

  elements.sources.className = "source-list";
  for (const source of sources) {
    elements.sources.append(createSourceCard(source));
  }

  for (const incident of relatedIncidents.slice(0, 3)) {
    const item = document.createElement("div");
    item.className = "source-item related";
    const title = document.createElement("span");
    title.className = "source-title";
    title.textContent = incident.external_id || `incident:${incident.id}`;
    const excerpt = document.createElement("span");
    excerpt.className = "source-excerpt";
    excerpt.textContent = `${incident.title || "Incidencia relacionada"} (${incident.status || "sin estado"})`;
    item.append(title, excerpt);
    elements.sources.append(item);
  }
}

async function requestJson(path, options = {}) {
  const response = await fetch(`${state.apiBase}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text };
    }
  }
  if (!response.ok) {
    const detail = payload?.detail || payload?.raw || `${response.status} ${response.statusText}`;
    throw new Error(Array.isArray(detail) ? JSON.stringify(detail) : String(detail));
  }
  return payload;
}


async function sendMessage(message) {
  if (!message || state.busy) {
    return;
  }

  state.busy = true;
  elements.sendButton.disabled = true;
  appendMessage("user", message);
  const assistantMessage = appendMessage("assistant", "");
  assistantMessage.item.classList.add("streaming");
  startPendingStatus();

  const payload = {
    conversation_id: state.conversationId,
    user_id: state.userId,
    message,
    channel: "web-demo",
  };

  let receivedUsefulStreamEvent = false;
  try {
    await requestStream("/chat/stream", payload, {
      onToken(text) {
        receivedUsefulStreamEvent = true;
        if (assistantMessage.body.textContent === "") {
          setAssistantState("Generando respuesta...", "busy");
        }
        assistantMessage.body.textContent += text;
        scrollMessages();
      },
      onSources(data) {
        renderSources(data.sources || [], data.related_incidents || []);
      },
      onFinal(response) {
        receivedUsefulStreamEvent = true;
        applyChatResponse(response, assistantMessage);
      },
      onError(data) {
        throw new Error(data.message || "No se pudo completar el stream.");
      },
    });
  } catch (error) {
    if (!receivedUsefulStreamEvent) {
      try {
        const response = await requestJson("/chat", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        applyChatResponse(response, assistantMessage);
      } catch (fallbackError) {
        assistantMessage.item.remove();
        appendMessage("error", fallbackError.message, "error");
        clearPendingStatus("Error al procesar la consulta.");
      }
    } else {
      appendMessage("error", error.message, "error");
      clearPendingStatus("El stream se interrumpió antes de completar la respuesta.");
    }
  } finally {
    state.busy = false;
    elements.sendButton.disabled = false;
    elements.messageInput.focus();
  }
}

function applyChatResponse(response, messageParts) {
  state.conversationId = response.conversation_id;
  state.lastMessageId = response.message_id;
  messageParts.item.classList.remove("streaming");
  messageParts.body.textContent = response.answer || response.fallback_text || "Respuesta vacía";
  renderInlineSources(messageParts.inlineSources, response.sources || [], response.related_incidents || []);
  renderSources(response.sources, response.related_incidents);
  setFeedbackEnabled(Boolean(response.conversation_id));
  clearPendingStatus("Respuesta completada.");
  scrollMessages();
}

async function requestStream(path, payload, handlers) {
  const response = await fetch(`${state.apiBase}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split(/\n\n/);
    buffer = events.pop() || "";
    for (const rawEvent of events) {
      dispatchSseEvent(rawEvent, handlers);
    }
  }
  if (buffer.trim()) {
    dispatchSseEvent(buffer, handlers);
  }
}

function dispatchSseEvent(rawEvent, handlers) {
  const parsed = parseSseEvent(rawEvent);
  if (!parsed) {
    return;
  }
  if (parsed.event === "token") {
    handlers.onToken?.(parsed.data.text || "");
  } else if (parsed.event === "sources") {
    handlers.onSources?.(parsed.data);
  } else if (parsed.event === "final") {
    handlers.onFinal?.(parsed.data);
  } else if (parsed.event === "error") {
    handlers.onError?.(parsed.data);
  }
}

function parseSseEvent(rawEvent) {
  let event = "message";
  const dataLines = [];
  for (const line of rawEvent.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (!dataLines.length) {
    return null;
  }
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return { event, data: { raw: dataLines.join("\n") } };
  }
}

function setFeedbackEnabled(enabled) {
  elements.usefulButton.disabled = !enabled;
  elements.notUsefulButton.disabled = !enabled;
  elements.feedbackStatus.textContent = enabled
    ? "Puedes marcar la última respuesta como útil o no útil."
    : "Disponible tras recibir una respuesta.";
}

async function sendFeedback(feedbackType) {
  if (!state.conversationId) {
    return;
  }
  try {
    await requestJson("/feedback", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: state.conversationId,
        message_id: state.lastMessageId,
        user_id: state.userId,
        feedback_type: feedbackType,
      }),
    });
    elements.feedbackStatus.textContent = "Feedback registrado.";
  } catch (error) {
    elements.feedbackStatus.textContent = `No se pudo guardar feedback: ${error.message}`;
  }
}

function resetConversation() {
  state.conversationId = null;
  state.lastMessageId = null;
  elements.messages.innerHTML = "";
  renderSources([], []);
  setFeedbackEnabled(false);
  clearPendingStatus();
  appendMessage(
    "assistant",
    [
      "Nueva conversación iniciada.",
      "Puedo ayudarte con almacén, pedidos, rutas, accesos, onboarding, calidad, documentos y KPIs.",
      "Elige una tarjeta del panel de demo o escribe una consulta operativa. Si faltan datos, pediré una aclaración antes de inventar una respuesta.",
    ].join("\n\n")
  );
}

function renderStarterActions() {
  elements.starterActions.innerHTML = "";
  for (const action of starterActions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "starter-card";
    button.addEventListener("click", () => fillComposer(action.question));

    const title = document.createElement("span");
    title.className = "starter-title";
    title.textContent = action.title;

    const detail = document.createElement("span");
    detail.className = "starter-detail";
    detail.textContent = action.detail;

    button.append(title, detail);
    elements.starterActions.append(button);
  }
}

function renderSystems() {
  elements.systems.innerHTML = "";
  for (const system of systems) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "system-card";
    item.addEventListener("click", () => fillComposer(system.example));

    const name = document.createElement("span");
    name.className = "system-name";
    name.textContent = system.name;

    const area = document.createElement("span");
    area.className = "system-area";
    area.textContent = system.area;

    item.append(name, area);
    elements.systems.append(item);
  }
}

function renderSamples() {
  elements.samples.innerHTML = "";
  for (const group of sampleGroups) {
    const section = document.createElement("details");
    section.className = "sample-group";
    section.open = group.title === "Documentación operativa" || group.title === "Incidencias conocidas";

    const summary = document.createElement("summary");
    summary.textContent = group.title;

    const description = document.createElement("p");
    description.textContent = group.description;

    const list = document.createElement("div");
    list.className = "sample-buttons";
    for (const sample of group.questions) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = sample;
      button.addEventListener("click", () => fillComposer(sample));
      list.append(button);
    }

    section.append(summary, description, list);
    elements.samples.append(section);
  }
}

function suggestQuestion() {
  const question = recommendedQuestions[state.recommendationIndex % recommendedQuestions.length];
  state.recommendationIndex += 1;
  fillComposer(question);
}

elements.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  elements.messageInput.value = "";
  sendMessage(message);
});

elements.surpriseButton.addEventListener("click", suggestQuestion);
elements.resetButton.addEventListener("click", resetConversation);
elements.usefulButton.addEventListener("click", () => sendFeedback("useful"));
elements.notUsefulButton.addEventListener("click", () => sendFeedback("not_useful"));

loadState();
renderStarterActions();
renderSystems();
renderSamples();
resetConversation();
