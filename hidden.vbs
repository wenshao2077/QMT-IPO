Option Explicit
Dim shell, fso, scriptPath, action, powershell, command
If WScript.Arguments.Count <> 2 Then WScript.Quit 2
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptPath = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "run_scheduled.ps1")
If LCase(fso.GetAbsolutePathName(WScript.Arguments(0))) <> LCase(scriptPath) Then WScript.Quit 2
action = WScript.Arguments(1)
If action <> "cycle" And action <> "notify" And action <> "monitor" And action <> "backup" Then WScript.Quit 2
powershell = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
command = """" & powershell & """ -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & scriptPath & """ -Action " & action
WScript.Quit shell.Run(command, 0, True)
