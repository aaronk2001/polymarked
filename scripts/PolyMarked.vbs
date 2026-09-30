' PolyMarked hidden launcher: no console window -- only the app window shows.
' Migrations + app run in a hidden cmd; all output goes to data\logs\launcher.log.
' If the app exits with an error, a message box points to the log.
Option Explicit
Dim sh, fso, root, uv, logf, q, inner, cmd, rc
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
uv   = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.local\bin\uv.exe"

If Not fso.FolderExists(root & "\data")      Then fso.CreateFolder(root & "\data")
If Not fso.FolderExists(root & "\data\logs") Then fso.CreateFolder(root & "\data\logs")
logf = root & "\data\logs\launcher.log"

sh.CurrentDirectory = root
sh.Environment("PROCESS")("POLYMARKED_OPEN_BROWSER") = "0"

q = Chr(34)
inner = q & uv & q & " run alembic upgrade head > " & q & logf & q & " 2>&1" & _
        " && " & _
        q & uv & q & " run python -m polymarket_agent_app >> " & q & logf & q & " 2>&1"
cmd = "cmd /c " & q & inner & q

' 0 = hidden window, True = wait until the app window is closed
rc = sh.Run(cmd, 0, True)

If rc <> 0 Then
  sh.Popup "PolyMarked exited with an error (code " & rc & ")." & vbCrLf & _
           "See the log: " & logf, 15, "PolyMarked", 48
End If
