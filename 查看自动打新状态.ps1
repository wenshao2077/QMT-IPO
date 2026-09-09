$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$ipoRoot=$PSScriptRoot
$ipoConfig=Get-Content -LiteralPath (Join-Path $ipoRoot 'config.json') -Raw | ConvertFrom-Json
Write-Host ('每日实盘开关：'+$ipoConfig.enable_execution)
Get-ScheduledTask -TaskName 'QmtIPO3-*' -ErrorAction SilentlyContinue | ForEach-Object {
    $info=$_|Get-ScheduledTaskInfo
    [pscustomobject]@{任务=$_.TaskName;状态=$_.State;下次=$info.NextRunTime;结果=$info.LastTaskResult}
} | Format-Table -AutoSize
$ipoStatus=Join-Path $ipoConfig.control_dir ('daily/'+(Get-Date -Format 'yyyy-MM-dd')+'-live.json')
if(Test-Path -LiteralPath $ipoStatus){Get-Content -LiteralPath $ipoStatus -Raw}else{Write-Host '今天尚无实盘运行记录。'}
Write-Host '未启用时应显示Disabled；启用后由Windows定时运行，不需要程序窗口常驻。'
[void](Read-Host '按回车关闭')
