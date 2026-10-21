### 任务背景

用户希望在原有 LaDe viewer 系统上扩展两类功能：一是在每个仿真 step 为每个 agent 写入 Neo4j 时序状态节点；二是在运行过程中让 agent 查询 Neo4j 数据库，用于后续多智能体协同、状态感知和查询性能测试。

### 关键结论

- 推荐整合进原系统，但必须默认关闭，通过环境变量或启动参数开启。
- Step 仍作为全局时间片汇总节点，AgentState 作为每个 step、每个 agent 的状态快照节点。
- 第一版不要让每个 agent 每个 step 都查 Neo4j，应先做事件触发或固定间隔查询，避免数据库 I/O 拖慢仿真。
- Neo4j 查询结果第一阶段只作为 observation/context 记录，不直接改变调度逻辑；第二阶段再接入 planner 或 coordinator。
- 写入和查询都应进入 `simulation/knowledge.py`，仿真环境只调用抽象接口，避免业务逻辑直接写 Cypher。
- 必须给 `AgentState(run_id, step_index)`、`AgentState(run_id, drone_id)`、`AgentState(run_id, drone_id, step_index)`、`Event(run_id, drone_id, time_minute)` 建索引。

### 执行过的操作

- 本次未读取或修改业务代码。
- 基于当前项目既有结构进行方案设计：`simulation/knowledge.py`、`simulation/delivery_sim.py`、`simulation/ray_delivery_sim.py`、`scripts/run_dynamic_sim.py`。

### 修改内容

- 新增本设计说明文件：`docs/codex_notes/2026-08-27-step-agentstate-neo4j查询方案.md`。
- 未修改项目代码。

### 后续建议

- 第一阶段只实现可开关的 AgentState 写入和只读查询接口。
- 第二阶段再把查询结果接入 agent 决策，例如任务转交、低电协同、区域负载均衡。
- 第三阶段再做 100 agent 并发查询压测和百万级 AgentState 查询优化。
