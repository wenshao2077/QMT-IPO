# GitHub 接续与审查说明

> 本机已接续完整源码，修复 Windows 问题并完成升级；当前审查分支为 `codex/qmt-ipo-3.3-windows-acceptance`。后续验收见[Windows安装与升级验收](docs/Windows安装与升级验收.md)。下文保留网页端交付时的状态，不代表本机后续未完成。

## 当前交付状态

这次完整实现交付为 **3.3.0-rc1 源码包 + 基于原 main 的完整补丁**。远端 `main` 在任务开始时实查为 `922f7b1c193b0b3e180ea09b99dc6f1d89411629`。

曾创建预备分支 `improve/calendar-gate-portable-delivery-20260912`，仅推送 `88fb9e119d66928159d37bfef93f341933eef501`（导出 Git 跟踪源码的只读工作流）。随后完整源码的批量写入被工具拦截。因此，**该远端分支不是本轮完成的版本，没有完整优化提交或完整PR**。没有合并 main、发布 Release、部署或触发生产任务。不以修改编码、换通道或CI绕过写入拦截。

本地工作目录已完成实现和测试；源码包/补丁包含全部修改，并非只含部分模块。包中保留的 Linux/Windows 离线工作流尚未随完整代码推送，**不能将预备源码导出任务成功当作候选代码 CI 通过**。

## 在本机创建真正的审查分支

在可信的仓库克隆中，先保证工作树干净。以下操作只改源码；没有部署、下单、启用计划或真实通知。不要在生产安装根目录执行 Git 操作。

```powershell
$Base='922f7b1c193b0b3e180ea09b99dc6f1d89411629'
$Patch='D:\Downloads\QMT-IPO-3.3.0-rc1-from-922f7b1.patch'

git fetch origin
git status --short
# 若不是空输出，先由操作者保存/处理既有改动，不能自动覆盖。
git switch -c review/qmt-ipo-3.3-rc1 $Base
if ($LASTEXITCODE -ne 0) { throw 'Cannot create clean review branch' }
git apply --check $Patch
if ($LASTEXITCODE -ne 0) { throw 'Patch does not apply cleanly; stop and inspect' }
git apply $Patch
if ($LASTEXITCODE -ne 0) { throw 'Patch application failed' }

py -3.11 -B -c "from installer import payload; payload('.')"
if ($LASTEXITCODE -ne 0) { throw 'Delivery manifest mismatch' }
py -3.11 -B -m unittest -v test_system test_panel test_run_history test_calendar_delivery
if ($LASTEXITCODE -ne 0) { throw 'Offline Python tests failed' }
powershell -NoProfile -ExecutionPolicy Bypass -File .\test_tasks.ps1
if ($LASTEXITCODE -ne 0) { throw 'Offline PowerShell tests failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Patch whitespace check failed' }
git diff --stat
git status --short
```

本补丁基于 **原 main**，包含新增 `.github/workflows/offline.yml`。不要直接应用在已有这个文件的预备分支上；从上面的精确基线创建新分支即可。若 main 后续有其他提交，应先审查差异，再决定重放/合并，不可强推覆盖。

审阅实际修改文件，确认没有加入真实 config.json、密钥、数据库、日志、虚拟环境或个人目录，再提交：

```powershell
git add --all
git diff --cached --check
git diff --cached --name-only
# 人工确认上述暂存清单仅含本轮源码、测试、示例和文档后：
git commit -m "fix: gate IPO runs by verified calendar and add portable delivery"
git push -u origin review/qmt-ipo-3.3-rc1
```

然后在 GitHub 为该分支创建对 main 的 PR；运行随包提供的离线 CI，附本机 Windows 测试结果和检查清单。不要自动合并、不要自动发布表示实盘验收完成的版本。

## 本机部署与回滚

代码审查通过不等于获得真实部署授权。Owner 确认后，由本机 TARS 按 README 的 Upgrade/回滚步骤执行；必须保留原配置、原账本、原任务开关和后续新增的所有委托意图。真实只读探针/通知测试各需明确授权，真实申购只能由原获准计划自然触发并按券商回报核对。
