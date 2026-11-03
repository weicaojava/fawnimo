---
title: 准备本机预览并建立回退档案
description: 新建独立账号，离线备份完整网关根目录，并恢复到新路径。
---

本指南使用三个独立位置：私有维护档案、全新预览数据、原稳定网关数据。每次恢复另选新目标。维护目录包含账号领取文件和完整私有备份，应保存在自己的私有磁盘路径；这些内容不上传 Git 或 CI。

```sh
cd backend
python manage_local_release.py --release-root /srv/fawnimo-release-private --initialize-release-root
```

为已有公开变更准备预览请求，替换以下占位变更包/提交 SHA：

```json
{"request_key":"local-preview-001","bundle_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source_commit":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
```

```sh
python manage_local_release.py --release-root /srv/fawnimo-release-private --preview-root /srv/fawnimo-preview-001 --prepare-preview-file preview.json
```

输出给出新预览数据根目录和私有领取文件位置；从该文件分别领取 A/B 账号。两账号/空间独立，未复制稳定资料或配置模型。提交 SHA 是声明的追溯标签，实际软件/镜像版本需要另行核对。此步骤只准备数据。

如需人工进行本机体验，可用已核对的安装版本运行现有 `run_controlplane.py`，为该预览选择独立回环端口和数据根目录，并按[网关部署指南](../workspace-container/)及网关入口参数配置。此处没有启动公网或切换稳定服务的步骤。

先停止选定网关及其空间工作进程，再准备备份请求：

```json
{"request_key":"before-local-release-001","source_commit":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
```

```sh
python manage_local_release.py --release-root /srv/fawnimo-release-private --source-root /srv/fawnimo-stable-data --snapshot-file snapshot.json
```

`--source-root` 是整个身份网关根目录，内含 `control.db` 和已登记的 `workspaces`，不是单个旧版 `studio.db`。活动进程持锁、未认领空间、链接、损坏数据库、容量/时限超限或来源变化会拒绝完成。临时档案与原意图保留供维护者核对。旧版迁移仍使用[工作空间迁移指南](../workspace-migration/)。

将返回的备份 SHA 编号填入恢复请求；目标必须不存在，且与原数据、维护目录互不嵌套：

```json
{"request_key":"local-rollback-data-001","snapshot_id":"已有64位备份编号"}
```

```sh
python manage_local_release.py --release-root /srv/fawnimo-release-private --verify-snapshot <备份编号>
python manage_local_release.py --release-root /srv/fawnimo-release-private --destination /srv/fawnimo-restored-001 --restore-file restore.json
```

恢复结果是新路径的旧数据副本，账号/空间与备份一致；当前目录和后来的编辑保留。实际版本切换、未决外部任务/账单对账和公网验收仍按发布计划执行。不要同时启动同一付费任务历史的原库与恢复库；本入口不调用模型或授予费用。

原操作的历史可随时读取：

```sh
python manage_local_release.py --release-root /srv/fawnimo-release-private --get-release <原操作编号>
```

原请求键只恢复历史，不重置预览或重做中断复制。保留部分目标和日志核对后，如需新的备份/恢复，使用新的请求键及新目标。档案内容变化时验证失败，原数据不会被覆盖。

本机意见另用反馈请求记录：

```json
{"request_key":"local-feedback-001","release_id":"已有预览或恢复编号","decision":"accepted_locally","note":"本机检查意见"}
```

```sh
python manage_local_release.py --release-root /srv/fawnimo-release-private --feedback-file feedback.json
```

反馈只保存在本机私有日志，不发送消息，不等于真实独立审核、真实用户验收或发布成功。当前能力及限制见[本机回退架构](../../architecture/local-releases/)。

如需明确启动和切换新的自有本机稳定/预览容器，使用独立的[本机版本运行指南](../local-runtime-releases/)。该入口不接管上述原目录；它另外保留当前安全/费用状态并只恢复到新命名卷。两个维护命令的原请求键和历史不能互换。

启用[固定身份参考视频交付](../workspace-reference-delivery/)时，私有全根备份包含 `.reference-delivery.key`，应与身份库和全部空间共同保管。恢复只到新的私有根，既有链接仍受期限、次数、原任务和输入证明约束；不能把密钥单独放进前端或公开备份。
