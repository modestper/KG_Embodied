### 任务背景

用户希望把通用查询压测模板调整成更贴近真实运行的“drone 调用 LLM 前先查 Neo4j”的即时查询模板，并将这些查询语句放到另一个 Python 文件中。

### 关键结论

- 已新增 `scripts/lade_llm_context_queries.py`，专门用于模拟 LLM 调用前的 Neo4j 即时上下文检索。
- 新脚本与 `scripts/lade_cypher_benchmark.py` 分离：前者面向在线决策上下文，后者面向通用查询压测。
- 即时查询模板命名为 `i0-i11`，覆盖自我状态、短期轨迹、当前任务、近期事件、近期表现、区域负载、邻近 drone、区域待处理任务、全局 step 摘要和全局区域瓶颈。
- 默认查询包 `core` 包含 `i0,i1,i3,i4,i5,i6,i9`，适合作为每次 LLM 调用前的轻量上下文。
- 脚本会输出两类日志：逐条查询耗时 JSONL，以及每个 drone 的 LLM context JSONL。
- 支持 `--queries decision`、`--queries coordination`、`--queries diagnostic` 和 `--queries all` 等查询包。

### 执行过的操作

- 读取现有代码：
  - `simulation/knowledge.py`
  - `simulation/delivery_sim.py`
  - `scripts/lade_cypher_benchmark.py`
- 新增文件：
  - `scripts/lade_llm_context_queries.py`
  - `tests/lade_llm_context_queries_test.py`
- 执行验证命令：
  - `./run_in_lade.sh python -m unittest tests/lade_llm_context_queries_test.py`
  - `./run_in_lade.sh python scripts/lade_llm_context_queries.py --list-queries`
  - `./run_in_lade.sh python scripts/lade_llm_context_queries.py --run-id test-run --drone-ids drone-1,drone-2 --step-index 100 --time-minute 600 --queries core --dry-run`
  - `./run_in_lade.sh python -m unittest tests/lade_llm_context_queries_test.py tests/lade_cypher_benchmark_test.py tests/concurrent_neo4j_query_test.py tests/timed_neo4j_query_test.py`
  - `KG_STEP_INDEX=100 KG_TIME_MINUTE=600 ./run_in_lade.sh python scripts/lade_llm_context_queries.py --run-id test-run --drone-ids drone-1 --queries core --dry-run`

### 修改内容

- `scripts/lade_llm_context_queries.py`
  - 新增 `ContextQuery` 和 `CONTEXT_QUERIES`，内置 `i0-i11` 即时查询模板。
  - 新增查询包：`core`、`decision`、`coordination`、`diagnostic`、`all`。
  - 支持 `--run-id`、`--drone-id/--drone-ids`、`--step-index`、`--time-minute`、`--history-steps`、`--event-window-minutes`、`--deadline-horizon-minutes`。
  - 支持输出 `query_log_jsonl`、`context_jsonl`、`summary_json`。
  - 支持通过环境变量 `KG_STEP_INDEX`、`KG_TIME_MINUTE` 传当前仿真时刻。
- `tests/lade_llm_context_queries_test.py`
  - 覆盖查询包选择、即时窗口参数生成、context 结构、逐条查询日志、summary 输出、自定义查询参数推断。

### 后续建议

- 如果要接入真实仿真流程，应在每个 drone 调用 LLM 前传入当前 `run_id`、`drone_id`、`step_index` 和 `time_minute`。
- 推荐先使用 `core` 查询包，等确认 Neo4j 延迟稳定后再使用 `decision` 或 `coordination`。
- `known_open_tasks_in_region` 只能查询知识图谱中已经写入的任务；如果未来希望 LLM 看到所有等待任务，需要在 Neo4j 写入流程中补充 pending/waiting task 的节点写入。
- 如果 100 个 drone 同时调用 LLM 前都查 Neo4j，建议配合 `--max-workers` 和 `--max-connection-pool-size` 做并发延迟测试。
