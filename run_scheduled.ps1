param([Parameter(Mandatory=$true)][ValidateSet('cycle','notify','monitor','backup')][string]$Action)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$ipoRoot=$PSScriptRoot
$ipoConfig=Join-Path $ipoRoot 'config.json'
$ipoPython=Join-Path $ipoRoot '.venv/Scripts/python.exe'
$ipoCode=Join-Path $ipoRoot 'code/app.py'
$ipoLogs=Join-Path $ipoRoot 'runtime/launcher-logs'
New-Item -ItemType Directory -Path $ipoLogs -Force | Out-Null
$ipoPrefix=Join-Path $ipoLogs ($Action+'-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
$ipoArguments=@('-B',('"'+$ipoCode+'"'),$Action,'--config',('"'+$ipoConfig+'"'),'--send')
if($Action -eq 'cycle'){$ipoArguments+='--live'}
$ipoProcess=Start-Process -FilePath $ipoPython -ArgumentList $ipoArguments -PassThru -WindowStyle Hidden -RedirectStandardOutput ($ipoPrefix+'.out.log') -RedirectStandardError ($ipoPrefix+'.err.log')
if(-not $ipoProcess.WaitForExit(180000)) {
    $ipoProcess.Kill($true)
    $ipoProcess.WaitForExit()
    exit 124
}
exit $ipoProcess.ExitCode
