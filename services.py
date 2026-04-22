from datetime import datetime
from typing import Iterable

from models import Message

SKILL_REGISTRY = [
    {
        "key": "code_interpreter",
        "name": "代码解释器",
        "description": "执行和分析 Python 代码。",
        "enabled_by_default": False,
    },
    {
        "key": "web_search",
        "name": "联网搜索",
        "description": "检索互联网实时信息。",
        "enabled_by_default": False,
    },
    {
        "key": "file_parser",
        "name": "文件解析",
        "description": "读取并提取常见文件内容。",
        "enabled_by_default": False,
    },
    {
        "key": "image_generation",
        "name": "图片生成",
        "description": "根据文本提示生成图片。",
        "enabled_by_default": False,
    },
    {
        "key": "data_analysis",
        "name": "数据分析",
        "description": "执行结构化分析与统计汇总。",
        "enabled_by_default": False,
    },
]


def estimate_tokens(text: str) -> int:
    # A practical approximation for quick UI cost hints.
    return max(1, len(text.split()))


def preview_text(text: str, limit: int = 80) -> str:
    compact = " ".join(text.strip().split())
    return compact if len(compact) <= limit else f"{compact[:limit]}..."


def build_assistant_reply(
    user_content: str,
    referenced_messages: Iterable[Message],
    enabled_skills: list[str],
) -> str:
    lines = [
        "已收到你的请求，以下是基于当前输入的初步处理：",
        f"- 请求摘要：{preview_text(user_content, 120)}",
    ]

    referenced_messages = list(referenced_messages)
    if referenced_messages:
        lines.append(f"- 引用了 {len(referenced_messages)} 条历史消息：")
        for msg in referenced_messages[:5]:
            lines.append(f"  - [{msg.role}] {preview_text(msg.content, 60)}")
    else:
        lines.append("- 本次请求未附带历史上下文。")

    if enabled_skills:
        lines.append(f"- 已启用 Skill：{', '.join(enabled_skills)}")
    else:
        lines.append("- 本次请求未启用 Skill。")

    lines.extend(
        [
            "",
            "你可以继续追问，我会沿用当前会话上下文并保留可追溯信息。",
        ]
    )
    return "\n".join(lines)


def fake_skill_latency(skill_key: str) -> int:
    seed = sum(ord(ch) for ch in skill_key)
    return 120 + (seed % 480)


def skill_summary(skill_key: str, now: datetime) -> str:
    timestamp = now.strftime("%H:%M:%S")
    return f"{skill_key} 完成执行，时间 {timestamp}"
