"""
Provider-neutral durable JSON store.

GitHub storage is the free-friendly artifact fallback; local disk remains the
last layer inside ProjectManager as a temporary cache.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .git_json_store import GitJsonStore


class DurableStore:
    """Fan-out/fallback JSON store for small Horizon XL artifacts."""

    providers = (GitJsonStore,)

    @classmethod
    def enabled(cls) -> bool:
        return any(provider.enabled() for provider in cls.providers)

    @classmethod
    def provider_status(cls) -> dict:
        return {
            "git": GitJsonStore.enabled(),
        }

    @classmethod
    def set_json(cls, key: str, value: Any) -> bool:
        ok = False
        for provider in cls.providers:
            if provider.enabled():
                ok = provider.set_json(key, value) or ok
        return ok

    @classmethod
    def get_json(cls, key: str) -> Optional[Any]:
        for provider in cls.providers:
            if provider.enabled():
                value = provider.get_json(key)
                if value is not None:
                    return value
        return None

    @classmethod
    def delete(cls, key: str) -> bool:
        ok = False
        for provider in cls.providers:
            if provider.enabled():
                ok = provider.delete(key) or ok
        return ok

    @classmethod
    def list_json(cls, prefix: str, limit: int = 100) -> List[Any]:
        seen = set()
        values: List[Any] = []
        for provider in cls.providers:
            if not provider.enabled():
                continue
            for value in provider.list_json(prefix, limit=limit):
                identity = None
                if isinstance(value, dict):
                    identity = value.get("project_id") or value.get("simulation_id") or repr(value)[:200]
                else:
                    identity = repr(value)[:200]
                if identity in seen:
                    continue
                seen.add(identity)
                values.append(value)
                if len(values) >= limit:
                    return values
        return values
