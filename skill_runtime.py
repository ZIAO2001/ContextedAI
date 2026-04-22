import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app_settings import get_settings
from services import SKILL_REGISTRY, preview_text


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class SkillRunResult:
    skill_key: str
    status: str
    summary: str
    latency_ms: int
    output: str = ""


LOCAL_SKILL_MANIFESTS = ("skill.json", "manifest.json", "clawhub.json", "_meta.json")

SKILL_REGISTRY_KEYS = {item["key"] for item in SKILL_REGISTRY}


def load_skill_catalog() -> list[dict[str, Any]]:
    # Built-ins as fallback so UI always works.
    registry: dict[str, dict[str, Any]] = {item["key"]: dict(item) for item in SKILL_REGISTRY}
    for key, conf in _load_local_skill_index().items():
        registry[key] = {
            "key": key,
            "name": conf["name"],
            "description": conf["description"],
            "enabled_by_default": conf["enabled_by_default"],
        }
    return list(registry.values())


def _candidate_local_skill_dirs() -> list[Path]:
    settings = get_settings()
    env_dir = settings.get("local_skills_dir") or _env("CLAWHUB_LOCAL_SKILLS_DIR")
    cwd = Path(__file__).resolve().parent
    candidates = [
        Path(env_dir).expanduser() if env_dir else None,
        Path.home() / ".openclaw" / "skills",
        Path.home() / ".config" / "openclaw" / "skills",
        Path.home() / "Library" / "Application Support" / "OpenClaw" / "skills",
        Path.home() / ".clawhub" / "skills",
        Path.home() / ".config" / "clawhub" / "skills",
        cwd / "skills",
        cwd / ".openclaw" / "skills",
        cwd / ".clawhub" / "skills",
    ]
    result: list[Path] = []
    for item in candidates:
        if not item:
            continue
        if item.exists() and item.is_dir():
            result.append(item)
    return result


def _load_local_skill_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for root in _candidate_local_skill_dirs():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            manifest_path = None
            for filename in LOCAL_SKILL_MANIFESTS:
                candidate = child / filename
                if candidate.exists():
                    manifest_path = candidate
                    break
            payload: dict[str, Any] = {}
            if manifest_path:
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}

            # Support SkillHub/OpenClaw package layout (SKILL.md + _meta.json).
            skill_md = child / "SKILL.md"
            fm = _parse_skill_markdown_frontmatter(skill_md) if skill_md.exists() else {}

            key = (
                payload.get("key")
                or payload.get("id")
                or payload.get("slug")
                or fm.get("slug")
                or child.name
            )
            if not key:
                continue
            index[key] = {
                "key": key,
                "name": (
                    payload.get("name")
                    or fm.get("name")
                    or payload.get("slug")
                    or key
                ),
                "description": (
                    payload.get("description")
                    or fm.get("description")
                    or "Local ClawHub Skill"
                ),
                "enabled_by_default": bool(payload.get("enabled_by_default", False)),
                "dir": str(child),
                "entrypoint": payload.get("entrypoint") or payload.get("run"),
                "command": payload.get("command"),
                "version": str(payload.get("version", "")).strip(),
            }
    return index


def _parse_skill_markdown_frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    # Minimal YAML-ish frontmatter parser for name/description.
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    header = text[3:end]
    result: dict[str, str] = {}
    name_match = re.search(r"^name:\s*(.+)\s*$", header, flags=re.MULTILINE)
    desc_match = re.search(r'^description:\s*"?(.*?)"?\s*$', header, flags=re.MULTILINE)
    if name_match:
        result["name"] = name_match.group(1).strip().strip('"').strip("'")
    if desc_match:
        result["description"] = desc_match.group(1).strip().strip('"').strip("'")
    return result


def list_local_skills() -> list[dict[str, Any]]:
    return sorted(_load_local_skill_index().values(), key=lambda x: x.get("key", ""))


def _parse_dotenv_local(skill_dir: Path) -> dict[str, str]:
    env_file = skill_dir / ".env.local"
    values: dict[str, str] = {}
    if not env_file.exists() or not env_file.is_file():
        return values
    for line in env_file.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, val = s.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


_ROOT_FALLBACK_PY = ("run.py", "main.py", "skill.py", "execute.py", "index.py", "cli.py")
_SCRIPTS_FALLBACK_PY = ("scripts/run.py", "scripts/main.py", "scripts/skill.py")


def _guess_entrypoint_command(skill_dir: Path) -> list[str] | None:
    """Best-effort discovery for SkillHub/OpenClaw packages without manifest entrypoint."""
    for rel in _ROOT_FALLBACK_PY:
        path = skill_dir / rel
        if path.is_file():
            return [sys.executable, str(path)]
    for rel in _SCRIPTS_FALLBACK_PY:
        path = skill_dir / rel
        if path.is_file():
            return [sys.executable, str(path)]
    run_sh = skill_dir / "run.sh"
    if run_sh.is_file() and os.access(run_sh, os.X_OK):
        return [str(run_sh)]
    pkg = skill_dir / "package.json"
    if pkg.is_file():
        try:
            payload = json.loads(pkg.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        main_js = payload.get("main") or "index.js"
        script_path = skill_dir / main_js
        if script_path.is_file():
            return ["node", str(script_path)]
        idx = skill_dir / "index.js"
        if idx.is_file():
            return ["node", str(idx)]
    return None


def resolve_skill_directory(skill_key: str) -> Path | None:
    idx = _load_local_skill_index()
    conf = idx.get(skill_key)
    if not conf:
        return None
    return Path(conf["dir"])


def read_skill_documentation_excerpt(skill_key: str, max_chars: int = 4000) -> str:
    """Body of SKILL.md after YAML frontmatter, truncated (for agent prompt)."""
    root = resolve_skill_directory(skill_key)
    if not root:
        return ""
    md = root / "SKILL.md"
    if not md.exists():
        return ""
    try:
        text = md.read_text(encoding="utf-8")
    except Exception:
        return ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            body = text[end + 4 :].lstrip()
    body = body.strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…(truncated)"
    return body


def _resolve_skill_command(skill_conf: dict[str, Any]) -> list[str] | None:
    command = (skill_conf.get("command") or "").strip()
    if command:
        return shlex.split(command)
    entrypoint = (skill_conf.get("entrypoint") or "").strip()
    skill_dir = Path(skill_conf["dir"])
    if entrypoint:
        path = skill_dir / entrypoint
        if not path.exists():
            return None
        if path.suffix == ".py":
            return [sys.executable, str(path)]
        if path.suffix in {".js", ".mjs", ".cjs"}:
            return ["node", str(path)]
        return [str(path)]
    return _guess_entrypoint_command(skill_dir)


def execute_skills(
    enabled_skills: list[str],
    user_content: str,
    conversation_id: int,
    context_snippets: list[str],
) -> list[SkillRunResult]:
    if not enabled_skills:
        return []

    local_index = _load_local_skill_index()
    results: list[SkillRunResult] = []

    for skill in enabled_skills:
        start = time.perf_counter()
        if skill in local_index:
            conf = local_index[skill]
            args = _resolve_skill_command(conf)
            if args:
                skill_dir = Path(conf["dir"])
                env = os.environ.copy()
                env.update(_parse_dotenv_local(skill_dir))
                env["CLAW_INPUT"] = user_content
                env["CLAW_CONTEXT"] = json.dumps(context_snippets, ensure_ascii=False)
                env["CLAW_CONVERSATION_ID"] = str(conversation_id)
                try:
                    proc = subprocess.run(
                        args,
                        cwd=conf["dir"],
                        input=user_content,
                        text=True,
                        capture_output=True,
                        timeout=45,
                        env=env,
                    )
                    elapsed = int((time.perf_counter() - start) * 1000)
                    output = (proc.stdout or proc.stderr or "").strip()
                    if proc.returncode == 0:
                        results.append(
                            SkillRunResult(
                                skill_key=skill,
                                status="success",
                                summary=f"{conf['name']} 本地执行完成",
                                latency_ms=elapsed,
                                output=preview_text(output, 300),
                            )
                        )
                    else:
                        results.append(
                            SkillRunResult(
                                skill_key=skill,
                                status="error",
                                summary=f"{conf['name']} 执行失败，退出码 {proc.returncode}",
                                latency_ms=elapsed,
                                output=preview_text(output, 300),
                            )
                        )
                except Exception as exc:
                    elapsed = int((time.perf_counter() - start) * 1000)
                    results.append(
                        SkillRunResult(
                            skill_key=skill,
                            status="error",
                            summary=f"{conf['name']} 启动失败: {type(exc).__name__}",
                            latency_ms=elapsed,
                            output="",
                        )
                    )
            else:
                elapsed = int((time.perf_counter() - start) * 1000)
                results.append(
                    SkillRunResult(
                        skill_key=skill,
                        status="skipped",
                        summary=(
                            f"{conf['name']} 已附加文档，但未找到可自动执行的入口 "
                            "（可在 manifest 中配置 command/entrypoint，或提供 run.py / scripts/run.py）"
                        ),
                        latency_ms=elapsed,
                        output="",
                    )
                )
            continue

        elapsed = int((time.perf_counter() - start) * 1000)
        if skill in SKILL_REGISTRY_KEYS:
            results.append(
                SkillRunResult(
                    skill_key=skill,
                    status="skipped",
                    summary=f"{skill} 为内置能力标识，未在本地启动子进程（由对话模型按能力说明处理）",
                    latency_ms=elapsed,
                    output="",
                )
            )
        else:
            results.append(
                SkillRunResult(
                    skill_key=skill,
                    status="error",
                    summary=f"本地未找到技能目录 `{skill}`",
                    latency_ms=elapsed,
                    output="请确认技能已安装到 ~/.openclaw/skills 或配置的 local_skills_dir",
                )
            )

    return results
