import os

import httpx

from app_settings import get_default_model_config, get_settings, list_model_configs


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def list_available_models() -> list[dict[str, str]]:
    configs = [item for item in list_model_configs() if item.get("enabled")]
    if configs:
        return [{"id": item["id"], "label": item["label"]} for item in configs]

    settings = get_settings()
    configured = settings.get("model_list") or _env("MODEL_LIST")
    if configured:
        models: list[dict[str, str]] = []
        for raw in configured.split(","):
            item = raw.strip()
            if not item:
                continue
            if ":" in item:
                key, label = item.split(":", 1)
                models.append({"id": key.strip(), "label": label.strip()})
            else:
                models.append({"id": item, "label": item})
        if models:
            return models

    base_url = settings.get("model_api_base_url") or _env("MODEL_API_BASE_URL") or _env("OPENAI_BASE_URL")
    api_key = settings.get("model_api_key") or _env("MODEL_API_KEY") or _env("OPENAI_API_KEY")
    if base_url and api_key:
        endpoint = f"{base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.get(endpoint, headers=headers)
                response.raise_for_status()
                data = response.json().get("data", [])
            models = [{"id": item["id"], "label": item["id"]} for item in data if item.get("id")]
            if models:
                return models
        except Exception:
            pass

    return []


def resolve_default_model() -> str:
    default = get_default_model_config()
    if default:
        return default["id"]
    settings = get_settings()
    return (settings.get("model_name") or _env("MODEL_NAME")).strip()
