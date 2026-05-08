"""
GitHub-backed durable JSON artifact store.

This is intentionally a small key-value store over the GitHub Contents API. It
is slower than a database, but it is free-friendly and good enough for small
Horizon XL project/graph snapshots.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from ..config import Config

logger = logging.getLogger(__name__)


class GitJsonStore:
    """Small JSON store backed by files in a GitHub repository branch."""

    @classmethod
    def enabled(cls) -> bool:
        return bool(Config.GIT_STORE_ENABLED and Config.GIT_STORE_REPO and Config.GIT_STORE_TOKEN)

    @classmethod
    def _headers(cls) -> Dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {Config.GIT_STORE_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }

    @classmethod
    def _api_url(cls, path: str) -> str:
        return f"https://api.github.com/{path.lstrip('/')}"

    @classmethod
    def _request(cls, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        if not cls.enabled():
            return None
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            cls._api_url(path),
            data=data,
            headers=cls._headers(),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8")
                if not body:
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            detail = exc.read().decode("utf-8", errors="replace")
            logger.warning("Git store request failed: %s %s: %s", method, path, detail)
            return None
        except Exception as exc:
            logger.warning("Git store request failed: %s %s: %s", method, path, exc)
            return None

    @classmethod
    def _quote_path(cls, path: str) -> str:
        return "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))

    @classmethod
    def _path_for_key(cls, key: str) -> str:
        parts = [part for part in key.split(":") if part]
        safe_parts = [urllib.parse.quote(part, safe="") for part in parts]
        return "/".join([Config.GIT_STORE_BASE_PATH.strip("/"), *safe_parts]) + ".json"

    @classmethod
    def _contents_path(cls, file_path: str) -> str:
        return (
            f"repos/{Config.GIT_STORE_REPO}/contents/{cls._quote_path(file_path)}"
            f"?ref={urllib.parse.quote(Config.GIT_STORE_BRANCH, safe='')}"
        )

    @classmethod
    def _get_file(cls, key: str) -> Optional[Dict[str, Any]]:
        path = cls._path_for_key(key)
        return cls._request("GET", cls._contents_path(path))

    @classmethod
    def get_json(cls, key: str) -> Optional[Any]:
        file_info = cls._get_file(key)
        if not isinstance(file_info, dict) or not file_info.get("content"):
            return None
        try:
            raw = base64.b64decode(file_info["content"]).decode("utf-8")
            return json.loads(raw)
        except Exception as exc:
            logger.warning("Failed to decode Git store key %s: %s", key, exc)
            return None

    @classmethod
    def set_json(cls, key: str, value: Any) -> bool:
        if not cls.enabled():
            return False
        path = cls._path_for_key(key)
        existing = cls._get_file(key)
        payload = {
            "message": f"Update Horizon XL artifact {key}",
            "content": base64.b64encode(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii"),
            "branch": Config.GIT_STORE_BRANCH,
        }
        if isinstance(existing, dict) and existing.get("sha"):
            payload["sha"] = existing["sha"]
        result = cls._request(
            "PUT",
            f"repos/{Config.GIT_STORE_REPO}/contents/{cls._quote_path(path)}",
            payload,
        )
        return isinstance(result, dict)

    @classmethod
    def delete(cls, key: str) -> bool:
        existing = cls._get_file(key)
        if not isinstance(existing, dict) or not existing.get("sha"):
            return False
        path = cls._path_for_key(key)
        payload = {
            "message": f"Delete Horizon XL artifact {key}",
            "sha": existing["sha"],
            "branch": Config.GIT_STORE_BRANCH,
        }
        result = cls._request(
            "DELETE",
            f"repos/{Config.GIT_STORE_REPO}/contents/{cls._quote_path(path)}",
            payload,
        )
        return isinstance(result, dict)

    @classmethod
    def _branch_tree_sha(cls) -> Optional[str]:
        ref_path = (
            f"repos/{Config.GIT_STORE_REPO}/git/ref/heads/"
            f"{urllib.parse.quote(Config.GIT_STORE_BRANCH, safe='/')}"
        )
        ref = cls._request("GET", ref_path)
        if isinstance(ref, dict):
            return ((ref.get("object") or {}).get("sha"))
        return None

    @classmethod
    def list_json(cls, prefix: str, limit: int = 100) -> List[Any]:
        if not cls.enabled():
            return []
        tree_sha = cls._branch_tree_sha()
        if not tree_sha:
            return []
        tree = cls._request(
            "GET",
            f"repos/{Config.GIT_STORE_REPO}/git/trees/{tree_sha}?recursive=1",
        )
        if not isinstance(tree, dict):
            return []
        prefix_path = cls._path_for_key(prefix).removesuffix(".json")
        values: List[Any] = []
        for item in tree.get("tree", []):
            path = item.get("path", "")
            if item.get("type") != "blob" or not path.startswith(prefix_path) or not path.endswith(".json"):
                continue
            key_parts = path.removeprefix(Config.GIT_STORE_BASE_PATH.strip("/") + "/").removesuffix(".json").split("/")
            key = ":".join(urllib.parse.unquote(part) for part in key_parts)
            value = cls.get_json(key)
            if value is not None:
                values.append(value)
            if len(values) >= limit:
                break
        return values
