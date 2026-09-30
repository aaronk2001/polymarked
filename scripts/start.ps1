$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null

$env:POLYMARKED_OPEN_BROWSER = "1"

uv run alembic upgrade head
uv run python -m polymarket_agent_app
