### 任务背景

用户希望 `scripts/lade_llm_context_queries.py` 在运行时只传入 `step_index`，不要再手动传 `time_minute`，以避免 `step_index` 和 `time_minute` 不对应造成 LLM 前置上下文错位。

### 关键结论

- 已修改 `scripts/lade_llm_context_queries.py`，真实执行时只需要传 `--step-index`。
- 如果没有显式传 `--time-minute`，脚本会先查询 Neo4j：
  - `MATCH (s:Step {step_index: $step_index})-[:IN_RUN]->(:Run {id: $run_id})`
  - 读取 `s.time_minute`
- 读取到的 `time_minute` 会用于事件窗口、近期表现和任务截止窗口查询。
- 只有选中的查询需要 `time_minute` 或 `start_time` 时才会自动读取 Step 时间；只跑 `i0` 这类不依赖时间的查询不会强制要求 `step_index`。
- `--time-minute` 仍保留为高级手动覆盖参数。
- `--dry-run` 不连接 Neo4j，因此如果只 dry-run 且想展示完整窗口参数，仍需显式传 `--time-minute`。

### 执行过的操作

- 修改文件：
  - `scripts/lade_llm_context_queries.py`
  - `tests/lade_llm_context_queries_test.py`
- 执行验证命令：
  - `./run_in_lade.sh python -m unittest tests/lade_llm_context_queries_test.py`
  - `./run_in_lade.sh python scripts/lade_llm_context_queries.py --run-id test-run --drone-ids drone-1 --step-index 100 --queries core --dry-run`
  - `./run_in_lade.sh python scripts/lade_llm_context_queries.py --run-id test-run --drone-ids drone-1 --step-index 100 --time-minute 600 --queries core --dry-run`
  - `./run_in_lade.sh python -m unittest tests/lade_llm_context_queries_test.py tests/lade_cypher_benchmark_test.py tests/concurrent_neo4j_query_test.py tests/timed_neo4j_query_test.py`
  - `./run_in_lade.sh python scripts/lade_llm_context_queries.py --help`

### 修改内容

- `scripts/lade_llm_context_queries.py`
  - 新增 `fetch_step_time_minute(driver, args)`，从 `Step` 节点读取当前 step 对应的真实 `time_minute`。
  - 新增 `resolve_time_args(driver, args)`，在真实查询前自动填充 `args.time_minute`。
  - `main()` 中创建 Neo4j driver 后先解析 step 时间，再执行 context 查询。
  - 新增 `queries_require_params()`，避免不需要时间参数的查询被强制解析 Step 时间。
  - `print_dry_run()` 在没有 `--time-minute` 时明确提示 dry-run 不能自动解析。
  - 将失败查询的 `elapsed_ms` 也改为按单条查询起止时间统计，避免失败时误记为整个 context 的累计耗时。
- `tests/lade_llm_context_queries_test.py`
  - 新增自动读取 step 时间的测试。
  - 新增 step 不存在时报错的测试。

### 后续建议

- 正常真实查询建议只传 `--run-id`、`--drone-ids` 和 `--step-index`。
- 如果脚本提示找不到 Step 节点，先确认该 run 是否真的生成了对应 `step_index`。
- 如果需要 dry-run 查询计划，可以额外传 `--time-minute`，因为 dry-run 不会连接 Neo4j。
