# 源码同步交接：第二阶段

当前代码：3.4.0-alpha2，基于用户已验收的alpha1完整源码包，而非GitHub旧main。

本轮已读取远端main，仍指向bd667bf8dd072e8467b91432f1eb9986bce43e15。GitHub单文件tree对象写入成功，但未形成全量源码提交、分支更新或PR；一个未被分支引用的tree不是可部署版本。不能把远端旧main或第一轮仅版本标识分支当作本轮候选。

本轮正式交付为完整候选ZIP、相对alpha1的增量补丁，以及相对上述远端main的累计补丁。源码工作区按其实际基线选用一种补丁，不能重复叠加两种补丁；运行中的安装目录不能使用git apply或手工热拷贝。

同步前核对工作区干净、基线提交/包指纹；使用review分支，git apply --check后再应用。执行全部246项离线测试、包清单校验和最终ZIP解压重测；Windows/PowerShell增量按清单本机验证。没有合并main、发布正式Release或生产部署的默认授权。

SOURCE_IDENTITY.json和COMPONENTS.json由构建器为ZIP生成；源码清单及依赖说明不代表签名、完整传递SBOM或再分发许可。操作说明和Windows验收要求见README、docs/ROUND2.md。
