"""Coste real de una ejecucion de evaluacion a partir del uso medido.

El uso (tokens, search units) lo registran los providers en un
``UsageMeter``; aqui solo se multiplica por la tabla de precios versionada en
``evaluation/pricing.json`` (o la indicada en ``EVAL_PRICING_PATH``).
"""

from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
from typing import Any

from internal_assistant.observability.usage import UsageMeter


DEFAULT_PRICING_PATH = Path(__file__).resolve().parent / "pricing.json"
COMPONENT_LABELS = {"chat": "Chat LLM", "embeddings": "Embeddings", "reranker": "Reranker"}


def pricing_path() -> Path:
    override = os.getenv("EVAL_PRICING_PATH", "").strip()
    return Path(override) if override else DEFAULT_PRICING_PATH


def load_pricing(path: str | Path | None = None) -> dict[str, Any]:
    return _load_pricing_cached(str(Path(path) if path else pricing_path()))


@lru_cache(maxsize=8)
def _load_pricing_cached(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["_path"] = path
    return payload


def _lookup_model(table: dict[str, Any], model: str) -> tuple[str, dict[str, Any]] | None:
    """Busca el modelo exacto (sin mayusculas) o el prefijo mas largo (snapshots fechados)."""
    normalized = (model or "").strip().lower()
    if not normalized:
        return None
    lowered = {key.lower(): (key, value) for key, value in table.items()}
    if normalized in lowered:
        return lowered[normalized]
    candidates = [key for key in lowered if normalized.startswith(f"{key}-")]
    if not candidates:
        return None
    return lowered[max(candidates, key=len)]


def price_usage(row: dict[str, Any], pricing: dict[str, Any]) -> tuple[float | None, str]:
    """Devuelve (usd, nota) para una fila agregada de uso. ``usd`` es None si no hay precio."""
    component = row["component"]
    provider = (row.get("provider") or "").lower()
    model = row.get("model") or ""

    if provider in {item.lower() for item in pricing.get("free_providers", [])}:
        return 0.0, f"provider local/{provider}: coste 0"

    table = pricing.get(component) or {}
    match = _lookup_model(table, model)
    note = ""
    if match is None:
        fallback = (pricing.get("provider_default_models", {}).get(component) or {}).get(provider)
        if fallback:
            match = _lookup_model(table, fallback)
            note = f"modelo '{model}' tarificado como {fallback} (default de {provider})"
    if match is None:
        return None, f"sin precio para {component} model '{model}' ({provider})"

    price_key, prices = match
    if component == "chat":
        cached = min(int(row.get("cached_input_tokens", 0)), int(row.get("input_tokens", 0)))
        uncached = int(row.get("input_tokens", 0)) - cached
        input_price = float(prices.get("input_per_1m", 0.0))
        cached_price = float(prices.get("cached_input_per_1m", input_price))
        usd = (
            uncached * input_price
            + cached * cached_price
            + int(row.get("output_tokens", 0)) * float(prices.get("output_per_1m", 0.0))
        ) / 1_000_000
    elif component == "embeddings":
        usd = int(row.get("input_tokens", 0)) * float(prices.get("input_per_1m", 0.0)) / 1_000_000
    elif component == "reranker":
        usd = float(row.get("search_units", 0.0)) * float(prices.get("per_1k_search_units", 0.0)) / 1_000
    else:
        return None, f"componente desconocido '{component}'"
    return usd, note or f"precio {price_key}"


def usage_cost_usd(meter: UsageMeter | None, pricing: dict[str, Any] | None = None) -> float | None:
    """Coste total (None si algun uso no tiene precio conocido)."""
    if meter is None:
        return 0.0
    current_pricing = pricing or load_pricing()
    total = 0.0
    for row in meter.aggregate():
        usd, _ = price_usage(row, current_pricing)
        if usd is None:
            return None
        total += usd
    return round(total, 8)


def usage_row_fields(meter: UsageMeter | None, pricing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Campos de coste/tokens por turno para `per_question_results`."""
    rows = meter.aggregate() if meter is not None else []
    chat = [row for row in rows if row["component"] == "chat"]
    return {
        "cost_usd": usage_cost_usd(meter, pricing),
        "chat_input_tokens": sum(row["input_tokens"] for row in chat),
        "chat_cached_input_tokens": sum(row["cached_input_tokens"] for row in chat),
        "chat_output_tokens": sum(row["output_tokens"] for row in chat),
        "embedding_tokens": sum(row["input_tokens"] for row in rows if row["component"] == "embeddings"),
        "rerank_search_units": sum(row["search_units"] for row in rows if row["component"] == "reranker"),
    }


def build_cost_section(
    meter: UsageMeter | None,
    *,
    turns: int,
    scenarios: int | None = None,
    pricing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_pricing = pricing or load_pricing()
    breakdown: list[dict[str, Any]] = []
    by_component = {component: 0.0 for component in COMPONENT_LABELS}
    notes: list[str] = []
    total = 0.0
    complete = True

    for row in meter.aggregate() if meter is not None else []:
        usd, note = price_usage(row, current_pricing)
        if usd is None:
            complete = False
            notes.append(note)
        else:
            total += usd
            by_component[row["component"]] = by_component.get(row["component"], 0.0) + usd
        breakdown.append({**row, "usd": round(usd, 8) if usd is not None else None, "pricing_note": note})

    total = round(total, 8)
    if not breakdown:
        notes.append("No se registro uso facturable (provider mock o sin llamadas externas).")
    if not complete:
        notes.append("total_usd solo suma los componentes con precio conocido.")

    meta = {key: value for key, value in current_pricing.get("_meta", {}).items()}
    return {
        "currency": "USD",
        "total_usd": total,
        "complete": complete,
        "by_component_usd": {key: round(value, 8) for key, value in by_component.items()},
        "breakdown": breakdown,
        "turns": turns,
        "avg_usd_per_turn": round(total / turns, 8) if turns else 0.0,
        "scenarios": scenarios if scenarios is not None else turns,
        "avg_usd_per_scenario": round(total / scenarios, 8) if scenarios else (round(total / turns, 8) if turns else 0.0),
        "pricing_source": {
            "path": current_pricing.get("_path", ""),
            "updated_at": meta.get("updated_at", ""),
            "sources": meta.get("sources", []),
        },
        "notes": notes,
    }


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "n/d"
    return f"${value:.6f}"


def render_cost_markdown(cost: dict[str, Any]) -> str:
    """Seccion '## Cost' para los reportes Markdown."""
    lines = [
        "## Cost",
        f"- Total: {_fmt_usd(cost.get('total_usd'))}" + ("" if cost.get("complete", True) else " (incompleto: hay modelos sin precio)"),
        f"- Media por turno: {_fmt_usd(cost.get('avg_usd_per_turn'))} ({cost.get('turns', 0)} turnos)",
        f"- Media por escenario: {_fmt_usd(cost.get('avg_usd_per_scenario'))} ({cost.get('scenarios', 0)} escenarios)",
    ]
    if cost.get("phases_usd"):
        lines.append("- Por fase: " + ", ".join(f"{name} {_fmt_usd(usd)}" for name, usd in cost["phases_usd"].items()))
    source = cost.get("pricing_source", {})
    lines.append(f"- Precios: `{Path(source.get('path', '')).name}` (actualizados {source.get('updated_at') or 'n/d'})")
    lines.extend(
        [
            "",
            "| component | purpose | provider | model | requests | input tok | cached tok | output tok | search units | USD |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in cost.get("breakdown", []):
        lines.append(
            f"| {row['component']} | {row['purpose']} | {row['provider']} | {row['model']} | {row['requests']} | "
            f"{row['input_tokens']} | {row['cached_input_tokens']} | {row['output_tokens']} | "
            f"{row['search_units']:g} | {_fmt_usd(row['usd'])} |"
        )
    if not cost.get("breakdown"):
        lines.append("| - | - | - | - | 0 | 0 | 0 | 0 | 0 | $0.000000 |")
    if cost.get("notes"):
        lines.extend(["", "Notas:"])
        lines.extend(f"- {note}" for note in cost["notes"])
    return "\n".join(lines) + "\n"


def merge_cost_phases(cost: dict[str, Any], phases: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Anade el coste por fase (retrieval, answer, ...) a una seccion de coste agregada."""
    cost["phases_usd"] = {name: section.get("total_usd") for name, section in phases.items() if section}
    return cost
