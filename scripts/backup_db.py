r"""Nightly SQLite backup using the online backup API (WAL-safe).

Copies data/polymarked.db to data/backups/polymarked-YYYYMMDD-HHMMSS.db,
then prunes the directory to the most-recent N backups (default 14).

Designed for Windows Task Scheduler:
    schtasks /Create /SC DAILY /ST 04:00 /TN "PolyMarked Backup" ^
        /TR "powershell -ExecutionPolicy Bypass -Command \"cd C:\path\to\PolyMarked; uv run python scripts\backup_db.py\""
"""
from __future__ import annotations

import argparse
import contextlib
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


def _resolve_db_path(database_url: str | None) -> Path:
    """Pull the on-disk path from a SQLAlchemy URL.

    Handles the three SQLite URL forms used by SQLAlchemy:
        sqlite+aiosqlite:///./data/polymarked.db   -> relative path
        sqlite+aiosqlite:////absolute/foo.db        -> POSIX absolute
        sqlite+aiosqlite:///C:/data/foo.db          -> Windows absolute
    """
    url = database_url or os.environ.get(
        "DATABASE_URL", "sqlite+aiosqlite:///./data/polymarked.db"
    )
    if ":///" in url:
        _, _, raw = url.partition(":///")
    elif "://" in url:
        _, _, raw = url.partition("://")
    else:
        raw = url
    return Path(raw).resolve()


def backup(database_url: str | None, backup_dir: Path, retain: int) -> Path:
    src = _resolve_db_path(database_url)
    if not src.exists():
        raise FileNotFoundError(f"Source DB not found: {src}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dst = backup_dir / f"polymarked-{stamp}.db"

    # Use SQLite's online backup API; safe with active WAL writers.
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    # Prune oldest backups
    snaps = sorted(backup_dir.glob("polymarked-*.db"))
    while len(snaps) > retain:
        old = snaps.pop(0)
        with contextlib.suppress(OSError):
            old.unlink()

    return dst


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="backup_db", description=__doc__.splitlines()[0])
    p.add_argument("--database-url", default=None, help="Override DATABASE_URL")
    p.add_argument("--out", default="data/backups", help="Backup directory")
    p.add_argument("--retain", type=int, default=14, help="Keep N most-recent backups")
    args = p.parse_args(argv)

    try:
        out = backup(args.database_url, Path(args.out), args.retain)
    except Exception as e:
        print(f"backup_db: FAIL: {e}", file=sys.stderr)
        return 1
    print(f"backup_db: ok -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
