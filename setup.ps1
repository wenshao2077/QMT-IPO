# Public human/AI entry. No account, Webhook, live, enable or notification parameters.
param(
    [ValidateSet('Plan','Doctor','VerifyPackage','New','ResumeNew','Configure','Upgrade','Rollback','Health','RecoveryPlan','ExportSupport','CalendarCoverage','VerifyArchive')][string]$Operation='Plan',
    [string]$Root,
    [string]$PythonExe='py',
    [switch]$InstallDependencies,
    [string]$RollbackId,
    [switch]$Interactive,
    [string]$Output,
    [switch]$ConfirmExport,
    [string]$Archive,
    [string]$ExpectedSha256
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$source=$PSScriptRoot
$exitCode=2
$result=$null
$log=$null
try{
    if($Interactive){
        Write-Host 'QMT IPO - installation and local checks only. Trading stays disabled on NEW installation.'
        Write-Host '1 New install   2 Resume interrupted NEW install   3 Local checks   4 Configure locally   5 Reviewed upgrade   6 Health   7 Recovery plan (read only)   8 Export support   9 Calendar coverage'
        $choice=Read-Host 'Select 1-9'
        $map=@{'1'='New';'2'='ResumeNew';'3'='Doctor';'4'='Configure';'5'='Upgrade';'6'='Health';'7'='RecoveryPlan';'8'='ExportSupport';'9'='CalendarCoverage'}
        if(-not $map.ContainsKey($choice)){throw 'invalid_menu_choice'}
        $Operation=$map[$choice]
        $Root=Read-Host 'Installation directory (not the extracted source directory)'
        if($Operation -eq 'ExportSupport'){
            $Output=Read-Host 'Output ZIP path outside the installation directory (must not exist)'
            $ConfirmExport=((Read-Host 'Export a sanitized summary locally? Type YES to confirm; nothing is uploaded') -ceq 'YES')
        }
        if($Operation -in @('New','ResumeNew')){
            $InstallDependencies=((Read-Host 'Allow downloading the pinned Python dependencies? Type YES to allow') -ceq 'YES')
        }
    }
    if($Operation -notin @('VerifyPackage','Doctor','VerifyArchive') -and -not $Root){throw 'root_required'}
    if($Output -and $Operation -ne 'ExportSupport'){throw 'output_only_for_export'}
    if($ConfirmExport -and $Operation -ne 'ExportSupport'){throw 'export_confirmation_only_for_export'}
    if($Operation -eq 'ExportSupport' -and (-not $Output -or -not $ConfirmExport)){throw 'explicit_export_confirmation_and_output_required'}
    if(($Archive -or $ExpectedSha256) -and $Operation -ne 'VerifyArchive'){throw 'archive_parameters_only_for_VerifyArchive'}
    if($Operation -eq 'VerifyArchive' -and -not $Archive){throw 'archive_required'}
    if($Root){$Root=[IO.Path]::GetFullPath($Root)}
    if($InstallDependencies -and $Operation -notin @('New','ResumeNew')){throw 'dependency_install_only_for_new_or_resume'}
    if($RollbackId -and $Operation -ne 'Rollback'){throw 'rollback_id_only_for_rollback'}
    if($Operation -eq 'Rollback' -and -not $RollbackId){throw 'rollback_id_required'}
    $python=$PythonExe;$pyArgs=@();if($python -eq 'py'){$pyArgs=@('-3.11')}
    if($Root -and (Test-Path -LiteralPath (Join-Path $Root '.venv/Scripts/python.exe'))){
        $python=Join-Path $Root '.venv/Scripts/python.exe';$pyArgs=@()
    }
    if($Operation -in @('New','ResumeNew','Upgrade','Rollback')){
        # Native child process isolates install.ps1's exit statements. Capture locally,
        # return just one JSON object to AI; never publish raw install logs automatically.
        $powershell=Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
        $a=@('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $source 'install.ps1'),'-Mode',$Operation,'-Root',$Root,'-SourceRoot',$source,'-PythonExe',$PythonExe)
        if($InstallDependencies){$a+='-InstallDependencies'}
        if($RollbackId){$a+=@('-RollbackId',$RollbackId)}
        $log=[IO.Path]::GetTempFileName()
        # PS5.1 redirection defaults to UTF-16; choose UTF-8 explicitly for receipts.
        $savedPreference=$ErrorActionPreference
        try{
            $ErrorActionPreference='Continue'
            & $powershell @a 2>&1 | Out-File -LiteralPath $log -Encoding utf8 -ErrorAction Stop
            $exitCode=$LASTEXITCODE
        }finally{$ErrorActionPreference=$savedPreference}
        $result=@{schema_version=1;operation=$Operation;ok=($exitCode -eq 0);code=$(if($exitCode -eq 0){'operation_complete'}else{'operation_incomplete'});local_log=$log;account_connected=$false;message_sent=$false;submission_calls=0}
        if($Operation -in @('New','ResumeNew') -and $exitCode -eq 0){
            $lines=@(Get-Content -LiteralPath $log -Encoding UTF8)
            for($i=$lines.Count-1;$i -ge 0;$i--){
                try{
                    $receipt=$lines[$i]|ConvertFrom-Json
                    if($receipt.code){$result.code=$receipt.code;$result.next_action=$receipt.next_action;break}
                }catch{}
            }
            if($result.code -eq 'installed_disabled'){$result.new_install_default_execution=$false}
        }
        if($Operation -in @('Upgrade','Rollback')){$result.activation_policy='preserve_original_switches_not_automatic_activation'}
    }elseif($Operation -eq 'Configure'){
        $script=Join-Path $Root 'code/configure.py'
        if(-not (Test-Path -LiteralPath $script)){throw 'installed_configuration_wizard_missing'}
        & $python @pyArgs -B $script --root $Root
        $exitCode=$LASTEXITCODE
        $result=@{schema_version=1;operation=$Operation;ok=($exitCode -eq 0);code='local_wizard_closed';configuration_saved='check_Doctor';account_connected=$false;message_sent=$false;submission_calls=0}
    }elseif($Operation -in @('Health','RecoveryPlan','ExportSupport','CalendarCoverage','VerifyArchive')){
        $filename=if($Operation -eq 'CalendarCoverage'){'calendar_tool.py'}elseif($Operation -eq 'VerifyArchive'){'package_verify.py'}else{'maintenance.py'}
        $script=Join-Path $source $filename
        if(-not (Test-Path -LiteralPath $script)){$script=Join-Path $source ('code/'+$filename)}
        if($Operation -eq 'CalendarCoverage'){
            $a=@('-B',$script,'coverage','--config',(Join-Path $Root 'config.json'))
        }elseif($Operation -eq 'VerifyArchive'){
            $a=@('-B',$script,'--zip',$Archive)
            if($ExpectedSha256){$a+=@('--expected-sha256',$ExpectedSha256)}
        }else{
            $action=@{'Health'='status';'RecoveryPlan'='recovery-plan';'ExportSupport'='export-support'}[$Operation]
            $a=@('-B',$script,$action,'--root',$Root)
            if($Operation -eq 'ExportSupport'){$a+=@('--output',$Output,'--confirm-export')}
        }
        $text=& $python @pyArgs @a
        $exitCode=$LASTEXITCODE
        $result=$text|ConvertFrom-Json
    }else{
        $script=Join-Path $source 'deploy.py'
        if(-not (Test-Path -LiteralPath $script)){$script=Join-Path $source 'code/deploy.py'}
        $action=@{'Plan'='plan';'Doctor'='doctor';'VerifyPackage'='verify-package'}[$Operation]
        $a=@('-B',$script,$action,'--source',$source)
        if($Root){$a+=@('--root',$Root)}
        $text=& $python @pyArgs @a
        $exitCode=$LASTEXITCODE
        $result=$text|ConvertFrom-Json
    }
}catch{
    $exitCode=2
    $result=@{schema_version=1;operation=$Operation;ok=$false;code='setup_entry_failed';next_action='verify_Python_3_11_x64_and_review_local_installation';account_connected=$false;message_sent=$false;submission_calls=0}
    if($log){$result.local_log=$log}
}
$result|ConvertTo-Json -Depth 15 -Compress
if($Interactive){
    if($result.ok){Write-Host 'Operation completed. This does NOT prove account permissions or broker acceptance.'}
    else{Write-Host 'Operation not completed. Keep the receipt and ledger; do not delete a ledger or blindly retry New.'}
    [void](Read-Host 'Press Enter to close')
}
exit $exitCode
