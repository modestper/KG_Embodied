### 任务背景

用户想知道如何查看当前 Neo4j 图谱里已经存放的节点规模，以及 Neo4j 知识图谱有哪些基本操作。

### 关键结论

- 查看节点总数可用 `MATCH (n) RETURN count(n);`。
- 查看关系总数可用 `MATCH ()-[r]->() RETURN count(r);`。
- 按节点标签统计可用 `MATCH (n) RETURN labels(n), count(n) ORDER BY count(n) DESC;`。
- 按关系类型统计可用 `MATCH ()-[r]->() RETURN type(r), count(r) ORDER BY count(r) DESC;`。
- 本项目 Neo4j 运行数据建议按 `KG_RUN_ID` 查询，避免不同实验混在一起。
- 本项目常用节点标签包括 `Run`、`Region`、`Drone`、`Task`、`Event`。
- 本项目常用关系包括 `IN_RUN`、`ASSIGNED`、`COMPLETED`、`ABOUT_DRONE`、`ABOUT_TASK`、`IN_REGION` 等。

### 执行过的操作

- 根据当前项目 Neo4j 接入代码和已有运行约定，整理常用 Cypher 查询和基础管理命令。
- 新增本文档用于后续查阅。

### 修改内容

- 新增 `docs/codex_notes/2026-06-29-neo4j基本操作.md`。
- 未修改业务代码。

### 后续建议

- 查询本项目实验数据时优先带上 `Run.id` 或节点属性 `run_id`。
- 重复实验建议使用不同 `KG_RUN_ID`，否则节点和事件会混在同一个实验上下文中。

## 常用查询

连接本机 Neo4j：

```bash
cypher-shell -a bolt://127.0.0.1:17687 -u neo4j -p neo4j123456
```

查看所有节点数量：

```cypher
MATCH (n) RETURN count(n) AS node_count;
```

查看所有关系数量：

```cypher
MATCH ()-[r]->() RETURN count(r) AS relationship_count;
```

按标签统计节点：

```cypher
MATCH (n)
RETURN labels(n) AS labels, count(n) AS count
ORDER BY count DESC;
```

按关系类型统计：

```cypher
MATCH ()-[r]->()
RETURN type(r) AS relationship_type, count(r) AS count
ORDER BY count DESC;
```

查看有哪些实验 run：

```cypher
MATCH (run:Run)
RETURN run.id AS run_id
ORDER BY run_id;
```

查看某个 run 下的节点数量：

```cypher
MATCH (n)-[:IN_RUN]->(:Run {id: "jilin-0925-local001"})
RETURN labels(n) AS labels, count(n) AS count
ORDER BY count DESC;
```

查看某个 run 下的关系概况：

```cypher
MATCH (n)-[:IN_RUN]->(:Run {id: "jilin-0925-local001"})
MATCH (n)-[r]->(m)
WHERE m.run_id = "jilin-0925-local001" OR (m:Run {id: "jilin-0925-local001"})
RETURN type(r) AS relationship_type, count(r) AS count
ORDER BY count DESC;
```

查看最近事件：

```cypher
MATCH (e:Event)-[:IN_RUN]->(:Run {id: "jilin-0925-local001"})
RETURN e
ORDER BY e.time_minute DESC
LIMIT 20;
```

查看某个无人机完成情况：

```cypher
MATCH (d:Drone {run_id: "jilin-0925-local001"})-[r:COMPLETED]->(t:Task)
RETURN d.id AS drone_id, count(t) AS completed_count
ORDER BY completed_count DESC;
```

删除某个 run 及其数据：

```cypher
MATCH (n)-[:IN_RUN]->(:Run {id: "jilin-0925-local001"})
DETACH DELETE n;

MATCH (run:Run {id: "jilin-0925-local001"})
DETACH DELETE run;
```
