import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from skill_runtime import read_skill_documentation_excerpt

# MiniMax / smaller models hit limits quickly; OpenClaw also keeps a channel session history.
_SKILL_DOC_TOTAL_BUDGET_CHARS = 6000
_SKILL_DOC_PER_SKILL_CHARS = 1800
_CONTEXT_MAX_MESSAGES = 10
_CONTEXT_MAX_CHARS_EACH = 1800
_USER_CONTENT_MAX_CHARS = 8000
_OPENCLAW_TOTAL_MESSAGE_CHARS = 10000


OPENCLAW_INSTALL_CMD = "curl -fsSL https://openclaw.ai/install-cli.sh | bash"


class OpenClawError(Exception):
    pass


def _safe_run(
    args: list[str],
    *,
    timeout_sec: int = 120,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        args,
        cwd=str(Path.home()),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )


def resolve_openclaw_binary() -> str | None:
    candidates = [
        shutil.which("openclaw"),
        str((Path.home() / ".local" / "bin" / "openclaw")),
        str((Path.home() / ".openclaw" / "bin" / "openclaw")),
    ]
    for item in candidates:
        if not item:
            continue
        p = Path(item).expanduser()
        if p.exists() and p.is_file():
            return str(p)
    return None


def ensure_openclaw_installed(timeout_sec: int = 240) -> str:
    binary = resolve_openclaw_binary()
    if binary:
        return binary
    proc = subprocess.run(
        ["bash", "-lc", OPENCLAW_INSTALL_CMD],
        cwd=str(Path.home()),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    binary = resolve_openclaw_binary()
    if proc.returncode != 0 or not binary:
        stderr = (proc.stderr or proc.stdout or "").strip()
        raise OpenClawError(f"OpenClaw install failed: {stderr[:800]}")
    return binary


def ensure_openclaw_configured(
    *,
    binary: str,
    model_id: str,
    api_base_url: str,
    api_key: str,
    timeout_sec: int = 180,
) -> None:
    if not model_id or not api_base_url or not api_key:
        raise OpenClawError("Missing model config for OpenClaw onboarding")
    normalized_base = api_base_url.strip().rstrip("/")
    if normalized_base.endswith("/chat/completions"):
        normalized_base = normalized_base[: -len("/chat/completions")].rstrip("/")
    args = [
        binary,
        "onboard",
        "--non-interactive",
        "--accept-risk",
        "--mode",
        "local",
        "--auth-choice",
        "custom-api-key",
        "--custom-base-url",
        normalized_base,
        "--custom-model-id",
        model_id,
        "--custom-api-key",
        api_key,
        "--skip-channels",
        "--skip-search",
        "--skip-health",
        "--skip-ui",
        "--no-install-daemon",
        "--json",
    ]
    proc = _safe_run(args, timeout_sec=timeout_sec)
    # onboarding can return non-zero for "already configured"; allow if health works.
    if proc.returncode == 0:
        return
    health = _safe_run([binary, "health", "--json"], timeout_sec=30)
    if health.returncode == 0:
        return
    stderr = (proc.stderr or proc.stdout or "").strip()
    raise OpenClawError(f"OpenClaw onboard failed: {stderr[:800]}")


def _extract_text_from_json(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("final", "answer", "content", "text", "message"):
            value = payload.get(key)
            text = _extract_text_from_json(value)
            if text:
                return text
        for value in payload.values():
            text = _extract_text_from_json(value)
            if text:
                return text
        return ""
    if isinstance(payload, list):
        for item in payload:
            text = _extract_text_from_json(item)
            if text:
                return text
        return ""
    return ""


def _try_parse_json_block(raw: str) -> Any | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try parsing a trailing JSON block after diagnostic lines.
    starts = [idx for idx, ch in enumerate(text) if ch == "{"]
    for idx in reversed(starts):
        chunk = text[idx:]
        try:
            return json.loads(chunk)
        except Exception:
            continue
    return None


def _truncate_context_blocks(blocks: list[str]) -> list[str]:
    out: list[str] = []
    for raw in blocks[:_CONTEXT_MAX_MESSAGES]:
        s = raw.strip()
        if len(s) > _CONTEXT_MAX_CHARS_EACH:
            s = s[: _CONTEXT_MAX_CHARS_EACH] + "\n…(truncated)"
        out.append(s)
    return out


def _build_openclaw_prompt(
    *,
    user_content: str,
    enabled_skills: list[str],
    context_blocks: list[str],
    skill_briefs: list[str],
) -> str:
    blocks = _truncate_context_blocks(context_blocks)
    lines = [
        "请仅输出最终答案，不要输出思考过程，不要输出工具调用协议。",
        "【运行环境】你在 ContextedAI 中通过 OpenClaw 本地代理回复。若所选技能可用，请优先真实调用对应技能能力。"
        "所选技能的 TOKEN 若存在，已通过进程环境注入（见各技能目录下的 .env.local）。",
        "不要使用“没有网络能力/系统不支持技能路由/只能模拟执行”这类笼统说法；若调用失败，必须说明具体失败原因。",
        "若某能力必须在本机执行脚本而你无法代为执行，请如实说明限制并给出用户可执行的步骤，不要编造已成功调用。",
    ]
    if enabled_skills:
        lines.append(f"本次要求优先使用这些技能: {', '.join(enabled_skills)}")
    if skill_briefs:
        lines.append("已选技能信息：")
        lines.extend([f"- {item}" for item in skill_briefs])
    doc_budget = _SKILL_DOC_TOTAL_BUDGET_CHARS
    for key in enabled_skills:
        if doc_budget <= 400:
            break
        take = min(_SKILL_DOC_PER_SKILL_CHARS, doc_budget)
        excerpt = read_skill_documentation_excerpt(key, max_chars=take)
        if excerpt:
            header = f"### 技能 `{key}` — SKILL.md 节选\n"
            chunk = header + excerpt
            lines.append(chunk)
            doc_budget -= len(chunk)
    if blocks:
        lines.append("上下文参考：")
        lines.extend([f"- {item}" for item in blocks])
    lines.append("用户请求：")
    lines.append(user_content.strip())
    return "\n".join(lines)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists() or not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, val = s.split("=", 1)
        key = key.strip()
        val = val.strip()
        if key:
            values[key] = val
    return values


def _collect_skill_env(enabled_skills: list[str]) -> dict[str, str]:
    root = Path.home() / ".openclaw" / "skills"
    if not root.exists():
        return {}
    envs: dict[str, str] = {}
    targets = enabled_skills or []
    if not targets:
        return {}
    for skill in targets:
        env_file = root / skill / ".env.local"
        envs.update(_parse_env_file(env_file))
    return envs


def _read_skill_brief(skill_key: str) -> str:
    skill_md = Path.home() / ".openclaw" / "skills" / skill_key / "SKILL.md"
    if not skill_md.exists():
        return skill_key
    text = skill_md.read_text(encoding="utf-8")
    front = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            front = text[3:end]
    desc = ""
    if front:
        m = re.search(r'^description:\s*"?(.*?)"?\s*$', front, flags=re.MULTILINE)
        if m:
            desc = m.group(1).strip().strip('"')
    if not desc:
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and len(s) > 8:
                desc = s
                break
    desc = desc or "已安装技能"
    if len(desc) > 140:
        desc = desc[:140] + "..."
    return f"{skill_key}: {desc}"


def _is_context_overflow_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "context overflow" in lower
        or "prompt too large" in lower
        or "maximum context length" in lower
        or "context_length_exceeded" in lower
        or "too many tokens" in lower
    )


def _decode_agent_output(proc: subprocess.CompletedProcess[str]) -> str:
    raw = (proc.stdout or "").strip()
    combined = "\n".join([part for part in [raw, (proc.stderr or "").strip()] if part]).strip()
    if not raw:
        raw = combined
    if not raw:
        raise OpenClawError("OpenClaw returned empty output")
    text = ""
    parsed = _try_parse_json_block(raw)
    if parsed is not None:
        text = _extract_text_from_json(parsed)
    else:
        # try JSONL style output
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                text = _extract_text_from_json(json.loads(line))
            except Exception:
                text = line
            if text:
                break
    if not text and combined and combined != raw:
        parsed = _try_parse_json_block(combined)
        if parsed is not None:
            text = _extract_text_from_json(parsed)
    if not text:
        text = raw
    if text.lower().startswith("http 404:") or text.lower().startswith("http 401:"):
        raise OpenClawError(f"OpenClaw upstream error: {text[:800]}")
    return text.strip()


def _run_openclaw_agent_once(
    *,
    binary: str,
    message: str,
    timeout_sec: int,
    extra_env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    # Fresh channel id every turn: ContextedAI already persists chat history; a fixed `--to`
    # would let OpenClaw accumulate prior turns on top of our prompt and overflow small models.
    channel_id = f"contextedai-{uuid.uuid4().hex}"
    args = [
        binary,
        "agent",
        "--local",
        "--to",
        channel_id,
        "--message",
        message,
        "--json",
        "--timeout",
        str(timeout_sec),
    ]
    return _safe_run(args, timeout_sec=timeout_sec + 15, extra_env=extra_env)


def run_openclaw_turn(
    *,
    user_content: str,
    enabled_skills: list[str],
    context_blocks: list[str],
    model_id: str,
    api_base_url: str,
    api_key: str,
    timeout_sec: int = 180,
) -> str:
    binary = ensure_openclaw_installed(timeout_sec=240)
    ensure_openclaw_configured(
        binary=binary,
        model_id=model_id,
        api_base_url=api_base_url,
        api_key=api_key,
        timeout_sec=timeout_sec,
    )
    skill_env = _collect_skill_env(enabled_skills)
    skill_briefs = [_read_skill_brief(k) for k in enabled_skills]
    safe_user_content = (user_content or "").strip()
    if len(safe_user_content) > _USER_CONTENT_MAX_CHARS:
        safe_user_content = safe_user_content[:_USER_CONTENT_MAX_CHARS] + "\n…(truncated)"

    attempts = [
        {
            "name": "full",
            "enabled_skills": enabled_skills,
            "context_blocks": context_blocks,
            "skill_briefs": skill_briefs,
            "user_content": safe_user_content,
        },
        {
            "name": "light-no-context",
            "enabled_skills": enabled_skills,
            "context_blocks": [],
            "skill_briefs": skill_briefs,
            "user_content": safe_user_content,
        },
        {
            "name": "minimal-user-only",
            "enabled_skills": [],
            "context_blocks": [],
            "skill_briefs": [],
            "user_content": safe_user_content,
        },
    ]

    last_err = ""
    for attempt in attempts:
        message = _build_openclaw_prompt(
            user_content=attempt["user_content"],
            enabled_skills=attempt["enabled_skills"],
            context_blocks=attempt["context_blocks"],
            skill_briefs=attempt["skill_briefs"],
        )
        if len(message) > _OPENCLAW_TOTAL_MESSAGE_CHARS:
            message = message[:_OPENCLAW_TOTAL_MESSAGE_CHARS] + "\n…(truncated for model context)"
        proc = _run_openclaw_agent_once(
            binary=binary,
            message=message,
            timeout_sec=timeout_sec,
            extra_env=skill_env,
        )
        if proc.returncode == 0:
            text = _decode_agent_output(proc)
            if not _is_context_overflow_error(text):
                return text
            # Some upstreams return overflow text with exit_code=0.
            last_err = text
            continue
        err = (proc.stderr or proc.stdout or "").strip()
        last_err = err or last_err
        if not _is_context_overflow_error(err):
            break

    if _is_context_overflow_error(last_err):
        raise OpenClawError(
            "OpenClaw agent failed: context overflow after retries. "
            "请减少上下文引用，关闭不必要技能，或切换更大上下文模型。"
        )
    raise OpenClawError(f"OpenClaw agent failed: {last_err[:1000]}")
