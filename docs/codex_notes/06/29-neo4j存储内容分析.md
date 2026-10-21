### 任务背景

用户希望仔细分析当前代码会把哪些内容写入 Neo4j，包括节点、关系、属性、写入时机，以及哪些内容不会写入。

### 关键结论

- Neo4j 只有在 `KG_BACKEND=neo4j` 或 `KG_BACKEND=graph` 时启用；否则只使用内存事件列表。
- 写入入口集中在 `simulation/knowledge.py` 的 `Neo4jKnowledgeStore._write_event()`。
- 当前只有两个业务时机会写 Neo4j：任务派单 `task_assigned` 和任务完成 `task_completed`。
- 写入节点包括 `Run`、`Region`、`Drone`、`Task`、`Event`。
- 写入关系包括 `IN_RUN`、`HOME_REGION`、`CURRENTLY_IN`、`BELONGS_TO`、`ABOUT_DRONE`、`ABOUT_TASK`、`IN_REGION`、`ASSIGNED`、`COMPLETED`。
- `Run`、`Region`、`Drone`、`Task` 使用 `MERGE`，同一个 `run_id` 下重复运行会复用并更新节点。
- `Event` 使用随机 UUID 组成的 id，每次派单/完成都会新增事件。
- LLM 调用记录写到 `data/llm_logs/*.jsonl`，不会写入 Neo4j。

### 执行过的操作

- 读取 `simulation/knowledge.py`，确认 Neo4j 连接、节点属性、关系和查询逻辑。
- 读取 `simulation/delivery_sim.py`，确认 local 仿真在派单和完成时调用 KG 写入。
- 读取 `simulation/ray_delivery_sim.py`，确认 Ray 仿真在派单和完成时调用 KG 写入。
- 读取 `scripts/run_dynamic_sim.py`，确认动态服务构建 runtime 时注入 `knowledge_store`。

### 修改内容

- 新增本文档：`docs/codex_notes/2026-06-29-neo4j存储内容分析.md`。
- 未修改业务代码。

### 后续建议

- 如果希望 LLM 调用也进入 Neo4j，可新增 `LLMCall` 或 `Decision` 节点，并关联到 `Run`、`Region`、`Task`、`Drone`。
- 如果希望避免同一 `KG_RUN_ID` 下重复实验互相覆盖，应每次实验使用唯一 `KG_RUN_ID`。
- 如果希望按实验参数追踪结果，可扩展 `Run` 节点属性，例如 city、day、task_limit、drone_count、backend、planner。

## 详细存储内容

### 启用条件

`build_knowledge_store_from_env()` 会读取：

```text
KG_BACKEND
NEO4J_URI
NEO4J_USER
NEO4J_PASSWORD
KG_RUN_ID
```

只有 `KG_BACKEND=neo4j` 或 `KG_BACKEND=graph` 时才创建 `Neo4jKnowledgeStore`。

### 写入时机

local 仿真：

- `SimulationEnv.release_and_assign()` 调用 `record_assignment()`。
- `SimulationEnv.run()` / 动态 step 调用 `record_completion()`。

Ray 仿真：

- `RaySimulationEnv.release_and_assign()` 调用 `record_assignment()`。
- `RaySimulationEnv.step_once()` 调用 `record_completion()`。

### 节点

`Run`：

```text
id
```

`Region`：

```text
id
run_id
city
depot_x
depot_y
```

`Drone`：

```text
id
run_id
region_id
capacity
speed_m_per_minute
battery_range_m
status
x
y
queue_len
completed_count
total_distance_m
updated_at
```

`Task`：

```text
id
run_id
city
region_id
release_minute
deadline_minute
status
assigned_to
completed_at
late
pickup_x
pickup_y
dropoff_x
dropoff_y
aoi_id
```

`Event`：

```text
id
run_id
type
time_minute
time
region_id
task_id
drone_id
queue_len   # 派单事件
late        # 完成事件
```

### 关系

基础关系：

```text
(Drone)-[:HOME_REGION]->(Region)
(Drone)-[:CURRENTLY_IN]->(Region)
(Task)-[:BELONGS_TO]->(Region)
(Event)-[:ABOUT_DRONE]->(Drone)
(Event)-[:ABOUT_TASK]->(Task)
(Event)-[:IN_REGION]->(Region)
(Region)-[:IN_RUN]->(Run)
(Drone)-[:IN_RUN]->(Run)
(Task)-[:IN_RUN]->(Run)
(Event)-[:IN_RUN]->(Run)
```

派单事件额外关系：

```text
(Drone)-[:ASSIGNED {time_minute, time}]->(Task)
```

完成事件额外关系：

```text
(Drone)-[:COMPLETED {time_minute, time, late}]->(Task)
```

### 不会写入 Neo4j 的内容

- 每一帧完整画面 `frames` 不写入 Neo4j。
- 前端边界控件设置不写入 Neo4j。
- LLM 请求、响应、决策目前不写入 Neo4j，只记录到 JSONL 日志。
- Ray actor 状态本身不写入 Neo4j，只通过任务派单/完成时的 `Drone` 快照属性体现。
- 地图区域边界 polygon 不写入 Neo4j；Neo4j 中的 `Region` 只有区域 id、city 和 depot 坐标等简要属性。
