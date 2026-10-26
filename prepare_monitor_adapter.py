"""Stage narrow QuantPush compatibility changes; never install or run a monitor."""
import argparse
import hashlib
import json
from pathlib import Path


def stage(monitor_root, ipo_root, output):
    monitor_root, ipo_root, output = map(lambda p: Path(p).resolve(), (monitor_root, ipo_root, output))
    if output.exists() or output == monitor_root or monitor_root in output.parents:
        raise ValueError('new_separate_output_required')
    path = monitor_root/'watch_live.ps1'
    original = path.read_text(encoding='utf-8-sig')
    marker = "foreach ($processName in @('miniquote', 'rocket')) {"
    if original.count(marker) != 1:
        raise ValueError('unrecognized_watch_live_version')
    # Replace only the old mini-only gate. Rocket/Fuel/portfolio checks stay intact.
    anchor = str(ipo_root).replace("'", "''")
    probe = str(ipo_root/'probe_qmt_trade.ps1').replace("'", "''")
    addition = ("try {\n$tradeProbeText = @(& (Join-Path $PSHOME 'pwsh.exe') -NoProfile -File '"+probe+
                "' -InstallRoot '"+anchor+"')\n"
                "$tradeProbeExit = $LASTEXITCODE\n"
                "$tradeProbe = ($tradeProbeText -join \"`n\") | ConvertFrom-Json -NoEnumerate\n"
                "$details.qmt_trade_probe = $tradeProbe.details\n"
                "if ($tradeProbeExit -ne 0 -or $tradeProbe -is [array] -or $tradeProbe.healthy -isnot [bool] -or $tradeProbe.healthy -ne $true) {\n"
                "  $probeIssues = @($tradeProbe.issues | Where-Object { $_ -is [string] -and $_.Length -gt 0 })\n"
                "  if ($probeIssues.Count -eq 0) { $probeIssues = @('QMT交易通道探针失败或返回无效，不能判断为健康') }\n"
                "  foreach ($issue in $probeIssues) { Add-Issue $issues $issue }\n}\n"
                "} catch { Add-Issue $issues 'QMT交易通道探针未完成，不能判断为健康' }\n\n"
                "foreach ($processName in @('rocket')) {")
    changed = original.replace(marker, addition)
    changed = changed.replace('miniquote、QMT 数据端口', 'QMT交易接口、数据端口').replace('miniquote、58610', 'QMT交易接口、58610')
    wrapper = ("#Requires -Version 7\n# Compatibility entry; no state or message writes.\n"
               "$ErrorActionPreference='Stop'\n"
               "& '"+probe+"' -InstallRoot '"+anchor+"'\nexit $LASTEXITCODE\n")
    output.mkdir(parents=True)
    (output/'watch_live.ps1').write_bytes(changed.replace('\r\n', '\n').encode('utf-8'))
    (output/'probe_qmt_trade.ps1').write_bytes(wrapper.encode('utf-8'))
    names = ['watch_live.ps1', 'probe_qmt_trade.ps1']
    if (monitor_root/'monitor_qmt_preopen.ps1').is_file():
        name = 'monitor_qmt_preopen.ps1'
        preopen = (monitor_root/name).read_text(encoding='utf-8-sig')
        start = 'function Invoke-TradeProbe {'
        end = 'if (-not (Test-Path -LiteralPath $PROBE))'
        if preopen.count(start) != 1 or preopen.count(end) != 1:
            raise ValueError('unrecognized_preopen_version')
        first, last = preopen.index(start), preopen.index(end)
        if last <= first:
            raise ValueError('unrecognized_preopen_version')
        preopen = preopen[:first]+'''function Invoke-TradeProbe {
    $probeHost = Join-Path $PSHOME 'pwsh.exe'
    $output = @(& $probeHost -NoLogo -NoProfile -File $PROBE 2>&1)
    $exitCode = $LASTEXITCODE
    try {
        $text = ($output | ForEach-Object { "$_" }) -join [Environment]::NewLine
        $result = $text | ConvertFrom-Json -NoEnumerate
        if ($exitCode -ne 0 -or $result -is [array] -or $result.healthy -isnot [bool] -or $result.healthy -ne $true) {
            throw 'probe_not_healthy'
        }
    } catch {
        $result = [pscustomobject]@{
            checked_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            healthy = $false
            issues = @('QMT交易通道探针失败或输出无效，不能判断为健康；请查看本地诊断')
            details = [pscustomobject]@{}
        }
    }
    [pscustomobject]@{ ExitCode = $exitCode; Result = $result }
}

'''+preopen[last:]
        preopen = preopen.replace('miniquote、58610 和 accountdetail 心跳均已恢复', 'QMT交易接口连接、订阅与账户状态均已恢复')
        preopen = preopen.replace('recovery: accountdetail', 'recovery: sdk_account_status')
        preopen = preopen.replace('交易账户 accountdetail 心跳正常', '交易接口连接、订阅与账户状态正常')
        (output/name).write_bytes(preopen.replace('\r\n', '\n').encode('utf-8'))
        names.append(name)
    receipt = {'status': 'staged_not_installed', 'messages_sent': 0, 'submission_calls': 0,
               'original_sha256': {name: hashlib.sha256((monitor_root/name).read_bytes()).hexdigest()
                                   for name in names},
               'staged_sha256': {name: hashlib.sha256((output/name).read_bytes()).hexdigest()
                                 for name in names}}
    (output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--monitor-root', type=Path, required=True)
    p.add_argument('--ipo-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(stage(a.monitor_root, a.ipo_root, a.output), ensure_ascii=False))
