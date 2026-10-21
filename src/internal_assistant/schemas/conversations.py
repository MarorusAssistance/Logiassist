from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ConversationSummary(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class ConversationMessage(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime
    meta: dict | None = None


class ConversationDetail(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessage] = Field(default_factory=list)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        from internal_assistant.security.prompt_guard import normalize_untrusted_text

        return " ".join(normalize_untrusted_text(value).split()) if isinstance(value, str) else value
