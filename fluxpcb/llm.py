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
        # openai-compatible endpoints (GLM/bigmodel, vLLM, ...) — W-round
        self.base_url = os.environ.get(
            "FLUXNT_LLM_BASEURL", "https://api.openai.com/v1").rstrip("/")

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
        # ollama 本地模型专属字段(关思考+输出上限) — 仅本地端点携带,
        # 远程 OpenAI 兼容端点会因未知字段报 400(实测)
        if ("127.0.0.1" in self.base_url or "localhost" in self.base_url):
            body["options"] = {"num_predict": 512}
            body["chat_template_kwargs"] = {"enable_thinking": False}
            body["think"] = False
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        _tmo = int(os.environ.get("FLUXNT_LLM_TIMEOUT", "300"))
        with urllib.request.urlopen(req, timeout=_tmo) as r:
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
        _tmo = int(os.environ.get("FLUXNT_LLM_TIMEOUT", "300"))
        with urllib.request.urlopen(req, timeout=_tmo) as r:
            data = json.loads(r.read())
        return {"ok": True, "text": "".join(b.get("text", "") for b in data["content"]),
                "error": ""}


llm = LLMAdapter()
