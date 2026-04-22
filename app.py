import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, or_, select

from app_settings import (
    delete_model_config,
    get_model_config,
    get_settings,
    list_model_configs,
    update_model_config,
    update_settings,
    upsert_model_config,
)
from cli_gateway import (
    detect_cli_status,
    install_skillhub_cli,
    resolve_cli_binary,
    run_cli_command,
)
from database import create_db_and_tables, engine, get_session
from llm_client import ModelAPIError, generate_assistant_reply, stream_assistant_reply
from model_registry import list_available_models, resolve_default_model
from models import Bookmark, ContextReference, Conversation, Message, SkillExecution
from openclaw_local import OpenClawError, run_openclaw_turn
from schemas import (
    AppSettingsRead,
    AppSettingsUpdate,
    BookmarkRead,
    ContextSearchResult,
    LocalSkillRead,
    LocalSkillHealthRead,
    LocalSkillEnvRead,
    LocalSkillEnvUpdate,
    CliExecRequest,
    CliExecResponse,
    CliStatusRead,
    ContextSourceRead,
    ConversationCreate,
    ConversationDetailRead,
    ConversationRead,
    ConversationUpdate,
    MessageRead,
    MessageWithTraceRead,
    ModelConfigCreate,
    ModelConfigRead,
    ModelConfigUpdate,
    ModelDefinition,
    SendMessageRequest,
    SendMessageResponse,
    SkillhubCommandResult,
    SkillhubInstallRequest,
    SkillhubInstallSkillRequest,
    SkillhubInstallResponse,
    SkillhubUpgradeSkillRequest,
    SkillDefinition,
    SkillExecutionRead,
    TraceCardRead,
)
from services import estimate_tokens, preview_text
from skill_bridges import apply_skill_bridges
from skill_runtime import (
    SkillRunResult,
    execute_skills,
    list_local_skills,
    load_skill_catalog,
    resolve_skill_directory,
)

app = FastAPI(title="Claw-like AI Assistant API", version="0.2.0")
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
SKILL_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}")
SKILL_INSTALL_RESERVED = {"skillhub", "skill", "skills", "cli", "agent", "store", "shop", "商店"}

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR)), name="assets")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_conversation_read(conversation: Conversation) -> ConversationRead:
    return ConversationRead(
        id=conversation.id,
        title=conversation.title,
        is_pinned=conversation.is_pinned,
        is_archived=conversation.is_archived,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def as_message_read(message: Message) -> MessageRead:
    return MessageRead(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        token_count=message.token_count,
        created_at=message.created_at,
    )


def build_trace_for_message(session: Session, message_id: int) -> TraceCardRead:
    context_refs = session.exec(
        select(ContextReference).where(ContextReference.target_message_id == message_id)
    ).all()
    source_ids = [ref.source_message_id for ref in context_refs]
    source_map: dict[int, Message] = {}
    if source_ids:
        for msg in session.exec(select(Message).where(Message.id.in_(source_ids))).all():
            if msg.id is not None:
                source_map[msg.id] = msg
    skill_execs = session.exec(
        select(SkillExecution).where(SkillExecution.target_message_id == message_id)
    ).all()
    return TraceCardRead(
        context_sources=[
            ContextSourceRead(
                source_message_id=ref.source_message_id,
                source_conversation_id=ref.source_conversation_id,
                source_preview=preview_text(source_map.get(ref.source_message_id).content, 80)
                if source_map.get(ref.source_message_id)
                else "",
            )
            for ref in context_refs
        ],
        skill_executions=[
            SkillExecutionRead(
                skill_key=skill.skill_key,
                status=skill.status,
                summary=skill.summary,
                latency_ms=skill.latency_ms,
            )
            for skill in skill_execs
        ],
    )


def validate_skills_or_raise(enabled_skills: list[str]) -> list[dict]:
    catalog = load_skill_catalog()
    valid_keys = {item["key"] for item in catalog}
    unknown = [skill for skill in enabled_skills if skill not in valid_keys]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown skills: {', '.join(sorted(unknown))}")
    return catalog


def load_referenced_messages(session: Session, context_message_ids: list[int]) -> list[Message]:
    if not context_message_ids:
        return []
    refs = session.exec(select(Message).where(Message.id.in_(context_message_ids))).all()
    if len(refs) != len(set(context_message_ids)):
        raise HTTPException(status_code=400, detail="Some referenced messages do not exist")
    return refs


def store_trace_records(
    session: Session,
    assistant_message_id: int,
    referenced_messages: list[Message],
    skill_results: list[SkillRunResult],
) -> None:
    for source_message in referenced_messages:
        session.add(
            ContextReference(
                target_message_id=assistant_message_id,
                source_message_id=source_message.id,
                source_conversation_id=source_message.conversation_id,
            )
        )
    for skill in skill_results:
        session.add(
            SkillExecution(
                target_message_id=assistant_message_id,
                skill_key=skill.skill_key,
                status=skill.status,
                summary=skill.summary,
                latency_ms=skill.latency_ms,
            )
        )


@app.on_event("startup")
def on_startup() -> None:
    create_db_and_tables()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/settings", response_model=AppSettingsRead)
def read_settings() -> AppSettingsRead:
    return AppSettingsRead(**get_settings())


@app.put("/settings", response_model=AppSettingsRead)
def write_settings(payload: AppSettingsUpdate) -> AppSettingsRead:
    data = update_settings(payload.model_dump(exclude_none=True))
    return AppSettingsRead(**data)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index_file)


@app.get("/models", response_model=list[ModelDefinition])
def list_models() -> list[ModelDefinition]:
    return [ModelDefinition(**item) for item in list_available_models()]


@app.get("/model-configs", response_model=list[ModelConfigRead])
def list_model_configs_api() -> list[ModelConfigRead]:
    return [ModelConfigRead(**item) for item in list_model_configs()]


@app.post("/model-configs", response_model=list[ModelConfigRead])
def create_model_config(payload: ModelConfigCreate) -> list[ModelConfigRead]:
    configs = upsert_model_config(
        payload.id,
        label=payload.label,
        api_base_url=payload.api_base_url,
        api_key=payload.api_key,
        enabled=payload.enabled,
        provider=payload.provider,
        is_default=payload.is_default,
    )
    return [ModelConfigRead(**item) for item in configs]


@app.put("/model-configs/{model_id}", response_model=list[ModelConfigRead])
def update_model_config_api(
    model_id: str,
    payload: ModelConfigUpdate,
) -> list[ModelConfigRead]:
    current = get_model_config(model_id)
    if not current:
        raise HTTPException(status_code=404, detail="Model config not found")
    try:
        configs = update_model_config(
            model_id,
            model_id=payload.id if payload.id is not None else current["id"],
            label=payload.label if payload.label is not None else current["label"],
            api_base_url=payload.api_base_url if payload.api_base_url is not None else current["api_base_url"],
            api_key=payload.api_key if payload.api_key is not None else current["api_key"],
            enabled=payload.enabled if payload.enabled is not None else current["enabled"],
            provider=payload.provider if payload.provider is not None else current["provider"],
            is_default=payload.is_default if payload.is_default is not None else current["is_default"],
        )
    except ValueError as exc:
        if str(exc) == "duplicate_id":
            raise HTTPException(status_code=409, detail="Model id already exists") from exc
        if str(exc) == "not_found":
            raise HTTPException(status_code=404, detail="Model config not found") from exc
        raise HTTPException(status_code=400, detail="Invalid model update request") from exc
    return [ModelConfigRead(**item) for item in configs]


@app.delete("/model-configs/{model_id}", response_model=list[ModelConfigRead])
def delete_model_config_api(model_id: str) -> list[ModelConfigRead]:
    return [ModelConfigRead(**item) for item in delete_model_config(model_id)]


@app.get("/skills", response_model=list[SkillDefinition])
def list_skills() -> list[SkillDefinition]:
    return [SkillDefinition(**item) for item in load_skill_catalog()]


def _resolve_skill_install_dir(install_dir: str | None) -> Path:
    chosen = (install_dir or "").strip() or (get_settings().get("local_skills_dir") or "").strip()
    if chosen:
        return Path(chosen).expanduser().resolve()
    # OpenClaw-localized default directory.
    return (Path.home() / ".openclaw" / "skills").resolve()


def _resolve_local_skill_env_file(skill_key: str) -> Path:
    target_key = (skill_key or "").strip()
    if not target_key:
        raise HTTPException(status_code=400, detail="skill key is required")
    skill_dir = resolve_skill_directory(target_key)
    if not skill_dir:
        raise HTTPException(status_code=404, detail="Local skill not found")
    return skill_dir / ".env.local"


def _infer_primary_env_key(skill_key: str) -> str:
    skill_dir = resolve_skill_directory(skill_key.strip())
    if not skill_dir:
        return ""
    md = skill_dir / "SKILL.md"
    if not md.exists() or not md.is_file():
        return ""
    try:
        text = md.read_text(encoding="utf-8")
    except Exception:
        return ""
    # Supports JSON-ish metadata blocks: "primaryEnv":"TOKEN_KEY"
    m = re.search(r'primaryEnv"\s*:\s*"([A-Z][A-Z0-9_]{2,})"', text)
    if m:
        return m.group(1).strip()
    # Fallback for YAML-like style: primaryEnv: TOKEN_KEY
    m = re.search(r"^\s*primaryEnv\s*:\s*([A-Z][A-Z0-9_]{2,})\s*$", text, flags=re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


def _read_skill_md_text(skill_key: str) -> str:
    skill_dir = resolve_skill_directory(skill_key.strip())
    if not skill_dir:
        return ""
    md = skill_dir / "SKILL.md"
    if not md.exists() or not md.is_file():
        return ""
    try:
        return md.read_text(encoding="utf-8")
    except Exception:
        return ""


def _extract_required_bins(skill_key: str) -> list[str]:
    text = _read_skill_md_text(skill_key)
    if not text:
        return []
    # Supports JSON-like metadata in frontmatter: "bins": ["python3", ...]
    m = re.search(r'"bins"\s*:\s*\[(.*?)\]', text, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    chunk = m.group(1)
    bins = re.findall(r'"([^"]+)"', chunk)
    return [x.strip() for x in bins if x.strip()]


def _has_runnable_entry(skill: dict[str, Any]) -> bool:
    skill_dir = Path(skill.get("dir", ""))
    if not skill_dir.exists() or not skill_dir.is_dir():
        return False
    cmd = str(skill.get("command") or "").strip()
    if cmd:
        return True
    entry = str(skill.get("entrypoint") or "").strip()
    if entry:
        return (skill_dir / entry).exists()
    fallback = (
        "run.py",
        "main.py",
        "skill.py",
        "execute.py",
        "index.py",
        "cli.py",
        "scripts/run.py",
        "scripts/main.py",
        "scripts/skill.py",
        "run.sh",
        "package.json",
    )
    return any((skill_dir / rel).exists() for rel in fallback)


def _parse_env_pairs(text: str) -> tuple[dict[str, str], int]:
    values: dict[str, str] = {}
    invalid_lines = 0
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            invalid_lines += 1
            continue
        k, v = s.split("=", 1)
        key = k.strip()
        if key:
            values[key] = v.strip().strip('"').strip("'")
    return values, invalid_lines


def _extract_skill_install_slug(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    # Explicit command first: /skill install xxx
    if lower.startswith("/skill install "):
        candidate = raw.split(maxsplit=2)[-1].strip().lower()
        return candidate if SKILL_SLUG_RE.fullmatch(candidate) else None

    candidates: list[str] = []
    patterns = [
        # 安装skill xxx / 安装技能 xxx
        r"(?:安装|下载)\s*(?:skill|技能)\s*[:：]?\s*([a-z0-9][a-z0-9_-]{1,63})",
        # 安装xxx技能 / 下载xxxskill
        r"(?:安装|下载)\s*([a-z0-9][a-z0-9_-]{1,63})\s*(?:skill|技能)",
        # install skill xxx / add skill xxx
        r"(?:install|add)\s*(?:skill\s+)?([a-z0-9][a-z0-9_-]{1,63})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, lower):
            value = match.group(1).strip().lower()
            if SKILL_SLUG_RE.fullmatch(value):
                candidates.append(value)

    # If user says both "安装 skillhub" and then "安装 summarize 技能",
    # prefer the last valid non-reserved slug.
    for value in reversed(candidates):
        if value not in SKILL_INSTALL_RESERVED:
            return value
    if candidates:
        return candidates[-1]
    return None


def _extract_zip_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s\"'<>]+\.zip(?:\?[^\s\"'<>]*)?", text or "", flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(0)


def _extract_env_assignments(text: str) -> dict[str, str]:
    envs: dict[str, str] = {}
    for m in re.finditer(r"\b([A-Z][A-Z0-9_]{2,})=([^\s,;]+)", text or ""):
        key = m.group(1).strip()
        value = m.group(2).strip()
        if key and value:
            envs[key] = value
    return envs


def _safe_slug_from_name(name: str) -> str:
    raw = re.sub(r"\.zip$", "", (name or "").strip(), flags=re.IGNORECASE)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-").lower()
    if not slug:
        return "custom-skill"
    if SKILL_SLUG_RE.fullmatch(slug):
        return slug
    return (slug[:63] or "custom-skill").strip("-")


def _install_skill_from_zip_url(
    url: str,
    *,
    install_dir: Path,
    slug_hint: str = "",
    timeout_sec: int = 180,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    install_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="skill-zip-install-") as tmp:
        tmp_dir = Path(tmp)
        zip_path = tmp_dir / "skill.zip"
        dl = subprocess.run(
            ["curl", "-k", "-fL", url, "-o", str(zip_path)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        if dl.returncode != 0 or not zip_path.exists():
            return {
                "ok": False,
                "command": ["curl", "-k", "-fL", url, "-o", str(zip_path)],
                "exit_code": dl.returncode or 1,
                "stdout": dl.stdout[-20000:],
                "stderr": dl.stderr[-20000:] or "download failed",
                "install_dir": str(install_dir),
            }

        extract_root = tmp_dir / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_root)
        except Exception as exc:
            return {
                "ok": False,
                "command": ["unzip", str(zip_path)],
                "exit_code": 1,
                "stdout": "",
                "stderr": f"extract failed: {type(exc).__name__}: {exc}",
                "install_dir": str(install_dir),
            }

        top_dirs = [p for p in extract_root.iterdir() if p.is_dir()]
        top_files = [p for p in extract_root.iterdir() if p.is_file()]
        source_root = top_dirs[0] if len(top_dirs) == 1 and not top_files else extract_root
        inferred = _safe_slug_from_name(slug_hint or source_root.name or Path(url).name)
        target_dir = install_dir / inferred
        if target_dir.exists() and target_dir.is_dir():
            shutil.rmtree(target_dir)
        shutil.copytree(source_root, target_dir)

        envs = env_overrides or {}
        if envs:
            env_file = target_dir / ".env.local"
            env_file.write_text(
                "\n".join([f"{k}={v}" for k, v in envs.items()]) + "\n",
                encoding="utf-8",
            )
            for k, v in envs.items():
                os.environ[k] = v

    return {
        "ok": True,
        "command": ["zip-install", url],
        "exit_code": 0,
        "stdout": f"Installed from zip URL: {url}",
        "stderr": "",
        "install_dir": str(install_dir),
        "slug": inferred,
        "env_keys": sorted((env_overrides or {}).keys()),
    }


def _fallback_install_skill_by_zip(slug: str, install_dir: Path, timeout_sec: int) -> dict[str, Any]:
    if not SKILL_SLUG_RE.fullmatch(slug):
        return {
            "ok": False,
            "command": ["fallback-install", slug],
            "exit_code": 1,
            "stdout": "",
            "stderr": "invalid skill slug",
            "install_dir": str(install_dir),
        }
    install_dir.mkdir(parents=True, exist_ok=True)
    download_url = f"https://lightmake.site/api/v1/download?slug={slug}"
    with tempfile.TemporaryDirectory(prefix="skillhub-fallback-") as tmp:
        zip_path = Path(tmp) / f"{slug}.zip"
        dl = subprocess.run(
            ["curl", "-k", "-fL", download_url, "-o", str(zip_path)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        if dl.returncode != 0 or not zip_path.exists():
            return {
                "ok": False,
                "command": ["curl", "-k", "-fL", download_url, "-o", str(zip_path)],
                "exit_code": dl.returncode or 1,
                "stdout": dl.stdout[-20000:],
                "stderr": dl.stderr[-20000:] or "download failed",
                "install_dir": str(install_dir),
            }
        target_dir = install_dir / slug
        if target_dir.exists() and target_dir.is_dir():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(target_dir)
        except Exception as exc:
            return {
                "ok": False,
                "command": ["unzip-fallback", slug],
                "exit_code": 1,
                "stdout": "",
                "stderr": f"extract failed: {type(exc).__name__}: {exc}",
                "install_dir": str(install_dir),
            }
    return {
        "ok": True,
        "command": ["fallback-install", slug],
        "exit_code": 0,
        "stdout": f"Installed {slug} by fallback downloader",
        "stderr": "",
        "install_dir": str(install_dir),
    }


def _install_skill_with_fallback(slug: str, install_dir: Path, timeout_sec: int) -> dict[str, Any]:
    result = _run_skillhub_command(["install", slug], install_dir=install_dir, timeout_sec=timeout_sec)
    if result["ok"]:
        return result
    stderr_text = (result.get("stderr") or "").lower()
    needs_fallback = "certificate_verify_failed" in stderr_text or "ssl" in stderr_text
    if needs_fallback:
        return _fallback_install_skill_by_zip(slug, install_dir, timeout_sec)
    return result


def _maybe_handle_chat_skill_install(user_content: str) -> str | None:
    lower = (user_content or "").lower()
    zip_url = _extract_zip_url(user_content)
    install_dir = _resolve_skill_install_dir(None)
    # Natural-language zip install command handling (OpenClaw-like behavior).
    should_install_zip = bool(zip_url) and (
        ("安装" in user_content and ("skill" in lower or "技能" in user_content))
        or ("install" in lower and "skill" in lower)
    )
    if should_install_zip:
        env_overrides = _extract_env_assignments(user_content)
        slug_hint = Path(zip_url.split("?", 1)[0]).name
        result = _install_skill_from_zip_url(
            zip_url,
            install_dir=install_dir,
            slug_hint=slug_hint,
            timeout_sec=180,
            env_overrides=env_overrides,
        )
        if result["ok"]:
            env_msg = ""
            if result.get("env_keys"):
                env_msg = f"\n已写入环境变量：`{', '.join(result['env_keys'])}`（保存于 `.env.local`）"
            return (
                f"已从链接安装技能 `{result.get('slug', 'custom-skill')}`。\n"
                f"安装目录：`{result['install_dir']}`{env_msg}\n"
                "你现在可以在「设置 -> Skill 设置」里看到并管理它，"
                "也可以在聊天输入区点「🧩」选择启用。"
            )
        error_preview = (result.get("stderr") or result.get("stdout") or "").strip()
        if len(error_preview) > 600:
            error_preview = error_preview[:600] + "..."
        return (
            "技能 zip 安装失败。\n"
            f"安装目录：`{result['install_dir']}`\n"
            f"错误信息：\n{error_preview or '无详细错误输出'}"
        )

    slug = _extract_skill_install_slug(user_content)
    if not slug:
        return None
    if not resolve_cli_binary("skillhub"):
        install_result = install_skillhub_cli(timeout_sec=180, cli_only=True)
        if not install_result.get("ok"):
            err = (install_result.get("stderr") or install_result.get("stdout") or "").strip()
            if len(err) > 600:
                err = err[:600] + "..."
            return (
                "SkillHub CLI 未安装且自动安装失败。\n"
                f"错误信息：{err or '无详细错误输出'}"
            )
    try:
        result = _install_skill_with_fallback(slug, install_dir=install_dir, timeout_sec=180)
    except HTTPException as exc:
        return f"Skill 安装失败：{exc.detail}"
    if result["ok"]:
        return (
            f"已安装技能 `{slug}`。\n"
            f"安装目录：`{result['install_dir']}`\n"
            "你现在可以在「设置 -> Skill 设置」里看到并管理它，"
            "也可以在聊天输入区点「🧩」选择启用。"
        )
    error_preview = (result.get("stderr") or result.get("stdout") or "").strip()
    if len(error_preview) > 600:
        error_preview = error_preview[:600] + "..."
    return (
        f"技能 `{slug}` 安装失败（exit={result['exit_code']}）。\n"
        f"安装目录：`{result['install_dir']}`\n"
        f"错误信息：\n{error_preview or '无详细错误输出'}"
    )


def _run_skillhub_command(
    args: list[str],
    *,
    install_dir: Path,
    timeout_sec: int,
) -> dict[str, Any]:
    binary = resolve_cli_binary("skillhub")
    if not binary:
        raise HTTPException(status_code=404, detail="skillhub is not installed")
    install_dir.mkdir(parents=True, exist_ok=True)
    command = [binary, "--dir", str(install_dir), *args]
    completed = subprocess.run(
        command,
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-20000:],
        "stderr": completed.stderr[-20000:],
        "install_dir": str(install_dir),
    }


@app.get("/skills/local", response_model=list[LocalSkillRead])
def list_local_skills_api() -> list[LocalSkillRead]:
    return [LocalSkillRead(**item) for item in list_local_skills()]


@app.get("/skills/local/health", response_model=list[LocalSkillHealthRead])
def local_skill_health_api() -> list[LocalSkillHealthRead]:
    results: list[LocalSkillHealthRead] = []
    for skill in list_local_skills():
        key = skill.get("key", "")
        if not key:
            continue
        details: list[str] = []
        status = "ok"
        primary = _infer_primary_env_key(key)
        env_file = _resolve_local_skill_env_file(key)
        env_text = ""
        if env_file.exists() and env_file.is_file():
            env_text = env_file.read_text(encoding="utf-8")
        env_map, invalid_count = _parse_env_pairs(env_text)
        if invalid_count > 0:
            status = "warn"
            details.append(f".env.local 有 {invalid_count} 行不是 KEY=VALUE 格式")
        if primary and not env_map.get(primary):
            status = "error"
            details.append(f"缺少必填环境变量：{primary}")
        elif primary:
            details.append(f"{primary} 已配置")
        bins = _extract_required_bins(key)
        missing = [b for b in bins if shutil.which(b) is None]
        if missing:
            if status != "error":
                status = "warn"
            details.append(f"缺少依赖命令：{', '.join(missing)}")
        runnable = _has_runnable_entry(skill)
        if not runnable:
            if status == "ok":
                status = "warn"
            details.append("未发现可本地执行入口（将依赖 OpenClaw 按 SKILL.md 调用）")
        if not details:
            details.append("配置完整")
        summary = (
            "健康" if status == "ok" else
            "需关注" if status == "warn" else
            "配置缺失"
        )
        results.append(
            LocalSkillHealthRead(
                skill_key=key,
                status=status,
                summary=summary,
                details=details,
                primary_env_key=primary,
            )
        )
    return results


@app.get("/skills/local/{skill_key}/env", response_model=LocalSkillEnvRead)
def read_local_skill_env(skill_key: str) -> LocalSkillEnvRead:
    env_file = _resolve_local_skill_env_file(skill_key)
    primary = _infer_primary_env_key(skill_key)
    env_text = ""
    if env_file.exists() and env_file.is_file():
        env_text = env_file.read_text(encoding="utf-8")
    return LocalSkillEnvRead(skill_key=skill_key.strip(), env_text=env_text, primary_env_key=primary)


@app.put("/skills/local/{skill_key}/env", response_model=LocalSkillEnvRead)
def write_local_skill_env(skill_key: str, payload: LocalSkillEnvUpdate) -> LocalSkillEnvRead:
    env_file = _resolve_local_skill_env_file(skill_key)
    primary = _infer_primary_env_key(skill_key)
    text = (payload.env_text or "").replace("\r\n", "\n")
    stripped = text.strip()
    # If user pasted only a bare token, auto-wrap with the skill's primary env key.
    if stripped and "=" not in stripped and "\n" not in stripped and primary:
        text = f"{primary}={stripped}\n"
        stripped = text.strip()
    if stripped and "=" not in stripped and "\n" not in stripped:
        raise HTTPException(status_code=400, detail="请使用 KEY=VALUE 格式，例如 TENCENT_DOCS_TOKEN=xxx")
    if text.strip():
        if not text.endswith("\n"):
            text += "\n"
        env_file.write_text(text, encoding="utf-8")
    else:
        env_file.unlink(missing_ok=True)
    return LocalSkillEnvRead(skill_key=skill_key.strip(), env_text=text, primary_env_key=primary)


@app.post("/skills/local/install", response_model=SkillhubCommandResult)
def install_local_skill(payload: SkillhubInstallSkillRequest) -> SkillhubCommandResult:
    install_dir = _resolve_skill_install_dir(payload.install_dir)
    result = _install_skill_with_fallback(
        payload.slug.strip(),
        install_dir=install_dir,
        timeout_sec=payload.timeout_sec,
    )
    return SkillhubCommandResult(**result)


@app.post("/skills/local/upgrade", response_model=SkillhubCommandResult)
def upgrade_local_skill(payload: SkillhubUpgradeSkillRequest) -> SkillhubCommandResult:
    install_dir = _resolve_skill_install_dir(payload.install_dir)
    args = ["upgrade"]
    slug = (payload.slug or "").strip()
    if slug:
        args.append(slug)
    result = _run_skillhub_command(
        args,
        install_dir=install_dir,
        timeout_sec=payload.timeout_sec,
    )
    return SkillhubCommandResult(**result)


@app.delete("/skills/local/{skill_key}", response_model=SkillhubCommandResult)
def delete_local_skill(skill_key: str, install_dir: str | None = Query(default=None)) -> SkillhubCommandResult:
    target_key = skill_key.strip()
    if not target_key:
        raise HTTPException(status_code=400, detail="skill key is required")
    root = _resolve_skill_install_dir(install_dir)
    target_dir = (root / target_key).resolve()
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(status_code=404, detail="Local skill not found")
    if target_dir.parent != root:
        raise HTTPException(status_code=400, detail="Invalid skill directory")
    # Manual uninstall because current skillhub CLI variant may not provide remove command.
    for child in sorted(target_dir.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            child.rmdir()
    target_dir.rmdir()

    lock_path = root / ".skills_store_lock.json"
    if lock_path.exists():
        try:
            raw = json.loads(lock_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("skills"), dict):
                raw["skills"].pop(target_key, None)
                lock_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
    return SkillhubCommandResult(
        ok=True,
        command=["manual-remove", target_key],
        exit_code=0,
        stdout=f"Removed {target_key}",
        stderr="",
        install_dir=str(root),
    )


@app.get("/cli/status", response_model=CliStatusRead)
def cli_status() -> CliStatusRead:
    return CliStatusRead(**detect_cli_status())


@app.post("/cli/skillhub/install", response_model=SkillhubInstallResponse)
def install_skillhub(payload: SkillhubInstallRequest) -> SkillhubInstallResponse:
    result = install_skillhub_cli(timeout_sec=payload.timeout_sec, cli_only=payload.cli_only)
    return SkillhubInstallResponse(**result)


@app.post("/cli/exec", response_model=CliExecResponse)
def cli_exec(payload: CliExecRequest) -> CliExecResponse:
    tool = payload.tool.strip().lower()
    allowed = {"openclaw", "skillhub"}
    if tool not in allowed:
        raise HTTPException(status_code=400, detail=f"Tool '{tool}' is not allowed")
    if any("\x00" in arg for arg in payload.args):
        raise HTTPException(status_code=400, detail="Invalid argument payload")
    try:
        result = run_cli_command(
            tool=tool,
            args=payload.args,
            cwd=payload.cwd,
            timeout_sec=payload.timeout_sec,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=408, detail=f"CLI command timeout after {payload.timeout_sec}s") from exc
    return CliExecResponse(**result)


@app.get("/messages/{message_id}/trace", response_model=TraceCardRead)
def get_message_trace(message_id: int, session: Session = Depends(get_session)) -> TraceCardRead:
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    return build_trace_for_message(session, message_id)


@app.post("/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate, session: Session = Depends(get_session)
) -> ConversationRead:
    conversation = Conversation(title=payload.title.strip())
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return as_conversation_read(conversation)


@app.get("/conversations", response_model=list[ConversationRead])
def list_conversations(
    query: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> list[ConversationRead]:
    statement = select(Conversation)
    if not include_archived:
        statement = statement.where(Conversation.is_archived.is_(False))
    if query:
        keyword = f"%{query.strip()}%"
        matched_conversation_ids = session.exec(
            select(Message.conversation_id).where(Message.content.like(keyword))
        ).all()
        statement = statement.where(
            or_(
                Conversation.title.like(keyword),
                Conversation.id.in_(matched_conversation_ids or [-1]),
            )
        )
    statement = statement.order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
    return [as_conversation_read(item) for item in session.exec(statement).all()]


@app.get("/conversations/{conversation_id}", response_model=ConversationDetailRead)
def get_conversation_detail(
    conversation_id: int, session: Session = Depends(get_session)
) -> ConversationDetailRead:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    ).all()
    return ConversationDetailRead(
        conversation=as_conversation_read(conversation),
        messages=[as_message_read(message) for message in messages],
    )


@app.patch("/conversations/{conversation_id}", response_model=ConversationRead)
def update_conversation(
    conversation_id: int,
    payload: ConversationUpdate,
    session: Session = Depends(get_session),
) -> ConversationRead:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if payload.title is not None:
        conversation.title = payload.title.strip()
    if payload.is_pinned is not None:
        conversation.is_pinned = payload.is_pinned
    if payload.is_archived is not None:
        conversation.is_archived = payload.is_archived
    conversation.updated_at = now_utc()
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return as_conversation_read(conversation)


@app.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: int, session: Session = Depends(get_session)
) -> None:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    message_ids = session.exec(
        select(Message.id).where(Message.conversation_id == conversation_id)
    ).all()
    for row in session.exec(
        select(ContextReference).where(
            or_(
                ContextReference.target_message_id.in_(message_ids or [-1]),
                ContextReference.source_message_id.in_(message_ids or [-1]),
            )
        )
    ).all():
        session.delete(row)
    for row in session.exec(
        select(SkillExecution).where(SkillExecution.target_message_id.in_(message_ids or [-1]))
    ).all():
        session.delete(row)
    for row in session.exec(
        select(Bookmark).where(Bookmark.message_id.in_(message_ids or [-1]))
    ).all():
        session.delete(row)
    for message in session.exec(
        select(Message).where(Message.conversation_id == conversation_id)
    ).all():
        session.delete(message)
    session.delete(conversation)
    session.commit()


def _create_user_message(session: Session, conversation_id: int, payload: SendMessageRequest) -> Message:
    user_message = Message(
        conversation_id=conversation_id,
        role="user",
        content=payload.content.strip(),
        token_count=estimate_tokens(payload.content),
    )
    session.add(user_message)
    session.commit()
    session.refresh(user_message)
    _maybe_autorename_conversation_on_first_user_message(
        session=session,
        conversation_id=conversation_id,
        user_content=payload.content,
    )
    return user_message


def _guess_conversation_title_from_user_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "新对话"
    # Drop raw env assignment noise when users paste TOKEN lines.
    raw = re.sub(r"\b[A-Z][A-Z0-9_]{2,}\s*=\s*[^\s,;]+", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return "新对话"
    first = re.split(r"[。！？!?；;\n]", raw, maxsplit=1)[0].strip()
    if not first:
        first = raw
    if len(first) > 22:
        first = first[:22].rstrip() + "…"
    return first or "新对话"


def _maybe_autorename_conversation_on_first_user_message(
    *,
    session: Session,
    conversation_id: int,
    user_content: str,
) -> None:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        return
    if (conversation.title or "").strip() not in {"", "新对话"}:
        return
    user_count = session.exec(
        select(Message.id).where(
            Message.conversation_id == conversation_id,
            Message.role == "user",
        )
    ).all()
    if len(user_count) != 1:
        return
    title = _guess_conversation_title_from_user_text(user_content)
    conversation.title = title
    conversation.updated_at = now_utc()
    session.add(conversation)
    session.commit()


def _sanitize_assistant_output(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    # Hide reasoning/tool-call protocol blocks; keep only user-facing output.
    text = re.sub(r"<think\b[^>]*>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<function_calls\b[^>]*>[\s\S]*?</function_calls>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<invoke\b[^>]*>[\s\S]*?</invoke>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(function_call|arg)\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _resolve_model_or_raise(payload: SendMessageRequest) -> str:
    chosen = (payload.model or "").strip() or (resolve_default_model() or "").strip()
    if not chosen:
        raise HTTPException(status_code=400, detail="Model is not configured")
    conf = get_model_config(chosen)
    if not conf:
        raise HTTPException(status_code=400, detail=f"Model '{chosen}' is not configured")
    if not conf.get("enabled", True):
        raise HTTPException(status_code=400, detail=f"Model '{chosen}' is disabled")
    return chosen


def _recent_messages(session: Session, conversation_id: int) -> list[Message]:
    return session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    ).all()


def _skill_context_snippets(refs: list[Message]) -> list[str]:
    """Short strings for OpenClaw; one referenced message can still be huge."""
    max_each = 1200
    max_refs = 6
    lines: list[str] = []
    for item in refs[:max_refs]:
        body = (item.content or "").strip()
        if len(body) > max_each:
            body = body[:max_each] + "\n…(truncated)"
        lines.append(f"[{item.role}] {body}")
    return lines


def _refs_for_direct_api(refs: list[Message], *, max_chars: int = 4500, max_items: int = 8) -> list[Any]:
    """Lightweight copies with clipped content for chat/completions fallback."""
    out: list[Any] = []
    for m in refs[:max_items]:
        c = (m.content or "").strip()
        if len(c) > max_chars:
            c = c[:max_chars] + "\n…(truncated)"
        out.append(SimpleNamespace(role=m.role, content=c))
    return out


def _openclaw_overflow_message(exc: OpenClawError | str) -> bool:
    t = str(exc).lower()
    return "context overflow" in t or "prompt too large" in t or "maximum context length" in t


def _effective_reply_engine(payload: SendMessageRequest) -> str:
    """openclaw_local vs api_direct; explicit per-request field overrides settings."""
    explicit = (payload.reply_engine or "").strip()
    if explicit in ("openclaw_local", "api_direct"):
        return explicit
    settings = get_settings()
    eng = (settings.get("agent_engine") or "").strip()
    if eng == "openclaw_local":
        return "openclaw_local"
    return "api_direct"


def _generate_assistant_via_engine(
    *,
    payload: SendMessageRequest,
    chosen_model: str,
    referenced_messages: list[Message],
    recent_messages: list[Message],
    skill_results: list[SkillRunResult],
) -> str:
    mode = _effective_reply_engine(payload)
    if mode == "api_direct":
        try:
            return generate_assistant_reply(
                user_content=payload.content,
                referenced_messages=referenced_messages,
                enabled_skills=payload.enabled_skills,
                recent_messages=recent_messages,
                model=chosen_model,
                skill_outputs=[item.__dict__ for item in skill_results],
            )
        except ModelAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    model_conf = get_model_config(chosen_model)
    if not model_conf:
        raise HTTPException(status_code=400, detail=f"Model '{chosen_model}' is not configured")
    try:
        return run_openclaw_turn(
            user_content=payload.content,
            enabled_skills=payload.enabled_skills,
            context_blocks=_skill_context_snippets(referenced_messages),
            model_id=model_conf.get("id", chosen_model),
            api_base_url=model_conf.get("api_base_url", ""),
            api_key=model_conf.get("api_key", ""),
            timeout_sec=180,
        )
    except OpenClawError as exc:
        if _openclaw_overflow_message(exc):
            raise HTTPException(
                status_code=502,
                detail="OpenClaw 上下文溢出，请减少引用上下文/技能数量，或切换更大上下文模型。",
            ) from exc
        raise HTTPException(status_code=502, detail=f"OpenClaw 调用失败：{exc}") from exc


def _store_assistant_and_trace(
    session: Session,
    conversation: Conversation,
    assistant_content: str,
    referenced_messages: list[Message],
    skill_results: list[SkillRunResult],
) -> Message:
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=assistant_content,
        token_count=estimate_tokens(assistant_content),
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)
    store_trace_records(session, assistant_message.id, referenced_messages, skill_results)
    conversation.updated_at = now_utc()
    session.add(conversation)
    session.commit()
    return assistant_message


@app.post(
    "/conversations/{conversation_id}/messages/send",
    response_model=SendMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def send_message(
    conversation_id: int,
    payload: SendMessageRequest,
    session: Session = Depends(get_session),
) -> SendMessageResponse:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    referenced_messages = load_referenced_messages(session, payload.context_message_ids)
    user_message = _create_user_message(session, conversation_id, payload)
    local_skill_reply = _maybe_handle_chat_skill_install(payload.content)
    if local_skill_reply is not None:
        assistant_message = _store_assistant_and_trace(
            session=session,
            conversation=conversation,
            assistant_content=local_skill_reply,
            referenced_messages=referenced_messages,
            skill_results=[],
        )
        return SendMessageResponse(
            user_message=as_message_read(user_message),
            assistant_message=MessageWithTraceRead(
                message=as_message_read(assistant_message),
                trace=build_trace_for_message(session, assistant_message.id),
            ),
        )

    chosen_model = _resolve_model_or_raise(payload)
    validate_skills_or_raise(payload.enabled_skills)
    skill_results = execute_skills(
        enabled_skills=payload.enabled_skills,
        user_content=payload.content,
        conversation_id=conversation_id,
        context_snippets=_skill_context_snippets(referenced_messages),
    )
    bridge_payload, skill_results = apply_skill_bridges(payload, skill_results)
    recent_messages = _recent_messages(session, conversation_id)
    assistant_content = _generate_assistant_via_engine(
        payload=bridge_payload,
        chosen_model=chosen_model,
        referenced_messages=referenced_messages,
        recent_messages=recent_messages,
        skill_results=skill_results,
    )
    assistant_content = _sanitize_assistant_output(assistant_content)
    if not assistant_content:
        raise HTTPException(status_code=502, detail="Model returned no final answer")
    assistant_message = _store_assistant_and_trace(
        session=session,
        conversation=conversation,
        assistant_content=assistant_content,
        referenced_messages=referenced_messages,
        skill_results=skill_results,
    )
    return SendMessageResponse(
        user_message=as_message_read(user_message),
        assistant_message=MessageWithTraceRead(
            message=as_message_read(assistant_message),
            trace=build_trace_for_message(session, assistant_message.id),
        ),
    )


@app.post("/conversations/{conversation_id}/messages/stream")
def stream_message(
    conversation_id: int,
    payload: SendMessageRequest,
) -> StreamingResponse:
    def event_stream() -> Iterator[str]:
        def emit_error_and_persist(
            *,
            session: Session,
            conversation: Conversation,
            user_message: Message,
            message: str,
            referenced_messages: list[Message],
            skill_results: list[SkillRunResult] | None = None,
        ) -> str:
            err_text = (message or "请求失败").strip()
            assistant_message = _store_assistant_and_trace(
                session=session,
                conversation=conversation,
                assistant_content=f"请求失败：{err_text}",
                referenced_messages=referenced_messages,
                skill_results=skill_results or [],
            )
            trace = build_trace_for_message(session, assistant_message.id).model_dump()
            done_payload = {
                "assistant_message": {
                    "message": as_message_read(assistant_message).model_dump(mode="json"),
                    "trace": trace,
                }
            }
            return "event: done\ndata: " + json.dumps(done_payload, ensure_ascii=False) + "\n\n"

        with Session(engine) as session:
            conversation = session.get(Conversation, conversation_id)
            if not conversation:
                yield "event: error\ndata: " + json.dumps({"message": "Conversation not found"}) + "\n\n"
                return
            referenced_messages = []
            try:
                referenced_messages = load_referenced_messages(session, payload.context_message_ids)
            except HTTPException as exc:
                yield "event: error\ndata: " + json.dumps({"message": str(exc.detail)}) + "\n\n"
                return

            user_message = _create_user_message(session, conversation_id, payload)
            local_skill_reply = _maybe_handle_chat_skill_install(payload.content)
            if local_skill_reply is not None:
                yield "event: start\ndata: " + json.dumps(
                    {"user_message": as_message_read(user_message).model_dump(mode="json")},
                    ensure_ascii=False,
                ) + "\n\n"
                yield "event: chunk\ndata: " + json.dumps({"delta": local_skill_reply}, ensure_ascii=False) + "\n\n"
                assistant_message = _store_assistant_and_trace(
                    session=session,
                    conversation=conversation,
                    assistant_content=local_skill_reply,
                    referenced_messages=referenced_messages,
                    skill_results=[],
                )
                trace = build_trace_for_message(session, assistant_message.id).model_dump()
                done_payload = {
                    "assistant_message": {
                        "message": as_message_read(assistant_message).model_dump(mode="json"),
                        "trace": trace,
                    }
                }
                yield "event: done\ndata: " + json.dumps(done_payload, ensure_ascii=False) + "\n\n"
                return

            try:
                chosen_model = _resolve_model_or_raise(payload)
                validate_skills_or_raise(payload.enabled_skills)
            except HTTPException as exc:
                msg = str(exc.detail)
                yield "event: error\ndata: " + json.dumps({"message": msg}) + "\n\n"
                yield emit_error_and_persist(
                    session=session,
                    conversation=conversation,
                    user_message=user_message,
                    message=msg,
                    referenced_messages=referenced_messages,
                )
                return

            # Emit start early so the client doesn't sit on a silent connection while skills run / model stalls.
            yield "event: start\ndata: " + json.dumps(
                {"user_message": as_message_read(user_message).model_dump(mode="json")},
                ensure_ascii=False,
            ) + "\n\n"

            chunks: list[str] = []
            try:
                skill_results = execute_skills(
                    enabled_skills=payload.enabled_skills,
                    user_content=payload.content,
                    conversation_id=conversation_id,
                    context_snippets=_skill_context_snippets(referenced_messages),
                )
                bridge_payload, skill_results = apply_skill_bridges(payload, skill_results)
                recent_messages = _recent_messages(session, conversation_id)
                reply_mode = _effective_reply_engine(bridge_payload)

                if reply_mode == "openclaw_local":
                    # OpenClaw local turn is currently non-stream; emit as one chunk.
                    try:
                        one_shot = _generate_assistant_via_engine(
                            payload=bridge_payload,
                            chosen_model=chosen_model,
                            referenced_messages=referenced_messages,
                            recent_messages=recent_messages,
                            skill_results=skill_results,
                        )
                    except HTTPException as exc:
                        msg = str(exc.detail)
                        yield "event: error\ndata: " + json.dumps({"message": msg}) + "\n\n"
                        yield emit_error_and_persist(
                            session=session,
                            conversation=conversation,
                            user_message=user_message,
                            message=msg,
                            referenced_messages=referenced_messages,
                            skill_results=skill_results,
                        )
                        return
                    except ModelAPIError as exc:
                        msg = str(exc)
                        yield "event: error\ndata: " + json.dumps({"message": msg}) + "\n\n"
                        yield emit_error_and_persist(
                            session=session,
                            conversation=conversation,
                            user_message=user_message,
                            message=msg,
                            referenced_messages=referenced_messages,
                            skill_results=skill_results,
                        )
                        return
                    chunks.append(one_shot if one_shot else "")
                    if one_shot:
                        yield "event: chunk\ndata: " + json.dumps({"delta": one_shot}) + "\n\n"
                else:
                    try:
                        for delta in stream_assistant_reply(
                            user_content=bridge_payload.content,
                            referenced_messages=referenced_messages,
                            enabled_skills=bridge_payload.enabled_skills,
                            recent_messages=recent_messages,
                            model=chosen_model,
                            skill_outputs=[item.__dict__ for item in skill_results],
                        ):
                            chunks.append(delta)
                            yield "event: chunk\ndata: " + json.dumps({"delta": delta}) + "\n\n"
                    except ModelAPIError as exc:
                        msg = str(exc)
                        yield "event: error\ndata: " + json.dumps({"message": msg}) + "\n\n"
                        yield emit_error_and_persist(
                            session=session,
                            conversation=conversation,
                            user_message=user_message,
                            message=msg,
                            referenced_messages=referenced_messages,
                            skill_results=skill_results,
                        )
                        return

                final_content = _sanitize_assistant_output("".join(chunks))
                if not final_content:
                    msg = "Model returned no final answer"
                    yield "event: error\ndata: " + json.dumps({"message": msg}) + "\n\n"
                    yield emit_error_and_persist(
                        session=session,
                        conversation=conversation,
                        user_message=user_message,
                        message=msg,
                        referenced_messages=referenced_messages,
                        skill_results=skill_results,
                    )
                    return

                assistant_message = _store_assistant_and_trace(
                    session=session,
                    conversation=conversation,
                    assistant_content=final_content,
                    referenced_messages=referenced_messages,
                    skill_results=skill_results,
                )
                trace = build_trace_for_message(session, assistant_message.id).model_dump()
                done_payload = {
                    "assistant_message": {
                        "message": as_message_read(assistant_message).model_dump(mode="json"),
                        "trace": trace,
                    }
                }
                yield "event: done\ndata: " + json.dumps(done_payload, ensure_ascii=False) + "\n\n"
            except Exception as exc:
                msg = str(exc)[:2000]
                yield "event: error\ndata: " + json.dumps({"message": msg}, ensure_ascii=False) + "\n\n"
                yield emit_error_and_persist(
                    session=session,
                    conversation=conversation,
                    user_message=user_message,
                    message=msg,
                    referenced_messages=referenced_messages,
                )
        
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/messages/{message_id}/bookmark", response_model=BookmarkRead)
def bookmark_message(
    message_id: int, session: Session = Depends(get_session)
) -> BookmarkRead:
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    existing = session.exec(select(Bookmark).where(Bookmark.message_id == message_id)).first()
    if existing:
        return BookmarkRead(
            message_id=existing.message_id,
            conversation_id=message.conversation_id,
            content_preview=preview_text(message.content),
            created_at=existing.created_at,
        )
    bookmark = Bookmark(message_id=message_id)
    session.add(bookmark)
    session.commit()
    session.refresh(bookmark)
    return BookmarkRead(
        message_id=bookmark.message_id,
        conversation_id=message.conversation_id,
        content_preview=preview_text(message.content),
        created_at=bookmark.created_at,
    )


@app.delete("/messages/{message_id}/bookmark", status_code=status.HTTP_204_NO_CONTENT)
def remove_bookmark(
    message_id: int, session: Session = Depends(get_session)
) -> None:
    bookmark = session.exec(select(Bookmark).where(Bookmark.message_id == message_id)).first()
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    session.delete(bookmark)
    session.commit()


@app.get("/bookmarks", response_model=list[BookmarkRead])
def list_bookmarks(session: Session = Depends(get_session)) -> list[BookmarkRead]:
    bookmarks = session.exec(select(Bookmark).order_by(Bookmark.created_at.desc())).all()
    results: list[BookmarkRead] = []
    for bookmark in bookmarks:
        message = session.get(Message, bookmark.message_id)
        if not message:
            continue
        results.append(
            BookmarkRead(
                message_id=bookmark.message_id,
                conversation_id=message.conversation_id,
                content_preview=preview_text(message.content),
                created_at=bookmark.created_at,
            )
        )
    return results


@app.get("/context/messages/search", response_model=list[ContextSearchResult])
def search_context_messages(
    query: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[ContextSearchResult]:
    keyword = f"%{query.strip()}%"
    messages = session.exec(
        select(Message).where(Message.content.like(keyword)).order_by(Message.created_at.desc()).limit(limit)
    ).all()
    results: list[ContextSearchResult] = []
    for message in messages:
        conversation = session.get(Conversation, message.conversation_id)
        if not conversation:
            continue
        results.append(
            ContextSearchResult(
                message_id=message.id,
                conversation_id=message.conversation_id,
                conversation_title=conversation.title,
                role=message.role,
                snippet=preview_text(message.content, 120),
                created_at=message.created_at,
            )
        )
    return results
