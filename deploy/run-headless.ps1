# PolyMarked 24/7 headless runner (Windows).
# Runs the supervisor (watcher + API + dashboard + equity recorder) with NO
# desktop window, auto-restarting if it crashes. Point Task Scheduler at this.
#
#   Open dashboard at:  http://127.0.0.1:8765
#
# To run on boot without a login session, create a Task Scheduler task:
#   - Trigger:  At startup
#   - Action:   powershell.exe -ExecutionPolicy Bypass -File "C:\path\to\PolyMarked\deploy\run-headless.ps1"
#   - Settings: "Run whether user is logged on or not", "Restart on failure"

$ErrorActionPreference = "Stop"
$proj = Split-Path -Parent $PSScriptRoot
Set-Location $proj

# Headless: never try to open a browser or webview.
$env:POLYMARKED_OPEN_BROWSER = "0"

while ($true) {
    Write-Host "[$(Get-Date -Format o)] starting PolyMarked supervisor..."
    try {
        uv run python -m polymarket_agent_app.supervisor
    } catch {
        Write-Host "[$(Get-Date -Format o)] supervisor exited: $_"
    }
    Write-Host "[$(Get-Date -Format o)] restarting in 5s..."
    Start-Sleep -Seconds 5
}
