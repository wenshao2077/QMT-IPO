Option Explicit
Dim shell, arguments, script, action, command
Set shell=CreateObject("WScript.Shell")
Set arguments=WScript.Arguments
If arguments.Count <> 2 Then WScript.Quit 64
script=arguments(0)
action=arguments(1)
If InStr(script,Chr(34))>0 Then WScript.Quit 64
Select Case action
Case "cycle", "notify", "monitor", "backup"
Case Else
    WScript.Quit 64
End Select
command=Chr(34) & shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Microsoft\WindowsApps\pwsh.exe" & Chr(34) & " -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & script & Chr(34) & " -Action " & action
WScript.Quit shell.Run(command,0,True)
