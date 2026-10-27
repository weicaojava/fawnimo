---
title: 本地成片导出
---

`LocalExportWorker`与收费模型执行器独立。`EditExportJob`固定工程、章节、不可变`EditPublication`及其完整hash、请求标识和输出profile。`EditExportProduct`是不可改写的产物回执；`EditExportSlot`在同一SQLite内限制一个活动渲染，并用180秒租约与随机token限制提交。通用`GenerationTask`使用`local_export`类型，关联实际章节，任务中心只展示状态、取消和剪辑页回跳。

新提交先按工程/请求标识查原意图，再完整校验原媒体；不同时间线复用标识409。工具不可用只阻断新的处理，历史ready读取仍可校验下载。草稿当前采用或角色变化不影响历史输出和锁定输入。取消只登记真实导出意图，由worker终止并join实际子进程；旧film取消入口也转交本服务，不能用Celery撤销页面投影假装处理已停止。

视频与音频均在独立子进程完整解码。视频保留实际整数PTS、有理timebase、有效呈现区间与时钟hash；末帧缺可靠duration则拒绝。第k输出帧采样`source_in_ms/1000+k/30`，选不大于采样时刻的最后一帧且要求仍在其有效区间内。PyAV/FFmpeg固定libx264编码，不用末帧补长。音频用同一首视频时钟为原点，保留正负偏移和内部缺口的位置，按声明补静音、裁剪、拼接，再一次性编码AAC；补静音不能满足必需对白原声覆盖。

每次尝试使用媒体根内新随机目录，server构造路径和参数数组，无shell、请求URL、用户滤镜或字体路径。源解码单次45秒，整个渲染helper包含验证/编码/成品全解码在120秒内，父121秒外层回收。helper内部watchdog每0.5秒检查运行时长及768MiB目录总量；它不接收数据库配置、不连接数据库，仍以同一操作系统用户运行，不宣称OS权限隔离。正常取消/关闭立即终止进程树并join，不等watchdog。崩溃恢复最多3次且每次新目录，旧token不能发布。

固定成品profile为360×640、30fps/H264/yuv420p/AAC48k stereo。产物再次完整解码，必须实际拥有精确`duration_ms*3/100`帧、每帧PTS=`index/30`、零视频起点及≤100ms声画终点差。所有源在处理后再次hash。只有持有当前job和slot租约、未取消，才能在同一短事务中建立FileItem、ProductionMedia、不可变回执和ready任务；最终flush之后再验租约。提交确认未知不会删除可能已被引用的字节。

MP4作为ProductionMedia保存；SRT/VTT/ZIP由回执固定随机私有路径、hash和size，读取只接受owned job+固定文件类型。每次ready查询和下载验证实际字节。ZIP仅含`video.mp4`、`subtitles.srt`、`subtitles.vtt`和`manifest.json`；清单含固定逻辑来源/裁剪/版本和人工声明，去除文件路径/stat，无模型资格声明。字幕以整数毫秒派生，原说话人和文本固定在清单；显示序列转义换行及标记字符，不执行文本或将其插入滤镜。前端video显式anonymous跨源加载VTT。

新数据库和旧M4B数据库均通过`init_db`/`migrate_editing.py`安装增量schema与scope/不可变触发器。恢复复制一致SQLite备份及全部媒体根到新目录；完整快照hash和源内容时钟保持，文件系统stat可重新验证，不把恢复后ctime变化当作历史内容变化。失败和未发布的随机尝试文件可能留作审计，不自动删除整个目录。已提交产物不可覆盖。

该软件能力允许人工素材及明确未获资格的执行候选。人工剧情/对白区间不是自动语义定位、真实发声或口型证据；测试色片/音调不是AI短剧。正式生成、配音、口型、内容覆盖与质量验收另有门禁。

实现参考：[FFmpeg滤镜文档](https://ffmpeg.org/ffmpeg-filters.html)、[FFmpeg协议限制](https://ffmpeg.org/ffmpeg-protocols.html)。具体可执行行为以本工程固定依赖与实际解码测试为准。
