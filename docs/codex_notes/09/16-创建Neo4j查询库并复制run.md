### 任务背景

用户希望创建一个专门用于查询性能测试的独立 Neo4j 仓库，并从当前 `17687` 源实例中只复制指定 `run_id` 的节点和关系，而不是复制整个数据库。

### 关键结论

- 推荐新建第三个独立 Docker 容器 `lade-query-neo4j`，使用独立端口和独立 Docker volume，不覆盖当前两个 Neo4j 容器。
- 新容器应尽量使用与源容器相同的 Neo4j 版本；性能对比时版本、内存、CPU 和索引配置都应保持一致。
- `neo4j-admin database dump/load` 是整库离线备份方案，不能按 `run_id` 过滤；Community Edition 源库执行 dump 还需要数据库离线，因此不适合当前需求。
- 按 `run_id` 复制应采用子图迁移：复制目标节点、复制这些节点之间的关系、复制/重建相同 schema，最后逐标签和逐关系类型核对数量。
- 源 run 在复制过程中应停止写入，否则节点和关系可能来自不同时间点，无法得到一致快照。
- 当前仓库尚无现成的 `copy_neo4j_run.py`；最稳妥的下一步是生成一个带 `--dry-run`、`--apply` 和 `--verify` 的分批迁移工具，而不是手工拼接大量 Cypher。

### 第一步：确认源实例和版本

当前查询脚本默认源实例是：

```text
容器：neo4j
HTTP：http://127.0.0.1:17474
Bolt：bolt://127.0.0.1:17687
数据库：neo4j
```

查看版本：

```bash
sudo docker exec neo4j neo4j --version
```

记录输出的完整版本。新容器尽量使用相同的固定版本标签，不要为了方便再次使用可能随时间变化的 `neo4j:latest`。

### 第二步：确认要复制的 run

进入源库：

```bash
sudo docker exec -it neo4j cypher-shell -u neo4j -d neo4j
```

输入密码后列出 run：

```cypher
MATCH (r:Run)
OPTIONAL MATCH (n)-[:IN_RUN]->(r)
RETURN r.id AS run_id, count(n) AS linked_nodes
ORDER BY linked_nodes DESC;
```

选择 run 后，在 Cypher Shell 中设置参数：

```cypher
:param run_id => '要复制的-run-id'
```

按标签统计节点：

```cypher
MATCH (n)
WHERE n.run_id = $run_id OR (n:Run AND n.id = $run_id)
RETURN labels(n) AS labels, count(*) AS node_count
ORDER BY node_count DESC;
```

按关系类型统计目标子图内部关系：

```cypher
MATCH (a)-[r]->(b)
WHERE (a.run_id = $run_id OR (a:Run AND a.id = $run_id))
  AND (b.run_id = $run_id OR (b:Run AND b.id = $run_id))
RETURN type(r) AS relationship_type, count(*) AS relationship_count
ORDER BY relationship_count DESC;
```

记录这两份结果，迁移后必须在目标库得到相同数量。

还应保存源库 schema：

```cypher
SHOW INDEXES
YIELD name, state, type, labelsOrTypes, properties
RETURN name, state, type, labelsOrTypes, properties
ORDER BY name;
```

```cypher
SHOW CONSTRAINTS;
```

### 第三步：创建独立查询容器

当前已有端口 `17474/17687` 和 `27474/27687`，建议新容器使用：

```text
HTTP：37474
Bolt：37687
```

先创建独立数据卷：

```bash
sudo docker volume create lade-query-neo4j-data
```

避免把密码直接写入 shell 历史：

```bash
read -rsp '新 Neo4j 密码: ' LADE_QUERY_PASSWORD
echo
```

假设源库版本命令显示 `5.26.x`，可使用相同补丁版本；下面的 `<SOURCE_VERSION>` 必须替换成实际版本，例如 `5.26.12-community`：

```bash
sudo docker run -d \
  --name lade-query-neo4j \
  --restart unless-stopped \
  -p 37474:7474 \
  -p 37687:7687 \
  -v lade-query-neo4j-data:/data \
  -e NEO4J_AUTH="neo4j/${LADE_QUERY_PASSWORD}" \
  neo4j:<SOURCE_VERSION>
```

随后清除当前 shell 变量：

```bash
unset LADE_QUERY_PASSWORD
```

Neo4j 官方 Docker 文档确认 `-p` 用于端口映射、`NEO4J_AUTH` 用于初始认证、将 volume 挂到 `/data` 用于持久化。参考：[Neo4j Docker 入门](https://neo4j.com/docs/operations-manual/current/docker/introduction/) 和 [Docker volumes](https://neo4j.com/docs/operations-manual/current/docker/mounting-volumes/)。

检查启动日志：

```bash
sudo docker logs --tail 100 lade-query-neo4j
```

看到数据库启动完成后测试连接：

```bash
sudo docker exec -it lade-query-neo4j cypher-shell -u neo4j -d neo4j \
  'RETURN 1 AS ok;'
```

也可以访问：

```text
http://127.0.0.1:37474
```

### 第四步：选择复制方式

#### 不推荐：整库 dump/load

`neo4j-admin database dump` 会备份整个 database，不能只选某个 `run_id`。Neo4j 官方说明 Community Edition 只能对离线 DBMS 执行该操作，因此会要求停止当前源库。参考：[Neo4j 离线 dump 文档](https://neo4j.com/docs/operations-manual/current/backup-restore/offline-backup/)。

如果以后确实要完整克隆数据库，dump/load 很合适；本次只复制一个 run，不应使用它。

#### 可选：APOC 导出

APOC 支持把指定查询返回的节点和关系导出为 Cypher，官方接口为 `apoc.export.cypher.query`。参考：[APOC query export](https://neo4j.com/docs/apoc/current/overview/apoc.export/apoc.export.cypher.query/)。

但这种方法要求源库已经安装并允许 APOC 文件导出。为了复制数据再给正在运行的源容器安装插件并重启，风险高于收益。如果下面查询失败，表示当前没有可用 APOC：

```cypher
RETURN apoc.version();
```

除非 APOC 已经安装并配置好，否则本项目更推荐 Python 驱动迁移。

#### 推荐：Python 驱动分批复制

迁移工具应同时连接两个实例：

```text
源：bolt://127.0.0.1:17687，只读
目标：bolt://127.0.0.1:37687，写入
```

正确的复制顺序是：

1. 检查目标库不存在同名 run；默认拒绝覆盖。
2. 从源库按 `run_id` 流式读取节点的标签和全部属性。
3. 分批在目标库创建节点，并保存临时迁移键或使用稳定业务键定位节点。
4. 从源库流式读取“起点和终点都属于该 run”的关系，包括关系类型和全部属性。
5. 分批在目标库重建关系。
6. 创建与源库一致的索引和约束，等待全部为 `ONLINE`。
7. 删除临时迁移键。
8. 在源库和目标库分别执行节点、关系统计并逐项比较。

项目节点通常有 `id`，非 `Run` 节点还有 `run_id`，可以构造稳定业务键：

```text
Run:        (label, id)
其他节点:  (label, run_id, id)
```

不要把 Neo4j 内部 node ID 当作永久业务键。若迁移工具临时使用 `elementId()` 建立映射，复制完成后应移除该映射，且源 run 在整个迁移过程中不能变化。

### 第五步：目标库 schema

为得到可比较的查询耗时，目标库索引必须与源库一致，而不是随意增加更多索引。项目自动维护的 AgentState 索引为：

```cypher
CREATE INDEX lade_agent_state_run_drone_step IF NOT EXISTS
FOR (n:AgentState) ON (n.run_id, n.drone_id, n.step_index);

CREATE INDEX lade_agent_state_run_region_step IF NOT EXISTS
FOR (n:AgentState) ON (n.run_id, n.region_id, n.step_index);

CREATE INDEX lade_agent_state_run_step IF NOT EXISTS
FOR (n:AgentState) ON (n.run_id, n.step_index);
```

但是不能只盲目创建这三个。应以源库的 `SHOW INDEXES` 和 `SHOW CONSTRAINTS` 输出为准，在目标库重建与本次查询相关的 schema。迁移完成后执行：

```cypher
SHOW INDEXES
YIELD name, state, type, labelsOrTypes, properties
RETURN name, state, type, labelsOrTypes, properties
ORDER BY name;
```

开始性能测试前，所有目标索引必须是 `ONLINE`。

### 第六步：迁移后校验

目标库连接命令：

```bash
sudo docker exec -it lade-query-neo4j cypher-shell -u neo4j -d neo4j
```

重新设置相同 `run_id` 参数，并执行前面的节点与关系统计。需要满足：

```text
每个标签的节点数：源 = 目标
每种关系的数量：源 = 目标
目标库不存在其他 run
查询所需索引全部 ONLINE
```

确认目标库只包含一个 run：

```cypher
MATCH (r:Run)
RETURN collect(r.id) AS run_ids, count(r) AS run_count;
```

抽查关系完整性：

```cypher
MATCH (a)-[r]->(b)
WHERE a.run_id = $run_id OR (a:Run AND a.id = $run_id)
RETURN type(r), labels(a), labels(b), count(*)
ORDER BY type(r), labels(a), labels(b);
```

最后使用目标端口运行查询脚本：

```bash
./run_in_lade.sh python scripts/lade_llm_context_queries.py \
  --uri bolt://127.0.0.1:37687 \
  --user neo4j \
  --run-id '要复制的-run-id' \
  --drone-id drone-1 \
  --step-index 100 \
  --queries core
```

脚本默认密码是源库默认值。若目标库使用不同密码，应通过环境变量 `NEO4J_PASSWORD` 传入；为了避免密码留在命令历史，可先交互读取：

```bash
read -rsp '查询库密码: ' NEO4J_PASSWORD
echo
export NEO4J_PASSWORD
```

运行完成后：

```bash
unset NEO4J_PASSWORD
```

### 性能测试前的额外要求

- 不要在复制过程中测试；等待迁移和索引建立完成。
- 对源库和目标库使用相同查询参数、`max_workers`、连接池和日志配置。
- 固定是否预热；例如先执行一轮不计入统计，再正式测量。
- 独立容器与源容器仍在同一台宿主机上，会竞争宿主机 CPU、内存和磁盘。严格实验时不要同时压测两个库，也应避免 Ray/仿真持续高负载运行。
- 同时记录 P50/P95/P99、吞吐、节点数、关系数和索引状态。

### 执行过的操作

- 阅读 `simulation/knowledge.py`，核对 Run、Drone、Task、Region、Event、Step、AgentState 的属性和关系写入方式。
- 阅读 `scripts/build_neo4j_scale_dataset.py`，核对已有 schema 和大规模数据构造方式。
- 核对当前项目只自动创建三组 AgentState 复合索引。
- 查阅 Neo4j 官方 Docker、volume、离线 dump 和 APOC query export 文档。
- 设计独立容器、分批子图复制和迁移后校验流程。

### 修改内容

- 未启动或停止任何容器。
- 未读取、修改或复制 Neo4j 数据。
- 未修改现有业务代码。
- 新增本操作说明：`docs/codex_notes/2026-09-16-创建Neo4j查询库并复制run.md`。

### 后续建议

- 先执行源版本检查和 run 计数，确认具体 `run_id` 已停止写入。
- 确认目标端口 `37474/37687` 和容器名 `lade-query-neo4j` 可用后，再创建容器。
- 下一步建议新增一个项目内的 `scripts/copy_neo4j_run.py`：默认 dry-run，显式 `--apply` 才写入，并提供迁移后自动校验；需要用户提供要复制的 `run_id`，不需要在聊天中提供数据库密码。
