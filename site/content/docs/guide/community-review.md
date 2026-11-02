---
title: 维护者登记需求评估
---

先按[社区接入指南](../community-intake/)在独立目录保存明确提交的公开需求。使用同一数字repo ID、名称和标签；目录只容纳需求数据库及其日志，不与个人工作空间、媒体、模型配置或评估输入文件混放。以下命令位于`backend`，输入和输出都在本机，不向GitHub发送留言。

先保存一致数据库备份，再明确初始化评估扩展并查看一个准确需求版本的查重建议：

```sh
python review_community.py --root <独立需求目录> --repository-id 1438202832 --initialize-review --suggest <version_id>
```

已初始化后可省略`--initialize-review`。建议只表示有界文字相似度，先人工查看两个公开需求的目的及验收条件；不会自动确认重复、接受或排队。

回退到不含评估扩展的旧接入代码时，先停止该独立接入服务，把扩展前的一致备份恢复到新的专用目录；旧代码不识别评估表。保留现有扩展库供新版本继续查询，不通过删除表或改版本号伪装兼容。

在需求目录外准备UTF8 JSON文件。例如，资料不足时登记问题：

```json
{
  "request_key": "review-original-001",
  "version_id": "原需求version_id",
  "decision": "needs_clarification",
  "reason": "还缺少可检查的输出格式和恢复条件。",
  "questions": ["需要什么输出文件？怎样检查重启后的保留结果？"]
}
```

执行`python review_community.py --root <独立需求目录> --repository-id 1438202832 --evaluate-file <输入JSON文件>`。新评估会真实读取原公开repo/Issue，因此主机须能通过正常HTTPS访问GitHub；读取失败或来源改变时不保存新决定。当前使用公开未认证GET，受到GitHub限流，不自动循环重试；保留原输入与请求标识，稍后核对原来源再恢复。

`reasonable`须提供已有`feature_ids`和可检查的`acceptance`，`needs_clarification`须有`questions`，`duplicate`须明确另一个`duplicate_version_id`并复核两边来源。`not_actionable`仍须写具体理由。合理性不设置优先级、预算、开发排期或上线日期。每次输入最多32KiB，不接受任意命令、模型/网络设置或优先级字段。

如命令回复丢失，使用完全相同的文件与`request_key`重试，返回原评估而不重复登记。来源后来关闭或改变也保留旧回执，并显示旧版本已经不是当前来源；不要改原正文或请求标识来假装恢复。修改评估内容使用新的标识。

补充或复议时，先让受支持的源事件登记新的原需求版本，再以该准确版本提交新的评估，填写`appeal_of`为旧评估ID、`appeal_reason`为新增依据。相同版本也可明确复议；旧理由和问题始终保留。使用`--history <version_id>`查看同一逻辑需求的各次评估。

当前维护者决定在本机保存，`advisory_note`只作为未验证文字保留。AI模型评估、公开澄清/复议留言、依赖与容量排队及自动研发尚未接通。具体记录含义与来源检查见[当前架构](../../architecture/community-evaluations/)。
