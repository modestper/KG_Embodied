### 任务背景

用户提出另一种建立查询测试库的方法：先完整复制现有 Neo4j database，再在副本中删除不需要的 `run_id` 节点，只保留目标 run。

### 关键结论

- 该方案可行，并且能较完整地保留源库的数据结构、索引和约束。
- Neo4j Community Edition 的一致性整库 dump 需要数据库离线，因此源容器通常需要短暂停机；不能直接在运行中复制 `/data` volume。
- 删除必须只在新副本中进行，并应先核对目标 run 的节点和关系数量。
- 大量删除应使用 `CALL { ... } IN TRANSACTIONS` 分批执行，不能用一个巨大的 `MATCH ... DETACH DELETE` 事务。
- Neo4j 删除数据属于逻辑删除，store 文件不会立即缩小；空间会留待后续写入复用，事务日志在大规模删除期间还可能暂时增长。
- 如果目标 run 占整个源库的大部分，整库复制后删除通常更省事；如果只保留很小一部分，直接按 run 分批复制更节省时间和磁盘，也能得到更干净的测试库。

### 什么时候推荐这种方案

适合以下情况：

- 源库能够安排一次短暂停机；
- 有足够空间同时保存源库、dump 文件和目标库；
- 需要保留源库现有的所有索引和约束；
- 要保留的 run 占数据库较大比例；
- 希望降低自定义子图迁移漏关系、漏属性的风险。

不适合以下情况：

- 源库不能停机；
- 源库很大但只需要其中很小一个 run；
- 磁盘空间紧张；
- 希望目标库物理文件也接近目标 run 的实际大小；
- 其他 run 数量很多，大批量删除本身会非常耗时。

### 为什么不能直接复制运行中的 Docker volume

Neo4j 的 store、事务日志和检查点需要保持一致。运行中直接复制 `/data` 目录，文件可能来自不同时间点，恢复时可能出现不一致或需要恢复事务。

Neo4j 官方说明 `neo4j-admin database dump` 用于离线完整备份；Community Edition 只能在 DBMS 离线时执行。参考：[Neo4j 离线 dump](https://neo4j.com/docs/operations-manual/current/backup-restore/offline-backup/)。

正确路线应是：

```text
停止源库写入
  -> 生成完整 dump
  -> 尽快恢复源库服务
  -> 在独立目标容器 load dump
  -> 只在目标副本删除其他 run
```

### 删除前的统计

在目标副本中设置需要保留的 run：

```cypher
:param keep_run_id => '要保留的-run-id'
```

先确认它存在：

```cypher
MATCH (r:Run {id: $keep_run_id})
RETURN r.id;
```

统计准备保留的节点：

```cypher
MATCH (n)
WHERE n.run_id = $keep_run_id OR (n:Run AND n.id = $keep_run_id)
RETURN labels(n) AS labels, count(*) AS node_count
ORDER BY node_count DESC;
```

统计准备删除的节点：

```cypher
MATCH (n)
WHERE coalesce(n.run_id, '') <> $keep_run_id
  AND NOT (n:Run AND n.id = $keep_run_id)
RETURN labels(n) AS labels, count(*) AS node_count
ORDER BY node_count DESC;
```

这里使用 `coalesce()` 是为了同时发现没有 `run_id` 的遗留节点。执行删除前应人工查看这些无 `run_id` 节点是否属于需要保留的共享数据。

### 安全的分批删除语句

确认目标是独立副本、目标 run 正确且没有需要保留的共享节点后，可在目标库执行：

```cypher
MATCH (n)
WHERE coalesce(n.run_id, '') <> $keep_run_id
  AND NOT (n:Run AND n.id = $keep_run_id)
CALL (n) {
  DETACH DELETE n
} IN TRANSACTIONS OF 10000 ROWS;
```

这会删除：

- `run_id` 不等于目标值的节点；
- 其他 `Run` 节点；
- 没有 `run_id` 且不是目标 `Run` 的遗留节点；
- 与被删节点相连的关系。

因此该语句只能在专用目标副本中使用，不能直接在当前源库运行。

Neo4j 官方建议大批量删除使用 `CALL` 子查询分事务处理，避免单个巨大事务造成内存或 GC 问题。参考：[DELETE](https://neo4j.com/docs/cypher-manual/current/clauses/delete/) 和 [CALL 子查询事务](https://neo4j.com/docs/cypher-manual/current/subqueries/subqueries-in-transactions/)。

### 删除后的校验

确认只剩一个 Run：

```cypher
MATCH (r:Run)
RETURN collect(r.id) AS run_ids, count(r) AS run_count;
```

检查是否还有其他 run_id：

```cypher
MATCH (n)
WHERE n.run_id IS NOT NULL
RETURN n.run_id AS run_id, count(*) AS node_count
ORDER BY node_count DESC;
```

检查无 run_id 的非 Run 节点：

```cypher
MATCH (n)
WHERE n.run_id IS NULL AND NOT n:Run
RETURN labels(n) AS labels, count(*) AS node_count;
```

检查保留子图的关系数量：

```cypher
MATCH (a)-[r]->(b)
WHERE (a.run_id = $keep_run_id OR (a:Run AND a.id = $keep_run_id))
  AND (b.run_id = $keep_run_id OR (b:Run AND b.id = $keep_run_id))
RETURN type(r) AS relationship_type, count(*) AS relationship_count
ORDER BY relationship_count DESC;
```

这些结果应与删除前对目标 run 的统计一致。

检查索引是否仍在且为 ONLINE：

```cypher
SHOW INDEXES
YIELD name, state, type, labelsOrTypes, properties
RETURN name, state, type, labelsOrTypes, properties
ORDER BY name;
```

删除数据不会自动删除索引和约束，因此整库复制方案在 schema 保真方面有优势。

### 磁盘和性能影响

Neo4j 官方说明删除记录后，store 文件不会立即缩小；被删除记录的空间保留给未来写入复用。大规模删除还会产生事务日志，所以删除过程中磁盘占用可能短暂增加。参考：[Neo4j space reuse](https://neo4j.com/docs/operations-manual/current/performance/space-reuse/)。

这意味着：

- `MATCH/count` 看到的节点确实已删除；
- 索引也会移除相应条目；
- 但宿主机看到的数据库文件尺寸不一定下降；
- 物理 store 可能含有较多空闲、碎片化空间。

官方提供 `neo4j-admin database copy` 做压缩/碎片整理，但相关能力和选项受版本、edition 限制，Community 环境不能先假定全部可用。若磁盘空间不是问题，查询测试前通常不必为了缩小文件再做一次离线 copy；应优先保证源/目标使用相同版本、索引和预热流程。

### 对查询性能实验的影响

整库复制再删除得到的逻辑数据和索引可以与目标 run 一致，但物理布局不等同于“从零只导入该 run”的数据库。

因此它适合：

- 快速得到 schema 完整的隔离副本；
- 对现有查询进行功能与并发测试；
- 在不影响源库的情况下尝试索引方案。

如果研究重点是“只有目标 run 的全新数据库在最佳物理布局下能多快”，直接导入目标 run 更合适。如果研究重点是“从真实库克隆后隔离其他业务，查询能多快”，整库复制后删除更符合场景。

### 方案选择建议

可以按保留比例粗略判断：

- 保留大部分数据：优先整库复制后删除。
- 保留很小部分数据：优先按 run 子图迁移。
- 源库不能停机：优先按 run 在线只读迁移。
- 必须完整保留 schema：整库 dump/load 最省心。
- 磁盘紧张：避免整库复制后大批量删除。

对于当前项目，如果百万级数据库里目标 run 本身就是主要数据，整库复制后删除是很实用的方案；如果目标 run 只有几万节点、源库有数百万节点，则分批子图迁移更合理。

### 执行过的操作

- 核对 Neo4j Community 离线 dump 限制。
- 核对 Neo4j 官方大规模分批删除语法。
- 核对删除后的空间复用和 store 文件行为。
- 根据项目 `run_id` 属性和 `Run` 节点结构设计保留条件、删除条件与校验查询。

### 修改内容

- 未停止或启动任何容器。
- 未执行 dump、load 或删除操作。
- 未修改 Neo4j 数据和项目业务代码。
- 新增本分析文档：`docs/codex_notes/2026-09-16-整库复制后按run清理.md`。

### 后续建议

- 先统计数据库总节点数、目标 run 节点数和目标 run 占比，再选择整库复制或子图迁移。
- 如果选择整库方案，先确认源库允许的停机窗口和可用磁盘空间，再编写精确的 dump/load 命令。
- 删除前必须在目标副本核对容器名、Bolt 端口、保留 run_id 和无 run_id 节点清单。
- 下一步可根据源库版本、Docker volume 名称和目标 run_id 生成一套可逐条执行的 dump/load/清理命令。
