from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ConversationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_pinned: Optional[bool] = None
    is_archived: Optional[bool] = None


class ConversationRead(BaseModel):
    id: int
    title: str
    is_pinned: bool
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    token_count: int
    created_at: datetime


class ContextSourceRead(BaseModel):
    source_message_id: int
    source_conversation_id: int
    source_preview: str = ""


class SkillExecutionRead(BaseModel):
    skill_key: str
    status: str
    summary: str
    latency_ms: int


class TraceCardRead(BaseModel):
    context_sources: list[ContextSourceRead]
    skill_executions: list[SkillExecutionRead]


class MessageWithTraceRead(BaseModel):
    message: MessageRead
    trace: TraceCardRead


class ConversationDetailRead(BaseModel):
    conversation: ConversationRead
    messages: list[MessageRead]


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    context_message_ids: list[int] = Field(default_factory=list)
    enabled_skills: list[str] = Field(default_factory=list)
    model: Optional[str] = None
    # Per turn: openclaw_local | api_direct. Omit to fall back to settings agent_engine.
    reply_engine: Optional[str] = None

    @field_validator("reply_engine", mode="before")
    @classmethod
    def normalize_reply_engine(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        s = str(v).strip()
        if s in ("openclaw_local", "api_direct"):
            return s
        return None


class SendMessageResponse(BaseModel):
    user_message: MessageRead
    assistant_message: MessageWithTraceRead


class SkillDefinition(BaseModel):
    key: str
    name: str
    description: str
    enabled_by_default: bool = False


class BookmarkRead(BaseModel):
    message_id: int
    conversation_id: int
    content_preview: str
    created_at: datetime


class ContextSearchResult(BaseModel):
    message_id: int
    conversation_id: int
    conversation_title: str
    role: str
    snippet: str
    created_at: datetime


class ModelDefinition(BaseModel):
    id: str
    label: str


class ModelConfigRead(BaseModel):
    id: str
    label: str
    api_base_url: str
    api_key: str
    enabled: bool = True
    is_default: bool = False
    provider: str = "openai_compatible"


class ModelConfigCreate(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    api_base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    enabled: bool = True
    is_default: bool = False
    provider: str = "openai_compatible"


class ModelConfigUpdate(BaseModel):
    id: Optional[str] = Field(default=None, min_length=1)
    label: Optional[str] = None
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    provider: Optional[str] = None


class AppSettingsRead(BaseModel):
    model_api_base_url: str = ""
    model_api_key: str = ""
    model_name: str = ""
    model_list: str = ""
    local_skills_dir: str = ""
    agent_engine: str = "claw_native"
    openclaw_api_base_url: str = ""
    openclaw_api_key: str = ""
    model_configs: list[ModelConfigRead] = Field(default_factory=list)


class AppSettingsUpdate(BaseModel):
    model_api_base_url: Optional[str] = None
    model_api_key: Optional[str] = None
    model_name: Optional[str] = None
    model_list: Optional[str] = None
    local_skills_dir: Optional[str] = None
    agent_engine: Optional[str] = None
    openclaw_api_base_url: Optional[str] = None
    openclaw_api_key: Optional[str] = None


class LocalSkillEnvRead(BaseModel):
    skill_key: str
    env_text: str = ""
    primary_env_key: str = ""


class LocalSkillEnvUpdate(BaseModel):
    env_text: str = ""


class CliStatusItem(BaseModel):
    installed: bool
    path: str


class CliStatusRead(BaseModel):
    openclaw: CliStatusItem
    skillhub: CliStatusItem


class SkillhubInstallRequest(BaseModel):
    cli_only: bool = True
    timeout_sec: int = Field(default=180, ge=10, le=600)


class SkillhubInstallResponse(BaseModel):
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    installed_path: str


class CliExecRequest(BaseModel):
    tool: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    cwd: Optional[str] = None
    timeout_sec: int = Field(default=60, ge=1, le=300)


class CliExecResponse(BaseModel):
    tool: str
    binary: str
    command: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str


class LocalSkillRead(BaseModel):
    key: str
    name: str
    description: str
    enabled_by_default: bool = False
    dir: str = ""
    entrypoint: Optional[str] = None
    command: Optional[str] = None
    version: str = ""


class LocalSkillHealthRead(BaseModel):
    skill_key: str
    status: str  # ok | warn | error
    summary: str
    details: list[str] = Field(default_factory=list)
    primary_env_key: str = ""


class SkillhubInstallSkillRequest(BaseModel):
    slug: str = Field(min_length=1)
    install_dir: Optional[str] = None
    timeout_sec: int = Field(default=120, ge=10, le=600)


class SkillhubUpgradeSkillRequest(BaseModel):
    slug: Optional[str] = None
    install_dir: Optional[str] = None
    timeout_sec: int = Field(default=120, ge=10, le=600)


class SkillhubCommandResult(BaseModel):
    ok: bool
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    install_dir: str
