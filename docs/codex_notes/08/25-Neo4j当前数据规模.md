### 任务背景

用户询问刚才用于并发查询压测的 Neo4j 数据集规模有多大，是否只是千级节点规模。

### 关键结论

- 当前本机 Neo4j 总库规模是 `9974` 个节点、`40952` 条关系。
- 当前库里有 3 个 run：`chongqing-6-29`、`jilin-0925-ray001`、`jilin-0925-test001`。
- 刚才 100 并发压测实际使用的是 `jilin-0925-ray001`。
- `jilin-0925-ray001` 规模是 `9905` 个 run 内节点、`40705` 条相关去重关系。
- 因此刚才压测不是百万级图，也不是单纯千级节点，而是接近 1 万节点、4 万关系的万级以内图。
- `jilin-0925-ray001` 节点分布：`Event=6404`、`Task=3352`、`Region=99`、`Drone=50`。

### 执行过的操作

- 统计总节点数：`./run_in_lade.sh python scripts/timed_neo4j_query.py --cypher 'MATCH (n) RETURN count(n) AS node_count' --no-log`
- 统计总关系数：`./run_in_lade.sh python scripts/timed_neo4j_query.py --cypher 'MATCH ()-[r]->() RETURN count(r) AS relationship_count' --no-log`
- 统计各 run 节点和去重关系数量：`MATCH (run:Run) OPTIONAL MATCH (n)-[:IN_RUN]->(run) ... count(DISTINCT r)`
- 统计 `jilin-0925-ray001` 标签分布：`MATCH (n)-[:IN_RUN]->(:Run {id: $run_id}) RETURN labels(n), count(n)`
- 统计 `jilin-0925-ray001` 去重关系类型分布：`MATCH (n)-[:IN_RUN]->(:Run {id: $run_id}) MATCH (n)-[r]-() RETURN type(r), count(DISTINCT r)`

### 修改内容

- 新增本总结文件：`docs/codex_notes/2026-08-25-Neo4j当前数据规模.md`。
- 未修改项目代码或数据库。

### 后续建议

- 如果要验证“百万级图查询低于 500ms”，需要生成或导入百万级节点/关系的数据集后重新压测。
- 当前 `jilin-0925-ray001` 的 100 并发结果只能说明万级图上的点查并发性能，不能直接外推到百万级。
- 后续建议分别压测 10k、100k、1M 三档规模，并固定相同查询模板记录 p50/p95/p99。
