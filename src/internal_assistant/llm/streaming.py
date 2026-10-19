from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from internal_assistant.schemas.chat import AssistantDecision


@dataclass(slots=True)
class ChatStreamEvent:
    kind: Literal["token", "final"]
    text: str = ""
    decision: AssistantDecision | None = None


class JsonAnswerStreamExtractor:
    """Extract visible answer text from a streamed JSON object.

    The model still returns the structured AssistantDecision JSON. This helper
    keeps the raw JSON private and only emits decoded increments from the
    top-level `answer` string.
    """

    _answer_pattern = re.compile(r'"answer"\s*:\s*"')

    def __init__(self) -> None:
        self._buffer = ""
        self._answer_start: int | None = None
        self._emitted_length = 0
        self._closed = False

    @property
    def raw_json(self) -> str:
        return self._buffer

    def feed(self, delta: str) -> list[str]:
        if not delta or self._closed:
            self._buffer += delta or ""
            return []

        self._buffer += delta
        if self._answer_start is None:
            match = self._answer_pattern.search(self._buffer)
            if not match:
                return []
            self._answer_start = match.end()

        decoded, closed = self._decode_string_prefix(self._buffer[self._answer_start :])
        self._closed = closed
        if len(decoded) <= self._emitted_length:
            return []

        chunk = decoded[self._emitted_length :]
        self._emitted_length = len(decoded)
        return [chunk] if chunk else []

    @staticmethod
    def _decode_string_prefix(value: str) -> tuple[str, bool]:
        output: list[str] = []
        index = 0
        while index < len(value):
            char = value[index]
            if char == '"':
                return "".join(output), True
            if char != "\\":
                output.append(char)
                index += 1
                continue

            if index + 1 >= len(value):
                break
            escaped = value[index + 1]
            if escaped == "u":
                hex_value = value[index + 2 : index + 6]
                if len(hex_value) < 4:
                    break
                try:
                    output.append(chr(int(hex_value, 16)))
                except ValueError:
                    output.append("\\u" + hex_value)
                index += 6
                continue

            output.append(
                {
                    '"': '"',
                    "\\": "\\",
                    "/": "/",
                    "b": "\b",
                    "f": "\f",
                    "n": "\n",
                    "r": "\r",
                    "t": "\t",
                }.get(escaped, escaped)
            )
            index += 2
        return "".join(output), False
