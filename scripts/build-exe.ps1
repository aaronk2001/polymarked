# Builds dist\PolyMarked\PolyMarked.exe (one-folder, no console).
# The exe reads .env, alembic.ini and data\ from its working directory, so
# launch it with the repo root as the working directory (install-shortcut.ps1 does).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

uv run --with pyinstaller pyinstaller --noconfirm --clean --windowed `
    --name PolyMarked `
    --distpath dist --workpath build --specpath build `
    --add-data "$Root\packages\dashboard\index.html;packages\dashboard" `
    --collect-submodules polymarket_agent_core `
    --collect-submodules uvicorn `
    --collect-submodules alembic `
    --collect-submodules webview `
    --hidden-import aiosqlite `
    --hidden-import sqlalchemy.dialects.sqlite.aiosqlite `
    scripts\exe_entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built: $Root\dist\PolyMarked\PolyMarked.exe"
