"""Thin Ollama client wrapper. Falls back gracefully if daemon is unreachable. Phase 5."""
from .client import OllamaClient, narrate_red_flags, summarize_trade

__all__ = ["OllamaClient", "narrate_red_flags", "summarize_trade"]
