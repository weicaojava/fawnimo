---
title: Seedance 2.5 准备与软件适配
---

S25A使用既有M4B不可变镜头计划、受信准入、DurableExecution及实际媒体验证发布。新增Seedance25DurableTransport负责固定官方协议映射；不复用旧通用视频执行入口，不将软件夹具当成官方模型输出。

## 当前支持范围

精确模型为doubao-seedance-2-5-260628，仅文生视频、4–30整数秒、9:16、480p、MP4、关闭生成音频、保留水印。首尾帧、多图、视频/音频参考、自动时长、draft、编辑和延长均不在本软件切片。资产版本自带媒体与额外参考合并后非空会明确阻断；系统不会删除或转换这些输入。

固定输出、端点、下载host、网络限制及独立服务/账号版本进入profile hash，再由binding_id进入已有计划与任务绑定；密钥不参与hash或portable输入。默认准备档案使用PREPARATION_ONLY身份、quote_minor=null，生命周期无凭据、无dispatcher、无网络。app只在没有显式注入runtime时提供它，关闭时移除本次创建的对象。

## 不执行的请求预览

现有工作室计划抽屉可选择S25A准备模型、保存、编译、查看并下载实际wire body。只读API核对工程归属、不可变内容hash、当前M4B事实及精确准备binding。旧null/其他绑定或stale计划返回问题与body=null，需要新草稿修订重新编译。输入准备可用不要求付费can_submit；所有S25A预览的can_submit、execution_enabled、model_qualified始终false。

模型管理不再用定时器显示连接成功或模型测试成功。保存配置、只读鉴权、内容准备、远端任务成功、本地ready和质量通过分别表达。

## 持久协议和隐私

控制请求使用固定Ark HTTPS origin，15秒、64KiB响应、零重定向及隔离网络子进程。只有HTTP200和严格合法JSON/任务身份属于协议成功；本地未发出前拒绝与未知受理分开。没有已核实幂等或按本地attempt_id对账能力，reconcile返回None；超时、断连、缺id或不确定错误不自动补POST。

查询严格匹配remote id、精确模型与状态；expired按失败处理。数据库仅记录有限状态和remote locator，不保留签名下载URL或原错误信息。下载阶段重新查询同一任务，在内存验证签名URL并下载；控制与下载合计45秒、产物30秒/默认64MiB/至多3跳，逐跳DNS固定和目标检查。下载不携带认证头，失败重试不重新生成。

cancel只查询是否已cancelled，不发DELETE，避免删除已完成任务记录；取消意图不等于远端取消或退款。

## 限流与恢复

查询、取消查询、下载元数据及产物遇429时，通过QueryBackoff立即持久暂停原阶段，保留取消意图和预算。Retry-After支持整秒与HTTP日期，向上取整且不缩短；无合法值使用30秒。next_due保存not-before，精确和转为浮点时只向未来取可表示值。

worker及API恢复入口都将原暂停上下文、not-before、lease和state纳入实际写入CAS。未到等待截止返回409，过期后仍需显式恢复原任务；并发旧恢复不能清除新的限流暂停。长等待立即暂停，不让任务绕过自动等待上限。提交阶段429仍属未知受理，不能通过恢复路径重新提交。

当前真实模型资格、正式M3V/M5/M6、人物参考和有声短剧尚未通过；本软件接入不授予这些资格。

## 共同项目费用接线

应用执行由模型库显式原生用途与native_video命名报价组成，固定model/output/wire约束不变。新的托管绑定还封存逻辑模型、实际名称、供应商连接时钟及完整报价；key只在发送前一查询临时捕获，不进入profile/hash。新POST的价格绑定与旧任务原账号控制分别处理，详见[视频运行时](../workspace-video-runtime/)和[共同账单](../model-accounting/)。

create响应头的安全x-request-id仅作私有账单匹配；body任务handle只供query/download，不猜账单身份，错误/坏JSON仍保留已收到的安全头。无法匹配的账单继续未知。软件夹具的ready、已核对的自作账单和接线不授予真实供应商或模型资格。
