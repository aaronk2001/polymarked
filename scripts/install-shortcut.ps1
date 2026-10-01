$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Desktop = [Environment]::GetFolderPath("Desktop")

# Single Desktop launcher. Prefers the built exe (scripts\build-exe.ps1);
# falls back to the hidden VBS -> uv launcher. Both show only the app window.
$Exe = Join-Path $Root "dist\PolyMarked\PolyMarked.exe"
$Lnk = Join-Path $Desktop "PolyMarked.lnk"
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($Lnk)
if (Test-Path $Exe) {
    $s.TargetPath = $Exe
    $s.Arguments = ""
    $s.IconLocation = "$Exe,0"
} else {
    $s.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
    $s.Arguments = '"' + (Join-Path $Root "scripts\PolyMarked.vbs") + '"'
}
$s.WorkingDirectory = $Root
$s.WindowStyle = 7
$s.Description = "PolyMarked - Polymarket copy-trade agent"
$s.Save()
Write-Host "Installed: $Lnk -> $($s.TargetPath)"

# Keep the Desktop clean: only ONE icon. The debug console launcher lives in
# scripts\PolyMarked.bat (run it manually from the repo when troubleshooting).
foreach ($stale in @("PolyMarked (debug console).bat", "PolyMarked.bat")) {
    $p = Join-Path $Desktop $stale
    if (Test-Path $p) { Remove-Item $p -Force; Write-Host "Removed old: $p" }
}
