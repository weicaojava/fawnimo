---
title: "检查本机社区需求登记"
description: "维护者独立目录、源标识和事件回执的操作方式。"
---

1. 使用维护者独立目录，保留个人空间和其模型/媒体数据。默认 `python run_community.py --root <独立目录>` 仅监听本机，接入保持关闭；不会因此创建需求库。
2. 为真实接入另行准备独立HTTPS资源、目标repo ID/name及随机私有webhook secret文件。secret使用随机ASCII文本、无额外换行，保存于服务器私有位置，不写Git或聊天；测试中的公开自作secret不可用于真实服务。当前目标公开仓库为weicaojava/fawnimo，数字ID1438202832已只读核验；真实App和入站事件尚未配置。
3. 资源及私有secret就绪后，维护者可显式加 `--enable-intake --repository-id <数字ID> --repository-name <owner/repo> --webhook-secret-file <私有文件>`。事件处理地址为独立服务的/community/github/events；默认proposal标签为enhancement。不要把个人数据卷挂到该服务。
4. 使用GitHub真实投递标识处理重投。响应包含已提交的receipt和可选request/version ID；ignored表示未保留提案正文，仍可能保留防乱序的最小源时钟。stale_version不能覆盖新源，source_conflict须等待更明确的新源时钟并核对GitHub当前状态。
5. 在相同源配置下运行 `python run_community.py --root <独立目录> --repository-id <数字ID> --repository-name <owner/repo> --list` 查看有界登记摘要。原文版本不因迟到事件或SQL替换被改写。已关闭/撤回只表示源状态，不表示功能已开发、验收或上线。

当前43项自作签名HTTP/SQLite软件检查与独立复核通过；真实GitHub App、实时源复验、AI评估、队列、隔离编码/PR/CI/预览和公网发布仍未接通。详见[当前架构](../../architecture/community-intake/)。
