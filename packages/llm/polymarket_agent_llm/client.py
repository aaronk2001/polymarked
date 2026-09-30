"""Local Ollama HTTP wrapper. Used for narration only - never on the trade hot path."""
from __future__ import annotations

from typing import Any

import httpx
import structlog
from polymarket_agent_core.config import load_settings

log = structlog.get_logger(__name__)


class OllamaClient:
    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float = 30.0) -> None:
        s = load_settings()
        self._base = (base_url or s.llm_base_url).rstrip("/")
        self._model = model or s.llm_model
        self._timeout = timeout
        self._enabled = s.llm_enabled

    async def generate(self, prompt: str, *, system: str | None = None, max_tokens: int = 200) -> str | None:
        if not self._enabled:
            return None
        body: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.4},
        }
        if system:
            body["system"] = system
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post(f"{self._base}/api/generate", json=body)
            if r.status_code != 200:
                log.warning("llm.bad_status", status=r.status_code)
                return None
            return (r.json().get("response") or "").strip() or None
        except Exception as e:
            log.warning("llm.unreachable", error=str(e))
            return None


async def narrate_red_flags(flags: list[str], score: float, tier: str) -> str | None:
    if not flags:
        return None
    prompt = (
        f"This Polymarket trader scored {score:.0f}/100 ({tier}). "
        f"Red flags raised: {', '.join(flags)}. "
        "Explain in two short sentences why a copy-trader should be cautious."
    )
    return await OllamaClient().generate(prompt, system="You write terse risk callouts. Two sentences max.")


async def summarize_trade(title: str, side: str, size: float, price: float) -> str | None:
    prompt = (
        f"A trader just placed a {side} order of {size:.0f} on '{title}' at price {price:.2f}. "
        "Summarize what they're betting on in one sentence."
    )
    return await OllamaClient().generate(prompt, system="You write one-sentence trade summaries.")
