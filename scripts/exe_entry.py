"""PyInstaller entry point. The package __main__ uses relative imports, so it can't be the script itself."""
import sys
from pathlib import Path

# A --windowed build has no console: stdout/stderr are None and uvicorn's
# logging setup fails on them. Send them to a file instead.
if sys.stdout is None or sys.stderr is None:
    Path("data/logs").mkdir(parents=True, exist_ok=True)
    sys.stdout = sys.stderr = open("data/logs/exe-console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115

from polymarket_agent_app.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
