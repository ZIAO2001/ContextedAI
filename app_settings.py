import json
import os
import re
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "model_api_base_url": "",
    "model_api_key": "",
    "model_name": "",
    "model_list": "",
    "local_skills_dir": "",
    "agent_engine": "openclaw_local",
    "openclaw_api_base_url": "",
    "openclaw_api_key": "",
    "model_configs": [],
}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _settings_dir() -> Path:
    return Path.home() / ".contextedai"


def settings_path() -> Path:
    return _settings_dir() / "settings.json"


def _from_env() -> dict[str, Any]:
    return {
        "model_api_base_url": _env("MODEL_API_BASE_URL") or _env("OPENAI_BASE_URL"),
        "model_api_key": _env("MODEL_API_KEY") or _env("OPENAI_API_KEY"),
        "model_name": _env("MODEL_NAME"),
        "model_list": _env("MODEL_LIST"),
        "local_skills_dir": _env("CLAWHUB_LOCAL_SKILLS_DIR"),
        "agent_engine": _env("AGENT_ENGINE", "openclaw_local"),
        "openclaw_api_base_url": _env("OPENCLAW_API_BASE_URL"),
        "openclaw_api_key": _env("OPENCLAW_API_KEY"),
        "model_configs": [],
    }


def _safe_model_id(raw: str) -> str:
    value = raw.strip()
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return cleaned or "model"


def _normalize_model_configs(configs: Any) -> list[dict[str, Any]]:
    if not isinstance(configs, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in configs:
        if not isinstance(row, dict):
            continue
        mid = _safe_model_id(str(row.get("id", "")))
        if not mid or mid in seen:
            continue
        seen.add(mid)
        normalized.append(
            {
                "id": mid,
                "label": str(row.get("label", mid)).strip() or mid,
                "api_base_url": str(row.get("api_base_url", "")).strip(),
                "api_key": str(row.get("api_key", "")).strip(),
                "enabled": bool(row.get("enabled", True)),
                "is_default": bool(row.get("is_default", False)),
                "provider": str(row.get("provider", "openai_compatible")).strip()
                or "openai_compatible",
            }
        )
    # Enforce exactly one default model (mutually exclusive).
    if normalized:
        # Keep the last marked default when malformed input has multiple defaults.
        default_idx = 0
        for i, item in enumerate(normalized):
            if item["is_default"]:
                default_idx = i
        for i, item in enumerate(normalized):
            item["is_default"] = i == default_idx
    return normalized


def _migrate_legacy_model_fields(values: dict[str, Any]) -> None:
    configs = _normalize_model_configs(values.get("model_configs"))
    if not configs:
        legacy_name = str(values.get("model_name", "")).strip()
        legacy_base = str(values.get("model_api_base_url", "")).strip()
        legacy_key = str(values.get("model_api_key", "")).strip()
        if legacy_name:
            configs = [
                {
                    "id": _safe_model_id(legacy_name),
                    "label": legacy_name,
                    "api_base_url": legacy_base,
                    "api_key": legacy_key,
                    "enabled": True,
                    "is_default": True,
                    "provider": "openai_compatible",
                }
            ]
    values["model_configs"] = configs
    default = get_default_model_config(values)
    if default:
        values["model_name"] = default["id"]
        if not values.get("model_api_base_url"):
            values["model_api_base_url"] = default.get("api_base_url", "")
        if not values.get("model_api_key"):
            values["model_api_key"] = default.get("api_key", "")


def _normalize_agent_engine(values: dict[str, Any]) -> None:
    engine = str(values.get("agent_engine", "")).strip()
    if not engine or engine == "claw_native":
        values["agent_engine"] = "openclaw_local"


def get_settings() -> dict[str, Any]:
    values = dict(DEFAULT_CONFIG)
    values.update(_from_env())
    path = settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in DEFAULT_CONFIG:
                    if key in data and data[key] is not None:
                        values[key] = str(data[key]).strip() if isinstance(data[key], str) else data[key]
        except Exception:
            pass
    _normalize_agent_engine(values)
    _migrate_legacy_model_fields(values)
    return values


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_settings()
    for key, value in patch.items():
        if key in DEFAULT_CONFIG and value is not None:
            current[key] = value.strip() if isinstance(value, str) else value
    current["model_configs"] = _normalize_model_configs(current.get("model_configs"))
    _normalize_agent_engine(current)
    _migrate_legacy_model_fields(current)
    _settings_dir().mkdir(parents=True, exist_ok=True)
    settings_path().write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def list_model_configs() -> list[dict[str, Any]]:
    return _normalize_model_configs(get_settings().get("model_configs"))


def get_model_config(model_id: str) -> dict[str, Any] | None:
    model_id = _safe_model_id(model_id)
    for item in list_model_configs():
        if item["id"] == model_id:
            return item
    return None


def get_default_model_config(values: dict[str, Any] | None = None) -> dict[str, Any] | None:
    configs = _normalize_model_configs((values or get_settings()).get("model_configs"))
    if not configs:
        return None
    for item in configs:
        if item.get("is_default"):
            return item
    return configs[0]


def upsert_model_config(
    model_id: str,
    *,
    label: str,
    api_base_url: str,
    api_key: str,
    enabled: bool,
    provider: str = "openai_compatible",
    is_default: bool = False,
) -> list[dict[str, Any]]:
    model_id = _safe_model_id(model_id)
    current = get_settings()
    configs = _normalize_model_configs(current.get("model_configs"))
    updated: list[dict[str, Any]] = []
    found = False
    for item in configs:
        if item["id"] == model_id:
            updated.append(
                {
                    "id": model_id,
                    "label": label.strip() or model_id,
                    "api_base_url": api_base_url.strip(),
                    "api_key": api_key.strip(),
                    "enabled": bool(enabled),
                    "is_default": bool(is_default),
                    "provider": provider.strip() or "openai_compatible",
                }
            )
            found = True
        else:
            item["is_default"] = False if is_default else bool(item.get("is_default"))
            updated.append(item)
    if not found:
        if is_default:
            for item in updated:
                item["is_default"] = False
        updated.append(
            {
                "id": model_id,
                "label": label.strip() or model_id,
                "api_base_url": api_base_url.strip(),
                "api_key": api_key.strip(),
                "enabled": bool(enabled),
                "is_default": bool(is_default or not updated),
                "provider": provider.strip() or "openai_compatible",
            }
        )
    current["model_configs"] = updated
    save = update_settings(current)
    return _normalize_model_configs(save.get("model_configs"))


def update_model_config(
    existing_model_id: str,
    *,
    model_id: str,
    label: str,
    api_base_url: str,
    api_key: str,
    enabled: bool,
    provider: str = "openai_compatible",
    is_default: bool = False,
) -> list[dict[str, Any]]:
    existing_model_id = _safe_model_id(existing_model_id)
    model_id = _safe_model_id(model_id)
    current = get_settings()
    configs = _normalize_model_configs(current.get("model_configs"))

    existing = next((item for item in configs if item["id"] == existing_model_id), None)
    if not existing:
        raise ValueError("not_found")
    if model_id != existing_model_id and any(item["id"] == model_id for item in configs):
        raise ValueError("duplicate_id")

    updated: list[dict[str, Any]] = []
    for item in configs:
        if item["id"] == existing_model_id:
            updated.append(
                {
                    "id": model_id,
                    "label": label.strip() or model_id,
                    "api_base_url": api_base_url.strip(),
                    "api_key": api_key.strip(),
                    "enabled": bool(enabled),
                    "is_default": bool(is_default),
                    "provider": provider.strip() or "openai_compatible",
                }
            )
        else:
            item["is_default"] = False if is_default else bool(item.get("is_default"))
            updated.append(item)

    current["model_configs"] = updated
    save = update_settings(current)
    return _normalize_model_configs(save.get("model_configs"))


def delete_model_config(model_id: str) -> list[dict[str, Any]]:
    model_id = _safe_model_id(model_id)
    current = get_settings()
    configs = [item for item in _normalize_model_configs(current.get("model_configs")) if item["id"] != model_id]
    if configs and not any(item.get("is_default") for item in configs):
        configs[0]["is_default"] = True
    current["model_configs"] = configs
    save = update_settings(current)
    return _normalize_model_configs(save.get("model_configs"))
