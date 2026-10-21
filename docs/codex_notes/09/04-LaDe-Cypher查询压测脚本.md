### 任务背景

用户希望基于前面整理的图数据库查询工作负载理论和 LaDe-Cypher 查询模板，写一套可执行查询脚本，并记录每次查询的时间开销。

### 关键结论

- 已新增 `scripts/lade_cypher_benchmark.py`，内置 Q0-Q12 共 13 个 LaDe/Neo4j 查询模板。
- 脚本支持按模板 id、模板范围、类别选择查询，例如 `all`、`q0-q9`、`q3,q4`、`point,temporal`。
- 每次查询都会记录 JSONL 明细，包括模板名、类别、agent 编号、轮次、参数、是否成功、耗时、返回行数和错误信息。
- 脚本会生成 summary JSON，包含整体延迟、按模板延迟、按类别延迟统计。
- 脚本支持串行和并发；`--agents 100 --max-workers 100` 可以模拟 100 个 agent 并发查询。
- 支持自定义模板 JSON 文件，方便后续加入自己的 Cypher 查询。

### 执行过的操作

- 读取现有查询脚本和测试：
  - `scripts/timed_neo4j_query.py`
  - `scripts/concurrent_neo4j_query.py`
  - `tests/timed_neo4j_query_test.py`
  - `tests/concurrent_neo4j_query_test.py`
- 新增文件：
  - `scripts/lade_cypher_benchmark.py`
  - `tests/lade_cypher_benchmark_test.py`
- 执行验证命令：
  - `./run_in_lade.sh python -m unittest tests/lade_cypher_benchmark_test.py`
  - `./run_in_lade.sh python scripts/lade_cypher_benchmark.py --list-templates`
  - `./run_in_lade.sh python scripts/lade_cypher_benchmark.py --run-id test-run --templates q3,q4 --agents 2 --queries-per-agent 2 --start-step 1 --end-step 1000 --step-window 50 --dry-run`
  - `./run_in_lade.sh python -m unittest tests/lade_cypher_benchmark_test.py tests/concurrent_neo4j_query_test.py tests/timed_neo4j_query_test.py`
  - `./run_in_lade.sh python scripts/lade_cypher_benchmark.py --help`

### 修改内容

- `scripts/lade_cypher_benchmark.py`
  - 新增内置查询模板 Q0-Q12。
  - 新增模板选择、参数生成、并发执行、逐次 JSONL 日志、summary JSON 汇总。
  - 新增 `--template-file`，支持自定义查询模板。
  - 新增 `--dry-run` 和 `--list-templates`，便于执行前检查查询计划。
- `tests/lade_cypher_benchmark_test.py`
  - 覆盖模板选择、参数生成、时间窗口生成、单次查询计时、日志和 summary 输出、自定义模板参数推断。

### 后续建议

- 第一次真实运行建议先用 `--dry-run` 检查参数是否符合预期。
- 先跑轻量模板 `q0-q9`，确认 Neo4j 状态稳定后，再加入 `q10-q12` 复杂 join、路径和反连接查询。
- 如果要测 100 agent 并发，建议设置 `--max-connection-pool-size` 不小于 `--max-workers`。
- 如果 Neo4j 正在被仿真写入，建议先降低模板范围和窗口大小，避免复杂查询影响写入稳定性。
