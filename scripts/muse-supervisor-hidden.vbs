' Hidden launcher for the Muse local model supervisor.
'
' WHY: Task Scheduler launching a console program directly creates a visible
' console window (LogonType=Interactive). WScript.Shell.Run(..., 0) starts it
' with a hidden console instead, so the black window no longer sits on the
' desktop for the whole session.
'
' The supervisor redirects its own stdout/stderr to
'   <log_dir>/muse-model-supervisor.stdout.log
'   <log_dir>/muse-model-supervisor.stderr.log
' so hiding the console loses nothing.
Option Explicit
Dim sh
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd.exe /c ""D:\AI AGENT\Muse\scripts\muse-supervisor.cmd""", 0, True
Set sh = Nothing
