# Run from a reviewed SOURCE package, not from a partially replaced live directory.
param(
    [Parameter(Mandatory=$true)][ValidateSet('New','ResumeNew','Upgrade','Rollback')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$Root,
    [string]$SourceRoot,
    [string]$PythonExe='py',
    [switch]$InstallDependencies,
    [string]$RollbackId
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
if(-not $SourceRoot){$SourceRoot=$PSScriptRoot}
$Root=[IO.Path]::GetFullPath($Root)
$SourceRoot=[IO.Path]::GetFullPath($SourceRoot)
if($Root -eq $SourceRoot){throw 'Source and installation must be separate directories'}
if($Root.Contains('"')){throw 'Unsupported root quoting characters'}
. (Join-Path $SourceRoot 'task_identity.ps1')
$specs=@{ 'QmtIPO3-Cycle'='cycle'; 'QmtIPO3-Notify'='notify'; 'QmtIPO3-Monitor'='monitor'; 'QmtIPO3-Backup'='backup' }
$python=Join-Path $Root '.venv/Scripts/python.exe'
if($Mode -in @('New','ResumeNew')){
    # Serialize two human/AI installers for the same destination. No worker is killed.
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$key=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.TrimEnd('\').ToUpperInvariant())))).Replace('-','')}
    finally{$hash.Dispose()}
    $newMutex=[Threading.Mutex]::new($false,('Local\QmtIpoNew-'+$key))
    $newLocked=$false
    try{
        try{$newLocked=$newMutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$newLocked=$true}
        if(-not $newLocked){throw 'Another installer is running for this destination'}
        $pyArgs=@();if($PythonExe -eq 'py'){$pyArgs=@('-3.11')}
        & $PythonExe @pyArgs -c "import sys,struct; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8, 'Python 3.11 x64 required'"
        if($LASTEXITCODE -ne 0){throw 'Python 3.11 x64 validation failed'}
        function Invoke-NewJournal([string]$Action,[string]$Stage){
            $a=@('-B',(Join-Path $SourceRoot 'install_journal.py'),$Action,'--source',$SourceRoot,'--root',$Root)
            if($Stage){$a+=@('--stage',$Stage)}
            $text=& $PythonExe @pyArgs @a
            $nativeExit=$LASTEXITCODE
            $result=$text|ConvertFrom-Json
            if($nativeExit -ne 0 -or -not $result.ok){throw ('New-install checkpoint refused: '+$result.code)}
            return $result
        }
        if($Mode -eq 'New'){
            if(Test-Path -LiteralPath $Root){if(@(Get-ChildItem -LiteralPath $Root -Force).Count){throw 'New mode requires an empty root; use ResumeNew only for its own interrupted installation'}}
            foreach($name in $specs.Keys){if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){throw 'Task name already exists; use reviewed upgrade, not a second installation'}}
            $state=Invoke-NewJournal 'start' ''
        }else{
            $state=Invoke-NewJournal 'status' ''
            if($state.complete){
                @{schema_version=1;ok=$true;code='already_installed';next_action='Doctor';changes_applied=$false}|ConvertTo-Json -Compress
                exit 0
            }
        }
        # Protect new-install files before creating private configuration or a key file.
        $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        & icacls.exe $Root /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
        if($LASTEXITCODE -ne 0){throw 'Private directory ACL setup failed'}
        if($state.stage -eq 'prepared'){
            # Re-running venv here is permitted only before source initialization.
            $extra=@(Get-ChildItem -LiteralPath $Root -Force|Where-Object {$_.Name -notin @('.venv','new-install.json')})
            if($extra.Count){throw 'Unexpected files before source initialization; manual review required'}
            & $PythonExe @pyArgs -m venv (Join-Path $Root '.venv')
            if($LASTEXITCODE -ne 0){throw 'Virtual environment creation failed; ResumeNew may retry this checkpoint'}
            $state=Invoke-NewJournal 'advance' 'environment_ready'
        }
        if(-not (Test-Path -LiteralPath $python)){throw 'Private environment missing; manual review required'}
        if($state.stage -eq 'environment_ready'){$state=Invoke-NewJournal 'advance' 'source_started'}
        if($state.stage -eq 'source_started'){
            $extra=@(Get-ChildItem -LiteralPath $Root -Force|Where-Object {$_.Name -notin @('.venv','new-install.json')})
            if(-not $extra.Count){
                & $python -B (Join-Path $SourceRoot 'installer.py') new --source $SourceRoot --root $Root
                if($LASTEXITCODE -ne 0){throw 'Source initialization interrupted; preserve all files for recovery review'}
            }
            # Handles a crash after a complete initialization but before its checkpoint.
            # Partial/missing/damaged ledgers are NEVER initialized again here.
            Invoke-NewJournal 'verify-fresh' '' | Out-Null
            $state=Invoke-NewJournal 'advance' 'source_ready'
        }
        Invoke-NewJournal 'verify-fresh' '' | Out-Null
        if($state.stage -eq 'source_ready'){
            if($InstallDependencies){
                & $python -m pip install --disable-pip-version-check -r (Join-Path $SourceRoot 'requirements.txt')
                if($LASTEXITCODE -ne 0){throw 'Dependency installation failed; ResumeNew can retry, no tasks enabled'}
            }
            & $python -B (Join-Path $SourceRoot 'environment_check.py')
            if($LASTEXITCODE -ne 0){throw 'Dependencies/runtime not ready; review environment report and explicitly ResumeNew -InstallDependencies when appropriate'}
            $state=Invoke-NewJournal 'advance' 'dependencies_ready'
        }else{
            & $python -B (Join-Path $SourceRoot 'environment_check.py')
            if($LASTEXITCODE -ne 0){throw 'Runtime changed after checkpoint; review before resuming'}
        }
        . (Join-Path $SourceRoot 'tasks.ps1')
        $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
        foreach($def in @(New-IpoTaskDefinitions -Root $Root -User $user)){
            $existing=@(Get-ScheduledTask -TaskName $def.Name -ErrorAction SilentlyContinue)
            if($existing.Count -gt 1){throw 'Ambiguous task ownership; no task overwritten'}
            if($existing.Count){
                Assert-IpoOwnedTask $existing[0] $Root $specs[$def.Name]
                if($existing[0].Settings.Enabled -or $existing[0].State -eq 'Running'){throw 'Existing task is enabled/running; not an unused new installation'}
            }else{
                Register-ScheduledTask -TaskName $def.Name -InputObject $def.Task | Out-Null
            }
            $check=Get-ScheduledTask -TaskName $def.Name
            Assert-IpoOwnedTask $check $Root $specs[$def.Name]
            if($check.Settings.Enabled -or $check.State -eq 'Running'){throw 'New task unexpectedly enabled/running'}
        }
        if($state.stage -eq 'dependencies_ready'){$state=Invoke-NewJournal 'advance' 'tasks_ready'}
        & (Join-Path $SourceRoot 'install_panel.ps1') -Root $Root
        Invoke-NewJournal 'verify-fresh' '' | Out-Null
        $state=Invoke-NewJournal 'advance' 'complete'
        @{schema_version=1;ok=$true;code='installed_disabled';execution_enabled=$false;account_connected=$false;message_sent=$false;submission_calls=0;next_action='Configure'}|ConvertTo-Json -Compress
        exit 0
    }finally{
        if($newLocked){$newMutex.ReleaseMutex()}
        $newMutex.Dispose()
    }
}
if($InstallDependencies){throw 'Upgrade/rollback does not change the existing SDK environment'}
if(-not (Test-Path -LiteralPath $python)){throw 'Existing isolated environment missing'}
$configPath=Join-Path $Root 'config.json'
$configHash=(Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash
$config=Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$tasks=@{}
foreach($name in $specs.Keys){
    $task=Get-ScheduledTask -TaskName $name -ErrorAction Stop
    Assert-IpoOwnedTask $task $Root $specs[$name]
    $tasks[$name]=@{Enabled=[bool]$task.Settings.Enabled;Xml=(Export-ScheduledTask -TaskName $name)}
}
# A prior interrupted maintenance session is not silently replaced.
$marker=Join-Path $Root 'maintenance.json'
if(Test-Path -LiteralPath $marker){throw 'Existing maintenance marker: inspect saved state and finish recovery manually'}
$rid=(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8)
$records=Join-Path $config.control_dir 'maintenance'
New-Item -ItemType Directory -Path $records -Force | Out-Null
$beforeFile=Join-Path $records ($rid+'.json')
$before=@{id=$rid;mode=$Mode;root=$Root;tasks=$tasks;config_sha256=$configHash;status='prepared'}
[IO.File]::WriteAllText($beforeFile,($before|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($marker,($before|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
$restoreOk=$true
$sourceStarted=$false
$sourceSucceeded=$false
try{
    foreach($name in $specs.Keys){Disable-ScheduledTask -TaskName $name | Out-Null}
    # Do not kill a worker, GUI or miniQMT. Refuse replacement until all are closed.
    foreach($name in $specs.Keys){if((Get-ScheduledTask -TaskName $name).State -eq 'Running'){throw 'Worker is running; let it finish before retrying'}}
    $ownedProcesses=@(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -and $_.CommandLine.Contains($Root) -and
        ($_.CommandLine -match '(panel|app|launcher|probe_readonly|configure)\.py')
    })
    if($ownedProcesses.Count){throw 'Close the console and wait for owned workers; no process has been killed'}
    $sourceStarted=$true
    if($Mode -eq 'Upgrade'){
        & $python -B (Join-Path $SourceRoot 'installer.py') upgrade --source $SourceRoot --root $Root --quiesced
    }else{
        if(-not $RollbackId){throw 'RollbackId is required'}
        & $python -B (Join-Path $SourceRoot 'installer.py') rollback --root $Root --rollback-id $RollbackId --quiesced
    }
    if($LASTEXITCODE -ne 0){throw 'Source operation failed; inspect maintenance and rollback records'}
    if((Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash -ne $configHash){throw 'Private configuration changed; inspect before resuming'}
    $sourceSucceeded=$true
}finally{
    if($sourceStarted -and -not $sourceSucceeded){
        $restoreOk=$false
        Write-Warning 'Source operation failed: owned tasks remain disabled and maintenance marker is retained for explicit recovery.'
    }else{
    foreach($name in $specs.Keys){
        try{
            # Definitions are NOT recreated on upgrade. Restore exact prior switches.
            $current=Get-ScheduledTask -TaskName $name
            Assert-IpoOwnedTask $current $Root $specs[$name]
            if($tasks[$name].Enabled){Enable-ScheduledTask -TaskName $name | Out-Null}
            else{Disable-ScheduledTask -TaskName $name | Out-Null}
            if([bool](Get-ScheduledTask -TaskName $name).Settings.Enabled -ne $tasks[$name].Enabled){throw 'State readback mismatch'}
        }catch{$restoreOk=$false}
    }
    }
    if($restoreOk){Remove-Item -LiteralPath $marker -Force}
    else{Write-Warning 'Task-state restoration incomplete. Maintenance marker retained: inspect saved maintenance record, do not blindly enable tasks.'}
}
if(-not $restoreOk){throw 'Manual restoration required; no claims of successful activation'}
Write-Output 'SOURCE_OPERATION_COMPLETE: private config/ledger preserved, original task switches restored; no task manually started.'
