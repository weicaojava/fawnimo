---
title: "本机视频生成配置与项目预算"
description: "托管原生模型、单份项目预留、独立原账号控制与当前提交边界。"
---

# 本机视频生成配置与项目预算

默认目录只用于准备。应用使用 `build_managed_video_runtime`：服务器文件明确选择本空间库内model_id，模型标记 `seedance25_text_v1`，实际名称为 `doubao-seedance-2-5-260628`，受支持Ark供应商/固定origin和本协议独立命名报价一致时才允许新生成。只读配置检查不捕获key值、不发请求；发送前一次查询捕获同一连接时钟与临时key。

单独工作室使用 `VIDEO_RUNTIME_CONFIG` / `--video-config` 和 `MODEL_PRICE_CONFIG`。固定身份worker只加载自身已标记、未导入空间的 `private/native-video.json` / `private/model-prices.json`，不继承主机路径或key。导入执行锁仍覆盖所有scanner。文件不接受key、端点、CDN或网络例外；生产固定Ark HTTPS/CDN，回环例外仅由受信测试服务独立注入。旧credential_env/account_version/quote_minor/quote_note字段可解析旧文件，当前应用不以它们授予新费用权限。

固定档案仍为文字输入、4至30整数秒、9:16/480p/MP4、无生成音频并保留水印；参考图/视频/音频不被静默忽略。准备档案、历史输入与只读预览不自动获得执行或模型质量资格。`GET /video-runtime/models` 只投影明确用途与报价，`setup`显示本机就绪状态，`configuration-template`默认关闭。旧Film/Celery动作继续关闭。

## 准入与原请求恢复

不可变计划按完整当前policy绑定；旧null报价准备计划须显式保存新修订并重新编译。请求预览GET不建任务、不预留、不访问模型，固定can_submit=false；页面新提交还须通过当前同计划预览与精确报价确认。当前UI/API逐镜提交，内部原子批次最多64项不等于已提供整章批量界面。

原生job先创建其实际ledger预留，共同账单直接使用同一job.id，不再reserve第二份。全部job/调用/原项目/提交人/原计划与报价在同一SQLite writer事务封存。发送每项前检查完整集合、每个兄弟ledger/attempt/最终收据与差额及全部账单门禁，预算不足或结构失配没有已付前缀。准备unknown和真实attempt后才做唯一POST；响应头的账单请求ID与内容任务ID分开。

界面保存原项目、原计划/镜头、确认布尔值、请求key与完整价格指纹。已接受请求先返回原记录，不受当前价格、运行开关、满预算或dirty影响；刷新后手动核对保留当前编辑。另生成候选明确新增完整预留，原未知费用保留。不同actor或内容不能复用原意图。

## 原账号控制与费用

新生成开关关闭时只允许已封存任务继续原账号query/download；不自动执行无共同绑定的旧队列。改当前价格、迭代限额或移除新生成用途，不给旧任务换账号，也不阻断仍能证明相同原连接的控制读。逻辑模型、实际名称、供应商时钟或固定服务profile变化后不拿新key访问旧任务。已接受任务不因当前剧本事实改变而重新提交。

取消只释放无attempt且planned的原预留，并推进共同call为not_sent；prepared/accepted费用保持unknown，旧finish/settle/recovery不能退还它。明确匹配的人工规范化账单才能结算或释放。ready、失败、取消意图、tokens和请求ID均不代表实际账单或免费；真实供应商认证仍false。原生取消协议仍仅GET确认已cancelled，不发DELETE。

短DB工作覆盖事务、Session关闭和两lease清理，重复停止也等待物理清理结束；外部HTTP可取消，取消后再join其清理。费用重放/控制的真实SQLite读快照避免账单并发造成混读。候选接收后仍由剪辑页独立采用，不自动入时间线。

## 项目预算和验收边界

每个项目有独立累计上限、预留、已结算和待补账单；无工作空间总上限，不借其他项目。上限使用修订CAS，不能低于预留、已结算及待补金额。保存预算不启用生成或释放未知费用。页面在“项目设置→项目预算”，执行任务区复用同一组件。

自作SQLite、实际回环HTTP/MP4与浏览器验证有界软件行为；源码证据见 `capabilities/native-video-accounting-validation.json`。两个项目不是两个身份空间。全部收费身份联合验收、真实模型/账单/成片和公网仍待完成，身份网关收费门禁继续关闭。
