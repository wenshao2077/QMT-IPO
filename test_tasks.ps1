# Offline Windows definitions/quoting checks. NEVER register, enable or run a task.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'tasks.ps1')
$root=Join-Path $env:TEMP ([string][char]0x4e2d+[string][char]0x6587+' IPO Space')
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$defs=@(New-IpoTaskDefinitions -Root $root -User $user -StartDay ([datetime]'2026-09-14'))
if($defs.Count -ne 4){throw 'Expected four tasks'}
foreach($def in $defs){
    if($def.Task.Settings.Enabled){throw 'New tasks must be disabled'}
    $name=$def.Name
    $action=@{'QmtIPO3-Cycle'='cycle';'QmtIPO3-Notify'='notify';'QmtIPO3-Monitor'='monitor';'QmtIPO3-Backup'='backup'}[$name]
    Assert-IpoOwnedTask $def.Task $root $action
    if($def.Task.Actions[0].Execute -notlike '*pythonw.exe'){throw 'Hidden Python required'}
    foreach($trigger in $def.Task.Triggers){
        if($trigger.StartBoundary -and -not $trigger.StartBoundary.EndsWith('+08:00')){throw 'Exchange timezone missing'}
    }
}
$cycle=@($defs|Where-Object Name -eq 'QmtIPO3-Cycle')[0].Task
if($cycle.Triggers.Count -ne 3){throw 'Daily, final and logon triggers required'}
if($cycle.Triggers[0].StartBoundary -ne '2026-09-14T09:35:00+08:00'){throw 'Submit schedule changed'}
if($cycle.Triggers[1].StartBoundary -ne '2026-09-14T15:05:00+08:00'){throw 'Final reconciliation schedule changed'}
if($cycle.Triggers[0].Repetition.Interval -ne 'PT5M'){throw 'Polling interval changed'}
$bad=[pscustomobject]@{Actions=@([pscustomobject]@{Execute='cmd.exe';Arguments='bad';WorkingDirectory=$root})}
$rejected=$false
try{Assert-IpoOwnedTask $bad $root 'cycle'}catch{$rejected=$true}
if(-not $rejected){throw 'Unowned action accepted'}
$legacy=[pscustomobject]@{Actions=@([pscustomobject]@{
    Execute=(Join-Path $env:SystemRoot 'System32/wscript.exe');WorkingDirectory=$root;
    Arguments=('//B "'+(Join-Path $root 'hidden.vbs')+'" "'+(Join-Path $root 'run_scheduled.ps1')+'" cycle')})}
Assert-IpoOwnedTask $legacy $root 'cycle'
$script:count=0
foreach($path in Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1'){
    $tokens=$null;$errors=$null
    [void][Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($path.FullName,[Text.Encoding]::UTF8),[ref]$tokens,[ref]$errors)
    if($errors.Count){throw ('PowerShell syntax failed: '+$path.Name+': '+$errors[0].Message)}
    $script:count++
}
Write-Output ('WINDOWS_OFFLINE_CHECKS_PASS: four disabled task definitions, timezone, Unicode/spaces, strict ownership, '+$script:count+' parsed scripts; no registration or execution.')
