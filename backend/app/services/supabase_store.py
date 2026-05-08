"""
Minimal Supabase-backed durable JSON store.

This keeps Horizon XL independent from Zep for persistence. It intentionally
uses Supabase's PostgREST API via the Python standard library so deployment does
not require adding another package.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from ..config import Config

logger = logging.getLogger(__name__)


class SupabaseStore:
    """Small key-value JSON store backed by a Supabase table."""

    @classmethod
    def _api_key(cls) -> Optional[str]:
        return Config.SUPABASE_SERVICE_ROLE_KEY or Config.SUPABASE_ANON_KEY

    @classmethod
    def enabled(cls) -> bool:
        return bool(Config.SUPABASE_ENABLED and Config.SUPABASE_URL and cls._api_key())

    @classmethod
    def _base_url(cls) -> str:
        return Config.SUPABASE_URL.rstrip("/")

    @classmethod
    def _headers(cls, prefer: Optional[str] = None) -> Dict[str, str]:
        api_key = cls._api_key() or ""
        headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    @classmethod
    def _request(
        cls,
        method: str,
        path: str,
        payload: Optional[Any] = None,
        prefer: Optional[str] = None,
    ) -> Optional[Any]:
        if not cls.enabled():
            return None

        url = f"{cls._base_url()}/rest/v1/{path.lstrip('/')}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers=cls._headers(prefer=prefer),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8")
                if not body:
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            logger.warning("Supabase request failed: %s %s: %s", method, url, detail)
            return None
        except Exception as exc:
            logger.warning("Supabase request failed: %s %s: %s", method, url, exc)
            return None

    @classmethod
    def set_json(cls, key: str, value: Any) -> bool:
        if not cls.enabled():
            return False
        payload = [{"key": key, "value": value}]
        result = cls._request(
            "POST",
            Config.SUPABASE_TABLE,
            payload=payload,
            prefer="resolution=merge-duplicates,return=minimal",
        )
        return result is not None or cls.enabled()

    @classmethod
    def get_json(cls, key: str) -> Optional[Any]:
        if not cls.enabled():
            return None
        quoted = urllib.parse.quote(key, safe="")
        path = f"{Config.SUPABASE_TABLE}?key=eq.{quoted}&select=value&limit=1"
        rows = cls._request("GET", path)
        if isinstance(rows, list) and rows:
            return rows[0].get("value")
        return None

    @classmethod
    def delete(cls, key: str) -> bool:
        if not cls.enabled():
            return False
        quoted = urllib.parse.quote(key, safe="")
        cls._request("DELETE", f"{Config.SUPABASE_TABLE}?key=eq.{quoted}")
        return True

    @classmethod
    def list_json(cls, prefix: str, limit: int = 100) -> List[Any]:
        if not cls.enabled():
            return []
        quoted_prefix = urllib.parse.quote(f"{prefix}%", safe="")
        path = (
            f"{Config.SUPABASE_TABLE}?key=like.{quoted_prefix}"
            f"&select=key,value&order=updated_at.desc&limit={int(limit)}"
        )
        rows = cls._request("GET", path)
        if not isinstance(rows, list):
            return []
        return [row.get("value") for row in rows if isinstance(row, dict) and row.get("value") is not None]
