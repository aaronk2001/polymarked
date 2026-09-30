$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Desktop = [Environment]::GetFolderPath("Desktop")

# Single Desktop launcher: hidden VBS -> only the app window shows, no console.
$Vbs = Join-Path $Root "scripts\PolyMarked.vbs"
$Lnk = Join-Path $Desktop "PolyMarked.lnk"
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($Lnk)
$s.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
$s.Arguments = '"' + $Vbs + '"'
$s.WorkingDirectory = $Root
$s.WindowStyle = 7
$s.Description = "PolyMarked - Polymarket copy-trade agent"
$s.Save()
Write-Host "Installed: $Lnk"

# Keep the Desktop clean: only ONE icon. The debug console launcher lives in
# scripts\PolyMarked.bat (run it manually from the repo when troubleshooting).
foreach ($stale in @("PolyMarked (debug console).bat", "PolyMarked.bat")) {
    $p = Join-Path $Desktop $stale
    if (Test-Path $p) { Remove-Item $p -Force; Write-Host "Removed old: $p" }
}
