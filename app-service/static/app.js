const starterQuestions = [
  { question: "Cómo cierro una expedición pendiente en AlmaTrack WMS?", detail: "Procedimiento documentado, con fuentes" },
  { question: "Cómo se resolvió la ruta congelada tras parada prioritaria en RutaNexo TMS?", detail: "Incidencia conocida y su resolución" },
  { question: "Cómo se gestiona un permiso temporal en SafeGate?", detail: "Política de seguridad y accesos" },
  { question: "No encuentro solución para un error nuevo en AlmaTrack WMS.", detail: "Sin evidencia: aclaración y registro de incidencia" },
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

const pendingStatuses = ["Entendiendo la consulta…", "Buscando en documentación e incidencias…", "Redactando la respuesta…"];
const IDLE_STATUS = "Enter envía · Shift+Enter, salto de línea";

const state = {
  apiBase: "",
  userId: "",
  conversationId: null,
  busy: false,
  statusTimer: null,
  turn: 0,
};

const elements = {
  emptyState: document.querySelector("#emptyState"),
  starterList: document.querySelector("#starterList"),
  messages: document.querySelector("#messages"),
  chatForm: document.querySelector("#chatForm"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  status: document.querySelector("#status"),
  systems: document.querySelector("#systems"),
  samples: document.querySelector("#samples"),
  resetButton: document.querySelector("#resetButton"),
  sources: document.querySelector("#sources"),
};

function defaultApiBase() {
  if (window.location.protocol.startsWith("http")) {
    return `${window.location.origin}/api`;
  }
  return "http://127.0.0.1:8000/api";
}

function loadState() {
  let storedBase = null;
  let storedUser = null;
  try {
    storedBase = localStorage.getItem("logiassist.apiBase");
    storedUser = localStorage.getItem("logiassist.userId");
  } catch {
    // Sin almacenamiento local (modo privado): valores por defecto.
  }
  state.apiBase = (storedBase || defaultApiBase()).trim().replace(/\/+$/, "");
  state.userId = storedUser || `web-demo-${Math.random().toString(16).slice(2, 8)}`;
  try {
    localStorage.setItem("logiassist.userId", state.userId);
  } catch {
    // Ignorado.
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* Estado */

function setStatus(text) {
  elements.status.textContent = text;
}

function startPendingStatus() {
  stopPendingStatus();
  let index = 0;
  setStatus(pendingStatuses[index]);
  state.statusTimer = window.setInterval(() => {
    index = Math.min(index + 1, pendingStatuses.length - 1);
    setStatus(pendingStatuses[index]);
  }, 1400);
}

function stopPendingStatus(text = IDLE_STATUS) {
  if (state.statusTimer) {
    window.clearInterval(state.statusTimer);
    state.statusTimer = null;
  }
  setStatus(text);
}

function showEmptyState(visible) {
  elements.emptyState.hidden = !visible;
}

/* Mensajes */

function appendUserMessage(text) {
  const item = el("article", "msg msg--user");
  item.append(el("div", "msg__who", "Tú"), el("div", "msg__body", text));
  elements.messages.append(item);
  return item;
}

function appendAssistantMessage() {
  state.turn += 1;
  const item = el("article", "msg msg--assistant");
  item.dataset.turn = String(state.turn);
  const body = el("div", "msg__body");
  const live = el("p", "msg__live");
  const caret = el("span", "stream-caret");
  caret.setAttribute("aria-hidden", "true");
  live.append(document.createTextNode(""), caret);
  body.append(live);
  item.append(el("div", "msg__who", "Logiassist"), body);
  elements.messages.append(item);
  return { item, body, live };
}

function appendErrorMessage(text) {
  const item = el("article", "msg msg--assistant msg--error");
  item.append(el("div", "msg__who", "Logiassist"), el("div", "msg__body", text));
  elements.messages.append(item);
  scrollToEnd();
}

function scrollToEnd() {
  window.scrollTo({ top: document.body.scrollHeight, behavior: "auto" });
}

/* Texto en línea: **negrita** y citas "[2]" enlazadas a su fila de fuente. Nunca innerHTML. */
function appendInline(parent, text, turn, sourceCount) {
  const pattern = /\*\*([^*]+)\*\*|\[(\d{1,2})\]/g;
  let last = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match[2] !== undefined) {
      const number = Number(match[2]);
      if (number < 1 || number > sourceCount) continue;
      parent.append(document.createTextNode(text.slice(last, match.index)));
      const link = el("a", "cite", `[${number}]`);
      link.href = `#src-${turn}-${number}`;
      link.addEventListener("click", () => document.getElementById(`src-${turn}-${number}`)?.closest("details")?.setAttribute("open", ""));
      parent.append(link);
    } else {
      parent.append(document.createTextNode(text.slice(last, match.index)));
      parent.append(el("strong", null, match[1]));
    }
    last = match.index + match[0].length;
  }
  parent.append(document.createTextNode(text.slice(last)));
}

/* Markdown mínimo que devuelve el modelo: párrafos, listas con "-" o "1." y negritas. */
function renderAnswerBody(body, text, turn, sourceCount) {
  body.replaceChildren();
  const lines = String(text || "").trim().split("\n");
  let paragraph = null;
  let list = null;
  const closeBlocks = () => {
    paragraph = null;
    list = null;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-•*]\s+(.*)$/);
    const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    if (!line.trim()) {
      closeBlocks();
    } else if (bullet || numbered) {
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tagName.toLowerCase() !== tag) {
        list = el(tag);
        if (numbered && Number(numbered[1]) > 1) list.start = Number(numbered[1]);
        body.append(list);
      }
      paragraph = null;
      const item = el("li");
      appendInline(item, bullet ? bullet[1] : numbered[2], turn, sourceCount);
      list.append(item);
    } else {
      list = null;
      if (!paragraph) {
        paragraph = el("p");
        body.append(paragraph);
      } else {
        paragraph.append(el("br"));
      }
      appendInline(paragraph, line.trim(), turn, sourceCount);
    }
  }
}

const INCIDENT_STATUS = { resolved: "resuelta", open: "abierta", in_progress: "en curso", closed: "cerrada", pending: "pendiente" };

function evidenceFor(response) {
  const behavior = response.behavior || "";
  const sourceCount = (response.sources || []).length;
  if (behavior === "reject_prompt_injection") {
    return { kind: "none", text: "Consulta bloqueada por seguridad" };
  }
  if (behavior === "abstain_and_offer_incident_registration") {
    return { kind: "none", text: "Sin evidencia en la documentación ni en las incidencias" };
  }
  if (behavior === "ask_clarification" || response.needs_clarification) {
    return { kind: "weak", text: "Evidencia débil: necesito un dato más" };
  }
  if (sourceCount > 0) {
    return { kind: "ok", text: `Evidencia suficiente · ${sourceCount} ${sourceCount === 1 ? "fuente" : "fuentes"}` };
  }
  return null;
}

function sourceLabel(source) {
  const kind = source.source_type === "document" ? "Documento" : "Incidencia";
  return `${kind} ${source.source_id}`;
}

function truncate(value, maxLength = 220) {
  const text = String(value || "").trim().replace(/\s+/g, " ");
  return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1).trim()}…`;
}

function sourceRow(source, index, turn, statusById) {
  const row = el("li", "source-row");
  if (turn) row.id = `src-${turn}-${index + 1}`;
  const status = source.source_type === "incident" ? statusById.get(source.source_id) : null;
  const content = el("div");
  content.append(
    el("span", "source-row__title", source.title || sourceLabel(source)),
    el("span", "source-row__meta", status ? `${sourceLabel(source)} · ${status}` : sourceLabel(source)),
    el("span", "source-row__excerpt", truncate(source.excerpt))
  );
  row.append(el("span", "source-row__n", String(index + 1)), content);
  return row;
}

function buildSourceRows(sources, relatedIncidents, turn, visible = Infinity) {
  const statusById = new Map(
    relatedIncidents.map((incident) => [incident.id, INCIDENT_STATUS[incident.status] || incident.status || "sin estado"])
  );
  const wrapper = el("div");
  const list = el("ol", "rows");
  sources.slice(0, visible).forEach((source, index) => list.append(sourceRow(source, index, turn, statusById)));
  wrapper.append(list);

  const rest = sources.slice(visible);
  if (rest.length) {
    const more = el("details", "more-sources");
    more.append(el("summary", null, `Ver ${rest.length} ${rest.length === 1 ? "fuente más" : "fuentes más"}`));
    const restList = el("ol", "rows");
    rest.forEach((source, offset) => restList.append(sourceRow(source, visible + offset, turn, statusById)));
    more.append(restList);
    wrapper.append(more);
  }
  return wrapper;
}

function renderSidebarSources(sources = [], relatedIncidents = []) {
  elements.sources.replaceChildren();
  if (!sources.length) {
    elements.sources.className = "muted";
    elements.sources.textContent = "La última respuesta no cita fuentes: fue una aclaración, un rechazo o un registro.";
    return;
  }
  elements.sources.className = "";
  elements.sources.append(buildSourceRows(sources, relatedIncidents, null));
}

function buildFeedback(response) {
  const foot = el("div", "msg__foot");
  if (response.message_id && response.behavior !== "feedback") {
    const label = el("span", null, "¿Te ha servido?");
    const useful = el("button", "link-button", "Útil");
    const notUseful = el("button", "link-button", "No útil");
    useful.type = "button";
    notUseful.type = "button";
    const done = (text) => {
      useful.disabled = true;
      notUseful.disabled = true;
      label.textContent = text;
    };
    useful.addEventListener("click", () => sendFeedback(response, "useful").then(done, (error) => done(error.message)));
    notUseful.addEventListener("click", () => sendFeedback(response, "not_useful").then(done, (error) => done(error.message)));
    foot.append(label, useful, notUseful);
  }
  if (response.request_id) {
    const ref = el("span", "msg__ref mono", `ref ${response.request_id.slice(0, 8)}`);
    ref.title = `Identificador de la traza: ${response.request_id}`;
    foot.append(ref);
  }
  return foot;
}

function applyChatResponse(response, parts) {
  state.conversationId = response.conversation_id;
  const sources = response.sources || [];
  const related = response.related_incidents || [];
  const turn = parts.item.dataset.turn;

  if (response.behavior === "reject_prompt_injection") {
    parts.item.classList.add("msg--blocked");
  }
  renderAnswerBody(parts.body, response.answer || response.fallback_text || "Respuesta vacía", turn, sources.length);

  const evidence = evidenceFor(response);
  if (evidence) {
    parts.item.append(el("p", `evidence evidence--${evidence.kind}`, evidence.text));
  }
  if (sources.length) {
    const block = el("section", "msg__sources");
    block.append(el("h4", null, "Fuentes"), buildSourceRows(sources, related, turn, 4));
    parts.item.append(block);
  }
  parts.item.append(buildFeedback(response));
  renderSidebarSources(sources, related);
  stopPendingStatus();
}

/* API */

async function requestJson(path, options = {}) {
  const response = await fetch(`${state.apiBase}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
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
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split(/\n\n/);
    buffer = events.pop() || "";
    for (const rawEvent of events) dispatchSseEvent(rawEvent, handlers);
  }
  if (buffer.trim()) dispatchSseEvent(buffer, handlers);
}

function dispatchSseEvent(rawEvent, handlers) {
  const parsed = parseSseEvent(rawEvent);
  if (!parsed) return;
  if (parsed.event === "token") handlers.onToken?.(parsed.data.text || "");
  else if (parsed.event === "final") handlers.onFinal?.(parsed.data);
  else if (parsed.event === "error") handlers.onError?.(parsed.data);
}

function parseSseEvent(rawEvent) {
  let event = "message";
  const dataLines = [];
  for (const line of rawEvent.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trimStart());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return { event, data: { raw: dataLines.join("\n") } };
  }
}

async function sendMessage(message) {
  if (!message || state.busy) return;

  state.busy = true;
  elements.sendButton.disabled = true;
  showEmptyState(false);
  appendUserMessage(message);
  const parts = appendAssistantMessage();
  const liveText = parts.live.firstChild;
  scrollToEnd();
  startPendingStatus();

  const payload = { conversation_id: state.conversationId, user_id: state.userId, message, channel: "web-demo" };
  let received = false;
  try {
    await requestStream("/chat/stream", payload, {
      onToken(text) {
        if (!received) stopPendingStatus("Redactando la respuesta…");
        received = true;
        liveText.textContent += text;
        scrollToEnd();
      },
      onFinal(response) {
        received = true;
        applyChatResponse(response, parts);
      },
      onError(data) {
        throw new Error(data.message || "No se pudo completar la respuesta.");
      },
    });
  } catch (error) {
    if (!received) {
      try {
        const response = await requestJson("/chat", { method: "POST", body: JSON.stringify(payload) });
        applyChatResponse(response, parts);
      } catch (fallbackError) {
        parts.item.remove();
        appendErrorMessage(`No se pudo procesar la consulta: ${fallbackError.message}`);
        stopPendingStatus();
      }
    } else {
      parts.live.querySelector(".stream-caret")?.remove();
      appendErrorMessage("La respuesta se interrumpió antes de terminar. Prueba de nuevo.");
      stopPendingStatus();
    }
  } finally {
    state.busy = false;
    elements.sendButton.disabled = false;
    elements.messageInput.focus();
    scrollToEnd();
  }
}

async function sendFeedback(response, feedbackType) {
  await requestJson("/feedback", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: response.conversation_id,
      message_id: response.message_id,
      user_id: state.userId,
      feedback_type: feedbackType,
    }),
  });
  return "Gracias, anotado.";
}

/* Conversación y panel */

function resetConversation() {
  state.conversationId = null;
  elements.messages.replaceChildren();
  elements.sources.className = "muted";
  elements.sources.textContent = "Aún no hay respuesta.";
  stopPendingStatus();
  showEmptyState(true);
  window.scrollTo({ top: 0 });
}

function fillComposer(question) {
  elements.messageInput.value = question;
  autosize();
  elements.messageInput.focus();
}

function rowButton(title, detail, onClick) {
  const button = el("button", "row-button");
  button.type = "button";
  button.append(document.createTextNode(title));
  if (detail) button.append(el("span", "row-button__detail", detail));
  button.addEventListener("click", onClick);
  const item = el("li");
  item.append(button);
  return item;
}

function renderStarters() {
  elements.starterList.replaceChildren(
    ...starterQuestions.map((item) => rowButton(item.question, item.detail, () => sendMessage(item.question)))
  );
}

function renderSystems() {
  elements.systems.replaceChildren(
    ...systems.map((system) => rowButton(system.name, system.area, () => fillComposer(system.example)))
  );
}

function renderSamples() {
  elements.samples.replaceChildren();
  for (const group of sampleGroups) {
    const topic = el("details", "topic");
    topic.open = group.title === "Documentación operativa";
    const summary = el("summary", null, group.title);
    const list = el("ul", "rows");
    list.append(...group.questions.map((question) => rowButton(question, null, () => fillComposer(question))));
    topic.append(summary, el("p", "topic__desc", group.description), list);
    elements.samples.append(topic);
  }
}

function autosize() {
  const input = elements.messageInput;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, window.innerHeight * 0.4)}px`;
}

elements.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  elements.messageInput.value = "";
  autosize();
  sendMessage(message);
});

elements.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements.chatForm.requestSubmit();
  }
});
elements.messageInput.addEventListener("input", autosize);
elements.resetButton.addEventListener("click", resetConversation);

loadState();
renderStarters();
renderSystems();
renderSamples();
resetConversation();
