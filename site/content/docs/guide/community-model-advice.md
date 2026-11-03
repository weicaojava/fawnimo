---
title: 请求本地模型评估建议
---

此工具面向本机维护者，建议仍需人工审阅。它不会创建需求评审结论、留言、开发任务或PR。

1. 使用已经明确配置的本地Ollama服务。工具不会启动服务、下载模型或使用项目模型。通过本地`/api/tags`确认准确名称及64位安装摘要，只选择本地模型。
2. 将`backend/community-advice.example.json`复制到私有配置位置，填写`model`和`model_digest`，将`enabled`设为`true`，明确填写`max_requests`。当前只支持`external_fee_fen=0`；配额是本工具总请求次数，不是项目预算。原失败和不明请求仍占一次名额。
3. 准备原请求JSON，包含一个新的`request_key`及已登记的准确`version_id`。公开需求库、建议日志及配置文件各用自己的位置；建议目录不能与需求目录相同、嵌套或混放其他资料。建议目录须为空，首次明确初始化。

例如原请求内容：

```json
{"request_key":"advice-original-001","version_id":"已登记的准确需求版本ID"}
```

在`backend`目录运行，替换路径及目标仓库数字ID：

```sh
python advise_community.py --intake-root /private/community/intake --advice-root /private/community/advice --repository-id 1438202832 --request-file /private/community/request.json --configuration-file /private/community/local-model.json --initialize-advice
```

新请求会重新读取当前公开来源，只向配置的数字loopback端口发送原公开需求的有界文本和功能目录。结果中`advisory_only=true`、`execution_authorized=false`；正文截断、来源是否仍当前、调用情况和原模型摘要另行列出。请按当前需求实际内容核对建议，不直接把建议当已接受需求。

断线、取消或退出后，用同一个原请求文件恢复记录；恢复不会重新推理，不需要当前模型配置或开启开关：

```sh
python advise_community.py --intake-root /private/community/intake --advice-root /private/community/advice --repository-id 1438202832 --request-file /private/community/request.json --history-only
```

`uncertain`表示原操作没有确认结果；保留原记录，不自动重发。模型服务是独立进程，本工具取消不保证服务端推理立即停止。确需新评估时，应先核对原历史及当前来源，再明确使用新请求键；不要删除日志来重复使用旧键。

审阅后，按[维护者评估指南](../community-review/)明确填写自己的理由、验收、澄清问题或复议。查重建议、模型建议、正式评估与路线图优先级分别保存。日志只在本地，访问权限由维护者的操作系统账号管理；不要提交私有日志或模型配置到公开仓库。
