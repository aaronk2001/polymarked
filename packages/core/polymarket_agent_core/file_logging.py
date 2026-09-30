"""Rotating file log handler for the desktop supervisor.

Writes one JSON-line file per day under data/logs/. Keeps `LOG_RETENTION_DAYS`
days of files (default 14). Console logging is unchanged — this just adds a
parallel sink for postmortem debugging.
"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from .config import load_settings

_attached = False


def attach_file_handler(log_dir: Path | None = None, retention_days: int = 14) -> Path:
    """Attach a daily-rotating file handler to the root logger. Idempotent."""
    global _attached
    settings = load_settings()
    target_dir = log_dir or Path("data/logs")
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / "polymarked.log"

    if _attached:
        return log_path

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8",
        utc=True,
    )
    handler.setLevel(level)
    # structlog already serializes to JSON via the configured renderer; the file
    # handler just records the rendered string verbatim.
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    _attached = True
    return log_path
