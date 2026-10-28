---
title: Video DNA 来源与不可变快照
---

DNA绑定工程内实际ReverseAnalysis与不可变ProductionMedia。源快照保留原index/pts/time_base有理数/relative_us、confirmed_cut_indices和analysis修订；frame_clock_hash仅覆盖固定schema1与完整原帧列表，不混入机器候选分数、确认修订或代理fps。机器manifest独立作opaque hash，浮点候选分数不进入整数portable DNA。

DnaDraft身份、source、初始请求key/hash冻结，文档采用CAS修订。DnaSnapshot同草稿修订唯一且数据库禁止更新删除，canonical JSON只含安全整数、布尔、null和字符串。创建/导入精确原意图重放先于当前源可用性检查，不能把动态模板或后来修改的草稿作为原请求正文。

原视频及版本媒体完整hash在写锁外完成，短SQLite事务复查metadata/stat与当前分析manifest/cuts/revision。生产版本使用原M2规范核payload hash，但DNA只冻结version/asset/kind/contenthash和引用媒体sha/size/mime，不搬任意历史payload，避免JS往返浮点变化。私有路径及stat不进入portable快照。

Claim保留unknown/observed/inferred、证据帧半开区间、双语文本与人工置信度。镜头证据限本镜，continuity允许跨镜。所有角色/道具/场景和说话人引用按局部ID验证；未知字段不静默删除，也不转换为原作者事实。

历史owned_snapshot只核工程与完整canonical内容，不追随新cuts；preflight分别验证源和生产版本媒体可用性，并独立给出source_current提示。root纯compiler/comparison只读固定payload，准备清单显式禁执行，改变资产影响旧新引用镜头并集，元数据改变不扩散为请求变化。

R1没有可靠末帧duration，帧N只能作为序列哨兵，不能按邻帧间隔或标称fps补尾。此软件阶段不授予正式R2/R3、视觉/ASR、模型调用、重建或成片质量资格。

## 固定快照与七层准备

当前仅支持schema_version=1。文档上限512KiB；冻结、导出和导入使用同一个2MiB完整portable封包预算，包含外层id、content_hash与payload，不只计算内部正文。所有JSON数值必须是安全整数，布尔、浮点或指数写法不能充当版本或切点。

确定性compiler输出全局风格、角色锁定、场景锁定、逐镜图像、逐镜视频、负向约束、声音与剪辑七层。各层保留中英文人工声明、原帧证据、未知项及未映射的生产版本问题。准备结果上限8MiB，超限显式失败，不截断。随机snapshot ID保留在外层，语义hash只覆盖固定准备payload，因此相同内容另建快照仍得到相同准备hash。

两快照比较按稳定局部ID定位修改，并计算旧、新引用并集。全局或来源变化影响全部镜头；角色等资产变化只影响引用镜；标题等元数据变化不触发镜头请求变化。准备和比较均不创建生成任务、预留预算、采用候选或修改生产实体。

## 本地启动与已知运行边界

受支持的backend/run_local.py只绑定127.0.0.1，将HTTP优雅关闭等待设为5秒、keep-alive设为2秒；应用持久worker仍独立负责停止、join及数据库清理。它不声称为整个操作系统生命周期提供5秒上限。

一次本轮验证API关闭停滞的原因为UNKNOWN，记录为R2A-OPS-01 / OPEN / P2。受支持入口在实际原片、帧、代理和DNA浏览循环后已验证两个进程实际exit 0；这证明有限缓解和该次退出成功，不能认定旧停滞根因已修复。相关证据与旧数据库锁、备份I/O风险分别保留。
