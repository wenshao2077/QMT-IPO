# 第二阶段实际测试记录

日期：2026-09-14。版本：3.4.0-alpha2。基线：用户确认验收的alpha1源码ZIP（指纹见ROUND2.md）。

## 本轮实际执行

环境：Linux x86_64 / CPython 3.13.5 / SQLite 3.46.1 / Xvfb。没有Windows或PowerShell运行时。SQLite运行时的官方修复提示会要求进一步审查；没有修改运行库，参见RUNTIME_ADVISORIES.md。

基线包在修改前实际复测165项，全部通过。当前源码执行246项，全部通过，0失败、0跳过：保留165项旧测试，新增81项测试。旧测试中仅调整DEPLOY_PROTOCOL操作集合的精确期望，以包含新增的五个非交易入口；不删除原安全检查。

命令：

```text
python -B build_manifest.py
python -B -c "from installer import payload; payload('.')"
xvfb-run -a python -B -m unittest -v test_system test_panel test_run_history test_calendar_delivery test_delivery_round1 test_discovery_round1 test_release_round1 test_maintenance_round2 test_package_round2 test_ui_round2
python -B build_release.py --output <源码目录以外的新ZIP>
python -B package_verify.py --zip <上述ZIP>
```

新测试覆盖：跨年/周末/覆盖缺口/稳定事件身份；暂停后通知故障；历史消息时间不伪造；损坏和丢失的监控账本返回未知；只读活动WAL可见性；不迁移或重建账本；脱敏输出及本地导出确认；输出拒绝覆盖、私有目录和部分写失败；只读恢复计划；SDK版本元数据过滤；SQLite官方修复版本识别；ZIP路径/大小写/链接/重复/体积/哈希/身份；静态源文件白名单；图形界面取消/未知/告警/维护操作。

最终ZIP另行解压复测，确切ZIP指纹、计数和结果写入随交付的外部verification.json与完整日志。包内文档不递归写入本ZIP自己的哈希。CI文件已补上Windows/Linux最终ZIP解压重测门槛；本轮没有把尚未执行的远端CI写成通过。

实际打开了使用模拟数据的Tk界面并检查“维护与诊断”布局；这不是Windows桌面或实盘运行截图。

## 修正与准确边界

开发期间发现并修复：控制台后备配置缺control_dir；旧协议测试需要声明新增入口；SQLite只读查询会产生WAL/SHM辅助文件。最后一项通过增加准确的只读说明和验证原文件/委托内容不变处理，不使用immutable忽略活动WAL，也不删除辅助文件伪装无变化。

生产账户、真实Webhooks、真实委托、生产调度和QMT进程没有访问或改动。没有真实SDK连接、测试通知或提交。没有宣称Windows/PowerShell/客户端兼容/券商受理验收；这些新增验证见WINDOWS_ROUND2_ACCEPTANCE.md。
