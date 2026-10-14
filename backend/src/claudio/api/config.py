"""Configuration endpoints: read, update, validate, test, history, restore."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

from .deps import require_auth, ApiError
from ..settings import (
    AppConfig,
    validate_candidate,
    write_config_atomic,
    redact_secrets,
    changed_keys,
    restart_required_for,
    config_schema_json,
)

router = APIRouter()


def _current_dict(supervisor) -> dict:
    return supervisor.config.model_dump()


@router.get("/config")
async def get_config(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    cfg = _current_dict(supervisor)
    return {
        "config": redact_secrets(cfg),
        "generation": supervisor.config_generation,
        "restart_required": [],
    }


@router.get("/config/schema")
async def get_config_schema(request: Request) -> dict:
    require_auth(request)
    return config_schema_json()


@router.put("/config")
async def put_config(request: Request, payload: dict) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    current = _current_dict(supervisor)

    # Merge-patch: candidate over current.
    merged = _merge(current, payload)
    try:
        validated = AppConfig.model_validate(merged)
    except Exception as e:  # noqa: BLE001
        raise ApiError(422, "validation_error", f"Invalid config: {e}")

    changed = changed_keys(current, merged)
    required = restart_required_for(changed)

    # Persist atomically + history.
    write_config_atomic(supervisor.config_path, merged)
    await supervisor.db.add_config_version(merged, changed)
    supervisor.reload_config()

    return {
        "config": redact_secrets(validated.model_dump()),
        "generation": supervisor.config_generation,
        "changed_keys": changed,
        "restart_required": required,
    }


@router.post("/config/validate")
async def validate_config(request: Request, payload: dict) -> dict:
    require_auth(request)
    try:
        validate_candidate(payload)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        raise ApiError(422, "validation_error", f"Invalid config: {e}")


@router.post("/config/llm/test")
async def test_llm(request: Request, payload: dict) -> dict:
    """Live connectivity test with candidate LLM settings (not yet saved)."""
    require_auth(request)
    llm = payload.get("llm", payload)
    base_url = llm.get("base_url")
    api_key = llm.get("api_key") or "local"
    model = llm.get("model")
    if not model:
        raise ApiError(422, "validation_error", "model is required for the test")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=30.0)
    start = time.time()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
            max_tokens=16,
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        latency_ms = int((time.time() - start) * 1000)
        code = _classify_llm_error(e)
        raise ApiError(
            422 if code == "llm_auth_error" else 503,
            code,
            f"LLM test failed: {e}",
            retryable=code in ("llm_unavailable",),
            details={"latency_ms": latency_ms},
        )
    latency_ms = int((time.time() - start) * 1000)
    content = ""
    if resp.choices:
        content = resp.choices[0].message.content or ""
    return {
        "ok": True,
        "model": model,
        "latency_ms": latency_ms,
        "echo": content.strip()[:120],
    }


@router.get("/config/history")
async def config_history(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    return {"history": await supervisor.db.list_config_history()}


@router.post("/config/restore")
async def config_restore(request: Request, payload: dict) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    version_id = payload.get("id")
    if version_id is None:
        raise ApiError(422, "validation_error", "id is required")
    version = await supervisor.db.get_config_version(int(version_id))
    if not version:
        raise ApiError(404, "not_found", f"No config version {version_id}")
    candidate = version["config"]
    try:
        AppConfig.model_validate(candidate)
    except Exception as e:  # noqa: BLE001
        raise ApiError(422, "validation_error", f"Stored version invalid: {e}")
    current = _current_dict(supervisor)
    changed = changed_keys(current, candidate)
    required = restart_required_for(changed)
    write_config_atomic(supervisor.config_path, candidate)
    await supervisor.db.add_config_version(candidate, ["restore:" + str(version_id)])
    supervisor.reload_config()
    return {
        "config": redact_secrets(candidate),
        "generation": supervisor.config_generation,
        "restored_from": version_id,
        "restart_required": required,
    }


@router.get("/config/raw")
async def config_raw(request: Request) -> dict:
    """Unredacted config — explicitly gated, off by default."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    if not getattr(supervisor.config.server, "expose_raw_config", False):
        raise ApiError(
            403,
            "forbidden",
            "Raw config exposure is disabled (set server.expose_raw_config=true)",
        )
    return _current_dict(supervisor)


def _merge(base: dict, override: dict) -> dict:
    import copy

    result = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def _classify_llm_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "401" in msg or "invalid api key" in msg or "unauthorized" in msg:
        return "llm_auth_error"
    if "404" in msg or "model" in msg and "not found" in msg:
        return "llm_model_not_found"
    if "connection" in msg or "timeout" in msg or "unreachable" in msg:
        return "llm_unavailable"
    return "llm_unavailable"
