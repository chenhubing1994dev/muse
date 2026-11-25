' Hidden launcher for the Muse GitHub realtime backup watcher.
'
' WHY: Task Scheduler launching powershell.exe directly creates a brief conhost
' window even with -WindowStyle Hidden (the console is allocated by the task
' service, not by the script). WScript.Shell.Run(..., 0) avoids it.
'
' Muse-GitHubWatch.ps1 writes its own log to <repo>/.muse-sync/watch.log, so
' nothing is lost by hiding the console.
Option Explicit
Dim sh
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd.exe /c """ & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\muse-github-watch.cmd""", 0, True
Set sh = Nothing
