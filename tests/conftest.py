from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import importlib.util

import pytest


class DummySession:
    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


@dataclass
class DummyConversation:
    id: int
    user_id: str
    channel_id: str
    teams_conversation_id: str | None
    state: dict


@dataclass
class DummyMessage:
    id: int
    conversation_id: int
    role: str
    content: str
    intent: str | None = None
    created_ticket_id: int | None = None


class FakeConversationRepository:
    def __init__(self):
        self.conversations: dict[int, DummyConversation] = {}
        self.next_id = 1

    def get_or_create(self, conversation_id, user_id, channel_id, teams_conversation_id=None):
        if conversation_id and conversation_id in self.conversations:
            return self.conversations[conversation_id]
        if conversation_id and conversation_id not in self.conversations:
            conversation = DummyConversation(conversation_id, user_id, channel_id, teams_conversation_id, {"clarification_attempts": 0})
            self.conversations[conversation_id] = conversation
            self.next_id = max(self.next_id, conversation_id + 1)
            return conversation
        conversation = DummyConversation(self.next_id, user_id, channel_id, teams_conversation_id, {"clarification_attempts": 0})
        self.conversations[self.next_id] = conversation
        self.next_id += 1
        return conversation

    def save_state(self, conversation, state):
        conversation.state = state
        self.conversations[conversation.id] = conversation
        return conversation


class FakeMessageRepository:
    def __init__(self):
        self.messages: list[DummyMessage] = []
        self.next_id = 1

    def create(self, conversation_id, role, content, intent=None, created_ticket_id=None):
        message = DummyMessage(self.next_id, conversation_id, role, content, intent, created_ticket_id)
        self.messages.append(message)
        self.next_id += 1
        return message

    def list_by_conversation(self, conversation_id, limit=20):
        return [message for message in self.messages if message.conversation_id == conversation_id][-limit:]


class FakeFeedbackRepository:
    def __init__(self):
        self.items = []

    def create(self, payload):
        self.items.append(payload)
        return payload


class FakeRetrievalLogsRepository:
    def __init__(self):
        self.items = []

    def create(self, **kwargs):
        self.items.append(kwargs)
        return kwargs


class FakeConversationMemoryRepository:
    def __init__(self, results=None):
        self.results = results or []
        self.items = []
        self.search_calls = []

    def upsert_for_message(self, **kwargs):
        existing = next((item for item in self.items if item["message_id"] == kwargs["message_id"]), None)
        if existing:
            existing.update(kwargs)
            return existing
        self.items.append(kwargs)
        return kwargs

    def search(self, *, conversation_id, query_embedding, limit=5, exclude_message_id=None):
        self.search_calls.append(
            {
                "conversation_id": conversation_id,
                "query_embedding": query_embedding,
                "limit": limit,
                "exclude_message_id": exclude_message_id,
            }
        )
        return self.results[:limit]

    def list_recent(self, conversation_id, limit=6):
        return self.items[-limit:]


class FakeIncidentRepository:
    def __init__(self):
        self.items = []

    def list_related(self, ids):
        return [item for item in self.items if item.id in ids]


class FakeRetriever:
    def __init__(self, results=None):
        self.results = results or []

    def search(self, query, query_embedding, limit=5, config=None, filters=None):
        return self.results[:limit]


@pytest.fixture
def app_paths():
    root = Path(__file__).resolve().parents[1]
    return {
        "app_service": root / "app-service" / "main.py",
        "custom_incidents": root / "functions" / "custom-incidents-api-function" / "local_main.py",
        "indexer": root / "functions" / "indexer-function" / "local_main.py",
    }


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def load_module():
    return _load_module
