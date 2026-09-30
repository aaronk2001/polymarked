$Root = Split-Path -Parent $PSScriptRoot
$Desktop = [Environment]::GetFolderPath("Desktop")
$LinkPath = Join-Path $Desktop "PolyMarked.lnk"

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($LinkPath)
$lnk.TargetPath = "powershell.exe"
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Root\scripts\start.ps1`""
$lnk.WorkingDirectory = $Root
$lnk.WindowStyle = 1
$lnk.Description = "PolyMarked - Polymarket intelligence agent"
$lnk.Save()
Write-Host "Created shortcut: $LinkPath"
