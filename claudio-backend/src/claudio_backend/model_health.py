"""In-memory availability state for configured model profiles."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ModelHealthRegistry:
    """Track successful and failed model probes without retaining secrets."""

    def __init__(self) -> None:
        self._profiles: dict[str, dict[str, Any]] = {}

    def mark(
        self,
        profile: str,
        *,
        healthy: bool,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        self._profiles[profile] = {
            "status": "healthy" if healthy else "unavailable",
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "latency_ms": latency_ms,
            "error_code": None if healthy else (error_code or "llm_unavailable"),
        }

    def is_unavailable(self, profile: str) -> bool:
        return self._profiles.get(profile, {}).get("status") == "unavailable"

    def snapshot(self, config) -> dict[str, dict[str, Any]]:
        profiles = {"llm": {"model": config.llm.model, "label": "Primary (llm)"}}
        profiles.update(
            {
                name: {"model": profile.model, "label": profile.label or name}
                for name, profile in config.model_profiles.items()
            }
        )
        return {
            name: {
                **details,
                **self._profiles.get(
                    name,
                    {
                        "status": "unknown",
                        "last_checked": None,
                        "latency_ms": None,
                        "error_code": None,
                    },
                ),
            }
            for name, details in profiles.items()
        }


def profile_name_for_role(config, role: str) -> str:
    """Return the configured profile key, or ``llm`` for the implicit profile."""
    return getattr(config.roles, role, None) or "llm"


__all__ = ["ModelHealthRegistry", "profile_name_for_role"]
