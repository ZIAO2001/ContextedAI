import os
from collections.abc import Iterable
from typing import Any, Iterator
import json

import httpx

from app_settings import get_default_model_config, get_model_config, get_settings
from models import Message


class ModelAPIError(Exception):
    pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _chat_completions_endpoint(base_or_endpoint: str) -> str:
    endpoint = base_or_endpoint.strip().rstrip("/")
    if not endpoint:
        return endpoint
    if endpoint.endswith("/chat/completions"):
        return endpoint
    return f"{endpoint}/chat/completions"


def _clip_text(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + "\n…(truncated)"


def _build_messages(
    user_content: str,
    referenced_messages: Iterable[Message],
    enabled_skills: list[str],
    recent_messages: Iterable[Message],
    skill_outputs: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    system_lines = [
        "你是一个专业的 AI 协作助手，请使用简洁、结构化中文回答。",
        "如果有引用历史上下文，请优先利用这些信息。",
    ]
    if enabled_skills:
        system_lines.append(f"本次启用技能: {', '.join(enabled_skills)}")
    else:
        system_lines.append("本次未启用外部技能。")

    references = list(referenced_messages)
    if references:
        system_lines.append("引用上下文如下：")
        for msg in references:
            system_lines.append(f"- [{msg.role}] {_clip_text(msg.content, 5000)}")

    if skill_outputs:
        system_lines.append("可用的 Skill 执行结果：")
        for item in skill_outputs:
            status = item.get("status", "unknown")
            system_lines.append(
                f"- {item.get('skill_key', 'skill')} ({status}): {item.get('output', item.get('summary', ''))}"
            )

    messages: list[dict[str, str]] = [{"role": "system", "content": "\n".join(system_lines)}]

    history = list(recent_messages)[-8:]
    for msg in history:
        if msg.role in {"user", "assistant"}:
            messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": _clip_text(user_content, 12000)})
    return messages


def generate_assistant_reply(
    user_content: str,
    referenced_messages: Iterable[Message],
    enabled_skills: list[str],
    recent_messages: Iterable[Message],
    model: str | None = None,
    skill_outputs: list[dict[str, str]] | None = None,
) -> str:
    settings = get_settings()
    model_name = (model or "").strip()
    model_conf = get_model_config(model_name) if model_name else get_default_model_config(settings)
    if not model_conf:
        raise ModelAPIError("Model is not configured")
    if not model_conf.get("enabled", True):
        raise ModelAPIError(f"Model '{model_conf['id']}' is disabled")

    api_key = model_conf.get("api_key") or settings.get("model_api_key") or _env("MODEL_API_KEY") or _env("OPENAI_API_KEY")
    base_url = model_conf.get("api_base_url") or settings.get("model_api_base_url") or _env("MODEL_API_BASE_URL") or _env("OPENAI_BASE_URL")
    if not api_key or not base_url:
        raise ModelAPIError(f"Model '{model_conf['id']}' missing api_base_url or api_key")

    model_name = model_conf["id"]

    # OpenClaw compatible mode: prefer dedicated endpoint if configured.
    if settings.get("agent_engine") == "openclaw_compatible" and settings.get("openclaw_api_base_url"):
        base_url = settings.get("openclaw_api_base_url")
        api_key = settings.get("openclaw_api_key") or api_key

    endpoint = _chat_completions_endpoint(base_url)
    payload = {
        "model": model_name,
        "temperature": 0.4,
        "messages": _build_messages(
            user_content=user_content,
            referenced_messages=referenced_messages,
            enabled_skills=enabled_skills,
            recent_messages=recent_messages,
            skill_outputs=skill_outputs,
        ),
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=45.0) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if content:
            return content
        raise ModelAPIError("Model API returned empty content")
    except httpx.HTTPError as exc:
        raise ModelAPIError(f"Model API request failed: {exc}") from exc


def stream_assistant_reply(
    user_content: str,
    referenced_messages: Iterable[Message],
    enabled_skills: list[str],
    recent_messages: Iterable[Message],
    model: str | None = None,
    skill_outputs: list[dict[str, str]] | None = None,
) -> Iterator[str]:
    settings = get_settings()
    model_name = (model or "").strip()
    model_conf = get_model_config(model_name) if model_name else get_default_model_config(settings)
    if not model_conf:
        raise ModelAPIError("Model is not configured")
    if not model_conf.get("enabled", True):
        raise ModelAPIError(f"Model '{model_conf['id']}' is disabled")

    api_key = model_conf.get("api_key") or settings.get("model_api_key") or _env("MODEL_API_KEY") or _env("OPENAI_API_KEY")
    base_url = model_conf.get("api_base_url") or settings.get("model_api_base_url") or _env("MODEL_API_BASE_URL") or _env("OPENAI_BASE_URL")
    if not api_key or not base_url:
        raise ModelAPIError(f"Model '{model_conf['id']}' missing api_base_url or api_key")
    model_name = model_conf["id"]

    if settings.get("agent_engine") == "openclaw_compatible" and settings.get("openclaw_api_base_url"):
        base_url = settings.get("openclaw_api_base_url")
        api_key = settings.get("openclaw_api_key") or api_key

    endpoint = _chat_completions_endpoint(base_url)
    payload: dict[str, Any] = {
        "model": model_name,
        "temperature": 0.4,
        "stream": True,
        "messages": _build_messages(
            user_content=user_content,
            referenced_messages=referenced_messages,
            enabled_skills=enabled_skills,
            recent_messages=recent_messages,
            skill_outputs=skill_outputs,
        ),
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=60.0) as client:
            with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                response.raise_for_status()
                any_chunk = False
                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        chunk = line[6:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            data = json.loads(chunk)
                        except Exception:
                            continue
                        delta = (
                            data.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content", "")
                        )
                        if delta:
                            any_chunk = True
                            yield delta
                if not any_chunk:
                    raise ModelAPIError("Model API returned no stream chunks")
                return
    except httpx.HTTPError as exc:
        raise ModelAPIError(f"Model API stream failed: {exc}") from exc
