from __future__ import annotations

from typing import Any


def build_message_memory_text(*, role: str, content: str, summary: str, metadata: dict[str, Any]) -> str:
    systems = ", ".join(metadata.get("mentioned_systems") or [])
    sources = ", ".join(str(item) for item in metadata.get("source_keys") or [])
    flags = []
    if metadata.get("needs_clarification"):
        flags.append("needs_clarification")
    if metadata.get("should_offer_incident"):
        flags.append("should_offer_incident")

    parts = [
        f"role: {role}",
        f"summary: {summary}",
    ]
    if systems:
        parts.append(f"systems: {systems}")
    if sources:
        parts.append(f"sources: {sources}")
    if flags:
        parts.append(f"flags: {', '.join(flags)}")
    parts.append(f"content: {content}")
    return "\n".join(parts)


def summarize_message(content: str, *, max_chars: int = 280) -> str:
    normalized = " ".join(content.strip().split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."
