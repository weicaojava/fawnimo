---
title: "迁移旧本地项目"
description: "维护者制作私有备份包，明确认领到新个人空间，并在独立目录验证回退。"
---

# 迁移旧本地项目

先备份并停止旧服务写入，明确迁入账号，关闭目标控制根的网关及worker。使用此版本的受支持启动入口；旧版本没有维护锁，需要维护者确认它已停机。以下命令均在 `backend` 目录使用项目Python环境运行，路径示例需替换为自己的私有目录，不能放到公开仓库或共享下载目录。

1. 制作未认领的备份包：

   ```text
   python migrate_workspace.py stage --database <旧studio.db> --media <旧media目录> --files <旧files目录> --bundle <全新私有备份包目录>
   python migrate_workspace.py verify --bundle <私有备份包目录>
   ```

2. 使用已有的有效owner账号。该账号的个人空间必须尚未初始化；不能把旧数据直接盖到已经打开使用的账号目录。需要专用账号时，先用 `run_controlplane.py --root <控制根> --provision-account <账号> --credential-file <全新私有凭据文件>` 建立账号，尚不要从浏览器进入其项目页。

3. 明确认领：

   ```text
   python migrate_workspace.py claim --bundle <私有备份包目录> --root <控制根> --account <明确的账号>
   ```

   正常输出包含 `runtime_locked=true`。启动原控制根的网关后，可用指定账号查看旧项目和媒体。导入的后台任务保持暂停执行，手工编辑可用；这一步不恢复模型调用、旧任务或导出队列。密钥仍可能保存在私有数据库中，不要分享备份包。

4. 中断后使用同一个备份包、控制根和账号重试原命令。不要删掉控制库的认领记录或手工替换目标目录。目标已存在但不匹配时，工具拒绝处理；已完成后重试不会覆盖新编辑。未完成的隐藏待发布副本保留用于排查，不会被工作进程当成账号目录。

5. 在另一个全新目录验证回退：

   ```text
   python migrate_workspace.py restore --bundle <私有备份包目录> --destination <全新独立恢复目录>
   ```

   该命令恢复旧版原始数据，不启动服务，不更改已迁入后的编辑。不要把恢复目录指向原数据目录、备份包内部或现有空间。

若提示源变化，应停止写入后制作新的备份包；若提示缺少本地文件或外部存储，不要绕过检查。当前支持完整本地 SQLite/media/files，不支持在线合并、跨空间迁移或自动对象存储下载。当前机制与执行锁见[迁移架构](/docs/architecture/workspace-migration/)。
