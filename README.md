# Fawnimo 鹿帧

<p align="center"><img src="./front/public/fawnimo.svg" alt="Fawnimo" width="112" /></p>

An open-source, self-hosted AI short-video workspace, from ideas and scripts to editable storyboards, assets, generation candidates and editing.

**开源、可自行部署的 AI 短视频工作台。** 短视频制作是主线，短剧与参考视频拆解是能力组成。创意、分镜、演员、服装、场景、道具、提示词、候选和制作记录保持可编辑、可追溯。

Fawnimo 基于 [Forget-C/Jellyfish](https://github.com/Forget-C/Jellyfish) 继续开发，保留 [Apache-2.0 许可证](LICENSE)、来源与版权记录。现有代码包含上游实现和后续开发成果；完整说明见 [UPSTREAM.md](UPSTREAM.md)。

<!-- features:begin -->
## 当前开发与验收状态

本分支继续开发开源AI短视频制作工具，登记15个功能区、64项子功能：46项已实现、15项部分实现、3项待实现。实现、软件验证、真实模型验证和部署分别记录，数量不表示全部验收通过。

- [功能总表与代码/入口/验证矩阵](site/content/docs/reference/feature-catalog.md)
- [逐项验收指南](site/content/docs/guide/feature-acceptance.md)
- [A–F分阶段开发与验收计划](site/content/docs/plans/platform-delivery.md)
- [公开路线图](site/content/docs/plans/public-roadmap.md)
- [单一功能登记源](capabilities/features.json)，检查/生成：`python scripts/features.py --check` / `python scripts/features.py`

身份与个人空间、项目独立预算、可导出的原创示例、剧本与分镜候选、已声明参考图/视频，以及本机配音与声音剪辑已有软件流程。金额分别归属原项目，没有工作空间总预算。

独立图片及文字/剧本/视频理解/原生视频准入默认关闭；维护者明确列出空间和执行类型后，仍需各空间自己的模型服务、报价和项目额度。自作双账号HTTP测试不能授权真实模型费用。操作见[图片准入](site/content/docs/guide/workspace-image-generation.md)与[其他执行类型](site/content/docs/guide/workspace-execution-admission.md)。

社区需求的本机签名登记、评估/澄清、容量排队、模型变更包、隔离候选检查与草稿/审核衔接已有分段软件；实际App、独立审核、发布联合验收继续进行。稳定/预览运行与回退已有自有Linux容器证据，服务器尚未购置；真实模型/账单、完整成片及公网未通过，费用授权0。

原请求恢复、在途取消、明确重试、原生暂停恢复和连接变更已通过本机58项软件检查，含14项新动作。未知费用保留在原项目；操作见[取消、重试与恢复](site/content/docs/guide/workspace-execution-recovery.md)。

本机模型调用的整体等待上限已修正并新增防滴流检查；真实CPU建议2例结构通过、语义预期仅1/2符合，模型质量仍未通过，人工审阅和执行边界保持。

OpenAI 官方 Videos API 已于2026-09-24停止，官方视频调用在密钥/网络前拒绝；现有资料保留，其他兼容地址不自动取得供应商资格。见[视频服务可用性](site/content/docs/guide/video-service-availability.md)。

万相3.0已增加默认关闭的原生文字/图片档案及原项目预算/恢复软件链，使用自制素材完成本地验证；真实模型质量、账单和公网仍未验。见[万相配置指南](site/content/docs/guide/wan-video-generation.md)。

建议去重、官方视频退役与万相默认关闭档案已汇总，并修正全量及独立审查发现的旧空间升级/IDNA问题；旧失败保留，主线晋级仍须准确检查及最终独立审查。见[本地汇总版本](site/content/docs/architecture/local-delivery-integration.md)。


准确汇总源码8bb7f6d已完成独立审查、PR合并及主线2405项后端和实际容器检查。双账号原创声音/原生下载/重启和一次真实免费模型journal已独立补证，模型建议质量仍不通过。见[新增本地验收](site/content/docs/architecture/local-verification-observations.md)。

双账号真实免费系统配音与原字幕/五种下载/重启已有独立联合证据；真实AI编码新单次尝试超时，没有候选。配音页面改用原计划冻结角色名称，稳定身份和历史保持。本修订准确检查以Actions为准，17项当前未完成及费用0继续保留。
本机地址、截图或上游镜像不能作为本项目公网部署。Docker/MySQL配置不等于迁移、并发与恢复已经验收。

代码保留并基于[Forget-C/Jellyfish](https://github.com/Forget-C/Jellyfish)继续开发，遵循[现有许可证](LICENSE)，来源和发布边界见[UPSTREAM.md](UPSTREAM.md)。现有代码包含上游、既有改动和AI辅助开发成果，不能宣称全部从零AI原创。仓库里的上游Issues、Actions、Pages与镜像链接属于上游，不是已交付的自有平台。
<!-- features:end -->

## 当前可用范围

- 项目、章节、分镜、演员、服装、场景、道具、提示词和文件的持久编辑。
- 独立身份入口、个人空间和固定空间工作进程；旧本地模式保留。
- 两种纯自作示例：7镜、21条提示词的文字练习，以及含7段无声视频、图片与28秒剪辑草稿的[可导出示例](site/content/docs/guide/original-example.md)；新建保留旧工程与编辑。
- 有界顺序文本Agent、图片候选与采用、视频理解候选、视频执行及项目独立预算等软件协议；文本、图片、原片视觉/ASR及托管原生视频已接原项目费用；完整收费执行隔离仍待验，新身份空间收费提交保持关闭。
- 已声明Seedream图片协议支持原项目最多3张参考图、用途和顺序、候选预览与单独采用；[使用说明](site/content/docs/guide/reference-image-generation.md)列明费用和下载边界，真实模型与外部下载未验收。
- 已声明Seedance参考协议支持原项目显式图片/视频、首帧或首尾帧及顺序、候选和单独采用；[使用说明](site/content/docs/guide/video-reference-generation.md)列明完整费用与交付边界，真实模型与远程交付未验收。
- 不可变剪辑版本、硬切与交叉淡化、本地FFmpeg渲染和字幕导出。
- 独立声音时间线支持Windows已安装系统声音生成原对白候选、WAV音效/配乐导入、明确采用、裁剪音量及五类导出；[使用说明](site/content/docs/guide/sound-editing.md)。云端声音与真实成片质量仍待验收，适用范围及证据以功能登记为准。

实现、软件验证、真实模型质量和部署是不同状态。业务资源及明确嵌套/历史文件隔离、项目独立预算和离线迁移/回退工具已通过本机验证；完整收费执行隔离、正式账号切换、其余参考生成协议、声音扩展、完整成片、社区自动研发和公网稳定/预览平台仍待完成。

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

[工作空间部署准备](deploy/workspace/README.md)提供含静态前端、身份网关、独立空间进程与FFmpeg的单节点方案，稳定与预览使用不同数据卷。`2ced83d`版本已在GitHub Linux runner实际构建并通过40项容器检查，资格绑定该提交与镜像；当前改动按对应CI结果单独判断。公网尚未部署。旧MySQL/Celery Compose与上游镜像配置保留为来源资料，不能替代当前功能的部署验证。

## 开发与功能登记

```sh
python scripts/features.py --check
python scripts/features_check.py
```

功能登记源为[capabilities/features.json](capabilities/features.json)。API变化后运行 `pnpm run openapi:update`，前端使用生成客户端。编码、职责和文档规范见[AGENTS.md](AGENTS.md)。新增证据单独记录，保留历史发布与验收记录。

品牌变更只影响显示名称、图标和文档。数据库、业务编号、原请求标识、旧偏好键和内部包名保持兼容，不因改名重置生成请求。

## 社区与来源

功能需求可通过[本仓库Issues](https://github.com/weicaojava/fawnimo/issues)提交。已有默认关闭的独立本机签名登记与版本基础；真实事件接入、需求评估、排期、隔离研发、审核与预览发布仍在推进。现有顺序文本Agent不等同于研发Agent。

- [公开路线图](site/content/docs/plans/public-roadmap.md)
- [来源与发布边界](UPSTREAM.md)
- [许可证](LICENSE)
- [上游日文资料](docs/README.ja.md)，保留其原始产品名称及历史上下文。

维护者可在独立公开需求库查看有界文字相似建议，并登记原需求版本的评估、澄清和复议历史，见[维护者指南](site/content/docs/guide/community-review.md)。当前通过本机命令操作；AI自动评估、公开留言、排队和自动研发尚未接通。

已有独立社区需求库升级历史保护前，请按[维护者指南](site/content/docs/guide/community-review.md)运行明确的保护约束迁移；原记录保持原ID与内容。

身份图片准入默认关闭；明确列入空间后，预算仍按原项目管理。双账号真实固定进程与完整页面使用自作模型响应通过，不授予真实供应商费用。参见[维护者操作说明](site/content/docs/guide/workspace-image-generation.md)。
