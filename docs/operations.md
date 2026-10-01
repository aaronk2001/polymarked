# PolyMarked Operations

How to run PolyMarked unattended on a Windows desktop with restart-on-failure
and nightly backups. Everything below assumes an Administrator PowerShell.

## Logs

`core/logging.py` attaches a daily-rotating file handler at startup. Files
land at:

```
data/logs/polymarked.log              # current day
data/logs/polymarked.log.2026-05-03   # rotated daily, kept 14 days
```

Inspect health from the dashboard or:

```powershell
curl http://127.0.0.1:8765/api/v1/health | ConvertFrom-Json
```

A healthy response includes `"ok": true`, a small `db.latency_ms`, and a
`heartbeats.watcher_age_s` < `watch_poll_interval_seconds` * 3.

## Run on logon, restart on failure

The `make-shortcut.ps1` shortcut works for manual launch. For unattended
operation, register a Task Scheduler job that restarts on crash:

```powershell
$action  = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\path\to\PolyMarked\scripts\start.ps1" `
    -WorkingDirectory "C:\path\to\PolyMarked"

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "PolyMarked Supervisor" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "PolyMarked desktop agent (watcher + bot + dashboard)"
```

To stop:

```powershell
Stop-ScheduledTask -TaskName "PolyMarked Supervisor"
Unregister-ScheduledTask -TaskName "PolyMarked Supervisor" -Confirm:$false
```

## Nightly DB backup

```powershell
$action  = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -Command `"cd C:\path\to\PolyMarked; uv run python scripts\backup_db.py`"" `
    -WorkingDirectory "C:\path\to\PolyMarked"

$trigger = New-ScheduledTaskTrigger -Daily -At 4:00AM

Register-ScheduledTask `
    -TaskName "PolyMarked Nightly Backup" `
    -Action $action -Trigger $trigger `
    -Description "SQLite online backup of polymarked.db"
```

Backups land in `data/backups/polymarked-YYYYMMDD-HHMMSS.db`. The script keeps
the 14 most recent and deletes older ones.

To restore: stop the supervisor task, copy the chosen backup over
`data/polymarked.db`, restart.

## TRADE_MODE walkthrough

| Mode | What you should be doing |
|---|---|
| `off` | Brand-new install. Watch alerts in Telegram for a day. No fills anywhere. |
| `paper` | Default. Validate the strategy for a week+. Check `/balance` daily. |
| `live` | Only after a smoke test on Amoy AND `set_allowances.py` ran on mainnet AND proxy wallet funded with a deliberately small bankroll. |

Never flip `live` directly from `off`. Always sit in `paper` for at least a
week so any silent bug shows up against the paper ledger first.

## Kill switches

Telegram:

```
/panic        # disables auto_execute on every follow + globally pauses fills
/unpanic      # resumes paper fills only — live stays off until you flip auto_execute=true again
```

API (also cancels open CLOB orders when a key is configured):

```powershell
Invoke-RestMethod -Method POST http://127.0.0.1:8765/api/v1/panic
```

## Recovery from a crash loop

1. Stop the scheduled task (`Stop-ScheduledTask -TaskName "PolyMarked Supervisor"`).
2. Tail today's log: `Get-Content data\logs\polymarked.log -Tail 200 | Select-String error`.
3. If the DB is suspect, restore from the most recent backup.
4. Run the supervisor manually once (`uv run python -m polymarket_agent_app`) to see crashes in the console.
5. Re-enable the scheduled task.
