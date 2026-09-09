# Installs/opens the requested passive GUI only. No live enable or pause operation.
param([string]$Root='C:/Users/77184/qmt-ipo-v3')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$ipoDesktop=[Environment]::GetFolderPath('DesktopDirectory')
$ipoConfigPath=Join-Path $Root 'config.json'
$ipoHash=(Get-FileHash -LiteralPath $ipoConfigPath -Algorithm SHA256).Hash
$ipoBefore=@(Get-ScheduledTask -TaskName 'QmtIPO3-*'|Select-Object TaskName,@{N='Enabled';E={$_.Settings.Enabled}})|ConvertTo-Json -Compress
$ipoPython=Join-Path $Root '.venv/Scripts/pythonw.exe'
$ipoPanel=Join-Path $Root 'code/panel.py'
foreach($file in @($ipoPython,$ipoPanel,(Join-Path $Root 'panel_tasks.ps1'))){if(-not(Test-Path -LiteralPath $file)){throw 'Panel files missing'}}
$ipoShell=New-Object -ComObject WScript.Shell
$ipoShortcut=Join-Path $ipoDesktop '自动打新控制台.lnk'
if(Test-Path -LiteralPath $ipoShortcut){throw 'Existing panel shortcut needs review'}
$link=$ipoShell.CreateShortcut($ipoShortcut)
$link.TargetPath=$ipoPython
$link.Arguments='-B "'+$ipoPanel+'" --root "'+$Root+'"'
$link.WorkingDirectory=$Root
$link.Description='图形控制台：查看状态，用按钮授权、启用或暂停每日自动打新。打开面板不下单。'
$link.IconLocation='C:/Windows/System32/shell32.dll,24'
$link.Save()
# Preserve only this task's old shortcuts in a recoverable folder; no other desktop files.
$archive=Join-Path $ipoDesktop '旧版打新入口（保留）'
$old=@('打新工具（只读预览）.lnk','打新工具（实盘手动启动）.lnk','自动打新V3—启用每日计划.lnk','自动打新V3—查看运行状态.lnk')
New-Item -ItemType Directory -Path $archive -Force|Out-Null
foreach($name in $old){
    $source=Join-Path $ipoDesktop $name
    $target=Join-Path $archive $name
    if(Test-Path -LiteralPath $source){
        if(Test-Path -LiteralPath $target){throw 'Archived shortcut already exists'}
        Move-Item -LiteralPath $source -Destination $target
    }
}
$name='QmtIPO-Panel-Open'
if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){throw 'Existing panel-open task needs review'}
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$action=New-ScheduledTaskAction -Execute $ipoPython -Argument ('-B "'+$ipoPanel+'" --root "'+$Root+'" --smoke-report "'+(Join-Path $Root 'runtime/panel-visible.json')+'"') -WorkingDirectory $Root
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([timespan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Description '手动打开图形控制台；无定时触发；打开只读，不授权不下单。'|Out-Null
$ipoAfter=@(Get-ScheduledTask -TaskName 'QmtIPO3-*'|Select-Object TaskName,@{N='Enabled';E={$_.Settings.Enabled}})|ConvertTo-Json -Compress
if($ipoBefore -cne $ipoAfter -or $ipoHash -ne (Get-FileHash -LiteralPath $ipoConfigPath -Algorithm SHA256).Hash){throw 'Installation unexpectedly changed daily execution settings'}
Start-ScheduledTask -TaskName $name
@{PanelShortcut=$ipoShortcut;OldShortcutsPreserved=$archive;GuiOpened=$true;DailyExecutionSettingsUnchanged=$true}|ConvertTo-Json
