---
title: 公开源码提案与本地候选提交
---

`develop_community.py`将准确已prepared的公开需求准备记录转成惰性Python源码提案，并可形成本地候选Git提交。提案绑定原需求、功能编号、评估顺序、计划哈希、选择修订号及公开代码版本。提案程序本身不执行代码，也没有模型调用、GitHub推送、PR创建或部署操作；`execution_authorized`、`development_complete`、独立审核和CI资格始终分别保持false。候选测试也是待审源码，保存测试文字不表示测试运行或通过。

`public_python_change_v1`接收最多16个文件修改、8个明确的新候选测试、完整原输入2MiB。每个替换文件最多128KiB、全部新文字最多1MiB；新文字为UTF8/LF并保留末尾换行，修改前哈希绑定原始公开字节，原文件CRLF不被默默当成LF。既有文件修改只限准确功能登记的Python代码路径，新测试只限`backend/tests/test_community_candidate_*.py`。路径拒绝穿越、反斜杠、Windows设备名、大小写别名及模糊分段。控制器、来源校验、存储/租约/审核边界、CI、依赖或任意命令不在此提案权限内，即使功能登记提到它们也不放开。

新提案先复查原公开Issue，再通过固定`api.github.com`读取同一数字repo及准确main引用、commit、完整tree和所需regular100644文件blob，核对实际Git blob SHA1字节，最后再次核对main。截断tree、身份变化、私有/归档/停用仓库、重定向、链接、子模块、可执行模式、无关路径或内容替换均拒绝。本版只处理SHA1 Git仓库，单HTTP JSON最多1MiB、完整tree最多4096项；只收集功能登记及明确新测试所需的公开文件，不复制私人工作树。

公开代码检查在无数据库、无Git或模型凭据的自有子进程中运行，45秒总时限、16KiB输入、8MiB输出，只继承有限操作系统环境。固定公开GET不跟随重定向，不读取令牌、客户端host、个人模型配置或项目媒体，不自动重试，观察最多有效60秒。协议基础见GitHub的[引用](https://docs.github.com/en/rest/git/refs#get-a-reference)、[commit](https://docs.github.com/en/rest/git/commits#get-a-commit-object)、[tree](https://docs.github.com/en/rest/git/trees#get-a-tree)和[blob](https://docs.github.com/en/rest/git/blobs#get-a-blob)接口。

网络发生在SQLite写事务外。取得观察后，重新检查当前准备状态、原来源、最新评估及计划选择，在原需求库短写事务中固定该检查时点，再向独立提案库追加。远程main和安装代码的功能登记版本须相同。生成的bundle固定原输入、修改前后完整文字/哈希、所选公开代码清单、源码scope和patch。读取时重算所有派生关系；重算外层哈希仍不能隐藏文件、patch、测试列表或原输入之间的不一致。所有资格字段和整个协议形状严格校验。

提案存入单独目录的`changes.sqlite3`，与需求库及个人视频资料分开，不放入源需求目录的子目录。两张准确已知表保存不可变profile/repo和原提案。记录拒绝更新、删除、逻辑键替换和显式rowid碰撞；根容量为128个bundle、每个work16个、合计64MiB。满额停止新写，原请求恢复保留。操作系统维护者是受信操作者，这些约束不提供同一操作系统用户之间的权限隔离。

本地候选提交再次复查原Issue和公开代码，重新绑定准确当前准备及原bundle。Git仅使用本公开克隆中的已提交对象与单独临时index，不读入工作树的已有暂存、未暂存或未跟踪内容，不checkout、不reset、不沿用全局凭据/钩子/签名配置。固定Git操作每次最多15秒，整个嵌套构建/恢复最多20秒。候选tree只包含原提案声明的文件变化，以CAS新建由bundle哈希派生的`community/change-…`本地分支。原HEAD和index保持原内容。

原本地提交恢复核对准确tree、唯一原base父提交及固定bundle/scope消息，返回同一个commit，不重新生成时间戳，也不覆盖后来或他人占用的分支。Git对象存在于本机不代表已创建远程PR、CI通过或已发布；提交署名也不是独立审核者身份。ZIP源码可保存/读取提案；创建本地候选需要本项目Git克隆及已存在的准确公开base对象，工具不自动fetch。

曾实际复现Windows下多个控制器使用不同index仍同时写同一Git对象，`git-write-tree`返回Permission denied；原失败记录与锁定修正证据保留。现在按实际共享Git objects目录取得跨进程内核写锁，覆盖不同工作目录，线程内嵌套可重入。标识文件位于`objects/info/fawnimo-community-writes.lock`，先取得内核锁再读标识，以适配Windows字节锁的强制读取限制。进程异常退出后等待实际内核所有权释放，不根据PID死亡删锁文件；未知或不完整标识停止，不自动覆盖。锁解决对象写入互斥，不把Git或本机进程提升为安全执行环境。

原提案请求恢复和已存在本地候选恢复均先于当前网络/来源门禁。来源后来变化时旧结果仍可读取，新提案或新候选须按当前准确来源重新验证。准备租约不会变成代码执行能力。单独明确启用的[固定Linux检查程序](../community-code-checks/)实现了有界候选执行、未知结果保留及确认清理；实际OCI资格由独立准确CI记录证明。远程PR及准确头的独立审查/CI晋级仍待完成。操作见[维护指南](../../guide/community-changes/)，当前软件证据和资格边界见[功能总表](../../reference/feature-catalog/)。
