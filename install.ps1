# Run from a reviewed SOURCE package, not from a partially replaced live directory.
param(
    [Parameter(Mandatory=$true)][ValidateSet('New','Upgrade','Rollback')][string]$Mode,
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
if($Mode -eq 'New'){
    if(Test-Path -LiteralPath $Root){if(@(Get-ChildItem -LiteralPath $Root -Force).Count){throw 'New mode requires an empty root; do not overwrite an existing installation'}}
    foreach($name in $specs.Keys){if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){throw 'Task name already exists; use reviewed upgrade, not a second installation'}}
    $pyArgs=@();if($PythonExe -eq 'py'){$pyArgs=@('-3.11')}
    & $PythonExe @pyArgs -c "import sys; assert sys.version_info[:2]==(3,11), 'Python 3.11 required'"
    if($LASTEXITCODE -ne 0){throw 'Python 3.11 validation failed'}
    & $PythonExe @pyArgs -m venv $Root/.venv
    if($LASTEXITCODE -ne 0){throw 'Virtual environment creation failed'}
    & $python -B (Join-Path $SourceRoot 'installer.py') new --source $SourceRoot --root $Root
    if($LASTEXITCODE -ne 0){throw 'Source installation failed; no tasks enabled'}
    # Protect the entire private installation before adding any real identifiers.
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $Root /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Private directory ACL setup failed'}
    if($InstallDependencies){
        & $python -m pip install --disable-pip-version-check -r (Join-Path $SourceRoot 'requirements.txt')
        if($LASTEXITCODE -ne 0){throw 'Dependency installation failed; tasks remain absent/disabled'}
    }
    . (Join-Path $SourceRoot 'tasks.ps1')
    $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    foreach($def in @(New-IpoTaskDefinitions -Root $Root -User $user)){
        Register-ScheduledTask -TaskName $def.Name -InputObject $def.Task | Out-Null
        if((Get-ScheduledTask -TaskName $def.Name).Settings.Enabled){throw 'New task unexpectedly enabled'}
    }
    & (Join-Path $SourceRoot 'install_panel.ps1') -Root $Root
    Write-Output 'NEW_INSTALL_DISABLED: configure private files and verify locally before user authorization.'
    exit 0
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
        ($_.CommandLine -match '(panel|app|launcher|probe_readonly)\.py')
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
