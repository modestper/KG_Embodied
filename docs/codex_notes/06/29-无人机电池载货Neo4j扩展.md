### 任务背景

用户希望基于当前 `/data/zc/datasets/LaDe/viewer` 项目扩展无人机配送仿真环境，第一阶段实现电池续航、低电返航、depot 充电、最多同时携带 3 个货物、任务取货状态、Neo4j 写入扩展和动态页面展示；故障和接管暂不实现具体行为，只预留接口字段。local 与 Ray 两套后端需要同步支持。

### 关键结论

- `DroneAgent` 已扩展电池字段、`queue_capacity=6`、`cargo_capacity=3`、`onboard_tasks`、故障/接管预留字段。
- 接单前会检查完成任务并返回 depot 加安全余量所需电量；电量不足时不会接单。
- 低电且无载货的无人机会进入 `returning`，返回 region depot 或最近 depot，到达后立即充满并恢复 `idle`。
- 任务状态已支持 `waiting`、`assigned`、`picked_up`、`completed`，到 pickup 后记录 `pickup_time` 和 `onboard_drone_id`。
- local 与 Ray 后端都同步了新增字段、移动逻辑、取货/完成事件和 Step 概要记录。
- Neo4j 写入已扩展 Drone/Task 字段、新事件类型、`CARRYING` 关系和 `Step` 节点。
- 动态页面 Agent 表已增加载货和电量显示。

### 执行过的操作

- 读取并分析 `simulation/delivery_sim.py`、`simulation/ray_delivery_sim.py`、`simulation/knowledge.py`、`simulation/planner.py`、`dynamic.html`、`src/dynamic_app.mjs` 和相关测试。
- 新增设计文档：`docs/superpowers/specs/2026-06-29-drone-battery-cargo-kg-design.md`。
- 新增实现计划：`docs/superpowers/plans/2026-06-29-drone-battery-cargo-kg.md`。
- 先补失败测试，再实现功能，覆盖本地仿真、Ray、Neo4j 和页面数据字段。
- 运行验证命令：
  - `./run_in_lade.sh python -m unittest tests.delivery_sim_test tests.knowledge_test tests.ray_delivery_sim_test`
  - `./run_in_lade.sh python -m unittest tests.planner_test tests.dynamic_server_test`
  - `./run_in_lade.sh python -m unittest discover -s tests -p '*_test.py'`
  - `node --check src/dynamic_app.mjs`
  - `bash -n run_local_sim.sh run_ray_sim.sh run_in_lade.sh`

### 修改内容

- `simulation/delivery_sim.py`
  - 扩展 `DeliveryTask` 和 `DroneAgent` 数据结构。
  - 实现电池消耗、接单电量判断、低电返航、depot 即时充电。
  - 实现 `cargo_capacity`、`onboard_tasks`、取货状态和 earliest-deadline 送货顺序。
  - 增加 `step_once()`，统一记录取货、完成、低电、返航、充电和 Step 概要。
- `simulation/ray_delivery_sim.py`
  - 同步 Ray payload、snapshot、proxy 和 actor step 事件。
  - 保持 Ray 后端与 local 后端新增字段和行为一致。
- `simulation/knowledge.py`
  - 增加 `record_pickup()`、`record_drone_event()`、`record_step()`。
  - Neo4j 写入 Drone 电池/载货字段、Task 取货字段、`CARRYING` 关系和 `Step` 节点。
- `simulation/planner.py`
  - 调度成本计算接入新电池可行性判断。
- `dynamic.html`、`src/dynamic_app.mjs`
  - Agent 状态表增加载货和电量列。
- `tests/delivery_sim_test.py`、`tests/knowledge_test.py`、`tests/ray_delivery_sim_test.py`
  - 增加电池、充电、多货物、状态转换、Neo4j 和 Ray 字段同步测试。
- `README.md`
  - 补充仿真约束和 Neo4j 扩展记录说明。

### 后续建议

- 第二阶段可以在现有 `failure_mode`、`takeover_mode` 字段基础上实现故障和人工/LLM 接管。
- 可以继续增加 Neo4j 索引和清理脚本，尤其是 `Run`、`Step`、`Drone(run_id,id)`、`Task(run_id,id)`。
- 多货物送货顺序目前使用 deadline 最早优先，后续可替换为结合 deadline、距离和电量的局部路径规划器。
