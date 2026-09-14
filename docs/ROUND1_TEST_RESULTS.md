# 第一轮实际测试记录

日期：2026-09-14。代码身份：3.4.0-alpha1，基线 bd667bf8dd072e8467b91432f1eb9986bce43e15。

## 已执行

环境：Linux x86_64 / CPython 3.13.5 / Xvfb。**这不是声明的目标 Windows/Python 3.11 运行环境验收。** 本轮新增兼容性检查对环境的测试使用明确注入的元数据。

实际命令：

```text
python -B build_manifest.py
python -B -c "from installer import payload; print('MANIFEST_OK',len(payload('.')))"
xvfb-run -a python -B -m unittest -v test_system test_panel test_run_history test_calendar_delivery test_delivery_round1 test_discovery_round1 test_release_round1
```

结果：**165 项运行，165 项通过，0 失败，0 跳过**。原四组 109 项全部保留；新增环境/向导/恢复/部署 43 项、协调器重新发现 9 项、构建 4 项，共新增 56 项。完整原始 stdout/stderr 日志随外部交付材料提供。本文件记录的是组装源码目录上的实际运行，不冒充 Windows 或实盘结果。

所有涉及账户/消息/业务提交的测试均使用假数据、fake broker 和 fake sender；测试阻止 socket 连接。没有导入和运行真实交易 SDK，没有真实账户连接、真实通知或券商申报。GUI 使用 Xvfb 构造及交互测试，没有用“跳过 GUI”替代通过。

新增重点覆盖：依赖与位数错误、无 SDK 导入、首次配置禁用、不能迁移账本、已有授权/意图/通知/实盘记录拒绝、保存互斥、普通写失败回退/回退失败留阻断标记、完整初始化后断点接续/部分初始化拒绝、来源变化/丢失账本/已有活动拒绝、源码身份升级回滚、脱敏单 JSON、任务状态不一致、有限时点重新发现、午间/收盘不提交、未知意图不重发、ZIP确定性/白名单/拒绝覆盖和错误清单拒绝。

## 未执行，不能标记通过

Windows PowerShell 5.1/7 解析及真实执行；Windows/Python 3.11 完整回归；本机安装、普通用户/UAC/ACL、真实 Win32 命名互斥、计划任务注册/启动、快捷方式、中文路径及休眠/DST；真实环境故障注入与升级回滚演练；真实 SDK/客户端二进制兼容、账户只读接口、通知送达或申购受理。

Linux 下 native mutex 测试验证的是平台拒绝分支；其 Windows 竞争分支还没运行。安装回执测试不等价于实际 Windows 安装成功。

候选工作流已改为全部测试成功后才构建源码 artifact，但没有提交完整改动到 GitHub，**本轮工作流未执行**。2026-09-12 旧 CI 的成功仅用来取得已核验基线，不是本轮 CI 结果。

## 后续验收依据

Windows 逐项清单见 WINDOWS_ROUND1_ACCEPTANCE.md。最终交付 ZIP 的外部核验日志记录解压后检查；如文件哈希与验收时不同，应重新运行测试，不能复用本记录冒充通过。
