"""Defensas en capas frente a prompt injection (OWASP LLM01).

Capas implementadas:
1. Normalizacion de texto no confiable: NFKC y eliminacion de caracteres invisibles
   (zero-width, bidi, tag characters) que se usan para ocultar instrucciones.
2. Guardia de entrada: detecta inyeccion directa, peticiones de secretos/prompt,
   evasion de controles y peticiones de inventar contenido. Riesgo alto => se
   rechaza antes de llamar al LLM.
3. Cuarentena de contexto: los chunks del indice y la memoria conversacional que
   contienen instrucciones dirigidas al modelo (inyeccion indirecta) no llegan al prompt.
4. Spotlighting: los datos no confiables se envuelven en delimitadores con un nonce
   aleatorio por peticion y se neutraliza cualquier intento de cerrar el delimitador.
5. Guardia de salida: canary token en el system prompt, fuga del prompt, secretos
   con formato reconocible y enlaces/imagenes externas (exfiltracion) se bloquean o eliminan.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any


MAX_UNTRUSTED_FIELD_CHARS = 4000

# Identificador secreto por proceso. Si aparece en una respuesta, el modelo ha filtrado el system prompt.
CANARY_TOKEN = f"LGA-{secrets.token_hex(6)}"

DATA_TAG = "DATOS_NO_CONFIABLES"

_INVISIBLE_CHARS = re.compile(
    "["
    "\u00ad"  # soft hyphen
    "\u180e"
    "\u200b-\u200f"  # zero-width + marcas LTR/RTL
    "\u202a-\u202e"  # overrides bidi
    "\u2060-\u2064"
    "\u2066-\u2069"  # isolates bidi
    "\ufeff"
    "\U000e0000-\U000e007f"  # tag characters (ASCII smuggling)
    "]"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_untrusted_text(text: str | None) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = _INVISIBLE_CHARS.sub("", value)
    return _CONTROL_CHARS.sub("", value)


def _fold(text: str) -> str:
    """Minusculas y sin acentos para que los patrones sean robustos a variaciones."""
    decomposed = unicodedata.normalize("NFKD", normalize_untrusted_text(text).lower())
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", folded)


_INSTRUCTION_NOUNS = r"(instrucciones|reglas|normas|indicaciones|directrices|restricciones|politicas|prompt|contexto( recuperado)?|lo recuperado|fuentes)"

PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "instruction_override": (
        re.compile(rf"\b(ignora|ignorad|olvida|olvidate de|omite|salta(te)?|descarta|desobedece|anula)\b.{{0,30}}\b{_INSTRUCTION_NOUNS}"),
        re.compile(r"\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|all|your|the)\b.{0,20}\b(instructions|rules|prompt|context)"),
        re.compile(r"\ba partir de ahora\b.{0,40}\b(obedeces|obedece|eres|seras|actuas|actua|responde|ignora|solo)\b"),
        re.compile(r"\b(no sigas|no respetes|sin respetar|saltandote)\b.{0,20}\b(tus|las)\b.{0,15}\b(reglas|instrucciones|normas)"),
        re.compile(r"\bnuevas instrucciones\b|\bnew instructions\b"),
    ),
    "role_hijack": (
        re.compile(r"\b(actua|responde|habla|comportate|finge|haz)\b.{0,15}\b(como|de|ser)\b.{0,20}\b(administrador|admin|root|superusuario|desarrollador|developer|dan|sin restricciones|sin filtros)\b"),
        re.compile(r"\b(eres ahora|ahora eres|you are now)\b"),
        re.compile(r"\b(modo|mode)\b.{0,5}\b(desarrollador|developer|dios|god|dan|sin restricciones)\b|\bjailbreak\b"),
    ),
    "prompt_leak": (
        re.compile(r"\b(prompt|mensaje|instrucciones)\b.{0,10}\b(del|de) sistema\b|\bsystem prompt\b"),
        re.compile(r"\b(instrucciones|configuracion|reglas)\b.{0,10}\b(internas|ocultas|originales|exactas)\b.{0,20}\b(bot|asistente|modelo|tuyas|tus)\b"),
        re.compile(r"\bconfiguracion interna\b"),
        re.compile(r"\b(repite|muestra|imprime|copia)\b.{0,20}\b(todo )?(el texto|lo) (anterior|de arriba|previo)\b"),
    ),
    # "claves" tambien significa "puntos clave", por eso solo cuenta con un calificativo de secreto.
    "secret_request": (
        re.compile(
            r"\b(dame|dime|muestra(me)?|revela(me)?|lista(me)?|pasame|ensena(me)?|comparte|cuales son|extrae|imprime)\b"
            r".{0,40}\b(contrasenas|passwords|credenciales|secretos|api ?keys|connection strings|tokens de acceso)\b"
        ),
        re.compile(r"\b(contrasenas|passwords|credenciales|secretos|claves)\b.{0,15}\b(internas|internos|de administrador|de admin|maestras|privadas)\b"),
    ),
    "policy_evasion": (
        re.compile(r"\b(bypass|saltarme|saltarnos|como (me )?salto|evadir|burlar|desactivar)\b.{0,40}\b(control(es)?|validacion|doble validacion|auditoria|seguridad|aprobacion|supervision)\b"),
        re.compile(r"\bsin que quede (rastro|registro|constancia)\b"),
        re.compile(r"\bdame un bypass\b|\bun bypass\b"),
        re.compile(r"\bforzar?\b.{0,30}\bacceso permanente\b|\bfuerzo manualmente\b"),
    ),
    "fabrication_request": (
        re.compile(r"\b(inventa(te)?|invent(a|e) ?(me)?|fabrica|imagina(te)?)\b.{0,40}\b(procedimiento|politica|norma|respuesta|excepcion|dato)"),
        re.compile(r"\b(responde|contesta|dame)\b.{0,40}\baunque no (tengas|haya|exista)\b"),
        re.compile(r"\baunque no exista\b"),
        re.compile(r"\baunque no (tengas|haya|encuentres)\b.{0,30}\b(fuentes|nada|evidencia|resultados|indice|contexto)\b"),
        re.compile(r"\b(politica|procedimiento|norma|justificacion)\b.{0,25}\binventad[ao]s?\b"),
        re.compile(r"\blo que te parezca mas probable\b"),
    ),
    "delimiter_injection": (
        re.compile(r"<\|?(im_start|im_end|system|endoftext)\|?>"),
        re.compile(r"\[/?inst\]|<</?sys>>|</?system>"),
        re.compile(r"^\s*#{2,}\s*(system|sistema|instrucciones)\b", re.MULTILINE),
        re.compile(rf"{DATA_TAG.lower()}"),
    ),
    "exfiltration": (
        re.compile(r"!\[[^\]]*\]\(\s*https?://"),
        re.compile(r"\b(envia|manda|sube|post(ea)?)\b.{0,40}\b(a|to)\b.{0,10}https?://"),
    ),
}

# Categorias que implican intencion maliciosa en el mensaje del usuario.
HIGH_RISK_INPUT = {
    "instruction_override",
    "role_hijack",
    "prompt_leak",
    "secret_request",
    "policy_evasion",
    "fabrication_request",
    "delimiter_injection",
    "exfiltration",
}

# En documentos e incidencias solo nos importan instrucciones dirigidas al modelo:
# una politica de seguridad puede hablar legitimamente de contrasenas o controles.
INDIRECT_INJECTION_CATEGORIES = {
    "instruction_override",
    "role_hijack",
    "prompt_leak",
    "delimiter_injection",
    "exfiltration",
}

_BASE64_BLOB = re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")


@dataclass(slots=True)
class GuardResult:
    risk: str = "none"  # none | low | high
    categories: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.risk == "high"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_categories(text: str) -> list[str]:
    folded = _fold(text)
    return [category for category, patterns in PATTERNS.items() if any(pattern.search(folded) for pattern in patterns)]


def scan_user_input(text: str) -> GuardResult:
    categories = detect_categories(text)
    if _BASE64_BLOB.search(text or ""):
        categories.append("encoded_payload")
    if any(category in HIGH_RISK_INPUT for category in categories):
        return GuardResult(risk="high", categories=categories)
    return GuardResult(risk="low" if categories else "none", categories=categories)


def scan_retrieved_text(text: str) -> GuardResult:
    categories = [category for category in detect_categories(text) if category in INDIRECT_INJECTION_CATEGORIES]
    return GuardResult(risk="high" if categories else "none", categories=categories)


REFUSAL_MESSAGES = {
    "secret_request": (
        "No puedo compartir contrasenas, claves, credenciales ni otros secretos. "
        "Si necesitas acceso a un sistema, solicitalo por el procedimiento de accesos correspondiente."
    ),
    "prompt_leak": "No puedo compartir mis instrucciones internas ni la configuracion del asistente.",
    "policy_evasion": (
        "No puedo ayudar a saltarse controles, validaciones o auditorias. "
        "Si un control bloquea tu trabajo, puedo explicarte el procedimiento oficial o ayudarte a registrar una incidencia."
    ),
    "fabrication_request": (
        "No puedo inventar procedimientos, politicas ni respuestas: solo respondo con informacion documentada en el indice interno."
    ),
}
DEFAULT_REFUSAL = (
    "No puedo seguir instrucciones que intentan cambiar mis reglas de funcionamiento. "
    "Solo respondo con informacion documentada en el indice interno."
)


def build_refusal_answer(result: GuardResult) -> str:
    for category in ("secret_request", "prompt_leak", "policy_evasion", "fabrication_request"):
        if category in result.categories:
            message = REFUSAL_MESSAGES[category]
            break
    else:
        message = DEFAULT_REFUSAL
    return f"{message}\n\nSi tienes una duda operativa concreta, indica el sistema y el proceso y la reviso con las fuentes disponibles."


def wrap_untrusted(content: str, *, nonce: str, kind: str, **attributes: Any) -> str:
    """Spotlighting por delimitacion: el contenido no puede cerrar ni falsificar el bloque."""
    body = normalize_untrusted_text(content)[:MAX_UNTRUSTED_FIELD_CHARS]
    body = re.sub(DATA_TAG, "DATOS_NEUTRALIZADOS", body, flags=re.IGNORECASE)
    attrs = " ".join(f"{key}={value}" for key, value in attributes.items() if value not in (None, ""))
    header = f"<{DATA_TAG} id={nonce} tipo={kind}{(' ' + attrs) if attrs else ''}>"
    return f"{header}\n{body}\n</{DATA_TAG} id={nonce}>"


def new_nonce() -> str:
    return secrets.token_hex(4)


_SECRET_PATTERNS = (
    re.compile(r"\bsk-(proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(postgres(ql)?(\+psycopg)?|mysql|mongodb(\+srv)?)://[^\s:/]+:[^\s@]+@"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(AccountKey|SharedAccessKey|InstrumentationKey)=[A-Za-z0-9+/=\-]{16,}"),
    re.compile(r"\bls(v2)?_[a-z]{2,4}_[A-Za-z0-9]{20,}"),  # LangSmith
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
)
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_EXTERNAL_LINK = re.compile(r"\[([^\]]*)\]\(\s*https?://[^)]*\)")
_SYSTEM_PROMPT_FINGERPRINTS = (
    "reglas obligatorias:",
    "jerarquia de instrucciones",
    DATA_TAG.lower(),
)


@dataclass(slots=True)
class OutputCheck:
    answer: str
    blocked: bool = False
    findings: list[str] = field(default_factory=list)


def leaked_in_text(text: str) -> list[str]:
    findings: list[str] = []
    if CANARY_TOKEN in (text or ""):
        findings.append("canary_leak")
    lowered = (text or "").lower()
    if any(fingerprint in lowered for fingerprint in _SYSTEM_PROMPT_FINGERPRINTS):
        findings.append("system_prompt_leak")
    if any(pattern.search(text or "") for pattern in _SECRET_PATTERNS):
        findings.append("secret_leak")
    return findings


def check_output(answer: str) -> OutputCheck:
    findings = leaked_in_text(answer)
    if findings:
        return OutputCheck(
            answer=(
                "No puedo mostrar esta respuesta porque incluia informacion interna del asistente o datos sensibles. "
                "Reformula la pregunta sobre un procedimiento o incidencia concreta."
            ),
            blocked=True,
            findings=findings,
        )
    cleaned = answer or ""
    if _MARKDOWN_IMAGE.search(cleaned):
        cleaned = _MARKDOWN_IMAGE.sub("", cleaned)
        findings.append("markdown_image_removed")
    if _EXTERNAL_LINK.search(cleaned):
        cleaned = _EXTERNAL_LINK.sub(r"\1", cleaned)
        findings.append("external_link_removed")
    return OutputCheck(answer=cleaned.strip(), blocked=False, findings=findings)


class StreamingOutputGuard:
    """Deja de reenviar tokens en cuanto el texto acumulado contiene una fuga.

    La respuesta final (ya validada por check_output) sustituye al texto parcial en el cliente.
    """

    def __init__(self, forward) -> None:
        self.forward = forward
        self.buffer = ""
        self.tripped = False

    def __call__(self, token: str) -> None:
        if self.tripped:
            return
        self.buffer += token
        if leaked_in_text(self.buffer):
            self.tripped = True
            return
        self.forward(token)
