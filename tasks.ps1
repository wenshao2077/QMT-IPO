# Definitions only. Nothing is registered, enabled or started by dot-sourcing.
. (Join-Path $PSScriptRoot 'task_identity.ps1')
function New-IpoTaskDefinitions {
    param([Parameter(Mandatory=$true)][string]$Root,[Parameter(Mandatory=$true)][string]$User,
          [datetime]$StartDay=([TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([datetime]::UtcNow,'China Standard Time')).Date)
    $Root=Convert-IpoPath $Root
    $principal=New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -Disable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $specs=@(
        @{Name='QmtIPO3-Cycle';Action='cycle';At='09:35';Duration=315;Interval=5},
        @{Name='QmtIPO3-Notify';Action='notify';At='09:37';Duration=398;Interval=5},
        @{Name='QmtIPO3-Monitor';Action='monitor';At='09:26';Duration=410;Interval=5},
        @{Name='QmtIPO3-Backup';Action='backup';At='16:20';Duration=0;Interval=0}
    )
    foreach($spec in $specs){
        $at=$StartDay.Date.Add([timespan]::Parse($spec.At))
        $daily=New-ScheduledTaskTrigger -Daily -At $at
        # Explicit exchange timezone, independent of Windows display timezone/DST.
        $daily.StartBoundary=$at.ToString("yyyy-MM-dd'T'HH:mm:ss")+'+08:00'
        if($spec.Interval){
            $repeat=New-ScheduledTaskTrigger -Once -At $at -RepetitionInterval (New-TimeSpan -Minutes $spec.Interval) -RepetitionDuration (New-TimeSpan -Minutes $spec.Duration)
            $daily.Repetition=$repeat.Repetition
        }
        $triggers=@($daily)
        if($spec.Action -eq 'cycle'){
            $final=New-ScheduledTaskTrigger -Daily -At $StartDay.Date.AddHours(15).AddMinutes(5)
            $final.StartBoundary=$StartDay.ToString('yyyy-MM-dd')+'T15:05:00+08:00'
            $triggers+=$final
            $triggers+=(New-ScheduledTaskTrigger -AtLogOn -User $User)
        }
        $action=New-ScheduledTaskAction -Execute (Join-Path $Root '.venv/Scripts/pythonw.exe') -Argument (Get-IpoArguments $Root $spec.Action) -WorkingDirectory $Root
        [pscustomobject]@{Name=$spec.Name;Task=(New-ScheduledTask -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description 'QMT IPO: disabled until explicit user authorization; durable intent; no uncertain-order resubmission.')}
    }
}
