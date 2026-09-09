# Stages a new version and DISABLED task definitions. Does not activate or run them.
param([string]$Root='C:/Users/77184/qmt-ipo-v3',[string]$PreviousRoot='C:/Users/77184/qmt-ipo-v2')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$ipoOriginal=Join-Path $PreviousRoot 'config.local.json'
$ipoOriginalHash=(Get-FileHash -LiteralPath $ipoOriginal -Algorithm SHA256).Hash
$ipoOriginalTasks=@(Get-ScheduledTask|Where-Object TaskName -like 'QmtIPO-*'|ForEach-Object{Export-ScheduledTask -TaskName $_.TaskName}) -join "`n"
$ipoTarget=Join-Path $Root 'config.json'
if(Test-Path -LiteralPath $ipoTarget){throw 'Existing V3 config must be reviewed, not overwritten'}
$ipoConfig=Get-Content -LiteralPath $ipoOriginal -Raw|ConvertFrom-Json
$ipoConfig.enable_execution=$false
$ipoConfig|Add-Member -NotePropertyName control_dir -NotePropertyValue (Join-Path $Root 'runtime') -Force
[IO.File]::WriteAllText($ipoTarget,($ipoConfig|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
$ipoSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls $ipoTarget /inheritance:r /grant:r "*${ipoSid}:F" '*S-1-5-18:F' | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root 'runtime') -Force | Out-Null
. (Join-Path $Root 'tasks.ps1')
$ipoUser=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$ipoDefs=@(New-IpoTaskDefinitions -Root $Root -User $ipoUser)
foreach($def in $ipoDefs){if(Get-ScheduledTask -TaskName $def.Name -ErrorAction SilentlyContinue){throw 'Existing V3 task must be reviewed'}}
foreach($def in $ipoDefs){Register-ScheduledTask -TaskName $def.Name -InputObject $def.Task|Out-Null}
$ipoDesktop=[Environment]::GetFolderPath('DesktopDirectory')
$ipoShell=New-Object -ComObject WScript.Shell
foreach($item in @(@{Name='自动打新V3—启用每日计划';File='启用每日自动打新.ps1'},@{Name='自动打新V3—查看运行状态';File='查看自动打新状态.ps1'})){
    $path=Join-Path $ipoDesktop ($item.Name+'.lnk')
    if(Test-Path -LiteralPath $path){throw 'Desktop shortcut exists'}
    $link=$ipoShell.CreateShortcut($path)
    $link.TargetPath=Join-Path $env:LOCALAPPDATA 'Microsoft/WindowsApps/pwsh.exe'
    $link.Arguments='-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $Root $item.File)+'"'
    $link.WorkingDirectory=$Root
    $link.Description='V3每日自动申购方案；启用前不会运行，启用后由Windows计划任务每日处理。'
    $link.IconLocation='C:/Windows/System32/shell32.dll,24'
    $link.Save()
}
$ipoAfterTasks=@(Get-ScheduledTask|Where-Object TaskName -like 'QmtIPO-*'|ForEach-Object{Export-ScheduledTask -TaskName $_.TaskName}) -join "`n"
if($ipoOriginalHash -ne (Get-FileHash -LiteralPath $ipoOriginal -Algorithm SHA256).Hash -or $ipoOriginalTasks -ne $ipoAfterTasks){throw 'Previous chain changed unexpectedly'}
$ipoStates=@(Get-ScheduledTask -TaskName 'QmtIPO3-*'|Select-Object TaskName,State)
if(@($ipoStates|Where-Object State -ne 'Disabled').Count){throw 'V3 tasks must remain disabled'}
@{Mode='staged_not_activated';PreviousUnchanged=$true;ExecutionEnabled=$false;Tasks=$ipoStates} | ConvertTo-Json -Depth 4
