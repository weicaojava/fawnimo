---
title: 维护者使用需求路线图与准备队列
---

先按[评估指南](../community-review/)登记准确公开需求与维护者决定。以下命令在`backend`运行，沿用原数字repo ID、名称和标签；示例目标为weicaojava/fawnimo。输入文件放在独立需求目录外。命令不向GitHub发留言，也不写个人视频数据。

暂停独立接入服务并保存一致备份后，用新版本工具明确初始化路线图扩展；从旧7表库初始化后旧代码不能识别新12/15表。回退时停止该独立服务，把扩展前一致备份恢复到新的专用目录，保留现扩展库供新版本读取；不要删除表、改约束或丢掉后来历史来伪装兼容。这不升级或回退个人视频库。

准备`plan.json`，把`evaluation_id`替换成最新合理评估的实际ID，功能和验收已在评估中绑定：

```json
{
  "request_key": "roadmap-original-001",
  "title": "本轮明确范围",
  "proposals": [
    {"key": "first", "evaluation_id": "实际评估ID", "value_score": 60,
     "value_reason": "先解决已经明确的保存恢复问题。", "effort_minutes": 20,
     "estimated_external_fee_fen": 0, "after": []}
  ]
}
```

执行：

```sh
python queue_community.py --root <独立需求目录> --repository-id 1438202832 --initialize-queue --plan-file plan.json
```

首次初始化使用上述[架构](../../architecture/community-queue/)中的固定默认策略；已初始化时省略初始化参数。最多32条、完整输入64KiB，未知字段、模糊重复JSON键、循环及错误原评估均拒绝。输出显示确定排序、容量选择和阻塞理由；正数费用条目只保留为被阻塞的计划，绝不授予费用。

用返回的plan ID准备选择文件。首次`expected_revision`为0；之后使用实际当前修订号，不猜测或覆盖冲突：

```json
{"request_key":"selection-original-001","plan_id":"实际plan ID","expected_revision":0}
```

执行`python queue_community.py --root <独立需求目录> --repository-id 1438202832 --selection-file selection.json`。停用使用新的请求标识、当前修订号和`plan_id:null`。`--get-plan <plan ID>`显示当前选择和原评估/功能登记是否仍当前。选择路线图不会自动开工。

用准确plan ID和选择返回的`current_revision`准备：

```json
{"request_key":"prepare-original-001","plan_id":"实际plan ID","expected_revision":1}
```

执行`python queue_community.py --root <独立需求目录> --repository-id 1438202832 --initialize-preparation --materialize-file prepare.json`；以后省略初始化参数。仅已选容量条目产生work ID，阻塞条目仍在原路线图中。`--list-work <plan ID>`、`--get-work <work ID>`、`--history <work ID>`可在重启后查看，均不返回令牌。

如需验证准备租约，用work ID准备`claim.json`：

```json
{"request_key":"claim-original-001","work_id":"实际work ID","owner":"local-maintainer"}
```

以`--claim-file claim.json`执行同一命令前缀。新领取实际复查固定GitHub公开repo/Issue；当前未认证GET受GitHub限流，不自动循环重试，不接受host、凭据、任意命令或来源检查例外。来源/评估/选择改变时拒绝新领取，必须先核对原历史与新版本。

成功领取只返回准备attempt ID、有限租约和本机私有令牌，不启动任何研发。保存原claim文件；令牌以及后续含令牌的文件都留在本机私有位置，不写进Git、公开证据或留言。续租文件含新的`request_key`、原`work_id`、`owner`、`attempt_id`和`lease_token`，用`--heartbeat-file`执行；续租不能突破原硬时限。

准备结束使用同样身份字段和新的标识，再加`outcome:"prepared"`或`"failed"`及具体`note`，以`--finish-file`执行。准备成功会再次复查公开来源；失败在次数允许时回到queued。`prepared`不代表代码、测试、审核或上线完成，不能用此令牌调用未来编码执行器。

取消文件含新的`request_key`、准确`work_id`和具体`reason`，使用`--cancel-file`。失联或到期后，文件含新的`request_key`和准确`work_id`，使用`--recover-file`明确恢复；仍有效的租约不能被恢复。旧预留到明确恢复前占容量。当前无副作用准备可在有限次数内重排；真实外部结果未知不得套用这条规则。计划或来源已变时恢复为取消，不自动跟到新计划。

任何回复丢失，保留完全相同的输入文件和`request_key`恢复原操作。不要更改原目标或用新标识盲目再领；旧回执和现在状态分别显示，原选择不撤销后来的选择，原失败恢复也不会终止后来的attempt。所有费用和模型调用仍为0。独立审核、实际编码/PR/CI晋级和公网部署继续待验。

需要形成可审阅的代码提案时，按[维护者变更指南](../community-changes/)使用独立提案目录；新提案和新本地候选提交均再次复查来源。准备完成不表示已开发，后续执行、PR与独立检查仍另行验收。
