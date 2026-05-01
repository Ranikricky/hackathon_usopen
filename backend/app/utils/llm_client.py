"""
LLM client wrapper.
Uses the OpenAI-compatible chat API.
"""

import json
import re
from typing import Optional, Dict, Any, List
from openai import OpenAI

from ..config import Config


class LLMClient:
    """OpenAI-compatible LLM client."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY is not configured")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=90,
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
        
        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        # Some models include chain-of-thought style <think> blocks in content.
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content
    
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
