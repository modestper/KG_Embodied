### 任务背景

用户希望确认当前 Neo4j 知识图谱是否方便迁移到另一台服务器继续查询，并了解适合现有 `lade`（无实验索引）和 `lade-index`（有实验索引）对照库的迁移方式。

### 关键结论

- 方便迁移。Neo4j 官方推荐使用 `neo4j-admin database dump` 导出单个归档文件，在目标服务器用 `neo4j-admin database load` 恢复。
- 如果目标服务器也要进行有索引/无索引对照实验，最省传输量的方案是只导出一次无实验索引的 `lade`，在目标服务器加载成两份，再只给第二份创建实验索引。
- 如果目标服务器只需要正常查询，直接迁移 `lade-index` 即可；数据库内的节点、关系、属性和索引定义会随数据库一起迁移。
- Neo4j Community Edition 执行 dump/load 时应停止对应数据库或容器；Enterprise Edition另有在线备份能力。
- 不建议直接把正在使用的 Docker `/data` 目录通过 `scp` 或 `rsync` 搬走。Neo4j 官方明确建议用 dump/load，直接复制数据库文件不属于受支持的备份方式，并可能产生不一致存储。
- dump 主要保存数据库内容，不包含用户、角色等 DBMS 级安全元数据；目标容器的认证信息需要单独设置或迁移。
- 两端最好使用完全相同的 Neo4j 镜像版本；迁移后应检查数据库、节点数、`run_id`、索引状态和查询结果。

推荐流程：

```text
源服务器 lade（停止容器）
          │
          ├── neo4j-admin database dump
          ▼
      neo4j.dump + SHA256
          │
          ├── rsync/scp
          ▼
目标服务器（相同 Neo4j 版本）
          │
          ├── load → lade       （不添加实验索引）
          └── load → lade-index （添加实验索引）
                         │
                         └── 等待 SHOW INDEXES 全部 ONLINE
```

### 执行过的操作

- 查阅 Neo4j 官方 Operations Manual 中的离线 dump、load、备份规划、Docker dump/load 和 Docker 数据卷说明。
- 结合当前项目中的 `lade` / `lade-index` 双库设计，评估了只迁移查询库以及完整迁移 A/B 对照环境两种方案。
- 本次没有停止容器、导出数据库或修改任何 Neo4j 数据。

官方参考：

- [Offline backup with dump](https://neo4j.com/docs/operations-manual/current/backup-restore/offline-backup/)
- [Load a database dump](https://neo4j.com/docs/operations-manual/current/backup-restore/restore-dump/)
- [Backup and restore planning](https://neo4j.com/docs/operations-manual/current/backup-restore/planning/)
- [Dump and load a Neo4j database in Docker](https://neo4j.com/docs/operations-manual/current/docker/dump-load/)
- [Mounting volumes in Docker](https://neo4j.com/docs/operations-manual/current/docker/mounting-volumes/)

### 修改内容

- 新增本说明文件：`docs/codex_notes/10/08-Neo4j知识图谱跨服务器迁移.md`。
- 未修改项目代码、现有迁移脚本或 Neo4j 数据库。

### 后续建议

- 先确认目标服务器的操作系统、Docker 可用性、磁盘空间和 Neo4j 镜像版本。
- 如果只做查询，优先迁移 `lade-index`；如果继续做索引性能对照，优先采用“一次 dump、两次 load、单边建索引”的方式。
- 传输归档后用 `sha256sum` 校验文件完整性。
- 恢复后执行 `SHOW DATABASES`、`SHOW INDEXES`，并按目标 `run_id` 核对节点与关系数量。
- 若需要实际迁移，可以再生成一套包含源端导出、传输、目标端恢复和校验的脚本；执行前应提供目标服务器连接方式、Neo4j 镜像版本、端口和存储路径。
