"""Extraccion de terminos clave para la busqueda por palabras clave (BM25).

Equivale al analizador de un motor de busqueda (p. ej. el analizador `es.lucene` de Azure AI Search):
quita palabras vacias y relleno conversacional para que "donde esta el boton para subir una incidencia"
busque por "boton", "subir", "incidencia". La lematizacion la hace despues Postgres (`spanish`).
"""

from __future__ import annotations

import re
import unicodedata


# Palabras vacias del espanol mas relleno tipico de preguntas a un asistente.
STOPWORDS = frozenset(
    """
    a al algo algun alguna algunas alguno algunos ante antes aqui asi aun aunque bajo bien cada casi como con
    contra cual cuales cualquier cuando cuanto cuanta cuantos cuantas de del desde donde dos el ella ellas ello
    ellos en entre era eran es esa esas ese eso esos esta estaba estado estan estar estas este esto estos fue
    fueron ha haber habia han hasta hay he la las le les lo los mas me mi mis mucho muy nada ni no nos nosotros
    o os otra otras otro otros para pero poco por porque que quien quienes se sea segun ser si sido sin sobre
    solo son su sus tambien tan tanto te tenemos tener tengo tiene tienen todo todos tu tus un una unas uno unos
    usted ustedes vez y ya yo
    puedo puedes puede podemos pueden podria podrias debo debe deben deberia hago hacer hace hacen necesito
    necesita quiero quisiera saber dime dame explica explicame indica indicame ayuda ayudame favor hola gracias
    pasa paso ocurre sucede alguien algo forma manera caso tema cosa cosas
    """.split()
)

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_\-./]*", re.IGNORECASE)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def extract_keywords(text: str, *, max_terms: int = 12) -> list[str]:
    """Terminos significativos en orden de aparicion, sin duplicados ni palabras vacias.

    Conserva codigos y referencias (p. ej. `CMR`, `POD-2231`, `RF`) aunque sean cortos.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN.finditer(text or ""):
        raw = match.group(0).strip("-./")
        folded = _fold(raw)
        if not folded or folded in STOPWORDS or folded in seen:
            continue
        looks_like_code = any(char.isdigit() for char in raw) or (raw.isupper() and len(raw) >= 2)
        if len(folded) < 3 and not looks_like_code:
            continue
        seen.add(folded)
        terms.append(raw)
        if len(terms) >= max_terms:
            break
    return terms


def build_keyword_query(*parts: str | list[str] | None, max_terms: int = 16) -> str:
    """Une varias fuentes (palabras clave del planner, consulta reescrita, pregunta) en una sola consulta."""
    collected: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        text = " ".join(part) if isinstance(part, list) else part
        for term in extract_keywords(text, max_terms=max_terms):
            key = _fold(term)
            if key not in seen:
                seen.add(key)
                collected.append(term)
    return " ".join(collected[:max_terms])
