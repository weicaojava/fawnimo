---
title: 启动和切换自有本机稳定与预览版本
description: 明确选择本地镜像、新建独立资源、核对中断并保留旧数据。
---

本指南使用新的私有维护目录和自有命名卷，不接管已经运行的旧工作室。要求 Linux Docker Engine 28+、cgroup v2，以及已构建并核对的 Fawnimo 镜像。Windows 可连接本机 Linux Docker 引擎；本命令不会启动它。公网部署、模型付费和自动发布需要另外的明确配置。

在后端环境运行，先创建新私有目录。以下路径均为示例，不能替换为旧用户数据或源码目录：

```sh
python manage_runtime_release.py --root /srv/fawnimo-runtime-private init
python manage_runtime_release.py --root /srv/fawnimo-runtime-private status
```

默认关闭，以上两步不联系 Docker。准备 `runtime-policy.json`：

```json
{
  "enabled": true,
  "operator_allowed_images": [
    {"image_id": "sha256:实际64位镜像ID", "source_commit": "实际40位源码提交"}
  ],
  "helper_image_id": "sha256:实际64位辅助镜像ID",
  "bootstrap_sha256": "辅助镜像中backend/local_runtime_bootstrap.py的实际64位SHA256"
}
```

将占位值替换为实际完整值。辅助镜像也必须列在允许列表中，并包含本版本的辅助程序。每个镜像的 `org.opencontainers.image.revision` 标签必须匹配其声明提交，运行用户必须是 10001:10001。构建工作流记录的是实际源码；仅填写或修改标签不等于代码通过审核。镜像可以在另一个明确的构建步骤用 `deploy/workspace/Dockerfile` 和 `FAWNIMO_SOURCE_COMMIT` 构建；运行管理命令不执行构建或拉取。启动文件摘要按镜像内实际文件核对，不从不匹配的主机源码推算。

```sh
python manage_runtime_release.py --root /srv/fawnimo-runtime-private policy --input runtime-policy.json --expected-revision 1 --request-key enable-local-runtime-001
```

如需自选端口，在第一次策略配置中写入 `stable_port`、`preview_port`；之后不能改变。默认稳定 `http://127.0.0.1:18080`，预览 `http://127.0.0.2:18081`，两者地址不同用于分离会话 Cookie。不要把它们当公网地址。

准备稳定版首次启动请求 `activate-stable.json`：

```json
{"request_key":"activate-stable-001","slot":"stable","expected_generation":0,"image_id":"sha256:实际64位镜像ID","reason":"本机明确验收的稳定版本"}
```

```sh
python manage_runtime_release.py --root /srv/fawnimo-runtime-private activate --input activate-stable.json
```

预览首次启动使用另一个请求键和 `slot: preview`、`expected_generation: 0`。两边均建立独立新 A/B 账号，模型和注册关闭。账号领取文件应放在另一个已有私有目录，目标文件不能存在：

```sh
python manage_runtime_release.py --root /srv/fawnimo-runtime-private credentials --slot stable --expected-generation 1 --output /srv/fawnimo-claims-private/stable-initial.json
```

命令只报告是否保存成功，不输出密码。POSIX 领取目录必须是 0700；Windows 请选择只允许本账号访问的目录。该文件只反映初始账号，不承担密码重置；恢复保留当前身份库的密码状态。领取文件不应提交 Git 或发到聊天。

下一次切换使用新键和准确当前代次，例如 `expected_generation: 1`；镜像仍必须已允许。期间会短暂停止稳定服务，预览继续独立运行。存在未完成任务、费用预留、未确认模型调用、未处理账单、未认领资料或不兼容数据时拒绝完成。成功结果中的 `archive_sealed` 事件给出切换前完整资料的 `snapshot_id`；原服务卷和较新的编辑始终保留。

准备旧资料回退请求：

```json
{"request_key":"rollback-stable-data-001","slot":"stable","expected_generation":2,"snapshot_id":"同一环境已有64位备份编号","acknowledge_retained_later_edits":true,"reason":"明确恢复旧业务资料，保留较新卷供核对"}
```

```sh
python manage_runtime_release.py --root /srv/fawnimo-runtime-private rollback --input rollback.json
```

它恢复到新卷，使用备份绑定的原镜像，保留当前会话撤销、密码、成员、体验策略及签名密钥状态。费用/模型/执行策略或账号拓扑改变时会拒绝回退，不能借回退降低已授权预算或丢弃账单。后来的编辑没有自动合并到旧视图，可在保留的停止卷中继续核对。已有未知任务不得通过复制历史来重发。

停止只作用于准确当前代次：

```json
{"request_key":"stop-stable-001","slot":"stable","expected_generation":3,"reason":"维护者明确停止本机版本并保留数据"}
```

```sh
python manage_runtime_release.py --root /srv/fawnimo-runtime-private stop --input stop.json
```

关闭激活策略后仍可停止已登记的自有版本。旧停止请求重放只返回原历史，不会停下后来启动的新代次。

中断时保留私有维护日志及相关资源，先读取原记录和当前只读观察：

```sh
python manage_runtime_release.py --root /srv/fawnimo-runtime-private show --id 原操作64位编号
python manage_runtime_release.py --root /srv/fawnimo-runtime-private reconcile --id 原操作64位编号
```

`reconcile` 不重建、重启、重复复制或删除资源。确需停下这次中断及当前环境中的已核对自有服务时，将刚返回的 `observation_sha256` 填入新的显式请求：

```json
{"request_key":"quiesce-interrupted-001","operation_id":"原操作64位编号","expected_observation_sha256":"刚观察到的64位摘要","acknowledge_stopped_data_retained":true,"reason":"明确停止此次中断的自有服务，保留全部资料"}
```

```sh
python manage_runtime_release.py --root /srv/fawnimo-runtime-private quiesce --input quiesce.json
```

状态改变或身份不符时拒绝行动。若停止本身中断，重新只读观察；旧键不重复执行，新的显式请求须绑定新观察。最多三次显式处理；未知资源或持续错误需维护者在私有环境核对，不自动清除预约。成功停下后可使用新的启动请求，旧数据/档案不删除。到达操作/代次容量后应归档核对，不能靠改日志重置额度。

完整实现和限制见[本机运行架构](../../architecture/local-runtime-releases/)。原[数据准备与离线档案](../local-releases/)入口继续提供纯数据操作，独立审核、CI 和真实公网验收分别登记。

新数据代次会完整检查原业务资料；同代次后续重启使用其私有准备记录并保留正常编辑。不要修改、复制或单独移走运行配置/基线/准备记录。费用规则变化会拒绝旧资料回退，已验证原版恢复后继续使用当前预算。当前准确容器证据见[功能登记](../../reference/feature-catalog/)，本机通过不授予公网、独立审核或费用权限。
