from __future__ import annotations

import json
from typing import Any

from openai import BadRequestError

from internal_assistant.llm import build_default_provider
from internal_assistant.observability.usage import record_chat_usage
from internal_assistant.schemas import ChatResponse

from evaluation.types import ExpandedEvaluationTurn

from .base import BaseJudge
from .heuristic_judge import HeuristicJudge


class LLMJudge(BaseJudge):
    def __init__(self, session, llm_provider=None) -> None:
        self.session = session
        self.llm_provider = llm_provider or build_default_provider()
        self.heuristic = HeuristicJudge(session)
        chat_provider = getattr(self.llm_provider, "chat_provider", self.llm_provider)
        self.client = getattr(chat_provider, "client", None)
        self.model = getattr(getattr(chat_provider, "settings", None), "chat_model", None)
        self.provider_name = getattr(chat_provider, "provider_name", "openai")
        if self.client is None or self.model is None:
            raise ValueError("LLMJudge requiere un proveedor compatible con cliente OpenAI")

    def judge(self, turn: ExpandedEvaluationTurn, response: ChatResponse, meta: dict[str, Any]) -> dict[str, Any]:
        base = self.heuristic.judge(turn, response, meta)
        prompt = {
            "question": turn.message,
            "expected_behavior": turn.expected_behavior,
            "expected_summary": turn.expected_answer_summary,
            "required_terms": turn.must_include_terms,
            "forbidden_terms": turn.must_not_include_terms,
            "answer": response.answer,
            "sources": [item.model_dump() for item in response.sources],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "Evalua una respuesta RAG. Devuelve JSON con llm_score entre 0 y 1, "
                    "llm_verdict ('pass'|'fail') y llm_reason."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ]
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=messages,
            )
        except BadRequestError:
            completion = self.client.chat.completions.create(
                model=self.model,
                temperature=0.0,
                messages=messages,
            )
        record_chat_usage(
            getattr(completion, "usage", None),
            provider=self.provider_name,
            model=self.model,
            purpose="judge",
        )
        payload = _parse_judge_payload(completion.choices[0].message.content or "{}")
        base.update(
            {
                "llm_score": float(payload.get("llm_score", 0.0)),
                "llm_verdict": payload.get("llm_verdict", "fail"),
                "llm_reason": payload.get("llm_reason", ""),
            }
        )
        return base


def _parse_judge_payload(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                return {}
        return {}
