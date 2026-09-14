# Customer/AI entry; always verify the complete extracted package first.
[CmdletBinding()]
param(
    [ValidateSet('Menu','VerifyPackage','Plan','New','ResumeNew','Doctor','Configure','Upgrade','Rollback','Health','RecoveryPlan','ExportSupport','CalendarCoverage','OpenConsole')][string]$Operation='Menu',
    [string]$Root,
    [string]$RollbackId,
    [string]$Output,
    [switch]$ConfirmExport,
    [switch]$ConfirmMaintenance,
    [switch]$Interactive
)
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$package=$PSScriptRoot;$rc=2;$result=$null
$oldPythonPath=$env:PYTHONPATH;$oldPythonHome=$env:PYTHONHOME;$oldNoBytecode=$env:PYTHONDONTWRITEBYTECODE
try{
    . (Join-Path $package 'verify.ps1')
    $checked=Test-QmtDistribution $package
    if(-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess){throw 'windows_x64_required'}
    $env:PYTHONDONTWRITEBYTECODE='1';$env:PYTHONPATH=$null;$env:PYTHONHOME=$null
    $menu=($Operation -eq 'Menu')
    if($menu){
        $Interactive=$true
        Write-Host 'QMT 自动打新助手 3.4.0'
        Write-Host '1 首次安装（离线、默认不启用）  2 接续未完成的新安装'
        Write-Host '3 填写本地配置  4 检查环境  5 打开控制台'
        Write-Host '6 升级已有安装  7 回滚代码  8 健康状态'
        Write-Host '9 导出脱敏诊断包  10 日历覆盖  11 恢复建议（只读）'
        $map=@{'1'='New';'2'='ResumeNew';'3'='Configure';'4'='Doctor';'5'='OpenConsole';'6'='Upgrade';'7'='Rollback';'8'='Health';'9'='ExportSupport';'10'='CalendarCoverage';'11'='RecoveryPlan'}
        $choice=Read-Host '输入操作编号';if(-not $map.ContainsKey($choice)){throw 'invalid_menu_choice'}
        $Operation=$map[$choice]
        $Root=Read-Host '输入安装目录（不能是当前解压目录；升级请输入原目录）'
        if($Operation -in @('Upgrade','Rollback')){
            $ConfirmMaintenance=((Read-Host '确认已备份并关闭控制台，允许暂停本程序任务进行维护？输入 YES') -ceq 'YES')
        }
        if($Operation -eq 'Rollback'){$RollbackId=Read-Host '输入升级回执里的 RollbackId'}
        if($Operation -eq 'ExportSupport'){
            $Output=Read-Host '输入诊断 ZIP 保存路径（安装目录外，不覆盖已有文件）'
            $ConfirmExport=((Read-Host '确认仅导出脱敏摘要到本地，不上传？输入 YES') -ceq 'YES')
        }
    }
    if($Operation -eq 'VerifyPackage'){$result=$checked;$rc=0}
    else{
        if(-not $Root){throw 'installation_root_required'}
        $Root=[IO.Path]::GetFullPath($Root).TrimEnd([char[]]'\/')
        if($Root -match '["\r\n]'){throw 'invalid_installation_path'}
        Assert-QmtNoReparse $Root
        $packageFull=[IO.Path]::GetFullPath($package).TrimEnd([char[]]'\/')
        if($Root -eq $packageFull -or $Root.StartsWith($packageFull+'\',[StringComparison]::OrdinalIgnoreCase) -or $packageFull.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'package_and_installation_must_be_separate'}
        if($Operation -in @('Upgrade','Rollback') -and -not $ConfirmMaintenance){throw 'maintenance_confirmation_required'}
        if($ConfirmMaintenance -and $Operation -notin @('Upgrade','Rollback')){throw 'maintenance_confirmation_not_applicable'}
        if($Operation -eq 'ExportSupport' -and (-not $ConfirmExport -or -not $Output)){throw 'explicit_export_confirmation_required'}
        if(($ConfirmExport -or $Output) -and $Operation -ne 'ExportSupport'){throw 'export_arguments_not_applicable'}
        if($RollbackId -and $Operation -ne 'Rollback'){throw 'rollback_id_not_applicable'}
        if($Operation -eq 'Rollback' -and -not $RollbackId){throw 'rollback_id_required'}
        $python=Join-Path $package 'runtime/python/python.exe'
        if($Operation -in @('New','ResumeNew')){
            if($Operation -eq 'New' -and (Test-Path -LiteralPath $Root) -and @([IO.Directory]::EnumerateFileSystemEntries($Root)).Count){throw 'new_install_requires_empty_root'}
            if($Operation -eq 'ResumeNew' -and -not [IO.File]::Exists((Join-Path $Root 'new-install.json'))){throw 'new_install_journal_missing'}
            $cacheParent=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'QMT-IPO/Runtimes'
            $python=Get-QmtPrivateRuntime $package $cacheParent
        }elseif($Operation -ne 'Plan'){
            $python=Join-Path $Root '.venv/Scripts/python.exe'
            if(-not [IO.File]::Exists($python)){throw 'existing_python_environment_missing'}
        }
        if($Operation -eq 'OpenConsole'){
            $pythonw=Join-Path $Root '.venv/Scripts/pythonw.exe'
            $panel=Join-Path $Root 'code/panel.py'
            if(-not [IO.File]::Exists($pythonw) -or -not [IO.File]::Exists($panel)){throw 'console_missing'}
            Start-Process -FilePath $pythonw -ArgumentList ('-B "'+$panel+'" --root "'+$Root+'"') -WorkingDirectory $Root | Out-Null
            $result=@{ok=$true;code='console_launch_requested';account_connected=$false;message_sent=$false;submission_calls=0};$rc=0
        }else{
            $ps=Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
            $a=@('-NoProfile','-ExecutionPolicy','Bypass',( '-File'),(Join-Path $package 'payload/setup.ps1'),'-Operation',$Operation,'-Root',$Root,'-PythonExe',$python)
            if($Operation -in @('New','ResumeNew')){$a+=@('-InstallDependencies','-Wheelhouse',(Join-Path $package 'packages/wheels'))}
            if($RollbackId){$a+=@('-RollbackId',$RollbackId)}
            if($Operation -eq 'ExportSupport'){$a+=@('-Output',$Output,'-ConfirmExport')}
            $text=& $ps @a
            $rc=$LASTEXITCODE
            $result=($text -join "`n")|ConvertFrom-Json
            if($null -eq $result -or $result.ok -ne $true){if($rc -eq 0){$rc=2}}
        }
    }
}catch{
    $code=$_.Exception.Message
    if($code -notmatch '^[a-z][a-z0-9_]+$'){$code='delivery_entry_failed'}
    $result=@{ok=$false;code=$code;next_action='preserve_private_state_and_review_local_checks';account_connected=$false;message_sent=$false;submission_calls=0};$rc=2
}finally{
    $env:PYTHONPATH=$oldPythonPath;$env:PYTHONHOME=$oldPythonHome;$env:PYTHONDONTWRITEBYTECODE=$oldNoBytecode
}
$result|ConvertTo-Json -Depth 15 -Compress
if($Interactive){
    if($result.ok){Write-Host '操作完成。首次安装未启用交易；配置检查不等于券商权限或申购受理。'}
    else{Write-Host '操作未完成。保留配置、账本和回执，不要删除账本重试。'}
    [void](Read-Host '按回车关闭')
}
exit $rc
