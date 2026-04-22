from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True, max_length=200)
    is_pinned: bool = Field(default=False)
    is_archived: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(index=True, foreign_key="conversation.id")
    role: str = Field(max_length=20, index=True)
    content: str
    token_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class ContextReference(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    target_message_id: int = Field(index=True, foreign_key="message.id")
    source_message_id: int = Field(index=True, foreign_key="message.id")
    source_conversation_id: int = Field(index=True, foreign_key="conversation.id")
    created_at: datetime = Field(default_factory=utc_now)


class SkillExecution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    target_message_id: int = Field(index=True, foreign_key="message.id")
    skill_key: str = Field(index=True, max_length=80)
    status: str = Field(default="success", max_length=20)
    summary: str = Field(default="")
    latency_ms: int = Field(default=0)
    created_at: datetime = Field(default_factory=utc_now)


class Bookmark(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    message_id: int = Field(index=True, foreign_key="message.id", unique=True)
    created_at: datetime = Field(default_factory=utc_now)
