# Passive desktop shortcut only; never opens/enables a task or sends a message.
param([Parameter(Mandatory=$true)][string]$Root)
$ErrorActionPreference='Stop'
$Root=[IO.Path]::GetFullPath($Root)
if($Root.Contains('"')){throw 'Unsupported root quoting characters'}
$pythonw=Join-Path $Root '.venv/Scripts/pythonw.exe'
if(-not (Test-Path -LiteralPath $pythonw)){throw 'Install the isolated Python environment first'}
. (Join-Path $PSScriptRoot 'task_identity.ps1')
$prefix=Get-IpoTaskPrefix $Root
$label=if($prefix -eq 'QmtIPO3'){'QMT IPO Console'}else{'QMT IPO '+$prefix}
$linkPath=Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) ($label+'.lnk')
$shell=New-Object -ComObject WScript.Shell
if(Test-Path -LiteralPath $linkPath){
    $previous=$shell.CreateShortcut($linkPath)
    if([IO.Path]::GetFullPath($previous.TargetPath) -ne [IO.Path]::GetFullPath($pythonw)){throw 'Shortcut belongs to a different installation'}
}
$link=$shell.CreateShortcut($linkPath)
$link.TargetPath=$pythonw
$link.Arguments='-B "'+(Join-Path $Root 'code/panel.py')+'" --root "'+$Root+'"'
$link.WorkingDirectory=$Root
$link.Description='QMT IPO: read-only until explicit user authorization'
$link.IconLocation=(Join-Path $env:SystemRoot 'System32/shell32.dll')+',24'
$link.Save()
