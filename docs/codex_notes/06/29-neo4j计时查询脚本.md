### 任务背景

用户希望使用 Cypher 查询 Neo4j 时，同时记录每条查询所花的时间，并希望直接提供一个 Python 文件来完成这件事。

### 关键结论

- 已新增 `scripts/timed_neo4j_query.py`。
- 脚本默认连接 `bolt://127.0.0.1:17687`，默认用户 `neo4j`，默认密码 `neo4j123456`。
- 查询会打印耗时、返回行数和每行结果。
- 默认把每次查询记录追加到 `data/neo4j_query_logs.jsonl`。
- 支持内置查询，也支持 `--cypher` 直接传入自定义 Cypher。
- 支持 `--cypher-file` 从文件读取较长 Cypher。
- 支持 `--param key=value` 传查询参数，数值会优先按 JSON 解析。

### 执行过的操作

- 新增测试：`tests/timed_neo4j_query_test.py`。
- 先运行测试确认脚本缺失时报错：`./run_in_lade.sh python -m unittest tests.timed_neo4j_query_test`。
- 新增实现：`scripts/timed_neo4j_query.py`。
- 运行测试确认通过：`./run_in_lade.sh python -m unittest tests.timed_neo4j_query_test`。
- 验证命令行入口：`./run_in_lade.sh python scripts/timed_neo4j_query.py --help`。

### 修改内容

- `scripts/timed_neo4j_query.py`：新增带耗时记录的 Neo4j Cypher 查询脚本。
- `tests/timed_neo4j_query_test.py`：新增单元测试，验证耗时计算、日志写入和参数解析。
- `docs/codex_notes/2026-06-29-neo4j计时查询脚本.md`：新增本次任务总结。

### 后续建议

- 查看整体概览：

```bash
bash ./run_in_lade.sh python scripts/timed_neo4j_query.py
```

- 查看某个 run 下的节点统计：

```bash
bash ./run_in_lade.sh python scripts/timed_neo4j_query.py \
  --preset run-summary \
  --run-id jilin-0925-local001
```

- 查看某个 run 的最近事件：

```bash
bash ./run_in_lade.sh python scripts/timed_neo4j_query.py \
  --preset recent-events \
  --run-id jilin-0925-local001 \
  --limit 20
```

- 执行自定义 Cypher：

```bash
bash ./run_in_lade.sh python scripts/timed_neo4j_query.py \
  --cypher "MATCH (n) RETURN labels(n) AS labels, count(n) AS count ORDER BY count DESC"
```

- 带参数执行自定义 Cypher：

```bash
bash ./run_in_lade.sh python scripts/timed_neo4j_query.py \
  --cypher "MATCH (e:Event)-[:IN_RUN]->(:Run {id: \$run_id}) RETURN e.type AS type, count(e) AS count ORDER BY count DESC" \
  --param run_id=jilin-0925-local001
```

- 只打印不写日志：

```bash
bash ./run_in_lade.sh python scripts/timed_neo4j_query.py --no-log
```
