# 社区研发与发布独立审查

审查日期：2026-10-29。此文记录只读审查及后续部署准备边界，不代表社区控制器或公网服务已经上线。未操作真实用户数据库、读取密钥、调用收费模型或向 GitHub 发布。

## 当前事实

现有 `AgentExecutionRuntime` 是视频工作空间内的顺序文本节点执行器，不能作为社区研发执行器。现有 CI 主要检查提交标题和后端 Pylint；没有覆盖本阶段身份隔离、浏览器流程和发布门禁的完整检查。

上游 GHCR 工作流仍硬编码 `ghcr.io/forget-c/jellyfish-*`，main/tag 构建会推送 latest；文档站工作流仅发布 Hugo Pages。旧 compose 使用 MySQL/Celery，而新预算事务明确只支持 SQLite。旧后端镜像没有 FFmpeg，旧 compose 没有当前媒体、文件和工作空间持久卷。本阶段未修改这些工作流，也未向上游发布。

新建 `deploy/workspace` 仅提供独立的单节点 SQLite 网关部署准备；镜像构建、容器运行和公网验收状态由 `validation.json` 单独记录。

## 最小真实闭环

社区控制服务应使用自己的数据库、专用模型、预算、日志和开发执行环境，不读取私有视频空间。数据链为：

`WebhookDelivery → RequirementRevision → Evaluation → PlanVersion/QueueItem → DevelopmentAttempt → ReviewReceipt/CheckReceipt → PreviewDeployment → ReleaseDeployment → Acceptance`

每项记录输入版本、内容哈希、执行身份、时间和关联资源。合理、优先、实现、验收、部署分别保存。Issue 更新产生新版本；补充与复议可追溯，旧事件不能覆盖当前决定。

1. 入口对原始字节验签，验证安装、仓库和事件/action 白名单，限制体积。delivery ID 唯一并保存载荷哈希，持久化成功再响应。乱序事件使用版本和当前 GitHub 快照核对。
2. 评估器输出结构化查重候选、理由、澄清与评估。Issue 内容始终为数据，不能被用作系统指令或命令。队列按依赖、价值、估计成本和容量生成。
3. 执行控制具备 CAS 租约、fencing token、截止时间、有限尝试和并发槽位。执行前原子预留社区预算；未知远程结果保留费用和状态，不自动重发收费请求。
4. 开发环境只挂载授权仓库代码。控制器生成分支与目录，执行器不能读取产品数据、发布凭据或独立评审签名。缺少实际研发执行器时明确 blocked，不能用固定补丁冒充研发能力。
5. 独立审查和客观检查绑定当前提交 SHA、需求版本与政策版本；新增提交使旧审查失效。开发 Agent 不得自行批准、伪造检查来源或绕过门禁。
6. 预览使用独立 URL、账号和数据；稳定部署按预设策略晋级不可变构件，保存 digest、迁移版本和回退目标。实际公网验收后再关闭需求。
7. GitHub 输出通过持久 outbox，使用确定性标记定位已有评论和 PR。请求超时先对账，避免重复发布。公开内容只包含用户明确提交的需求和脱敏评审结果。

## 验收矩阵

| 范围 | 必验边界 |
| --- | --- |
| 事件 | 无效签名/错仓库拒绝；并发重复仅一次生效；同 ID 不同载荷冲突；乱序不回退；重启恢复 |
| 队列 | 依赖未完成不执行；提交前后取消；租约过期；旧 worker 迟到；有限重试；并发上限 |
| 限额 | 时间/输出/进程/费用上限；并发预留不超额；未知费用不释放；无报价禁止远程执行 |
| 隔离 | 恶意需求无法读取私有数据或凭据；开发执行器无私有挂载；公开日志不泄密 |
| 门禁 | 自审、过期 SHA、伪造来源、缺失/失败检查、政策变更均不能晋级 |
| GitHub | 实际需求→评估→分支/PR→CI启动和完成；PR更新重验；超时恢复无重复PR |
| 发布 | 实际预览登录与关键浏览器流程；稳定晋级；健康失败回退；完整数据恢复 |

本地可实现控制器、执行适配器、策略和夹具故障测试；夹具仅证明软件协议。真实闭环仍依赖明确授权的目标仓库、GitHub App 安装、独立执行资源、公网服务器/域名、发布政策和费用上限。

## GitHub 官方语义

Webhook 应校验原始载荷的 HMAC-SHA256，快速入队响应；重投沿用原 delivery ID。失败投递不会由 GitHub 自动重投，控制器需有主动恢复机制。参考：[验签](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)、[最佳实践](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)、[失败恢复](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries)。

当前官方文档说明：使用 `GITHUB_TOKEN` 创建/更新 PR 时，opened/synchronize/reopened 的工作流会进入待批准状态。需要无人值守 CI 时应使用权限和仓库范围受限的 GitHub App installation token，并实际确认 CI 启动及完成。参考：[GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token)。

installation token 可进一步缩小权限和仓库范围，有效期一小时；由控制器持有并刷新，不交给开发执行器。参考：[安装令牌](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)。

拥有高权限的 `pull_request_target` / `workflow_run` 不应检出执行不受信任 PR 代码。参考：[安全参考](https://docs.github.com/en/actions/reference/security/secure-use)。
