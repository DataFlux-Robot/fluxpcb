"""Provider-agnostic LLM adapter.

Reads FLUXNT_LLM_* env vars. When no provider is configured, stages fall back
to deterministic template generation (offline mode) — the pipeline stays
runnable and auditable without any API key.
"""
from __future__ import annotations

import json
import os
from typing import Optional


class LLMAdapter:
    def __init__(self):
        self.provider = os.environ.get("FLUXNT_LLM_PROVIDER", "none").lower()
        self.model = os.environ.get("FLUXNT_LLM_MODEL", "")
        self.api_key_env = os.environ.get("FLUXNT_LLM_API_KEY_ENV", "")

    @property
    def online(self) -> bool:
        if self.provider == "none":
            return False
        if self.api_key_env and not os.environ.get(self.api_key_env):
            return False
        return True

    def complete(self, system: str, prompt: str, schema: Optional[dict] = None) -> dict:
        """Return {"ok": bool, "text": str, "error": str}. Never raises."""
        if not self.online:
            return {"ok": False, "text": "", "error": "LLM offline(未配置provider)"}
        try:
            if self.provider == "openai":
                return self._openai(system, prompt, schema)
            if self.provider == "anthropic":
                return self._anthropic(system, prompt)
            return {"ok": False, "text": "", "error": f"未知provider: {self.provider}"}
        except Exception as e:
            return {"ok": False, "text": "", "error": str(e)}

    def _openai(self, system: str, prompt: str, schema: Optional[dict]) -> dict:
        import urllib.request
        key = os.environ.get(self.api_key_env or "OPENAI_API_KEY", "")
        body = {
            "model": self.model or "gpt-4o-mini",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
        }
        if schema:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        return {"ok": True, "text": data["choices"][0]["message"]["content"], "error": ""}

    def _anthropic(self, system: str, prompt: str) -> dict:
        import urllib.request
        key = os.environ.get(self.api_key_env or "ANTHROPIC_API_KEY", "")
        body = {"model": self.model or "claude-sonnet-4-20250514", "max_tokens": 8000,
                "system": system, "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        return {"ok": True, "text": "".join(b.get("text", "") for b in data["content"]),
                "error": ""}


llm = LLMAdapter()
