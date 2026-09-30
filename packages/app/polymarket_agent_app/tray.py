"""Windows tray icon. Runs pystray in a daemon thread alongside the asyncio supervisor."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

from polymarket_agent_core.config import load_settings


def _make_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (64, 64), color=(11, 13, 16))
    d = ImageDraw.Draw(img)
    d.ellipse((10, 10, 54, 54), fill=(30, 145, 100))
    d.text((24, 22), "P", fill=(230, 231, 234))
    return img


def _open_logs_folder() -> None:
    logs = Path("data/logs").resolve()
    logs.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(logs))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(logs)])
    else:
        subprocess.Popen(["xdg-open", str(logs)])


def _backup_now() -> None:
    """Fire scripts/backup_db.py without blocking the tray thread."""
    cmd = [sys.executable, str(Path("scripts/backup_db.py").resolve())]
    # Detached so the tray UI stays responsive
    subprocess.Popen(cmd, cwd=str(Path.cwd()))


def start_tray_thread(stop_callback: Callable[[], None]) -> threading.Thread:
    """Spawn a tray icon in a daemon thread. Returns the thread."""
    import pystray

    s = load_settings()
    url = f"http://{s.api_host}:{s.api_port}"

    def open_dashboard(_icon, _item) -> None:
        webbrowser.open(url)

    def open_health(_icon, _item) -> None:
        webbrowser.open(f"{url}/api/v1/health")

    def open_logs(_icon, _item) -> None:
        _open_logs_folder()

    def backup_now(_icon, _item) -> None:
        _backup_now()

    def quit_app(icon, _item) -> None:
        icon.stop()
        stop_callback()

    menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", open_dashboard, default=True),
        pystray.MenuItem("View /health", open_health),
        pystray.MenuItem("Open logs folder", open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"Mode: {s.trade_mode}", None, enabled=False),
        pystray.MenuItem("Backup DB now", backup_now),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("PolyMarked", _make_icon_image(), "PolyMarked", menu=menu)
    t = threading.Thread(target=icon.run, daemon=True, name="tray")
    t.start()
    return t
