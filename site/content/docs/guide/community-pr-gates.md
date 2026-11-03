---
title: 观察 PR 审查和 CI
description: 配置单独策略根目录，读取准确 PR 的观察记录与阻止原因。
---

此入口面向维护者，只观察当前准确候选的独立审查和 CI；它不会批准、留言、合并或部署。先按[变更指南](../community-changes/)准备本地候选，并按[固定 Linux 检查指南](../community-code-checks/)得到已通过且已确认清理的原检查。

为审查记录选择第三个独立目录，不能放进需求库、变更/检查库或个人视频目录。以下示例使用 Fawnimo 当前公开仓库的数字身份，目录需替换为自己的维护者目录。

```sh
cd backend
python observe_community_pr.py --gates-root /srv/fawnimo-review-audit --repository-id 1438202832 --root /srv/fawnimo-community-intake --changes-root /srv/fawnimo-community-changes --initialize-gates
```

这会保存默认关闭的固定策略，不进行网络观察。请求文件只接受以下四项，编号使用已有的真实原记录：

```json
{"request_key":"maintainer-pr-observation-001","bundle_id":"已有64位变更包编号","check_attempt_id":"已有检查UUID","pr_number":1}
```

```sh
python observe_community_pr.py --gates-root /srv/fawnimo-review-audit --evaluate-file pr-observation.json
```

未配置独立账号时得到 `review_gate_disabled`，不会访问 Git、需求库或 GitHub。默认关闭是策略状态，不表示需求已通过审查。不要把候选提供的账号、批准文字、工作流结论或链接当作策略。

启用需由维护者在本地策略 JSON 中明确提供 `enabled`、`independent_reviewer_ids`、`developer_actor_ids`、`trusted_check_image_ids`、`trusted_runner_sha256` 和 `trusted_workflows`。审查账号应为实际受信且独立的账号；不能配置成开发账号或依赖创建者自批。镜像必须来自准确基础版本的已验收构建，父程序哈希与安装版本一致；工作流 blob 使用已提交 Git 对象，不能取自工作树。当前支持的工作流固定为 `verify-fawnimo.yml` 和 `verify-workspace-container.yml`，完整路径在策略中必须准确匹配。

策略不可原地改写。配置完成后，用新目录和 `--policy-file policy.json --initialize-gates` 建立新策略根目录，保留旧目录历史。不要提交私有策略或任何认证材料到需求正文；此协议本身不接受令牌或凭据字段。

启用后的新观察需要同时指明原需求与变更/检查目录：

```sh
python observe_community_pr.py --gates-root /srv/fawnimo-review-audit-enabled --root /srv/fawnimo-community-intake --changes-root /srv/fawnimo-community-changes --evaluate-file pr-observation.json
```

它会核对当前准备/来源/计划选择、准确候选、原已通过检查、同仓库准确 PR 头/基础、受信工作流和独立账号。PR 草稿、来源改变、旧头、旧批准、待完成审查、缺失/跳过/失败的最新检查都不能放行。更换候选、检查或 PR 要使用新的明确请求键；相同键只返回原观察，不会重新解释成当前许可。

读取结果和历史无需重新提供当前来源目录或策略文件：

```sh
python observe_community_pr.py --gates-root /srv/fawnimo-review-audit --get-observation 观察编号
python observe_community_pr.py --gates-root /srv/fawnimo-review-audit --history 变更包编号
```

`eligible_at_observation` 表示当时条件，历史恢复始终标记 `historical_only` 和 `fresh_action_authorized: false`。即使观察通过，行动权限字段仍为 false；不能拿旧快照直接发布。远程 API 限制、来源变化或分页无法完整确认时会拒绝新观察，原历史保留。完整语义见[当前架构](../../architecture/community-pr-gates/)。

维护者另可明确配置[准确候选草稿发布](../community-publication/)。它保存独立意图和原PR追溯，默认关闭，不提交审查或授予合并/部署权限。
