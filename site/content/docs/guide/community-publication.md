---
title: 发布准确候选草稿 PR
description: 建立独立固定策略，发布已有已检查候选，并在中断后读取原记录。
---

先按[变更指南](../community-changes/)准备公开候选，再按[固定检查指南](../community-code-checks/)取得准确通过并确认清理的原检查。以下入口只创建草稿，不授予合并或部署权限。

创建第四个独立维护者目录，不能嵌入需求库、变更/检查库、审查库或个人视频数据目录：

```sh
cd backend
python publish_community.py --publication-root /srv/fawnimo-publication --repository-id 1438202832 --root /srv/fawnimo-intake --changes-root /srv/fawnimo-changes --initialize-publication
```

这只保存默认关闭的策略，不加载凭据或访问网络。策略字段见 `CommunityPublicationPolicy`；启用时另建新目录并通过 `--policy-file` 提供明确配置，包括仓库数字身份和名称、`enabled: true`、已核对的 `trusted_check_image_ids`、`trusted_runner_sha256` 与两个 `trusted_workflows` 的准确路径/blob。不要用候选自报的检查结果填这些字段。

维护者应准备仅授权目标仓库、具有 Contents write 和 Pull requests write 的 GitHub App 安装令牌或适合的细粒度令牌，将纯令牌存入独立私有文件。不要把文件放进任何审计/视频目录或 Git。入口不会自动发现现有登录状态或使用模型密钥。有关权限参见 [Git 提交接口](https://docs.github.com/en/rest/git/commits#create-a-commit) 和 [创建 PR 接口](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request)。

`GITHUB_TOKEN` 引发的事件通常不会创建新的工作流运行；需要真实触发时采用适当的 App 安装令牌/个人令牌，并实际核对准确 PR 的运行。参见 [GitHub 工作流触发说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)。不能因为建 PR 返回成功就登记 CI 已通过。

请求文件只含三项，用已有原记录替换编号：

```json
{"request_key":"maintainer-publication-001","bundle_id":"已有64位变更包编号","check_attempt_id":"已有检查UUID"}
```

在已明确配置启用的新根目录执行：

```sh
python publish_community.py --publication-root /srv/fawnimo-publication-enabled --root /srv/fawnimo-intake --changes-root /srv/fawnimo-changes --credential-file /srv/fawnimo-private/github-publication-token --publish-file publication.json
```

未配置可信策略时默认拒绝。成功状态 `published` 只表示准确草稿已被观察到。需求/main 改变、远端不一致或连接中断会保留原记录；服务不会覆盖分支或自动重发未知请求。

恢复原请求可再次传入相同文件；不需要凭据或当前来源。也可直接读取返回的原发布 UUID：

```sh
python publish_community.py --publication-root /srv/fawnimo-publication-enabled --get-publication <原发布UUID>
python publish_community.py --publication-root /srv/fawnimo-publication-enabled --credential-file /srv/fawnimo-private/github-publication-token --reconcile-publication <原发布UUID>
```

第二条只读 GitHub 并追加核对历史。未查到原资源仍不授权重新 POST。保留原根目录与编号供维护者处理；不要换请求键或变更包规避未知状态。后续明确转为可审查 PR 并有真实独立审查后，使用[PR 观察指南](../community-pr-gates/)核对准确版本与最新 CI。

本机可使用单独私有目录进行[预览资料与离线回退准备](../local-releases/)，建立全新账号、完整备份与新路径恢复；实际运行版本和策略上线仍分别验收。
