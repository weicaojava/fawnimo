# Fawnimo 单节点工作空间网关部署准备

此目录提供一个包含静态前端、身份网关、工作空间子进程和 FFmpeg 的本地镜像构建方案。持久数据完整保存在 `/data`。镜像使用本地名称，没有推送或发布动作。保留 Jellyfish 来源代码和根目录许可证。

当前状态见 [validation.json](validation.json)。Compose 配置解析通过不等于镜像构建、容器运行或公网验收通过；在 Docker engine 可用的主机上完成以下检查后才能更新验证状态。目标服务器、域名和 GitHub 仓库仍需明确指定。

## 构建和启动预览

先设置 `PREVIEW_PUBLIC_ORIGIN` 为获得授权的预览 HTTPS origin（例如 `https://preview.example.com`，不带路径或末尾斜线），再在仓库根目录执行：

```sh
docker compose -f deploy/workspace/compose.yaml --profile preview config --quiet
docker compose -f deploy/workspace/compose.yaml --profile preview build preview
docker compose -f deploy/workspace/compose.yaml --profile preview up -d preview
docker compose -f deploy/workspace/compose.yaml --profile preview ps
docker compose -f deploy/workspace/compose.yaml --profile preview logs --tail 100 preview
```

预览绑定 `127.0.0.1:18081`，稳定服务绑定 `127.0.0.1:18080`，只有明确指定对应 profile 才会启动。`PREVIEW_PORT`、`STABLE_PORT` 可修改端口；主机绑定保持回环地址。`preview-data` 与 `stable-data` 是两个独立命名卷，每个包含自己的身份数据库、工作空间数据库、文件、媒体和备份。不得为两个服务配置同一个卷，也不得把个人视频目录挂载为示例数据。

默认入口：

```sh
python run_controlplane.py --root /data --host 0.0.0.0 --port 8000 --frontend /app/front/dist --probe /usr/bin/ffmpeg
```

镜像以用户 10001 运行，代码文件系统只读，数据卷可写，另提供有界临时目录。Compose 限制每个服务的进程数、CPU、内存和日志大小；这些限制仍需在目标 Docker 环境实际验证。前端 `env.js` 固定使用同源 API，避免浏览器连接客户端自身的 localhost。

## 本机检查

默认会话 Cookie 使用 HttpOnly 和 Secure。容器方案保持该默认值，通过本机或服务器的 HTTPS 反向代理进行浏览器检查。需要纯 HTTP 本机验证时，使用原生 Python 入口并限定回环监听：

```sh
cd backend
python run_controlplane.py --root ./local-identity-check --host 127.0.0.1 --port 18000 --frontend ../front/dist --probe /absolute/path/to/ffmpeg --insecure-local-cookie
```

随后访问 `http://127.0.0.1:18000`，将 probe 路径替换成受信 FFmpeg 的绝对路径。`--insecure-local-cookie` 只允许回环监听，不能与容器内的 0.0.0.0 监听同用。稳定与预览浏览器检查应使用不同域名，避免同一主机不同端口共享 Cookie 名造成会话覆盖。

## HTTPS 稳定环境

由明确授权的服务器上的 TLS 反向代理转发到回环端口，为稳定和预览使用不同域名。设置 `STABLE_PUBLIC_ORIGIN`、`PREVIEW_PUBLIC_ORIGIN` 为各自 HTTPS origin，让浏览器写请求通过来源校验；未配置时使用保留的 `.invalid` 占位值，不能用于实际登录。代理应保留原始 Host；当前 CLI 不信任转发头，公开来源以显式 origin 配置为准。不要直接将内部端口对公网开放。

先在预览完成双账号隔离、示例创建/重置、上传/下载、生成候选、剪辑导出与重启恢复的浏览器验收，再构建并验收稳定环境：

```sh
docker compose -f deploy/workspace/compose.yaml --profile stable build stable
docker compose -f deploy/workspace/compose.yaml --profile stable up -d stable
```

`STABLE_IMAGE_TAG` 和 `PREVIEW_IMAGE_TAG` 分别设置版本名，默认值 `unreleased` 仅供准备阶段使用。正式发布应记录实际提交 SHA、镜像 digest、迁移版本和验收记录；当前文件尚未实现自动构件晋级。构建标签或 HTTP 200 不能证明发布和功能验收完成。

不会自动启用付费模型、共享主机凭据或社区研发执行器。真实模型连接需要在对应工作空间配置并按单独授权的预算验证。现有视频执行主机配置与工作空间身份绑定关系仍需按本阶段验收结果确认；没有验收前不要启用。

## 备份和回退

对一个服务执行停止，等待所有工作空间子进程退出后，再一致备份它的整个数据卷；同时保存身份数据、工作空间数据库、files、media 和 backups。不要只复制某个 SQLite 主文件，不能漏掉 WAL 或其他依赖文件。备份应存储在受限目录并做恢复演练，账号凭据不写入 GitHub 或本目录。

升级前记录旧镜像 digest 并完成独立备份。回退时停止服务，在新的独立卷中恢复升级前的完整备份，使用对应旧镜像启动并复验；不让旧代码直接读取已升级的数据卷。预览重置仅操作已确认的预览卷，操作前核对 Compose 解析出来的实际卷名。不要使用会同时删除稳定数据的全局卷清理命令。

## 构建边界和待验项

根 `.dockerignore` 采用默认排除，再允许应用源码、锁文件和构建输入；最后排除 env、私钥、数据库、媒体、工作目录、输出、依赖缓存等。源码中的资源文件仍需发布前审查，规则不代替凭据扫描。

镜像包含 FFmpeg 并固定 pnpm 9.15.9、uv 0.13.6，后端按已有锁文件安装。当前 `langchain-openai` 被声明在可选 dev extra，因此暂时安装该 extra 以保留文本运行能力，也会带入 pytest；归整正式依赖后可移除 extra。Python/Node/系统包的容器标签尚未按 digest 固定，正式发行前应锁定并记录构件来源。

当前方案只支持单节点持久卷，不支持多副本网关共享 SQLite、MySQL/PostgreSQL 迁移或 Redis/Celery 集群。旧 `deploy/compose` 和上游镜像工作流继续保留为来源历史，不能当作本方案的稳定发布配置。社区研发控制器必须另设执行环境，不挂载产品数据卷。

参考：[uv 官方 Docker 集成](https://docs.astral.sh/uv/guides/integration/docker/)。
