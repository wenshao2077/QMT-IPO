# QMT-IPO 主线同步交接

## 当前版本与依据

- 统一主线：`master`；产品版本：3.4.0。
- 已交付源码基准：`7675eef6a36e87618890568cc8567187b3c58978`（原 `codex/qmt-ipo-3.4-final`）。该版本整合 alpha2 和第三阶段安装入口，并完成最终交付验收。
- 旧默认 `main` 为 `bd667bf8dd072e8467b91432f1eb9986bce43e15`，仍是 3.3.0-rc1，不能作为最新版本。
- 2026-09-14 Owner 明确授权归档已提交论坛帖，并将历史支线正常合并到 master、同步 GitHub。

## 分支整合

| 原分支 | 原头提交 | 处理 |
|---|---|---|
| main | bd667bf | 已是最终交付分支祖先 |
| fix/calendar-delivery-20260912 | 922f7b1 | 已是祖先 |
| codex/qmt-ipo-3.3-windows-acceptance | 5d604fe | 已经由 main 的 PR #1 合并 |
| codex/qmt-ipo-3.4-final | 7675eef | 作为 master 的已验收源码基础 |
| review/delivery-round3-20260914 | 9e48ae7 | 普通 merge；补入三份遗漏工作流，冲突保留已验收的 setup/verify 脚本 |
| review/delivery-round1-20260914 | 32a675c | 普通 merge；alpha1 版本标识已被最终 3.4.0 取代 |
| improve/calendar-gate-portable-delivery-20260912 | 88fb9e1 | 普通 merge；早期源码构建步骤已被含完整测试的最终工作流取代 |

保留所有历史提交的可达性，未使用强推或重写历史。删除旧远端分支前逐一检查其原头提交是 master 的祖先；默认分支切换为 master 后才清理旧 main。

## 本次集成修正

1. Offline verification 的 push 触发改为 master。
2. 恢复第三阶段材料、运行时和统一入口冒烟工作流。材料工作流支持复用，两个冒烟工作流先生成同次运行的材料，不再依赖固定 run-id 的短期历史 artifact。
3. 源码包构建与验证显式接受上述三份受哈希约束的工作流，保留对未列文件、篡改文件的拒绝；补充合包和篡改回归。
4. 更新过期的 alpha2 同步说明及主线规则，补上 LF 行尾约定并重建清单。

本次没有改变申购业务、券商接口、配置、持久化账本或部署目标。论坛安装包仍对应已经发出的原始 3.4.0 构建，不把本次源码/CI 整合冒称重新部署。

## 已提交论坛帖

- 【工具分享】QMT 自动打新助手 3.4（附安装包）：https://bbs.quantclass.cn/thread/89098
- 发布时间及编辑时间：2026-09-14 15:32（Asia/Shanghai）。收录时待管理员敏感词审核，不代表审核通过。
- 全文与发布信息保存在 stock-quant-fen 的 `文少已发布帖子/`；离线归档和大附件留本地，不进入本仓库。
- 论坛受单附件限制提供 app-runtime、xtquant、offline-deps 三个 ZIP，必须全部解压到同一父目录合并同名根文件夹；共 4077 个条目逐文件与原始离线包相同。

## 验证

本次实际命令、测试计数和远端 CI 结果写入本地分支整合验收记录；原 257 项交付证据仍只绑定 7675eef 和原交付 ZIP。源码清单及依赖说明不代表发行签名或完整传递 SBOM。
