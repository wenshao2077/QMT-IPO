#Requires -Version 7
# Read-only SDK trade-channel health for either explicit IPC endpoint.
# No QMT process control, state/outbox write, task operation or order submission.
param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [string]$QmtUserdata
)
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$result=$null;$rc=2;$child=$null
try {
    $python=Join-Path $InstallRoot '.venv/Scripts/python.exe'
    $config=Join-Path $InstallRoot 'config.json'
    $probe=Join-Path $PSScriptRoot 'probe_readonly.py'
    if(-not (Test-Path -LiteralPath $probe)){$probe=Join-Path $PSScriptRoot 'code/probe_readonly.py'}
    foreach($file in @($python,$config,$probe)){
        if(-not [IO.File]::Exists($file)){throw 'read_only_probe_dependency_missing'}
    }
    $arguments=@('-B',$probe,'--config',$config,'--connection-only')
    if($QmtUserdata){$arguments+=@('--qmt-userdata',$QmtUserdata)}
    $child=[Diagnostics.Process]::new()
    $child.StartInfo.FileName=$python
    $child.StartInfo.UseShellExecute=$false
    $child.StartInfo.CreateNoWindow=$true
    $child.StartInfo.RedirectStandardOutput=$true
    $child.StartInfo.RedirectStandardError=$true
    $child.StartInfo.StandardOutputEncoding=[Text.Encoding]::UTF8
    $child.StartInfo.Environment['PYTHONUTF8']='1'
    $child.StartInfo.Environment['PYTHONIOENCODING']='utf-8'
    foreach($arg in $arguments){[void]$child.StartInfo.ArgumentList.Add($arg)}
    [void]$child.Start()
    $stdout=$child.StandardOutput.ReadToEndAsync()
    $stderr=$child.StandardError.ReadToEndAsync()
    if(-not $child.WaitForExit(20000)){
        # Only this read-only probe's child; never terminate QMT or a trading worker.
        $child.Kill();$child.WaitForExit();throw 'read_only_probe_timeout'
    }
    $text=$stdout.GetAwaiter().GetResult()
    $nativeRc=$child.ExitCode
    $report=($text -join "`n")|ConvertFrom-Json -NoEnumerate
    $healthy=($nativeRc -eq 0 -and $report -isnot [array] -and $report.status -is [string] -and
        $report.status -ceq 'read_only_connection_complete' -and $report.account_ready -is [bool] -and $report.account_ready -eq $true)
    $issues=@()
    if(-not $healthy){$issues=@('QMT交易接口未就绪：'+$report.error_stage+' / '+$report.error_type)}
    $result=@{checked_at=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss');healthy=$healthy;issues=$issues;
        details=@{probe_kind='sdk_read_only_account_status';client_binding=$report.connection;
                  account_ready=$report.account_ready;account_heartbeat_at=$report.checked_at;
                  error_stage=$report.error_stage;diagnostic=$report.diagnostic};
        submission_calls=0;message_sent=$false;configuration_written=$false}
    if($healthy){$rc=0}
}catch{
    $result=@{checked_at=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss');healthy=$false;
        issues=@('QMT只读探针未完成，请检查本地依赖与回执');details=@{probe_kind='sdk_read_only_account_status'};
        submission_calls=0;message_sent=$false;configuration_written=$false}
}finally{if($null -ne $child){$child.Dispose()}}
$result|ConvertTo-Json -Depth 8 -Compress
exit $rc
