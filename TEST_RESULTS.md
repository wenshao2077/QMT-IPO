# 3.4.0 当前验证记录

## 2026-10-26 连接、委托解析与监控兼容

Windows x64、隔离 uv Python 3.11.15 / SQLite 3.50.4：13 组回归共 **298 项通过，0 失败、0 错误、0 跳过**。新增 21 项兼容测试覆盖两个显式 IPC 目录、连接/订阅返回码、51 条委托中 16 条备注 getter 解码失败、核心字段失败阻断、同证券防重、已有/缺失委托号意图、暂停重绑保存原账本与其他配置、未完成安装阻断、监控暂存，以及真实 PowerShell 子进程的空 JSON/数组/字符串布尔/非零退出等反例。仅假账户、假 SDK，不调用真实申购或发送真实消息。

PS7 与系统 Windows PowerShell 5.1 的四个禁用任务定义、时区、归属及 16 份脚本语法检查均通过；未注册或触发任务。实际外部监控候选从现场副本暂存，保留原哈希，3 份生成脚本语法通过。

初轮发现旧 INTENT 展示兼容问题，已恢复原状态；更新了有意改变的连接告警和重绑操作白名单预期。独立只读审查发现的安装接续、路径解析及监控健康宽松判断边界，均已修复并加入反例测试。

命令：`uv run --no-project --python 3.11 python -B -m unittest -q test_system test_panel test_run_history test_calendar_delivery test_delivery_round1 test_discovery_round1 test_release_round1 test_maintenance_round2 test_package_round2 test_ui_round2 test_final_delivery test_notification_card test_client_compat`；PS7/PS5.1 分别运行 `test_tasks.ps1`。最终源码 ZIP 解压重测和小黑只读候选结果绑定包外验收回执；这里不提前宣称生产部署或券商受理完成。

## 2026-10-20 图片通知

Windows x64、隔离 uv Python 3.11.15：`python -B -m unittest discover -q` 共277项通过（含9项新增通知回归），0失败、0错误、0跳过。实际系统PowerShell/System.Drawing生成中文PNG已目视核验；测试发送使用假HTTP端点，不连接券商、不发送真实消息。成品ZIP复测与目标机部署另据包外回执。

## 2026-10-20 已确认状态识别修复

Windows x64，隔离 uv Python 3.11.15、SQLite 3.50.4：11组完整回归共268项通过，0失败、0错误、0跳过；其中新增4项覆盖原始状态86、已报转已确认的收盘闭环、外部已确认委托去重、未知状态继续告警。PS7与PS5.1任务定义及14个脚本解析检查均通过，没有注册或触发任务。

验证命令：`uv run --no-project --python 3.11 python -B -m unittest -q test_system test_panel test_run_history test_calendar_delivery test_delivery_round1 test_discovery_round1 test_release_round1 test_maintenance_round2 test_package_round2 test_ui_round2 test_final_delivery`；`pwsh -NoProfile -File test_tasks.ps1`；`powershell -NoProfile -File test_tasks.ps1`。仅合成账户与委托，不连接生产账户；原始现场取证与生产升级另据目标机回执。

本节为本轮结果。以下日期保留历史记录。

---

2026-09-14，Windows build 26200 x64：原246项加11项缺陷/离线依赖回归，共257项全部通过，0失败、0错误、0跳过。先以隔离 Python 3.11.15 通过256项，追加外部账本导出保护后，以随包 Python 3.11.16 完成257项；最终ZIP解压回归另记于外部验收记录。PS5.1/PS7 均通过四个禁用任务定义、时区/归属和14个PowerShell脚本解析。

本机隔离目录已执行真实离线 New：无预装依赖、固定Python缓存创建、7个锁定wheel安装、pip check、Tk创建/销毁、4个实际注册任务全部Disabled、配置false/占位账号、空通知地址。ResumeNew重复执行返回already_installed。没有导入SDK或连接账户。最新最终ZIP验证、升级回滚/移动包验证、源码提交及哈希以包外验收记录为准，避免将ZIP自身哈希递归写入包内。

命令：`python -B -m unittest discover -v`；`pwsh -File test_tasks.ps1`；`powershell -File test_tasks.ps1`。257项模拟覆盖不等于券商受理。本轮未部署生产、发送消息、触发真实申购；未做全缩放/UAC账号矩阵或强制断电测试。

下文保留3.3历史材料，不代表3.4.0新增验收。

---

# 3.3.0-rc1 离线验证结果

> 本文保留网页端原始验证记录。后续 Windows 修订与生产升级记录见[Windows安装与升级验收](docs/Windows安装与升级验收.md)。

核验日期：2026-09-12。基线：`wenshao2077/QMT-IPO` main `922f7b1c193b0b3e180ea09b99dc6f1d89411629`。

## 实际执行环境与结果

在隔离 Linux 容器、Python 3.13、Tkinter + Xvfb 下，原版 **64 项通过**，本次候选代码 **109 项通过、0失败、0错误、0跳过**。Python 3.13 是本次离线测试解释器，目标 Windows 环境仍要求验证既有的 Python 3.11 x64 和券商兼容 SDK；不能混为同一环境。

本次实际执行命令：

```sh
xvfb-run -a python -B -m unittest -v test_system test_panel test_run_history test_calendar_delivery
python -B -m compileall -q .
python -B -c "from installer import payload; payload('.')"
git diff --cached --check
```

GUI 用 fake backend/controller；业务与新增回归用例在临时目录使用 fake broker、fake sender，并拦截 socket。没有执行真实 SDK、连接真实账户、发送真实 Webhook、下单或部署。GUI 布局测试使用 Xvfb 实际运行，没有用跳过代替成功。初次不带显示服务的Tk测试曾因环境报错；引入Xvfb后基线和候选版完整通过。

逐项运行记录随交付提供 `QMT-IPO-3.3.0-rc1-offline-tests.txt`。源码包和补丁的独立复现校验记录随交付提供 `QMT-IPO-3.3.0-rc1-verification.txt`。

## 覆盖范围

| 范围 | 实际离线覆盖 |
|---|---|
| 周末与休市 | 65轮周末轮询：零连接、零委托、零新增连接失败通知；七类休市工作日；国务院调休周末仍休市 |
| 正常交易日 | 真实交易日日期搭配fake连接失败，保留连接错误与未完成监控，不误报为休市或无项目 |
| 日历可信性 | 缺失、JSON损坏、结构错误、覆盖上下界、跨年、源错误、旧缓存全区间核对、有效来源冲突 |
| 更新与去重 | 独立fake数据端首次更新；20秒超时和节流；更新失败保留旧缓存；未来全年列表逐日核对；时间戳变化不重复生成事件；cycle/monitor跨轮次共享去重 |
| 旧通知处理 | 纯休市可用性错误可审计抑制；原正文/送达标记不改写；真实结果、混合内容和有意图日期保留；日历未知不丢消息 |
| 不退化交易行为 | 保留原持久化意图、旧账本去重、午间等待、数量/价格算法、不确定回报不重报、通知失败重试等用例 |
| 界面与隐私 | 休市/未知正确呈现；连接和查询错误区分；抑制不伪装送达；配置校验不发送；测试通知取消不发送、确认后只调用fake发送器；脱敏 |
| 新安装/升级/回滚 | 默认关闭；外部旧state_dir和私有配置逐字节保留；历史记录保持；缺失账本拒绝；预览/通知/备份不重建账本；仅代码回滚不恢复旧账本 |
| 路径与保留 | 中文及空格目录实际创建/升级/回滚；无shell参数列表；禁用/维护启动不创建worker；原回执逐字节归档可读；健康备份数量保护 |

新增45项：`test_calendar_delivery.py` 40项、GUI4项、错误显示区分1项。原64项保留，其中3个旧期望按新要求更新：日历闸门不再信赖连接后的fake标志、两处“无权限”改为“程序未启用/未据此核实券商权限”。没有删去原去重、午间、委托不确定性或通知重试用例。

## 明确未执行的项目

**没有 Windows / 小黑运行环境，也没有实际运行 PowerShell。** `test_tasks.ps1` 已编写，其语法/任务定义/时区/路径归属检查仍须 Windows 实际执行；安装、UAC/ACL、真实快捷方式、无窗口启动、任务触发和升级中断恢复不能凭Linux测试宣布通过。

没有验证券商miniQMT/SDK实际返回类型、历史与未来日历的实际覆盖和下载联网行为、账户登录/交易权限；这些仅依据官方文档实现并以fake输入覆盖，需本机只读验证。

没有访问 QuantClass 客户端或数据库；period_offset/offset 的结构、覆盖、日期语义未验证、未集成。

没有本轮完整候选代码 CI 成功记录。GitHub仅成功创建预备分支和源码导出工作流，随后批量源码写入被拦截，完整代码未推送，也没有完整PR。随包工作流是待本机接续提交后运行的离线CI，不是已执行证据。

## 交易语义及生产验收

价格、数量、最小单位、市场范围、申报窗口和不确定委托只核对规则未变。改变的是进入交易流程的日历条件、缺失账本拒绝重建、监督/隐私/交付边界。109项测试通过不构成真实券商受理验收，也不保证所有券商环境兼容。

本机TARS获得Owner许可后，应先审查与跑Windows离线测试，再依README升级；检查配置哈希、原账本路径和意图、任务开关，独立只读核验SDK，最后由已获准原计划自然触发并按券商回报完成真实验收。本次没有启用或手动触发任何真实任务。
