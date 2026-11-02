---
title: 检查本地社区候选
description: 明确启用固定 Linux 检查，读取原结果、取消与恢复清理。
---

先按[变更维护指南](../community-changes/)创建准确来源的变更包及本地候选提交。此程序面向维护者的本机公开源码克隆；部署镜像本身没有个人仓库或 Docker 管理权限。

备份独立变更库，使用与该库相同的公开仓库配置明确启用检查。示例中的目录和仓库编号需使用自己的已有值；不要指向个人视频数据目录或需求库。

```sh
cd backend
python check_community.py --changes-root /srv/fawnimo-community-changes --repository-id 123456 --initialize-checks
```

启用只添加固定策略和执行记录，不开始检查。旧版本代码不识别五张业务表；回退时使用一致的旧备份及新专用目录，并保留扩展库中的全部新历史。不要移除表或改写策略来解除未清理的预留。

准备一份小 JSON，内容只有稳定请求键与已有变更包编号。重复同一键只读取原执行，不会启动第二次；若原操作未确认，先对账。新尝试必须另用明确的新键，且所有旧资源已确认清理。

```json
{"request_key":"maintainer-check-20261103-001","bundle_id":"填写已有的64位变更包编号"}
```

在 Linux/cgroup v2 的本地 Docker 中先构建与当前准确公开版本对应的工作空间镜像，读取其不可变 `sha256:…` ID。检查程序不会自动拉取或更改镜像。用准确需求根目录和镜像 ID 发起检查：

```sh
python check_community.py --changes-root /srv/fawnimo-community-changes --repository-id 123456 --root /srv/fawnimo-community-intake --image-id sha256:填写本地准确镜像ID --check-file check.json
```

检查会复查公开来源和当前选择；已退出、来源改变、镜像源码与基础版本不一致或候选超出登记范围时会拒绝执行。输入文件不能设置命令、主机目录、网络、凭据或资源上限。个人 Docker 认证、代理与请求头不会传入候选。

获取执行编号后可以读取原状态、取消或在程序中断后明确对账。这些操作只针对原执行，不会换成后来候选或再次启动原外部请求：

```sh
python check_community.py --changes-root /srv/fawnimo-community-changes --repository-id 123456 --get-check 执行编号
python check_community.py --changes-root /srv/fawnimo-community-changes --repository-id 123456 --cancel-check 执行编号
python check_community.py --changes-root /srv/fawnimo-community-changes --repository-id 123456 --reconcile-check 执行编号
```

`cancel_requested` 仅表示已记录停止意图；`active_reservation: true` 表示资源或外部接受结果尚未确认。引擎断开时保留此状态，恢复连接后再对账。创建请求已记录但具体容器身份未知时，查询不到容器也不会自动放行后续尝试；不要改库、改键或按过期时间清除这条预留。若确实无法确认，保留原历史并由维护者调查原引擎的准确资源记录。

终态并确认清理后才释放执行名额。`passed` 表示本次固定候选测试通过，仍需按后续独立审查和准确版本的 CI 门槛处理。完整协议和信任边界见[当前架构](../../architecture/community-code-checks/)。
