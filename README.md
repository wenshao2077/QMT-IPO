# QMT 自动打新助手 3.4.0-alpha2

**第二阶段完整源码候选包。第一阶段alpha1已由用户确认验收；本alpha2增量尚未在本轮执行Windows实机验收。**

基线是已验收的alpha1完整ZIP，不是GitHub旧main或仅版本标识的分支。基线包SHA-256、改动和限制见 `docs/ROUND2.md`；实际测试见 `docs/ROUND2_TEST_RESULTS.md`；Windows增量清单见 `docs/WINDOWS_ROUND2_ACCEPTANCE.md`。历史验收范围不自动扩写为当前版本的实盘认证。

人工先打开 `使用说明.html`；AI 先读 `AGENTS.md` 和 `AI_DEPLOY.md`。本包是完整源码候选包，不是补丁，不需要逐文件覆盖现有安装。

## 第二阶段新增

控制台“维护与诊断”汇总日历缺口、通知送达时间和健康问题。暂停申购不再隐藏账本/通知问题；通知读取失败显示未知，不冒充零条。未来120天本地日历检查提供90/30/7天分级预警，未知或冲突仍然禁止提交。

统一入口新增 Health、RecoveryPlan、ExportSupport、CalendarCoverage、VerifyArchive。脱敏包只保存到本机，不含原始日志/数据库，不自动上传；恢复建议只读，不执行修复。操作和准确返回值含义见 `docs/MAINTENANCE.md`。

发布包附SOURCE_IDENTITY和直接组件清单，校验所有文件与压缩包结构；未获得发布签名或完整离线依赖许可。SQLite运行时版本告警与只读WAL辅助文件边界见 `docs/RUNTIME_ADVISORIES.md`。

## 当前实现

本地环境检查覆盖 Windows、Python 3.11 x64、固定依赖的安装元数据、Tk 模块、配置、账本、日历及任务状态。不会导入交易 SDK；元数据正确不证明客户端二进制兼容、账户权限或券商受理。

首次配置使用本地图形向导，账户和 Webhook 留在本机。只处理无授权、无实盘运行记录、空委托/通知账本且四个任务均禁用的新实例。已运行实例需要独立维护流程，不能使用首次配置向导更换账户或重建账本。

新安装有持久化阶段回执。依赖安装、禁用任务注册、快捷方式等步骤失败后，可以在严格身份核验下接续。部分源码/账本初始化失败、账本缺失或损坏、已有业务活动、私有配置被改动等情况停止并保留证据，**不承诺所有中断自动修复**。

“完成”指截至本轮；10:00、11:00、13:00、14:00、14:40 做有限再查询，15:05 收盘核对。错过时点由既有五分钟计划的下一次实际唤醒执行；没有新建额外交易计划。晚到项目、早期零额度可以重新检查，只有从未产生意图的项目才可能提交。已有意图、已报或不确定结果都不重发。

## 安装前提

目标是 Windows x64、**已安装的 Python 3.11 x64（含 Tkinter）**、券商允许的 miniQMT 模式、用户自己的客户端和 SDK 权限。本源码包不含 Python 安装程序、SDK wheel、完整离线依赖、券商客户端或签名材料。首次下载依赖需显式允许网络访问；不能把此候选包称为“解压即可运行”或“完全离线安装”。

依赖沿用 `xtquant==250807.1.2`、`numpy==1.26.4`、`pandas==2.2.3`，以 `requirements.txt` 为准。没有经过验证的通用券商矩阵；`COMPATIBILITY.json` 中真实券商验收名单暂为空。

仅单机单账户；SH/SZ 和显式允许的科创板新股；不含 BJ。股票数量仍按市值额度、发行上限及发行单位计算；债券不使用股票额度，仍按原上限处理。未扩大市场、价格、数量规则或提交时段。配置允许某市场不等于券商授予权限。

Windows 计划使用已登录的交互式用户；不保存系统密码。不自动启动/重启 QMT，不保证注销、断电或睡眠时执行；睡眠唤醒等目标 Windows 行为仍需验收。

## 人工安装（隔离验收环境）

先核对来源、压缩包 SHA-256 和解压后的 `DELIVERY_MANIFEST.json`。清单不是发行者签名。源码解压目录与安装目录必须分开，不能放到彼此内部，不能直接在压缩软件中运行。

双击 `开始安装.cmd`，选择 New install，输入新的安装目录。只有明确输入 YES 才允许下载依赖。完成后选择 Configure locally 打开本机向导，再选择 Local checks。向导不启动 miniQMT、不连接账户、不发送通知、不启用交易。

也可从解压后的源码目录明确执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation VerifyPackage
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation Plan -Root 'D:\QMT IPO Test'
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation New -Root 'D:\QMT IPO Test' -InstallDependencies
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation Configure -Root 'D:\QMT IPO Test'
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation Doctor -Root 'D:\QMT IPO Test'
```

这些是安装/本地检查命令，**不是生产部署授权**。没有 Python 3.11 时先安装获准来源的运行时；不要由 AI 下载未知安装器、关闭杀毒软件或全局更改 PowerShell 执行策略。

## 安装中断

保留原源码包和目标目录。运行 Plan 判断；只有与本次源码清单匹配、从未投入使用的新安装才可接续：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation ResumeNew -Root 'D:\QMT IPO Test' -InstallDependencies
```

ResumeNew 不等于重新安装。缺失账本、账本已写入意图、存在授权、数据路径改变或初始化状态不明时停止。不要删 `state.sqlite3`、`new-install.json`、维护标记或配置写入标记来让检查通过。

配置写入若被硬中断，会保留 `runtime/configuration-pending.json`，检查与执行拒绝继续。该场景当前提供安全阻断、记录和只读恢复建议，需本机人工核验后按审查流程恢复，不是自动恢复入口。

## 三种不同的“通过”

| 层次 | 意义 |
|---|---|
| 包/本地检查通过 | 只说明当前检查项满足；不证明账户权限 |
| 单独授权的账户只读检查通过 | 只说明读取接口可用；不证明申购已受理 |
| 正常授权流程下的券商回报核对 | 才是对应业务场景的实盘验收证据 |

安装流程的默认终点为“已安装/已配置，尚未启用”。旧控制台的账户检查、测试通知、启用按钮仍按各自的用户确认边界运行，**本轮网页开发不执行这些操作**。

关闭控制台不等于暂停；暂停停止后续申购计划，不撤单，也不保证立即停止已开始的一轮。券商已报/已成不等于中签。本工具不做卖出、缴款或资金划转；中签及缴款请在券商渠道核实。

## 升级与回滚

本候选代码应先完成隔离 Windows 验收，不应直接升级现有生产 worker。确需获准维护时，使用原根目录和独立的、已审查新源码包，关闭控制台/配置向导并等待 worker 自然结束；不要逐文件热拷贝。

`setup.ps1 -Operation Upgrade -Root <原根目录>` 调用原受控升级机制。升级不改依赖、不换账户、不移动账本，保留原开关，不主动触发任务。源码身份回执随代码更新。

`setup.ps1 -Operation Rollback -Root <原根目录> -RollbackId <已核验ID>` 只恢复代码及其版本身份，绝不恢复过期数据库、抹除后来产生的意图或回报。回到旧版会恢复其已知行为限制，不能把回滚当业务状态重置。

## 开发与离线验证

```text
python -B -c "from installer import payload; payload('.')"
xvfb-run -a python -B -m unittest -v test_system test_panel test_run_history test_calendar_delivery test_delivery_round1 test_discovery_round1 test_release_round1 test_maintenance_round2 test_package_round2 test_ui_round2
```

Windows 下省略 `xvfb-run -a`，并运行 `test_tasks.ps1` 的禁用任务定义/脚本解析测试；它不注册、不启用任务。真实 Windows 安装/UAC/快捷方式/恢复仍是独立验收。

`build_release.py --output <源码目录外的新ZIP路径>` 仅从已核验清单生成候选源码包，不包含私有状态，也不代表已签名或已完成生产验收。
