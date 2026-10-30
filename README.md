# Fawnimo 鹿帧

<p align="center"><img src="./front/public/fawnimo.svg" alt="Fawnimo" width="112" /></p>

An open-source, self-hosted AI short-video workspace, from ideas and scripts to editable storyboards, assets, generation candidates and editing.

**开源、可自行部署的 AI 短视频工作台。** 短视频制作是主线，短剧与参考视频拆解是能力组成。创意、分镜、演员、服装、场景、道具、提示词、候选和制作记录保持可编辑、可追溯。

Fawnimo 基于 [Forget-C/Jellyfish](https://github.com/Forget-C/Jellyfish) 继续开发，保留 [Apache-2.0 许可证](LICENSE)、来源与版权记录。现有代码包含上游实现和后续开发成果；完整说明见 [UPSTREAM.md](UPSTREAM.md)。

<!-- features:begin -->
## 当前开发与验收状态

本分支继续开发开源AI短视频制作工具，登记15个功能区、64项子功能：45项已实现、6项部分实现、13项待实现。实现、软件验证、真实模型验证和部署分别记录，数量不表示全部验收通过。

- [功能总表与代码/入口/验证矩阵](site/content/docs/reference/feature-catalog.md)
- [逐项验收指南](site/content/docs/guide/feature-acceptance.md)
- [A–F分阶段开发与验收计划](site/content/docs/plans/platform-delivery.md)
- [公开路线图](site/content/docs/plans/public-roadmap.md)
- [单一功能登记源](capabilities/features.json)，检查/生成：`python scripts/features.py --check` / `python scripts/features.py`

身份、个人空间、文字示例和项目预算可用；六种有界单独SQLite协议接原项目预留/账单，剧本优化/精简/分镜/单镜提取支持候选审阅与明确采用。身份收费网关仍关闭，模型质量、全部收费隔离、社区、成片和公网继续推进。本机地址、截图或上游镜像不能作为本项目公网部署。Docker/MySQL配置不等于迁移、并发与恢复已经验收。

代码保留并基于[Forget-C/Jellyfish](https://github.com/Forget-C/Jellyfish)继续开发，遵循[现有许可证](LICENSE)，来源和发布边界见[UPSTREAM.md](UPSTREAM.md)。现有代码包含上游、既有改动和AI辅助开发成果，不能宣称全部从零AI原创。仓库里的上游Issues、Actions、Pages与镜像链接属于上游，不是已交付的自有平台。
<!-- features:end -->

## 当前可用范围

- 项目、章节、分镜、演员、服装、场景、道具、提示词和文件的持久编辑。
- 独立身份入口、个人空间和固定空间工作进程；旧本地模式保留。
- 可建立并重新建立的纯自作文字示例，含7镜、21条可编辑提示词，保留旧工程与编辑。
- 有界顺序文本Agent、图片候选与采用、视频理解候选、视频执行及项目独立预算等软件协议；文本、图片、原片视觉/ASR及托管原生视频已接原项目费用；完整收费执行隔离仍待验，新身份空间收费提交保持关闭。
- 不可变剪辑版本、硬切与交叉淡化、本地FFmpeg渲染和字幕导出；适用范围及验证证据以功能登记为准。

实现、软件验证、真实模型质量和部署是不同状态。业务资源及明确嵌套/历史文件隔离、项目独立预算和离线迁移/回退工具已通过本机验证；完整收费执行隔离、正式账号切换、参考生成与声音扩展、完整成片、社区自动研发和公网稳定/预览平台仍待完成。

## 本机身份体验

先安装Node.js、pnpm 9.15.9、Python 3.11+和FFmpeg，完成前后端依赖安装与前端构建，再使用独立数据目录启动：

```sh
cd backend
python run_controlplane.py --root ./local-identity --host 127.0.0.1 --port 18000 --frontend ../front/dist --probe /absolute/path/to/ffmpeg --insecure-local-cookie
```

浏览器访问 `http://127.0.0.1:18000/projects`。`--probe` 必须替换成受信FFmpeg的绝对路径。示例 `local-identity/` 已排除版本控制，正式数据根建议放在仓库之外。公网部署保持HTTPS和默认Secure Cookie；纯HTTP参数仅用于回环地址。

体验账号由维护者离线建立，密码仅写入指定的新私有文件。请在启动网关前创建；同一控制根已有网关运行时需先停机，维护锁会拒绝并发操作：

```sh
python run_controlplane.py --root ./local-identity --provision-account demo-a --credential-file /private/path/demo-a.json
```

个人空间在建立账号时一并创建。账号文件不能进入公开仓库。示例重建会创建全新工程，保留旧示例与编辑，不调用模型；更多操作见[身份与空间体验指南](site/content/docs/guide/workspace-experience.md)。

旧本地数据继续使用原入口。[离线迁移工具](site/content/docs/guide/workspace-migration.md)已完成原数据私有副本演练；正式迁入必须明确owner及从未初始化的目标空间，原入口尚未切换。不要手工覆盖新账号目录。

## 自行部署

[工作空间部署准备](deploy/workspace/README.md)提供含静态前端、身份网关、独立空间进程与FFmpeg的单节点方案，稳定与预览使用不同数据卷。当前仅通过静态配置检查，镜像、容器和公网部署尚未完成验收。旧MySQL/Celery Compose与上游镜像配置保留为来源资料，不能替代当前功能的部署验证。

## 开发与功能登记

```sh
python scripts/features.py --check
python scripts/features_check.py
```

功能登记源为[capabilities/features.json](capabilities/features.json)。API变化后运行 `pnpm run openapi:update`，前端使用生成客户端。编码、职责和文档规范见[AGENTS.md](AGENTS.md)。新增证据单独记录，保留历史发布与验收记录。

品牌变更只影响显示名称、图标和文档。数据库、业务编号、原请求标识、旧偏好键和内部包名保持兼容，不因改名重置生成请求。

## 社区与来源

功能需求可通过[本仓库Issues](https://github.com/weicaojava/fawnimo/issues)提交。社区需求评估、排期、隔离开发、独立审核、预览和策略发布的服务仍在规划中；现有顺序文本Agent不等同于研发Agent。

- [公开路线图](site/content/docs/plans/public-roadmap.md)
- [来源与发布边界](UPSTREAM.md)
- [许可证](LICENSE)
- [上游日文资料](docs/README.ja.md)，保留其原始产品名称及历史上下文。
