"""Desktop entrypoint: native window (pywebview) + in-process service supervisor.

The FastAPI server runs locally on 127.0.0.1; the dashboard renders inside a
real desktop window. The supervisor (watcher + bot + API) runs on a daemon
thread; pywebview owns the main thread because its Windows backend requires it.
Closing the window quits.

No system tray: pywebview must be the sole GUI/message-pump owner in the
process. A second tray message loop (pystray) is both redundant here and a
known source of Windows GUI conflicts, so it is intentionally not started.

Every launch and any uncaught failure (Python or native) is recorded to
data/logs/crash.log so window crashes are diagnosable post-mortem.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import faulthandler
import os
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import httpx
from polymarket_agent_core.config import load_settings
from polymarket_agent_core.logging import configure_logging

_LOG_DIR = Path("data/logs")
_CRASH = _LOG_DIR / "crash.log"
_fault_fp = None  # keep the faulthandler file handle alive for process lifetime


def _ts() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _crash(msg: str) -> None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _CRASH.open("a", encoding="utf-8") as f:
            f.write(f"[{_ts()}] {msg}\n")
    except Exception:
        pass


def _install_crash_capture() -> None:
    global _fault_fp
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _fault_fp = (_LOG_DIR / "faulthandler.log").open("a", encoding="utf-8")
    _fault_fp.write(f"\n=== faulthandler armed {_ts()} ===\n")
    _fault_fp.flush()
    faulthandler.enable(file=_fault_fp, all_threads=True)

    def _hook(exc_type, exc, tb) -> None:
        _crash("UNCAUGHT (main):\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook

    def _thook(args) -> None:
        _crash(
            f"UNCAUGHT (thread {args.thread.name if args.thread else '?'}):\n"
            + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        )

    threading.excepthook = _thook


def _run_supervisor_thread() -> threading.Thread:
    from .supervisor import run_supervisor

    def _target() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_supervisor())
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        except Exception:
            _crash("SUPERVISOR THREAD DIED:\n" + traceback.format_exc())
            raise
        finally:
            loop.close()

    t = threading.Thread(target=_target, daemon=True, name="supervisor")
    t.start()
    return t


def _wait_until_ready(url: str, timeout: float = 45.0) -> bool:
    health = f"{url}/api/v1/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(health, timeout=2.0).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _migrate_if_frozen() -> None:
    """The .exe has no alembic CLI, so it upgrades the DB itself. The uv
    launchers already run `alembic upgrade head` before starting the app."""
    if not getattr(sys, "frozen", False):
        return
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), "head")


def main() -> None:
    _install_crash_capture()
    _crash("=== launch ===")
    _migrate_if_frozen()
    configure_logging()
    os.environ.setdefault("POLYMARKED_OPEN_BROWSER", "0")

    s = load_settings()
    url = f"http://{s.api_host}:{s.api_port}"

    sup_thread = _run_supervisor_thread()

    if _wait_until_ready(url):
        _crash("services ready")
    else:
        _crash("services NOT ready within 45s")
        print("\n*** PolyMarked services did not come up within 45s. "
              "See data/logs/polymarked.log. ***\n", flush=True)

    try:
        import webview
    except Exception as e:
        _crash(f"pywebview import failed: {e!r}")
        print(f"[window] pywebview unavailable ({e}); opening in browser instead.")
        with contextlib.suppress(Exception):
            webbrowser.open(url)
        with contextlib.suppress(KeyboardInterrupt):
            sup_thread.join()
        return

    webview.create_window(
        "PolyMarked",
        url,
        width=1320,
        height=880,
        min_size=(960, 620),
        background_color="#0b0d10",
    )
    _crash("window created; entering webview.start()")

    # Blocks the main thread until the window is closed. pywebview is the ONLY
    # GUI owner in this process (no tray). Closing == quit; the supervisor is a
    # daemon thread and exits with the process.
    try:
        webview.start()
    except Exception:
        _crash("webview.start() RAISED:\n" + traceback.format_exc())
        raise
    _crash("webview.start() returned (window closed) -- exiting")


if __name__ == "__main__":
    main()
