# HANDOFF

本文档写给完全没有上下文的新会话。当前项目路径：

```text
/data/zc/datasets/LaDe/viewer
```

## 1. 我们在做什么任务

当前主线任务是扩展 LaDe viewer 的无人机配送仿真环境，把原来较简单的“无人机按队列取货、送货”升级成更接近实验需求的多 agent 仿真：

- 加入电池续航、低电返航、depot 即时充电。
- 区分 `queue_capacity=6` 和 `cargo_capacity=3`。
- 支持最多同时携带 3 个已取货未送达任务。
- 任务状态从原来的 `waiting / assigned / completed` 扩展为 `waiting / assigned / picked_up / completed`。
- local 后端和 Ray 后端必须保持同一套行为。
- Neo4j 知识图谱写入扩展 Drone/Task/Event/Step 字段和关系。
- 动态页面 Agent 表展示电量和载货状态。
- 故障和接管是第二阶段，不实现行为，但字段接口已预留。

用户明确要求：所有 Python 命令优先通过：

```bash
./run_in_lade.sh python ...
```

## 2. 已经完成了什么

### 核心仿真

主要文件：

- `simulation/delivery_sim.py`
- `simulation/planner.py`

已完成：

- `DeliveryTask` 新增：
  - `pickup_time`
  - `onboard_drone_id`
- `DroneAgent` 新增：
  - `queue_capacity`
  - `cargo_capacity`
  - `onboard_tasks`
  - `battery_capacity_m`
  - `battery_remaining_m`
  - `low_battery_threshold_m`
  - `safety_buffer_m`
  - `home_depot`
  - `depots`
  - `target_depot`
  - `failure_mode`
  - `takeover_mode`
- `capacity` 保留兼容，但现在作为 `queue_capacity` 的别名使用。
- 默认电池参数：
  - `battery_capacity_m = 60_000`
  - `battery_remaining_m = 60_000`
  - `low_battery_threshold_m = 12_000`
  - `safety_buffer_m = 5_000`
- 接单前会检查：

```text
当前位置到 pickup
+ pickup 到 dropoff
+ dropoff 到最近 depot
+ safety_buffer_m
```

- 电量不足则不能接单。
- 没有未完成任务且达到动态低电阈值时进入 `returning`，返回当前位置最近的城市 depot。
- 动态返航阈值为 `max(12_000, distance(current_position, nearest_depot) + 5_000)`。
- 到达 depot 后立即充满并回到 `idle`。
- 每飞行 1 米消耗 1 米 `battery_remaining_m`。
- `battery_consumed_m` 是属性，计算为 `battery_capacity_m - battery_remaining_m`。
- 取货后任务状态变为 `picked_up`。
- 送达后任务状态变为 `completed`，并从 `queue` 和 `onboard_tasks` 移除。
- 多个在手货物按 `deadline_minute` 最早优先配送。

### Ray 后端

主要文件：

- `simulation/ray_delivery_sim.py`

已完成：

- Ray actor、payload、snapshot、proxy 都同步了新增字段。
- actor step 返回新增事件，主进程再写入知识图谱。
- Ray frame 中也包含：
  - `queue_capacity`
  - `cargo_capacity`
  - `onboard`
  - `onboard_count`
  - `battery_capacity_m`
  - `battery_remaining_m`
  - `battery_consumed_m`
  - `low_battery_threshold_m`
  - `safety_buffer_m`
  - `failure_mode`
  - `takeover_mode`

### Neo4j / KG

主要文件：

- `simulation/knowledge.py`

已完成：

- 保留已有 `Run` 隔离逻辑，通过 `KG_RUN_ID` 隔离实验 run。
- `TemporalKnowledgeStore` 新增：
  - `record_pickup()`
  - `record_drone_event()`
  - `record_step()`
  - `steps`
- `Neo4jKnowledgeStore` 新增写入：
  - Drone 电池字段、载货字段。
  - Task 的 `picked_up`、`pickup_time`、`onboard_drone_id`。
  - 新事件：
    - `task_picked_up`
    - `drone_low_battery`
    - `drone_returning_to_charge`
    - `drone_charged`
  - `(Drone)-[:CARRYING]->(Task)` 关系。
  - 完成任务时把 `CARRYING.active` 置为 `false`。
  - 每轮仿真写入 `Step` 节点，并通过 `(Step)-[:IN_RUN]->(Run)` 挂到当前 run。

Step 节点表示每一轮仿真 tick 的整体摘要，不是任务事件。它记录：

- `step_index`
- `time_minute`
- `completed_tasks`
- `unfinished_tasks`
- `idle_drones`
- `delivering_drones`
- `returning_drones`
- `charging_drones`
- `low_battery_drones`
- `total_distance_m`

### 前端页面

主要文件：

- `dynamic.html`
- `src/dynamic_app.mjs`

已完成：

- Agent 状态表新增：
  - `载货`，格式如 `2/3`
  - `电量`，格式如 `48.2 / 60 km`

### 文档

已新增或更新：

- `README.md`
- `docs/superpowers/specs/2026-06-29-drone-battery-cargo-kg-design.md`
- `docs/superpowers/plans/2026-06-29-drone-battery-cargo-kg.md`
- `docs/codex_notes/2026-06-29-无人机电池载货Neo4j扩展.md`

## 3. 当前卡在哪

没有明确代码 blocker。

上次完成后验证通过：

```bash
./run_in_lade.sh python -m unittest discover -s tests -p '*_test.py'
node --check src/dynamic_app.mjs
bash -n run_local_sim.sh run_ray_sim.sh run_in_lade.sh
```

当时测试结果：

```text
Ran 73 tests
OK
```

但当前工作区仍是 dirty 状态，且不只包含仿真扩展相关文件。新会话接手后第一件事应该是看：

```bash
git status --short
```

当前已知 dirty 文件包括：

- `README.md`
- `dynamic.html`
- `simulation/delivery_sim.py`
- `simulation/knowledge.py`
- `simulation/planner.py`
- `simulation/ray_delivery_sim.py`
- `src/dynamic_app.mjs`
- `tests/delivery_sim_test.py`
- `tests/knowledge_test.py`
- `tests/ray_delivery_sim_test.py`
- `bash/time_neo4j_query.sh`
- `data/neo4j_query_logs.jsonl`
- `codex_history.py`
- 多个 `docs/codex_notes/` 文件
- `docs/superpowers/plans/2026-06-29-drone-battery-cargo-kg.md`
- `docs/superpowers/specs/`

其中 `bash/time_neo4j_query.sh`、`data/neo4j_query_logs.jsonl`、`codex_history.py` 看起来不是本轮电池/载货实现的核心文件，不要随手回滚或覆盖，先确认来源。

## 4. 下一步计划

建议按这个顺序接手：

1. 重新跑验证：

```bash
./run_in_lade.sh python -m unittest discover -s tests -p '*_test.py'
node --check src/dynamic_app.mjs
bash -n run_local_sim.sh run_ray_sim.sh run_in_lade.sh
```

2. 本地启动页面做人工验收：

```bash
./run_local_sim.sh
```

打开：

```text
http://127.0.0.1:8010/dynamic.html
```

重点看 Agent 表是否显示 `载货` 和 `电量`，任务是否出现 `picked_up` 阶段。

3. 如果本机 Neo4j 已启动，做 Neo4j 写入验收：

```bash
KG_BACKEND=neo4j \
KG_RUN_ID=handoff-check-001 \
NEO4J_URI=bolt://127.0.0.1:7687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD=your-password \
./run_local_sim.sh
```

检查 Neo4j 中是否有：

- `Run`
- `Step`
- `Drone`
- `Task`
- `Event`
- `CARRYING`
- `IN_RUN`

4. 若要进入第二阶段，优先实现：

- 故障状态真正触发：`failed`
- 接管逻辑：`takeover_mode`
- 其他 drone 对故障 drone 的任务重新接管
- Neo4j 记录故障和接管事件
- 前端可视化故障和接管状态

5. 后续增强：

- Neo4j 约束/索引初始化。
- 按 `KG_RUN_ID` 清理某次实验的脚本。
- 多货物路径规划从 earliest deadline first 升级为 deadline + 距离 + 电量联合优化。

## 5. 绝对不要再踩的坑

### 不要直接用 `python`

项目要求 Python 命令优先走：

```bash
./run_in_lade.sh python ...
```

不要随手用系统 Python，否则可能环境不一致。

### 不要把 `battery_range_m` 当新电池逻辑

`battery_range_m` 只保留兼容。实际逻辑使用：

- `battery_capacity_m`
- `battery_remaining_m`
- `low_battery_threshold_m`
- `safety_buffer_m`

### 不要把 `capacity` 当载货容量

现在容量分两种：

- `queue_capacity`：最多接受多少个未完成任务，默认 6。
- `cargo_capacity`：最多同时携带多少个货物，默认 3。

`capacity` 只是 `queue_capacity` 的兼容别名。

### 不要只改 local，不改 Ray

动态服务支持 local 和 Ray。任何字段、状态、事件、snapshot 改动都要同步：

- `simulation/delivery_sim.py`
- `simulation/ray_delivery_sim.py`
- 对应测试

Ray 特别容易漏：

- `task_to_payload()`
- `drone_to_payload()`
- `DroneStateProxy`
- `_DroneActor.step()`
- `drone_snapshot()`
- `proxy_from_snapshot()`
- `RaySimulationEnv.snapshot()`

### Ray proxy 的 `assign()` 必须返回 `True`

之前踩过坑：Ray proxy `assign()` 没返回值，导致 `AuctionCoordinator.assign()` 以为没有成功分配，actor 收不到任务。

### `queue` 和 `onboard_tasks` 不要混淆

- `queue`：已接受但未完成任务。
- `onboard_tasks`：已取货但未送达任务，是 `queue` 的子集。

完成任务时必须同时从两边清理。

### 不要回到旧状态表达

旧逻辑是 `status=assigned` 加 `picked_up=True` 暗示配送中。现在必须明确：

```text
waiting -> assigned -> picked_up -> completed
```

### 空载低电直接返航，载货低电先互助

空载且没有待执行任务时按动态阈值直接返航：

```text
battery_remaining_m <=
max(low_battery_threshold_m, distance(current_position, nearest_depot) + safety_buffer_m)
```

载货中的 drone 如果电量不足以完成在手货物并安全到达最近 depot，应先触发互助交接，再返回最近 depot，不得直接丢弃货物。

### `charging` 是瞬时状态

第一版充电模型是“到 depot 后立即充满”，所以页面上通常不会稳定看到 `charging`。这不是 bug。

### Step 和 Event 不要混用

- `Event`：具体事件，例如取货、完成、低电、充电。
- `Step`：每一轮仿真的整体统计摘要。

不要把每个事件都写成 Step，也不要用 Step 替代事件。

### 不要破坏 `KG_RUN_ID` 隔离

Neo4j 查询和写入必须按 `run_id` 隔离。不要改回只按 `Drone.id`、`Task.id` 合并，否则不同实验会混在一起。

### 不要随手回滚 dirty worktree

当前工作区有多类未提交改动，其中可能有用户或其他会话的产物。不要用：

```bash
git reset --hard
git checkout -- .
```

除非用户明确要求。

## 6. 关键文件速查

- 核心本地仿真：`simulation/delivery_sim.py`
- Ray 仿真：`simulation/ray_delivery_sim.py`
- 调度成本：`simulation/planner.py`
- 知识图谱：`simulation/knowledge.py`
- 动态服务：`scripts/run_dynamic_sim.py`
- 前端页面：`dynamic.html`
- 前端逻辑：`src/dynamic_app.mjs`
- 本地/Ray/KG 测试：
  - `tests/delivery_sim_test.py`
  - `tests/ray_delivery_sim_test.py`
  - `tests/knowledge_test.py`
  - `tests/planner_test.py`
  - `tests/dynamic_server_test.py`

## 7. 推荐交接后的第一组命令

```bash
cd /data/zc/datasets/LaDe/viewer
git status --short
./run_in_lade.sh python -m unittest discover -s tests -p '*_test.py'
node --check src/dynamic_app.mjs
bash -n run_local_sim.sh run_ray_sim.sh run_in_lade.sh
```
