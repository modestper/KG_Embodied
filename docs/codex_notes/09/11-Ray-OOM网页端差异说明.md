### 任务背景

用户想理解为什么之前使用非网页端 headless 仿真时 Ray 报 `OutOfMemoryError`，但通过网页端运行时没有同样报错，并且可以持续生成较多数据。

### 关键结论

- 报错本质是 Ray 节点内存保护触发：节点内存使用达到 `245.95GB / 251.65GB`，超过默认阈值 `0.95`。
- 非网页端 `scripts/run_headless_sim.py` 默认参数明显偏大：`task_limit=10424`、`drone_count=100`、`backend=ray`、`kg_backend=neo4j`、`record_agent_state=True`。
- 网页端启动脚本默认参数较小：`TASK_LIMIT=20`、`DRONE_COUNT=3`、`MAX_STEPS=1000`，`run_local_sim.sh` 还会强制使用 `--backend local`。
- headless 模式虽然禁用了 frame 留存，但每一步仍会调用 Ray actor 同步，并在开启 Neo4j AgentState 时写入 `steps * drones` 级别的状态节点。
- 网页端如果使用默认参数或 local 后端，Ray actor 数量、每步同步数量、Neo4j 写入压力都会低很多，因此不容易触发 Ray OOM。
- 如果网页端也切到 Ray、任务数/无人机数调到 headless 同等级，并开启 Neo4j AgentState，仍然可能触发相同问题。

### 执行过的操作

- 读取 `scripts/run_headless_sim.py`，确认 headless 默认参数和 frame 留存禁用逻辑。
- 读取 `simulation/ray_delivery_sim.py`，确认 Ray actor 创建、每步 `_sync_drones()`、`ray.get()` 和 `record_step()` 调用路径。
- 读取 `scripts/run_dynamic_sim.py`，确认网页端动态服务默认参数、SSE 事件流、请求参数上限和 Ray 初始化逻辑。
- 读取 `run_local_sim.sh`、`run_ray_sim.sh`，确认网页端常用启动脚本默认任务数、无人机数和后端选择。
- 读取 `simulation/knowledge.py`，确认 `KG_RECORD_AGENT_STATE=1` 时每步按所有 drone 写入 `AgentState`。

### 修改内容

- 未修改业务代码。
- 新增本说明文件：`docs/codex_notes/2026-09-11-Ray-OOM网页端差异说明.md`。

### 后续建议

- 复现对比时先让两端参数完全一致，尤其是 `backend`、`task_limit`、`drone_count`、`max_steps`、`KG_BACKEND` 和 `KG_RECORD_AGENT_STATE`。
- 大规模生成 Neo4j AgentState 时，优先降低 `DRONE_COUNT` 或关闭 `--record-agent-state` 做基线测试。
- 如果必须使用 Ray 大规模跑，建议限制并行度、分批运行、监控 `free -h` / `ray status` / `ray logs raylet.out`。
- 不建议单纯提高 `RAY_memory_usage_threshold` 作为主要方案，它只能推迟 worker 被杀，不能减少实际内存压力。
