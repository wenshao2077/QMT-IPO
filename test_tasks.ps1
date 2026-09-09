$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
. (Join-Path $PSScriptRoot 'tasks.ps1')
$ipoDefs=@(New-IpoTaskDefinitions -Root 'C:/Users/77184/qmt-ipo-v3' -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name))
if($ipoDefs.Count -ne 4){throw 'Expected four task definitions'}
foreach($def in $ipoDefs){
    if($def.Task.Settings.Enabled){throw 'Prepared tasks must be disabled'}
    if($def.Task.Settings.MultipleInstances -ne 2){throw 'Expected IgnoreNew'}
    if($def.Task.Principal.LogonType -ne 3){throw 'Expected interactive owner session'}
    if($def.Task.Actions[0].Execute -notmatch 'wscript.exe$'){throw 'Expected hidden wrapper'}
}
$ipoCycle=($ipoDefs|Where-Object Name -eq 'QmtIPO3-Cycle').Task
if($ipoCycle.Triggers.Count -ne 3){throw 'Expected daily, final and logon triggers'}
if($ipoCycle.Triggers[0].Repetition.Interval -ne 'PT5M'){throw 'Expected five-minute retries'}
$ipoStart=([datetimeoffset]::Parse($ipoCycle.Triggers[0].StartBoundary)).ToOffset([timespan]::FromHours(8)).TimeOfDay
$ipoFinal=([datetimeoffset]::Parse($ipoCycle.Triggers[1].StartBoundary)).ToOffset([timespan]::FromHours(8)).TimeOfDay
if($ipoStart -ne [timespan]::Parse('09:35')){throw 'Expected after-open start in China time'}
if($ipoFinal -ne [timespan]::Parse('15:05')){throw 'Expected final reconciliation in China time'}
Write-Output 'TASK_DEFINITION_CHECKS_PASS (no registration, activation or execution)'
