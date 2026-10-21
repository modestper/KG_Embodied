### 任务背景

用户希望在当前 `/data/zc/datasets/LaDe/viewer` 项目中新增“每个 step 下每个 agent 状态节点写入 Neo4j 知识图谱”的能力。要求新增 `AgentState` 节点、关联到 `Run`、`Step`、`Drone`，默认关闭，通过 `KG_RECORD_AGENT_STATE=1` 开启；Neo4j 写入必须批量化，local 和 Ray 后端保持一致，并补充测试。

### 关键结论

- 已新增 `AgentState` 快照写入能力，默认关闭。
- 通过 `KG_RECORD_AGENT_STATE=1` 启用后，每个 `record_step()` 会为当前 step 的所有 drone 批量写入 `AgentState`。
- `AgentState.id` 格式为 `"{run_id}-{step_index}-{drone_id}"`。
- 每个 `AgentState` 包含 `id`、`run_id`、`step_index`、`time_minute`、`drone_id`、`region_id`、`x`、`y`、`status`、`battery_remaining_m`、`queue_len`、`onboard_count`、`total_distance_m`。
- Neo4j 写入使用单次 `UNWIND $agent_states AS state` 批量提交，不按 agent 单独提交。
- local 和 Ray 后端本身都已统一调用 `knowledge_store.record_step()`，因此新功能在两套后端行为一致。
- README 已补充 `AgentState` schema 和 `KG_RECORD_AGENT_STATE` 配置说明。

### 执行过的操作

- 读取指定文件：
  - `sed -n '1,560p' simulation/knowledge.py`
  - `sed -n '1,660p' simulation/delivery_sim.py`
  - `sed -n '1,560p' simulation/ray_delivery_sim.py`
  - `sed -n '1,360p' scripts/run_dynamic_sim.py`
- 修改并运行单元测试：
  - `./run_in_lade.sh python -m unittest tests.knowledge_test`
  - 先验证新增测试失败，再实现功能后验证通过。
- 运行相关测试：
  - `./run_in_lade.sh python -m unittest tests.knowledge_test tests.delivery_sim_test tests.ray_delivery_sim_test tests.dynamic_server_test`
- 运行全量验证：
  - `./run_in_lade.sh python -m unittest discover -s tests -p '*_test.py'`
  - `node --check src/dynamic_app.mjs`
  - `bash -n run_local_sim.sh run_ray_sim.sh run_in_lade.sh`
- 查看 diff 与工作区状态：
  - `git diff --stat`
  - `git status --short`

### 修改内容

- `simulation/knowledge.py`
  - `Neo4jKnowledgeStore.__init__()` 新增 `record_agent_state` 参数。
  - `record_step()` 在写入 `Step` 后，根据开关决定是否写入 `AgentState`。
  - 新增 `_write_agent_states()`，使用 `UNWIND $agent_states AS state` 批量写入所有 drone 状态。
  - 新增 `agent_state_summary()`，集中生成 `AgentState` 属性。
  - `build_knowledge_store_from_env()` 读取 `KG_RECORD_AGENT_STATE`，仅当值为 `"1"` 时开启。
- `tests/knowledge_test.py`
  - 新增默认关闭时不写入 `AgentState` 的测试。
  - 新增开启时批量写入 `AgentState` 的测试。
  - 扩展环境变量构建测试，验证 `KG_RECORD_AGENT_STATE=1` 会开启写入。
- `README.md`
  - Neo4j schema 增加 `AgentState`、`IN_STEP`、`OF_DRONE`。
  - 启动示例和参数表增加 `KG_RECORD_AGENT_STATE`。
  - 补充说明开启后每个 step 会批量写入每个 drone 的状态快照。

### 后续建议

- 如果后续面向百万级图数据查询，建议为 `AgentState(run_id, step_index)`、`AgentState(run_id, drone_id)` 增加索引或约束。
- 如果 `KG_RECORD_AGENT_STATE=1` 用于长时间大规模实验，需要评估写入量：`step_count * drone_count` 会快速增长。
- 如果需要前端展示历史轨迹，可直接基于 `AgentState` 按 `run_id + drone_id + step_index` 查询。
- 当前工作区有大量历史 dirty 文件和 docx 删除状态，本次未处理这些无关改动，后续提交前需要单独梳理。
