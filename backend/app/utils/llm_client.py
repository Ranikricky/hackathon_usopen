"""
LLM client wrapper.
Uses the OpenAI-compatible chat API.
"""

import json
import os
import re
import subprocess
import tempfile
from typing import Optional, Dict, Any, List
from openai import APIConnectionError, OpenAI

from ..config import Config


class LLMClient:
    """OpenAI-compatible LLM client."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME
        self.timeout = timeout or Config.LLM_TIMEOUT_SECONDS
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY is not configured")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def _normalize_temperature(self, temperature: float) -> float:
        """
        Normalize provider-specific temperature constraints.

        Some Kimi/Moonshot models only accept temperature=1.
        """
        base = (self.base_url or "").lower()
        if "moonshot.ai" in base:
            return 1.0
        return temperature
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        Send a chat request.
        
        Args:
            messages: Message list.
            temperature: Sampling temperature.
            max_tokens: Max token count.
            response_format: Optional response format such as JSON mode.
            
        Returns:
            Model response text.
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self._normalize_temperature(temperature),
            "max_tokens": max_tokens,
        }
        
        if response_format:
            kwargs["response_format"] = response_format
        
        try:
            response = self.client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
        except APIConnectionError:
            if not Config.LLM_CURL_FALLBACK_ENABLED:
                raise
            content = self._chat_via_curl(kwargs)
        # Some models include chain-of-thought style <think> blocks in content.
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content

    def _chat_via_curl(self, payload: Dict[str, Any]) -> str:
        """Fallback for local environments where Python DNS/httpx is blocked.

        The API remains OpenAI-compatible; this only changes the transport. It
        is intentionally used after SDK connection failures, not as the primary
        path, so production behavior stays normal when Python networking works.
        """
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        body = json.dumps(payload)
        config_text = "\n".join([
            "silent",
            "show-error",
            f"max-time = {max(1, int(self.timeout))}",
            f"url = \"{endpoint}\"",
            "request = POST",
            "header = \"Content-Type: application/json\"",
            f"header = \"Authorization: Bearer {self.api_key}\"",
            f"data = {json.dumps(body)}",
            "",
        ])
        config_path = None
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, prefix="hxl_curl_", suffix=".conf") as handle:
                config_path = handle.name
                handle.write(config_text)
            os.chmod(config_path, 0o600)
            result = subprocess.run(
                ["curl", "--config", config_path],
                check=False,
                text=True,
                capture_output=True,
                timeout=self.timeout + 5,
            )
        finally:
            if config_path:
                try:
                    os.remove(config_path)
                except OSError:
                    pass

        if result.returncode != 0:
            raise RuntimeError(f"LLM curl fallback failed: {result.stderr.strip() or result.stdout.strip()}")

        try:
            parsed = json.loads(result.stdout)
            if "error" in parsed:
                raise RuntimeError(f"LLM provider error: {parsed['error']}")
            return parsed["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"LLM curl fallback returned unexpected response: {result.stdout[:500]}") from exc
    
    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        Send a chat request and parse the response as JSON.
        
        Args:
            messages: Message list.
            temperature: Sampling temperature.
            max_tokens: Max token count.
            
        Returns:
            Parsed JSON object.
        """
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        # Remove markdown fences if the model ignores JSON mode.
        cleaned_response = response.strip()
        cleaned_response = re.sub(r'^```(?:json)?\s*\n?', '', cleaned_response, flags=re.IGNORECASE)
        cleaned_response = re.sub(r'\n?```\s*$', '', cleaned_response)
        cleaned_response = cleaned_response.strip()

        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError:
            raise ValueError(f"LLM returned invalid JSON: {cleaned_response}")
