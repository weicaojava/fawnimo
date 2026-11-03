---
title: 在本机复验工作空间容器
description: 使用全新测试资源复验镜像、身份与持久数据。
---

准备 Linux Docker engine、Compose v2、Python 3.12 和可从 PATH 运行的 OpenSSL。使用本机 Docker context；不要设置 `DOCKER_HOST`、`DOCKER_CONTEXT`、`DOCKER_TLS_VERIFY` 或 `DOCKER_CERT_PATH`。该检查会拒绝远程上下文。

在仓库根目录先检查清理门禁，再构建本地候选镜像：

```sh
python -m unittest discover -s scripts/tests -p test_workspace_container_verifier.py -v
docker build --file deploy/workspace/Dockerfile --tag fawnimo-container-check:local .
```

使用一个未用过的项目名和不存在的报告路径执行：

```sh
python scripts/verify_workspace_container.py \
  --image fawnimo-container-check:local \
  --run-prefix fawnimo-check-local-20261030-001 \
  --report container-verification-local-001.json
```

每次复验换新的名称与报告文件。检查只创建专属测试容器、命名卷、网络和自作数据；完成或失败时核对随机标签后清理这些资源，候选镜像保留。不要指定已有 stable/preview 项目、业务卷或账号资料。中断后若留下资源，先核对报告与归属，不使用全局清理命令。

成功须同时满足 `status=passed`、`runtime_verified=true`、`cleanup_verified=true` 和全部检查通过。报告只保留检查结果、镜像 ID 及引擎版本，不含测试密码、Cookie、证书私钥和上传图片。实际检查范围与限制见[容器验收架构](/docs/architecture/workspace-container/)。

GitHub 的 `Verify workspace container` 运行同一入口，并保存精确提交对应的 JSON 结果 14 天。确认对应提交、镜像构建步骤、验收步骤和报告全部成功后，才登记容器证据。工作流不推送镜像，不开通付费模型，不创建公网平台。

日常稳定/预览部署的 origin、反向代理、版本、备份与回退步骤见仓库中的 [deploy/workspace/README.md](https://github.com/weicaojava/fawnimo/blob/main/deploy/workspace/README.md)。当前单节点文件不能用作共享 SQLite 的多副本方案，也不能替代服务器和公网实机验收。

本机可使用单独私有目录进行[预览资料与离线回退准备](../local-releases/)，建立全新账号、完整备份与新路径恢复；实际运行版本和策略上线仍分别验收。

维护者可按[有限体验注册指南](../trials/)明确开放名额/期限/个人项目数量；默认关闭，收费仍关闭，原账号和项目预算保持原规则。
