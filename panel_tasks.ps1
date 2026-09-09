param(
    [Parameter(Mandatory=$true)][ValidateSet('Snapshot','Prepare','Enable','Pause')][string]$Operation,
    [Parameter(Mandatory=$true)][string]$Root,
    [string]$RequestId,
    [switch]$Consent
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$newNames=@('QmtIPO3-Cycle','QmtIPO3-Notify','QmtIPO3-Monitor','QmtIPO3-Backup')
$oldNames=@('QmtIPO-Preview','QmtIPO-Reconcile','QmtIPO-Notify','QmtIPO-Health','QmtIPO-Backup')
$allNames=$newNames+$oldNames
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$sid=$identity.User
$admin=([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$configPath=Join-Path $Root 'config.json'
$control=Join-Path $Root 'runtime'
$scheduler=New-Object -ComObject Schedule.Service
$scheduler.Connect()
$folder=$scheduler.GetFolder('\')

function Get-OwnedTasks {
    $result=@{}
    foreach($name in $allNames){
        $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if($task){$result[$name]=$task}
    }
    return $result
}

function Test-OwnerWrite([string]$Name) {
    if($admin){return $true}
    $sddl=$folder.GetTask($Name).GetSecurityDescriptor(4)
    $descriptor=[Security.AccessControl.RawSecurityDescriptor]::new($sddl)
    foreach($ace in $descriptor.DiscretionaryAcl){
        if($ace.SecurityIdentifier -eq $sid -and $ace.AceQualifier -eq 'AccessAllowed' -and (($ace.AccessMask -band 0x1f01ff) -eq 0x1f01ff)){return $true}
    }
    return $false
}

function Get-PanelSnapshot {
    $config=Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $tasks=Get-OwnedTasks
    $rows=@(foreach($name in $allNames){
        if($tasks.ContainsKey($name)){
            $task=$tasks[$name]; $info=$task|Get-ScheduledTaskInfo
            [pscustomobject]@{name=$name;exists=$true;enabled=[bool]$task.Settings.Enabled;state=$task.State.ToString();
                next_run=$info.NextRunTime.ToString('o');last_run=$info.LastRunTime.ToString('o');last_result=$info.LastTaskResult;can_manage=(Test-OwnerWrite $name)}
        }elseif($name -in $newNames){[pscustomobject]@{name=$name;exists=$false;enabled=$false;state='Missing';can_manage=$false}}
    })
    $qmtProcesses=@(Get-Process -Name XtMiniQmt,miniquote -ErrorAction SilentlyContinue|Select-Object -ExpandProperty ProcessName)
    $account=[string]$config.account_id
    $tail=if($account.Length -gt 4){$account.Substring($account.Length-4)}else{'未配置'}
    return @{ok=$true;checked_at=(Get-Date).ToString('o');execution_enabled=[bool]$config.enable_execution;
        tasks=$rows;needs_admin=(@($rows|Where-Object {-not $_.can_manage}).Count -gt 0);
        account_tail=$tail;markets=@($config.allowed_markets);qmt_path=$config.qmt_userdata;
        qmt_process_present=($qmtProcesses -contains 'XtMiniQmt');quote_process_present=($qmtProcesses -contains 'miniquote');
        webhook_configured=(Test-Path -LiteralPath $config.webhook_file);state_dir=$config.state_dir;control_dir=$config.control_dir}
}

function Save-JsonAtomic([string]$Path,$Value) {
    $temp=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    [IO.File]::WriteAllText($temp,($Value|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temp -Destination $Path -Force
}

function Grant-OwnerTaskAccess([string]$Name) {
    # Only the nine exact project task names, never a folder-wide ACL.
    if($Name -notin $allNames){throw 'Task is outside this application'}
    $task=$folder.GetTask($Name)
    $descriptor=[Security.AccessControl.RawSecurityDescriptor]::new($task.GetSecurityDescriptor(7))
    foreach($ace in $descriptor.DiscretionaryAcl){
        if($ace.SecurityIdentifier -eq $sid -and $ace.AceQualifier -eq 'AccessAllowed' -and (($ace.AccessMask -band 0x1f01ff) -eq 0x1f01ff)){return}
    }
    $ace=[Security.AccessControl.CommonAce]::new([Security.AccessControl.AceFlags]::None,[Security.AccessControl.AceQualifier]::AccessAllowed,0x1f01ff,$sid,$false,$null)
    $descriptor.DiscretionaryAcl.InsertAce(0,$ace)
    $task.SetSecurityDescriptor($descriptor.GetSddlForm([Security.AccessControl.AccessControlSections]::All),0)
}

$receiptPath=$null
$mutex=$null
$locked=$false
try {
    if($Operation -eq 'Snapshot'){Get-PanelSnapshot|ConvertTo-Json -Depth 12 -Compress;exit 0}
    if($RequestId -notmatch '^[a-f0-9]{32}$'){throw 'Invalid operation request ID'}
    if($Operation -in @('Enable','Pause') -and -not $Consent){throw 'Human button confirmation is required'}
    New-Item -ItemType Directory -Path (Join-Path $control 'panel-actions') -Force|Out-Null
    $receiptPath=Join-Path $control ('panel-actions/'+$RequestId+'.json')
    $mutex=[Threading.Mutex]::new($false,'Local\QmtIpoPanelControl')
    try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
    if(-not $locked){throw 'Another control operation is in progress'}
    $before=Get-PanelSnapshot
    $configBefore=Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
    $config=$configBefore|ConvertFrom-Json
    $tasks=Get-OwnedTasks
    if(@($newNames|Where-Object {-not $tasks.ContainsKey($_)}).Count){throw 'Task definitions are missing; restore the application before enabling'}
    if($Operation -eq 'Prepare'){
        if(-not $admin){throw 'First-time permission preparation needs Windows approval'}
        $backup=Join-Path $control ('panel-actions/'+$RequestId+'-permissions.json')
        $descriptors=@{}
        foreach($name in $allNames){if($tasks.ContainsKey($name)){$descriptors[$name]=$folder.GetTask($name).GetSecurityDescriptor(7)}}
        Save-JsonAtomic $backup $descriptors
        foreach($name in $descriptors.Keys){Grant-OwnerTaskAccess $name}
        $after=Get-PanelSnapshot
        foreach($row in $before.tasks){
            $new=@($after.tasks|Where-Object name -eq $row.name)[0]
            if($row.enabled -ne $new.enabled){throw 'Preparation unexpectedly changed a task state'}
        }
        if($configBefore -cne (Get-Content -LiteralPath $configPath -Raw -Encoding UTF8)){throw 'Preparation unexpectedly changed configuration'}
    }else{
        if($before.needs_admin){throw 'Task permissions need preparation before this action'}
        if($Operation -eq 'Enable'){
            # Existing registered definitions are not recreated; ownership ACLs stay intact.
            foreach($name in $newNames){
                $task=$tasks[$name]
                $expected=([IO.Path]::GetFullPath((Join-Path $Root 'run_scheduled.ps1'))).Replace([char]92,[char]47)
                if($task.Actions.Count -ne 1 -or -not ($task.Actions[0].Arguments.Replace([char]92,[char]47)).Contains($expected)){throw 'Unexpected task entrypoint'}
            }
            $backup=Join-Path $control ('panel-actions/'+$RequestId+'-config.before.json')
            [IO.File]::WriteAllText($backup,$configBefore,[Text.UTF8Encoding]::new($false))
            $config.enable_execution=$true
            $cycleEnabled=$false
            try {
                Save-JsonAtomic $configPath $config
                foreach($name in $oldNames){if($tasks.ContainsKey($name)){Disable-ScheduledTask -TaskName $name|Out-Null}}
                foreach($name in @('QmtIPO3-Notify','QmtIPO3-Monitor','QmtIPO3-Backup','QmtIPO3-Cycle')){
                    Enable-ScheduledTask -TaskName $name|Out-Null
                    if($name -eq 'QmtIPO3-Cycle'){$cycleEnabled=$true}
                }
            }catch{
                if(-not $cycleEnabled){
                    foreach($row in $before.tasks){
                        if($row.enabled){Enable-ScheduledTask -TaskName $row.name -ErrorAction SilentlyContinue|Out-Null}
                        else{Disable-ScheduledTask -TaskName $row.name -ErrorAction SilentlyContinue|Out-Null}
                    }
                    Save-JsonAtomic $configPath ($configBefore|ConvertFrom-Json)
                }
                throw
            }
            Save-JsonAtomic (Join-Path $control 'panel-authorization.json') @{authorized=$true;time=(Get-Date).ToString('o');scope='daily_stock_and_bond_ipo';source='owner_gui_button';consent_version=1}
        }else{
            # Stop future dispatches; do not kill an in-flight worker or cancel orders.
            Disable-ScheduledTask -TaskName 'QmtIPO3-Cycle'|Out-Null
            $config.enable_execution=$false
            Save-JsonAtomic $configPath $config
            Save-JsonAtomic (Join-Path $control 'panel-authorization.json') @{authorized=$false;time=(Get-Date).ToString('o');source='owner_gui_button';consent_version=1}
        }
        $after=Get-PanelSnapshot
        $cycle=@($after.tasks|Where-Object name -eq 'QmtIPO3-Cycle')[0]
        if($Operation -eq 'Enable' -and (-not $after.execution_enabled -or -not $cycle.enabled)){throw 'Enable readback did not match requested state'}
        if($Operation -eq 'Pause' -and ($after.execution_enabled -or $cycle.enabled)){throw 'Pause readback did not match requested state'}
    }
    $result=@{ok=$true;operation=$Operation;finished_at=(Get-Date).ToString('o');snapshot=$after}
    Save-JsonAtomic $receiptPath $result
    $result|ConvertTo-Json -Depth 12 -Compress
}catch{
    $message=$_.Exception.Message -replace 'https?://\S+','[URL]'
    $result=@{ok=$false;operation=$Operation;error=$message;finished_at=(Get-Date).ToString('o')}
    if($receiptPath){try{Save-JsonAtomic $receiptPath $result}catch{}}
    $result|ConvertTo-Json -Depth 6 -Compress
    exit 1
}finally{
    if($locked){$mutex.ReleaseMutex()}
    if($mutex){$mutex.Dispose()}
}
