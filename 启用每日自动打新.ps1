# OWNER-OPERATED ACTIVATION. Never run this script as an agent or a scheduled task.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$ipoLiveTaskEnabled=$false
$ipoPrior=@()
$ipoOriginalConfigText=$null
try {
    $ipoRoot=$PSScriptRoot
    $ipoPath=Join-Path $ipoRoot 'config.json'
    $ipoOriginalConfigText=Get-Content -LiteralPath $ipoPath -Raw
    $ipoConfig=$ipoOriginalConfigText | ConvertFrom-Json
    Write-Host '一次性启用：每天自动申购新股、新债' -ForegroundColor Yellow
    Write-Host '启用后Windows于09:35开始自动申购；后续周期只处理未完成项目，15:05最终核对。'
    Write-Host '将停用旧的五个QmtIPO只读任务，切换到四个QmtIPO3任务。保留旧代码、去重账本和外部监控。'
    Write-Host '这是持续的真实证券申购授权；不包含卖出、资金划转或缴款，不需要每天再次确认。'
    $ipoAnswer=Read-Host '请本人输入“启用每日自动申购”确认；其他输入取消'
    if($ipoAnswer -cne '启用每日自动申购'){Write-Host '已取消，未改变计划任务。';exit 0}
    . (Join-Path $ipoRoot 'tasks.ps1')
    $ipoUser=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $ipoDefinitions=@(New-IpoTaskDefinitions -Root $ipoRoot -User $ipoUser)
    foreach($definition in $ipoDefinitions){
        if(Get-ScheduledTask -TaskName $definition.Name -ErrorAction SilentlyContinue){
            Disable-ScheduledTask -TaskName $definition.Name | Out-Null
        }
        Register-ScheduledTask -TaskName $definition.Name -InputObject $definition.Task -Force | Out-Null
    }
    $ipoPrior=@(Get-ScheduledTask | Where-Object TaskName -in @('QmtIPO-Preview','QmtIPO-Reconcile','QmtIPO-Notify','QmtIPO-Health','QmtIPO-Backup'))
    $ipoBackup=Join-Path $ipoRoot ('runtime/activation-backup-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $ipoBackup -Force | Out-Null
    foreach($task in $ipoPrior){
        [IO.File]::WriteAllText((Join-Path $ipoBackup ($task.TaskName+'.xml')),(Export-ScheduledTask -TaskName $task.TaskName),[Text.UTF8Encoding]::new($false))
    }
    Copy-Item -LiteralPath $ipoPath -Destination (Join-Path $ipoBackup 'config.before.json')
    $ipoConfig.enable_execution=$true
    $ipoTemporary=$ipoPath+'.new'
    [IO.File]::WriteAllText($ipoTemporary,($ipoConfig|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $ipoTemporary -Destination $ipoPath -Force
    foreach($task in $ipoPrior){Disable-ScheduledTask -TaskName $task.TaskName | Out-Null}
    foreach($name in @('QmtIPO3-Notify','QmtIPO3-Monitor','QmtIPO3-Backup','QmtIPO3-Cycle')){
        Enable-ScheduledTask -TaskName $name | Out-Null
        if($name -eq 'QmtIPO3-Cycle'){$ipoLiveTaskEnabled=$true}
    }
    Write-Host '已启用每日自动打新。保持Windows当前用户及miniQMT登录；无需每天再点击。' -ForegroundColor Green
    Get-ScheduledTask -TaskName 'QmtIPO3-*' | Get-ScheduledTaskInfo | Format-Table TaskName,NextRunTime,LastTaskResult
}catch{
    if(-not $ipoLiveTaskEnabled -and $ipoOriginalConfigText){
        foreach($name in @('QmtIPO3-Cycle','QmtIPO3-Notify','QmtIPO3-Monitor','QmtIPO3-Backup')){
            if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){Disable-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue|Out-Null}
        }
        [IO.File]::WriteAllText($ipoPath,$ipoOriginalConfigText,[Text.UTF8Encoding]::new($false))
        foreach($task in $ipoPrior){if($task.Settings.Enabled){Enable-ScheduledTask -TaskName $task.TaskName -ErrorAction SilentlyContinue|Out-Null}}
    }
    Write-Host ('启用未完整完成：'+$_.Exception.Message) -ForegroundColor Red
    Write-Host '请核对四个新任务的状态，不要重复手动下单。'
}
[void](Read-Host '按回车关闭')
