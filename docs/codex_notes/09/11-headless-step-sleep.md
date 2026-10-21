### 任务背景

用户希望让 headless 仿真也像网页端一样，在每个 step 后暂停一小段时间，以降低 Ray actor 同步和 Neo4j AgentState 写入的瞬时压力，验证 OOM 是否与全速推进有关。

### 关键结论

- 已为 `scripts/run_headless_sim.py` 增加每步后的 sleep 逻辑。
- 新增 `--wall-interval` 参数，默认读取 `WALL_INTERVAL` 环境变量；未设置时默认 `0.25` 秒，与网页端默认节流一致。
- 如果需要恢复全速 headless，可以传 `--wall-interval 0` 或设置 `WALL_INTERVAL=0`。
- sleep 发生在每次 `env.step_once()` 后，因此 10000 step 默认至少会增加约 41.7 分钟运行时间。
- 这会降低瞬时压力，但不会减少最终 Neo4j 写入总量。

### 执行过的操作

- 读取 `scripts/run_headless_sim.py`。
- 读取 `tests/run_headless_sim_test.py`。
- 修改 `scripts/run_headless_sim.py`。
- 修改 `tests/run_headless_sim_test.py`。
- 执行 `./run_in_lade.sh python -m unittest tests.run_headless_sim_test`。
- 执行 `./run_in_lade.sh python -m py_compile scripts/run_headless_sim.py tests/run_headless_sim_test.py`。
- 查看 `git status --short scripts/run_headless_sim.py tests/run_headless_sim_test.py docs/codex_notes`。

### 修改内容

- `scripts/run_headless_sim.py`
  - 新增 `env_float()`。
  - 新增 `sleep_after_step(args)`。
  - 在每次 `env.step_once()` 后调用 sleep。
  - 新增 CLI 参数 `--wall-interval`，默认 `WALL_INTERVAL` 或 `0.25`。
- `tests/run_headless_sim_test.py`
  - 给测试参数补充 `wall_interval` 默认值。
  - 新增测试确认设置 `wall_interval` 后每 step 都会调用 `time.sleep()`。
  - 扩展参数解析测试，确认默认 `wall_interval=0.25`。

### 后续建议

- 下一次 headless 大规模运行可先用默认 `0.25` 测试是否还触发 OOM。
- 如果仍然 OOM，再考虑降低 `DRONE_COUNT`、关闭部分 AgentState 写入、分批运行或检查 Neo4j 进程内存。
