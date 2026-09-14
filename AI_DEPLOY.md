# AI 部署协议（3.4.0）

本版有源码包和 Windows 离线包。离线包先用顶层 setup.ps1 VerifyPackage，再 Plan；New/ResumeNew 自动使用已校验持久 Python 缓存和本地 hash-locked wheels，不访问包索引。Upgrade/Rollback 需要已获准的 -ConfirmMaintenance，保持原虚拟环境。详细操作以 docs/FINAL_DELIVERY.md 为准。下文自备 Python、联网安装依赖的说明仅适用于源码包。

本文件供具有本机文件/命令权限的部署代理使用。只聊天而没有本机执行能力的 AI 可以指导，不能报告“已部署”。完整操作集合、字段和权限见 DEPLOY_PROTOCOL.json。统一入口是解压后源码目录的 setup.ps1；不要临场改写安装器。

## 默认流程

用户批准本机部署范围后，读取版本、来源和清单；VerifyPackage 成功只证明完整性，不证明来源可信。Plan 指明 New、ResumeNew、Doctor 或需审查的 Upgrade；不猜测路径。Doctor 无 Root 时只检查本地运行环境。

New 创建独立目录和禁用任务。需要下载依赖时单独请求用户明确许可；不能静默添加 `-InstallDependencies`。本候选包未包含 Python 或完整离线依赖，需要已经安装的 Python 3.11 x64。

Configure 打开本机向导，由用户填写账户和 Webhook。代理不得要求用户将密钥粘贴到聊天或命令行。向导只支持未授权、未使用的新实例；旧实例更换账户不在此流程中。

Doctor 后输出脱敏摘要：安装阶段、配置/环境问题、任务一致性、仍未验证的账户权限/券商受理，以及下一步是否需要用户单独批准。停止于已安装/已配置且未启用。不要把读取接口的检查插入安装步骤。

## 稳定入口示例

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation VerifyPackage
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation Plan -Root 'D:\QMT IPO Test'
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation Doctor -Root 'D:\QMT IPO Test'
```

非交互入口返回一份 JSON。`ok=true` 仅对 `operation` 的限定结果负责。退出 0 不等于交易已启用；退出 2 表示检查/恢复阻断，原生安装失败也可能有其他非零码。`local_log` 指向本机日志，不是上传授权。异常信息只报告稳定错误码，不回显密钥或完整私有配置。

## 恢复与维护

ResumeNew 必须使用同一份原始源码包；只接续有完整身份回执的新安装。已完成、已授权、配置变化、存在委托/通知历史或账本缺失，不允许通过重新 New 修复。

source_started 有两种可接续状态：还未生成源码和账本，或源码、默认配置、空账本均完整且能独立核验。部分初始化不能独立证明完整时必须人工核验，不能重新初始化账本。

配置中断留下 `configuration-pending.json`；维护失败留下 `maintenance.json`。这两种情况没有 AI 自动清除标记的权限。正常异常回退失败也必须保留标记。

Upgrade/Rollback 只在用户另行批准维护后运行，保留原配置、账本路径、委托历史及任务开关；不能自动升级 SDK、触发交易或回滚数据库。不要把 `--quiesced` 当确认过程的替代品直接调用内部更新器。

## 建议的部署回执

报告版本、包清单指纹、操作、阶段、环境检查结果、任务一致性、错误码与下一步。明确列出：未读取真实密钥/未连接账户/未发送通知/未提交申购。若部署目标已是原授权实例，准确写“保留原开关”，不能误报“现已禁用”。

## 第二阶段只读维护与本地导出

Health、CalendarCoverage、RecoveryPlan都不连接账户，不发送消息，不更改任务/配置/委托。RecoveryPlan只生成待办，不表示已经修复；不得根据其中的建议自动删除维护标记。

ExportSupport需要同时指定Output与ConfirmExport；只创建脱敏摘要ZIP，拒绝覆盖既有文件和在安装/状态/QMT目录内输出。ZIP含数量/状态/时间与组件版本，分享仍由用户决定；没有上传动作，也不是账本备份。

VerifyArchive接受Archive和可选ExpectedSha256，不执行待验证ZIP中的代码。期望哈希必须来自独立可信来源；使用同一个不可信包内的哈希不能验证发布者。COMPONENTS.json是声明的直接依赖清单，不是完整传递SBOM或厂商再分发许可。

所有维护结果必须区分操作完成、状态是否清晰、是否存在告警、是否曾受理。Health可能ok=true且存在warning，RecoveryPlan的ok=true仅表示计划生成。SQLite只读访问可能管理WAL/SHM辅助文件；不删除辅助文件，不以immutable方式忽略活动WAL。

第一阶段验收以用户确认记录；本轮未获得其详细主机/券商证据，不扩写兼容矩阵。新的Windows增量需要绑定alpha2包指纹验收。
