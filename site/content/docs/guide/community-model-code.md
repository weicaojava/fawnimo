---
title: 使用本地模型生成待审代码提案
---

先按[维护者评估](../community-review/)与[准备队列](../community-queue/)完成原公开需求的合理评估、路线图选择及prepared记录。模型建议不会替代这些明确决定。

1. 使用已配置的本地Ollama服务，在`/api/tags`核对准确名称和安装摘要；工具不会下载模型或读取项目模型。
2. 将`backend/local-code-model.example.json`复制到私有位置，填入原本地模型及摘要，明确启用并设置`max_requests`。仅支持外部费用0，次数上限128，每work最多8次；失败或不明请求仍占名额。普通评估建议配置不能直接作为代码模型配置使用。
3. 准备原请求JSON，选择该功能登记允许的现有Python源码及尚不存在的新候选测试。所有路径都要由模型完整返回；代码每文件/合计32KiB以内，新文字合计48KiB以内，超限会拒绝。

```json
{
  "request_key": "local-code-original-001",
  "work_id": "准确已prepared的work ID",
  "paths": ["scripts/features.py", "backend/tests/test_community_candidate_model.py"],
  "test_files": ["backend/tests/test_community_candidate_model.py"]
}
```

示例源码仅在原功能登记确实关联该路径时可用，不能借用其他功能的权限。模型调用、配额、存储、执行/来源/审核控制器不在候选修改范围。

需求库、提案库和模型日志使用三个互不嵌套的独立目录。原请求与私有模型配置放在这些目录外；首次明确初始化新模型日志和提案库。在`backend`目录运行，替换实际数字仓库ID及路径：

```sh
python generate_community.py --root /private/community/intake --changes-root /private/community/changes --model-root /private/community/code-model --repository-id 1438202832 --request-file /private/community/code-request.json --configuration-file /private/community/code-model.json --initialize-model --initialize-changes
```

返回`generated`只表示原模型文字已保存，`bundled`表示已通过当前来源及原base/准备记录检查，关联到惰性提案。若原main或准备记录改变，生成文字保留，但不会自动换版本保存。两种状态都不代表测试、独立审查或开发完成。查看具体源码、原模型摘要、原来源和bundle ID后，再进入[既有本地候选与隔离检查](../community-changes/)；本命令不会执行代码或创建PR。

断线、取消或错误后保留完全相同的请求文件，先查看原历史，不要换新键盲目重发：

```sh
python generate_community.py --model-root /private/community/code-model --repository-id 1438202832 --request-file /private/community/code-request.json --history-only
```

原结果不明时只返回原记录；不会重新调用模型。已生成文字但尚未保存bundle时，可用原参数重试来源检查与保存，仍使用原文字，不重新推理。若bundle已经保存但关联中断，可仅提供原`--changes-root`恢复关联，无须当前模型配置或需求源；仍须同一仓库与原请求。

检查并妥善保存原模型日志、配置、源码提案和回执，勿提交私有日志到公开仓库。独立本地模型服务与同一操作系统用户属于维护者的受信边界；软件日志与HTTP响应不证明实际AI质量或独立审查通过。
