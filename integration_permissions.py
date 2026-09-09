"""Limited-token permission test on a dedicated NO-OP fixture task only."""
import argparse
import base64
import json
from pathlib import Path
import subprocess

from panel_backend import WindowsController
from runtime import atomic_json

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);args=parser.parse_args()
    command=r'''
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$name='QmtIPO-Panel-ACLFixture'
$task=Get-ScheduledTask -TaskName $name
if($task.Description -ne 'Disposable no-op permission fixture, never trading'){throw 'Wrong fixture'}
if($task.Triggers.Count -gt 0){throw 'Fixture must have no automatic triggers'}
$admin=([Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Enable-ScheduledTask -TaskName $name|Out-Null
$enabled=(Get-ScheduledTask -TaskName $name).Settings.Enabled
Disable-ScheduledTask -TaskName $name|Out-Null
$disabled=-not (Get-ScheduledTask -TaskName $name).Settings.Enabled
@{ok=($enabled -and $disabled -and -not $admin);is_admin=$admin;enable_api_ok=$enabled;disable_api_ok=$disabled;trading_tasks_modified=$false}|ConvertTo-Json -Compress
'''
    encoded=base64.b64encode(command.encode('utf-16-le')).decode()
    ps=WindowsController(args.root).powershell
    result=subprocess.run([str(ps),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                          capture_output=True,encoding='utf-8-sig',errors='replace',timeout=30,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    data={'ok':False,'exit_code':result.returncode}
    for line in reversed(result.stdout.splitlines()):
        try:data=json.loads(line);break
        except ValueError:pass
    atomic_json(args.root/'runtime/panel-permission-test.json',data)
    return 0 if data.get('ok') else 1

if __name__=='__main__':raise SystemExit(main())
