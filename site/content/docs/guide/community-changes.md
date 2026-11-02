---
title: 维护者保存源码提案与候选提交
---

先按[准备队列指南](../community-queue/)形成准确当前计划及已prepared的work。准备成功只表示准备阶段结束；这份指南提供可审阅源码和本地提交，不执行候选代码、不调用模型，也不创建远程PR。所有命令在本项目Git克隆的`backend`目录运行，沿用原公开repo数字ID、名称和标签。

新提案使用两个相互独立的专用目录：原公开需求库和新的提案库。它们不能互为父子目录，也不与个人视频数据、模型配置、输入JSON或其他文件混放。提案库只保存本地`changes.sqlite3`及其SQLite日志。初始化前保存一致备份；这一步不迁移个人视频数据，也不改变原需求库表结构。

在目录外准备UTF8 JSON。必须替换实际work ID、原文件64位SHA256及修改后完整源码；下面说明输入形状，不能直接作为完成的代码运行：

```json
{
  "request_key": "change-original-001",
  "work_id": "实际已prepared的work ID",
  "title": "明确说明本次代码变化",
  "changes": [
    {"path":"scripts/features.py","before_sha256":"0000000000000000000000000000000000000000000000000000000000000000",
     "action":"write","text":"该公开文件修改后的完整LF源码，末尾换行\n"},
    {"path":"backend/tests/test_community_candidate_register.py","before_sha256":null,
     "action":"write","text":"填写可检查需求行为的完整候选测试源码，末尾换行\n"}
  ],
  "test_files": ["backend/tests/test_community_candidate_register.py"]
}
```

`scripts/features.py`仅在原需求明确关联F-015.01等登记中列出该路径的功能时适用，不能替其他功能借用。`before_sha256`来自准确公开main提交里的原始字节，不是本机尚未提交的文件。先核对`git ls-remote https://github.com/weicaojava/fawnimo.git refs/heads/main`返回的公开SHA，再在包含该对象的公开克隆中计算，例如替换下面的公开SHA和具体路径：

```sh
python -c "import hashlib,subprocess; print(hashlib.sha256(subprocess.check_output(['git','show','公开SHA:具体文件路径'])).hexdigest())"
```

修改已有文件填写真实旧哈希；新候选测试填写null且必须尚未存在。新测试文件名遵循上述前缀，测试文字和Issue内容都作为数据保存。替换文字为UTF8/LF，文件非空时以换行结束。当前只支持有界Python文字变更，拒绝越界路径和控制器/审核规则/CI/依赖修改。

首次明确初始化并保存：

```sh
python develop_community.py --changes-root <独立提案目录> --root <原独立需求目录> --repository-id 1438202832 --initialize-changes --prepare-file change.json
```

以后省略`--initialize-changes`。非默认repo名称或标签使用原值。新提案真实读取原公开Issue和固定GitHub代码接口，确认原准备/来源仍当前、公开main与安装代码的功能登记一致、原始文件内容未变化；来源/网络/版本不符时不保存新提案，不自动循环重试。限流时保留原输入，先查看既有记录，不用新标识盲目重复提交。

输出包含bundle ID、原输入/scope/所选公开代码哈希、修改前后文字、patch及将来使用的候选分支名称。保存bundle不会创建本地或远程分支。用`--get-bundle <bundle ID>`读取准确原提案，`--history <work ID>`查看该work的提案列表；这两种操作只需原`--changes-root`和repo配置，不需网络或原需求目录。

回复丢失时保留完全相同的`change.json`和`request_key`再次执行`--prepare-file`。原请求恢复先返回已保存的准确提案，来源后来关闭、main更新或原需求目录暂时不可用，也不把原内容改成新版本。修改任何输入使用新的明确标识。旧结果会继续要求新执行另做来源检查，不把旧观察当长期授权。

在本项目Git克隆里形成准确本地候选提交：

```sh
python develop_community.py --changes-root <独立提案目录> --root <原独立需求目录> --repository-id 1438202832 --create-local-commit <bundle ID>
```

本地须已拥有原公开base提交对象；缺少时按正常公开仓库更新流程取得对象后复查，工具不自动fetch。新候选提交再次核对原Issue、main和原准备，使用独立index读取已提交公开对象，以CAS写入由bundle ID派生的本地`community/change-…`分支。原工作目录的未提交内容保留，候选源来自提案的准确文字。这个命令不切换HEAD，也不执行候选测试。

回复丢失后重复相同bundle ID，会核对并返回原本地commit/tree/base，不生成另一个提交；已有准确候选恢复可以省略原需求目录。保留返回的bundle、分支、commit、tree和parent以供后续审查。分支已属于另一份结果时停止，不强制覆盖。公开main或计划后来变化时，新创建的候选需要当前准确来源，不能改旧bundle来伪装原请求恢复。

Windows并发Git对象写入由内核互斥约束。标识文件留在准确Git对象目录的`info`中，正常进程退出由内核释放锁，程序在剩余操作期限内等待实际所有权；不按PID猜测删除标识。未知标识或权限失败时保留原提交与提案，核对该公开克隆的准确Git元数据和运行状态，再恢复原请求，不做全局Git清理。

这些命令由操作系统维护者运行，没有公共写接口；同一系统用户间的权限隔离不由SQLite或锁文件提供。回退时停止使用该维护工具，保留原提案库的一致备份；旧需求/视频服务不读取此独立库。旧版本不识别此profile时，以匹配版本或明确备份读取，不改表/版本号冒充兼容。

独立审核、真实受限执行器及故障对账、远程PR/准确头CI、预览与部署继续待验。候选源码、本地提交和自写测试文字都不等于通过这些门槛。当前结构及限制见[架构](../../architecture/community-changes/)，可逐项核对[功能登记](../../reference/feature-catalog/)。

完成本地候选后，可按[固定Linux检查指南](../community-code-checks/)单独明确启用检查、读取原结果、取消及对账。提案和本地提交本身不会触发检查或授予审核/部署权限。

准确检查完成后，可按[PR 审查/CI 观察指南](../community-pr-gates/)建立独立策略根目录。默认不配置审查身份，旧结果仅用于追溯。
