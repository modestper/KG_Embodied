### 任务背景

用户询问上次按 step 为每个 agent 写入 Neo4j 节点后，为什么生成的节点数只有 `449200`，没有达到预期的百万级。

### 关键结论

- `449200` 对应的是目标 run 的 `AgentState` 节点数。
- Neo4j 查询确认：`AgentState=449200`、`drone_count=100`、`step_count=4492`。
- 因此数量来源是：`100 agents × 4492 steps = 449200 AgentState`。
- 脚本里 `MAX_STEPS=10000` 只是最大上限，不代表一定会跑满。
- 当前最后一个 `Step` 是 `step_index=4492`，说明这次实际只运行到第 4492 step。
- 最后一个 Step 仍有未完成任务，任务表也显示还有未完成任务，所以这次不是“任务全部完成后正常提前停止”，更可能是 `/events` 连接、服务进程、curl、浏览器连接或 Neo4j 写入过程在中途停止。
- 该 run 的全部节点不止 `449200`，标签分布约为：`AgentState=449200`、`Event=39434`、`Task=7513`、`Step=4492`、`Drone=100`、`Region=53`，合计约 `500792` 个 run 内节点。

### 执行过的操作

- 读取大规模启动脚本：`sed -n '1,180p' run_million_data_sim.sh`
- 搜索仿真停止和 AgentState 写入相关逻辑：`rg -n "is_complete\\(|max_steps|record_step|AgentState|record_agent_state" simulation scripts tests -S`
- 阅读本地仿真 step 逻辑：`sed -n '300,370p' simulation/delivery_sim.py`
- 阅读动态 SSE 运行逻辑：`sed -n '95,145p' scripts/run_dynamic_sim.py`
- 查询目标 run 的 AgentState 统计：
  `MATCH (a:AgentState {run_id: $run_id}) RETURN count(a), count(DISTINCT a.drone_id), count(DISTINCT a.step_index), min(a.step_index), max(a.step_index)`
- 查询目标 run 的 Step 统计：
  `MATCH (s:Step {run_id: $run_id}) RETURN count(s), min(s.step_index), max(s.step_index)`
- 查询目标 run 的最后一个 Step：
  `MATCH (s:Step {run_id: $run_id}) RETURN ... ORDER BY s.step_index DESC LIMIT 1`
- 查询目标 run 的 Task 完成情况：
  `MATCH (t:Task {run_id: $run_id}) RETURN count(t), completed, unfinished`
- 查询目标 run 的标签分布：
  `MATCH (n)-[:IN_RUN]->(:Run {id: $run_id}) RETURN labels(n), count(n)`

### 修改内容

- 新增本总结文件：`docs/codex_notes/2026-08-31-AgentState未达百万原因.md`。
- 未修改项目代码或 Neo4j 数据库。

### 后续建议

- 如果目标是单个 run 达到 100 万 `AgentState`，必须让该 run 实际跑满至少 `10000` 个 step。
- 用 `curl -N http://127.0.0.1:8010/events > /tmp/million_run_events.log` 触发时不要中途关闭终端、停止 curl 或刷新页面。
- 建议设置 `WALL_INTERVAL=0`、`FRAME_STRIDE=100` 降低 SSE 推送和等待开销，但 AgentState 仍会每 step 写入。
- 如果重复运行同一个 `KG_RUN_ID`，`AgentState` 使用 `MERGE (a:AgentState {id: state.id})`，相同 step/drone 的节点会被覆盖，不会重复新增；要么用新的 `KG_RUN_ID`，要么确保同一个 run 能跑到更大的 step。
- 运行期间可用查询实时监控：
  `MATCH (a:AgentState {run_id: $run_id}) RETURN count(a), count(DISTINCT a.step_index), max(a.step_index)`。
