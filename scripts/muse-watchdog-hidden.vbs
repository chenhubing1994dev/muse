' Hidden launcher for the Muse half-hourly watchdog.
'
' WHY: muse-watchdog.cmd is a console script. Task Scheduler launching it
' directly flashes a cmd window every time the task fires (every 30 minutes).
' WScript.Shell.Run(..., 0) starts cmd with a hidden console, and the py.exe
' children inherit that hidden console instead of creating their own.
Option Explicit
Dim sh
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\AI AGENT\Muse"
sh.Run "cmd.exe /c ""D:\AI AGENT\Muse\scripts\muse-watchdog.cmd""", 0, True
Set sh = Nothing
