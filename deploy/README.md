# Running PolyMarked 24/7

The app has two entrypoints:

| Entrypoint | Command | Use |
|---|---|---|
| **Desktop** (window) | `uv run python -m polymarket_agent_app` | Day-to-day, opens a native window |
| **Headless** (no window) | `uv run python -m polymarket_agent_app.supervisor` | 24/7 background service |

Both run the same services: wallet **watcher**, **FastAPI dashboard**, **equity recorder**, and (if `TELEGRAM_BOT_TOKEN` is set) the Telegram bot. The dashboard is served at `http://<host>:8765`.

The live paper book is mark-to-market: open positions are valued against live Polymarket CLOB midpoints (cached, with the largest `MARK_MAX_TOKENS` positions marked live and the long tail held at last fill price). Equity is snapshotted every `EQUITY_SNAPSHOT_INTERVAL_SECONDS` into the `paper_equity` table for the dashboard's equity curve.

## Windows

`deploy/run-headless.ps1` runs the supervisor with auto-restart. To run it on boot:

1. Open **Task Scheduler → Create Task**.
2. **Trigger:** At startup (or At log on).
3. **Action:** Start a program →
   `powershell.exe` with arguments
   `-ExecutionPolicy Bypass -File "C:\path\to\PolyMarked\deploy\run-headless.ps1"`
4. **Settings:** check *Run whether user is logged on or not* and *If the task fails, restart every 1 minute*.

Dashboard: `http://127.0.0.1:8765`.

## Raspberry Pi / Linux

Use the systemd unit:

```bash
git clone <repo> /home/pi/PolyMarked && cd /home/pi/PolyMarked
cp .env.example .env          # fill in keys; set TRADE_MODE=paper
uv sync
sudo cp deploy/polymarked.service /etc/systemd/system/
# edit User=, WorkingDirectory=, and the API_HOST security note in the unit
sudo systemctl daemon-reload
sudo systemctl enable --now polymarked
journalctl -u polymarked -f
```

The unit sets `Restart=always`, so it survives crashes and reboots.

### Reaching the dashboard from another device

- **Default (`API_HOST=127.0.0.1`)** — local only. From your laptop:
  `ssh -L 8765:127.0.0.1:8765 pi@<pi-ip>` then open `http://localhost:8765`. This is the safe option.
- **LAN (`API_HOST=0.0.0.0`)** — open `http://<pi-ip>:8765` from any device on the network. ⚠️ The dashboard's mutate endpoints (follow / mode / panic) have **no auth**, so only do this on a trusted home LAN and never port-forward it to the internet.

## Config knobs (`.env`)

| Var | Default | Meaning |
|---|---|---|
| `PAPER_STARTING_BANKROLL_USD` | 10000 | Starting paper cash |
| `MARK_CACHE_TTL_SECONDS` | 30 | How long a live price is reused |
| `MARK_NEGATIVE_TTL_SECONDS` | 3600 | How long a "no live book" result is cached (resolved markets) |
| `MARK_MAX_TOKENS` | 60 | Largest N positions marked live; rest use last fill price |
| `EQUITY_SNAPSHOT_INTERVAL_SECONDS` | 60 | Equity-curve sampling cadence |
| `API_HOST` / `API_PORT` | 127.0.0.1 / 8765 | Dashboard bind address |
