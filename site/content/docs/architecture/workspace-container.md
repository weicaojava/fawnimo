---
title: 工作空间容器验收
description: 单节点镜像、独立持久卷及自动化验收的当前边界。
---

当前单节点镜像包含同源前端、身份网关、固定空间子进程和 FFmpeg。Compose 的 stable 与 preview 使用不同命名卷、回环端口和明确的公开 origin；代码文件系统只读，以 10001 身份运行，临时目录与进程、CPU、内存、日志有界。账号、SQLite、媒体及私有配置保存在所属 `/data`，没有挂载个人原片或 Docker socket。

`scripts/verify_workspace_container.py` 为实际容器验证入口；`.github/workflows/verify-workspace-container.yml` 在独立 Linux runner 构建本提交镜像并运行它，不推送镜像、不部署服务器。当前结果以 [容器验证记录](https://github.com/weicaojava/fawnimo/blob/main/deploy/workspace/validation.json) 为准；脚本存在或流水线启动不代表运行通过。

## 新资源与清理边界

验收只接受本机 Unix socket/Windows named pipe 的 Linux 引擎，拒绝 Docker 环境变量覆盖及 TCP/SSH 上下文。之后每条 Docker/Compose 命令固定使用已核验的本机端点，不随 CLI 当前 context 切换。调用者必须提供新的 `fawnimo-check-…` 项目名；已有项目标签或名称包含该前缀的容器、卷、网络，即使未标记归属，也会在创建之前拒绝，避免 Compose 复用已有数据卷。

临时覆盖文件只设置候选镜像、随机所有权标签；端口通过原 Compose 变量设为动态回环端口，两个 HTTPS origin 来自新建的本机 TLS relay。原 Compose 中的用户、只读代码、资源上限、临时目录、健康检查和日志设置继续用于实际容器。

每次调用生成随机所有权标签。停止、重建和删除前，同时核对该标签及 Compose 项目标签；不使用全局 prune 或 `down --volumes`。失败时同样清理本次持有的资源，无法确认归属就拒绝删除并令验收失败。强制终止 runner 的清理不作跨主机保证；本机中断后的残留也不能改用普通业务项目继续测试。

## 实际验证范围

验收先在新卷通过离线 CLI 创建 A/B 账号，再启动网关；不同客户端各持独立 HTTPS Cookie jar。自签测试证书仅在本次客户端的信任上下文内使用，不修改系统信任、不关闭证书校验，不代表公网证书或浏览器 UI 验收。

脚本检查实际容器的非特权身份、代码只读、可写临时目录、FFmpeg、资源限制、健康检查配置、日志限制、回环端口及唯一 `/data` 命名卷。随后通过真实 HTTPS/HTTP 与固定 worker 验证：匿名拒绝、Secure/HttpOnly Cookie、A/B 同项目 ID、跨空间请求、来源校验、自作 PNG 上传/下载/Range、项目预算、示例重放与重建。

稳定容器停止并删除后，以同镜像、原测试卷重新创建；核对原会话、项目、图片字节、独立预算、示例收据和手工编辑仍在，且预览继续工作。收费入口仍关闭，测试不连接真实模型。

这证明的范围限于实际成功运行所记录的容器与上述流程。单节点重建不等于跨版本升级、备份恢复、完整成片、真实账单或公网部署。容器包围的是产品服务；此镜像没有授予社区 AI 任意代码执行资格。后续无人值守开发执行器必须另设经过验证的隔离边界，不能挂载产品数据卷。

操作步骤见[容器复验](/docs/guide/workspace-container/)，未完成的发布门槛见[分阶段计划](/docs/plans/platform-delivery/)。

参考：[Compose 项目名](https://docs.docker.com/compose/how-tos/project-name/)、[Docker 卷生命周期](https://docs.docker.com/engine/storage/volumes/)、[GitHub 流水线产物](https://docs.github.com/en/actions/tutorials/store-and-share-data)。


## 已记录的实际容器结果

2026-11-02，精确提交2ced83d的[GitHub容器检查](https://github.com/weicaojava/fawnimo/actions/runs/41377110733)构建镜像并通过40项断言，清理通过。完整脱敏报告保存在 `capabilities/container-ci-2ced83d.json`，镜像ID与产物hash见 `capabilities/container-runtime-validation.json`。原8项安全方法及30项原生HTTPS断言分别保留，不与40或主后端回归合计。本机Windows引擎仍未运行，用户服务器尚未购置；整体F-014.02仍部分实现且未完成公网/备份回退验收。


## 部署镜像中的固定声音检查

容器复验还在本次自有稳定镜像中，以10001:10001执行七项固定原生用例：两种采样格式的音乐循环和对白避让、实际配音/字幕及五项导出文件、严格声音HTTP与原任务、最终转场时钟上的音效、声音子进程环境隔离，以及七段原创几何示例的逐帧解码。所有素材由测试现场创建，临时数据库/文件仅在另建的`/tmp`目录，不使用网关账户的项目或数据卷。

固定执行不接受用户命令、用例或路径。240秒子进程期限、只读代码/实际非root身份、精确七个用例身份、零跳过/失败/错误，以及执行前后自有容器身份都必须满足。通用40项与该七项分别登记；`container-verification.json`的`sound_exports`保存用例、计数、原JUnit摘要和明确资格边界。任何缺失或跳过都会令本轮失败，仍执行原有自有资源清理。

成功只证明对应准确源码镜像的自作媒体软件行为；真实声音内容/音色、循环接缝试听、真实模型费用、完整叙事成片和公网仍需各自验收。固定列表、本机通过或仅有容器构建成功均不能代替这项实际运行结果。
