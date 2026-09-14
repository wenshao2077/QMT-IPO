# 长期运行与故障处理

## 先看哪个状态

计划启用只表示计划与开关一致，不等于客户端已登录、额度可用、委托已受理或中签。暂停只阻止后续批次，历史待核对意图和通知失败仍需处理；已经开始的批次可能结束。

“维护与诊断”显示日历首次未知日期、已实际检查到的日期、最近已知的通知确认时间与健康问题。没有送达时间时显示未知；没有收到消息不是成功证据。电脑断电、所有任务不运行或同一网络链路失效时，本程序不能保证远程告警。

## 人工操作

打开控制台“维护与诊断”。“查看恢复建议”只读，不执行修复；“导出脱敏诊断包”先确认，再选择安装目录、QMT目录及状态目录以外的新ZIP路径。

诊断包只含 `status.json`、文件校验清单和说明。状态含环境版本、日历覆盖、任务状态、失败/待核对数量及时间。没有账户/账户摘要、证券代码、委托标识、Webhook、私有路径、消息正文、原始日志、数据库或私有备份。分享前在本机审阅；程序不会上传。诊断包不是数据库备份。

## AI与命令行

在已核对的源码包中运行；路径仅为示例。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation Health -Root 'D:\QMT IPO'
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation CalendarCoverage -Root 'D:\QMT IPO'
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation RecoveryPlan -Root 'D:\QMT IPO'
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Operation ExportSupport -Root 'D:\QMT IPO' -Output 'D:\Support\qmt-support.zip' -ConfirmExport
```

Health 的 `status=attention` 与 issues 是主要判据；`ok=true` 只表示没有 critical 阻断项，仍可能有需要处理的 warning。RecoveryPlan 的 `ok=true` 只表示计划已生成，`needs_attention` 指明是否有待办；不证明环境已经恢复。`changes_applied=false` 指未执行应用层业务/配置/任务变更；只读SQLite仍可能产生或使用WAL/SHM文件。

代理只读取结构化摘要；不得为生成更详尽报告而自动上传原始日志、数据库或私有配置。账户只读查询、测试发送、行情联网刷新、交易启用均仍需各自授权。

## 故障处理原则

| 发现的问题 | 下一步 | 禁止操作 |
|---|---|---|
| 日历覆盖到期/冲突 | 从可信维护渠道获取年度公告包，核对来源后显式导入 | 猜下一年度、把未知改成交易日 |
| 有不确定意图 | 用户授权后，只读核对券商委托 | 清除意图、手工补报 |
| 账本丢失/损坏 | 保留现场，核对备份与券商侧事实，由维护人员审查 | 新建空库、用旧备份覆盖现有委托历史 |
| 安装中断 | 用原来确切源码包运行 Plan，再判断是否允许 ResumeNew | 删除回执后 New |
| 维护/配置标记存在 | 核对原维护记录、代码身份及配置原件 | 删除标记后盲目启用 |
| 通知连续失败 | 本地检查网络和私有通知配置，真实测试单独确认 | 标记 delivered 伪装已送达 |
| SQLite运行时待审查 | 核对官方修复与SDK兼容组合，安排受控更新 | 直接替换DLL或自动切换账本模式 |

已有年度包导入仍使用 `calendar_tool.py import --config <本机配置路径> --annual-file <年度JSON> --reviewed`。导入保留旧包的原始备份，不连接交易账户，不启动任务。`--reviewed` 表示操作者确认来源已经审查，不是自动验证官方签名。

## ZIP核对

在已经信任的工具副本上执行 `package_verify.py --zip <待核验ZIP> --expected-sha256 <从独立可信渠道取得的哈希>`，或统一 `setup.ps1 -Operation VerifyArchive -Archive <ZIP> -ExpectedSha256 <哈希>`。不执行待核验包里的脚本。

完整性哈希与源代码清单不能替代发布者身份验证；不要仅信任可疑ZIP内自己的校验脚本和校验值。当前交付仍没有数字签名与内置Python/SDK。
