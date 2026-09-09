function New-IpoTaskDefinitions {
    param([string]$Root,[string]$User,[datetime]$StartDay=(Get-Date).Date)
    $principal=New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -Disable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $specs=@(
        @{Name='QmtIPO3-Cycle';Action='cycle';At='09:35';Duration=315;Interval=5},
        @{Name='QmtIPO3-Notify';Action='notify';At='09:37';Duration=398;Interval=5},
        @{Name='QmtIPO3-Monitor';Action='monitor';At='09:26';Duration=410;Interval=5},
        @{Name='QmtIPO3-Backup';Action='backup';At='16:20';Duration=0;Interval=0}
    )
    foreach($spec in $specs){
        $at=$StartDay.Add([timespan]::Parse($spec.At))
        $daily=New-ScheduledTaskTrigger -Daily -At $at
        if($spec.Interval){
            $repeat=New-ScheduledTaskTrigger -Once -At $at -RepetitionInterval (New-TimeSpan -Minutes $spec.Interval) -RepetitionDuration (New-TimeSpan -Minutes $spec.Duration)
            $daily.Repetition=$repeat.Repetition
        }
        $triggers=@($daily)
        if($spec.Action -eq 'cycle'){
            $triggers+=(New-ScheduledTaskTrigger -Daily -At $StartDay.AddHours(15).AddMinutes(5))
            $triggers+=(New-ScheduledTaskTrigger -AtLogOn -User $User)
        }
        $arguments='//B "'+(Join-Path $Root 'hidden.vbs')+'" "'+(Join-Path $Root 'run_scheduled.ps1')+'" '+$spec.Action
        $action=New-ScheduledTaskAction -Execute 'C:/Windows/System32/wscript.exe' -Argument $arguments -WorkingDirectory $Root
        [pscustomobject]@{Name=$spec.Name;Task=(New-ScheduledTask -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description '自动新股新债申购V3；由Owner一次性启用，周期处理未完成项目，已提交委托不重报。')}
    }
}
