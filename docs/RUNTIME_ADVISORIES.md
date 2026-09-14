# 运行时与只读边界说明

核对日期：2026-09-14。仅记录官方依据和诊断提示；本轮没有下载/替换Python、SDK或SQLite二进制。

## SQLite WAL-reset

SQLite官方文档描述了特定WAL并发写入/检查点条件下可能导致损坏的竞态缺陷，官方识别的修复版本为3.51.3及以后，以及3.44.6、3.50.7维护分支。发生条件严格，不等于本产品已经发生过损坏。

环境检查会记录 sqlite3.sqlite_version，并通过上游版本号识别修复。供应商回移补丁不能单靠旧版本号判断，必须审查其构建材料。提示不自动改动既有授权，不静默更换SDK/DLL，不切换日志模式。正式运行环境验收应包括实际SQLite版本及适用修复，不能只写“Python 3.11”。

来源：SQLite官方 Write-Ahead Logging，第11节：
https://sqlite.org/wal.html#walreset

## 只读数据库也可能需要辅助文件

SQLite官方第5节说明，只读WAL访问可能需要已存在或能够创建的 -wal/-shm 文件。因此本轮的“只读”是：mode=ro连接、不初始化/迁移数据库、不改写交易历史，而不是许诺所有文件系统元数据和辅助文件完全不变。

不使用 immutable=1 对活动生产账本作只读优化；不能通过忽略或删除WAL来规避辅助文件。维护摘要的SQL查询设定了时间预算，以失败/未知返回，不无限阻塞。

来源：
https://sqlite.org/wal.html#readonly
https://docs.python.org/3.11/library/sqlite3.html

## ZIP完整性不等于发布来源真实性

ZIP可能具有重复成员、异常路径、链接、压缩放大或不一致的成员尺寸。本轮限制源包大小/文件数量/成员体积、核对每个允许文件，拒绝未列文件与Windows大小写碰撞。校验不会执行包内代码；可信来源仍需独立哈希或后续签名机制。

来源：
https://docs.python.org/3.11/library/zipfile.html
