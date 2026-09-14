# 第三阶段整合与验收修复

日期：2026-09-14。统一版本：3.4.0。

## 来源

- 3.3 基线：bd667bf8dd072e8467b91432f1eb9986bce43e15。
- 用户给定包 SHA256：7ba87d06cfb6621560dd596f7db98ee5a79251644d1674ad9ff75262ca896434；内部实际为 alpha2。
- 第三阶段远端：review/delivery-round3-20260914，9e48ae7；只增加两份 delivery 脚本与三个材料/冒烟工作流，未含 alpha2 完整源码。
- 公开运行时与依赖材料取自该仓库工作流 34805859480，逐一核对来源记录与固定哈希。前期第三阶段入口只对 stub payload 冒烟；本轮整合真实 payload，不能移用 stub 成功为完整安装成功。

## 需求到实现

| 范围 | 最终实现 | 验证入口 |
|---|---|---|
| 统一最终版本与整包构建 | release_info.py、build_release.py、build_distribution.py | 源码/离线包解压重测及版本核对 |
| 离线自带 Python/Tk | 固定3.11.16材料，受保护持久缓存 | delivery/verify.ps1，运行时/虚拟环境搬移检查 |
| 全部依赖离线安装 | dependencies.lock.json、requirements-offline.txt、offline_dependencies.py | 每个wheel哈希、pip无索引且require-hashes，pip check |
| 人工/AI统一入口 | delivery/setup.ps1 → payload/setup.ps1 → install.ps1 | PS5.1/7入口及保护分支，真实payload参数校验 |
| 升级保持原环境 | 非New/ResumeNew使用原.venv，不传依赖安装参数 | 原升级回滚测试和入口冒烟 |
| 原ZIP多根/身份不一致 | 单根源码ZIP与单根离线ZIP分开定义、各有完整校验 | 原包回归；不放额外未定义release根冒充源码ZIP |
| 目录验包遗漏 | 目录与源码ZIP共享verify_contents | test_final_delivery.DirectoryTests |
| Store异常未释放 | 构造失败显式close再抛出 | 损坏库、连接关闭跟踪及Windows重命名 |
| Doctor漏报维护 | 优先报告source_maintenance_pending | 隔离维护标记回归 |
| 配置中断恢复指引 | 只读结构检查与运行闸门分开，仍拒绝执行 | 原始标记保留、账本摘要可见、运行仍被拒绝 |
| Windows路径测试错误 | as_posix统一测试路径 | 原246项回归加最终交付回归 |

不新增交易策略、市场或补报通道。本轮属于交付完整性和可维护性修正。测试环境、数量、通过与未测项目以 TEST_RESULTS.md 和最终ZIP外部验收记录为准。
