### 任务背景

用户希望把现有 Neo4j 知识图谱接入升级到“第二点”：为每次仿真 run 做隔离，避免不同实验运行的数据写到同一批图谱节点和查询结果里后发生混淆。

### 关键结论

- 已为 `Neo4jKnowledgeStore` 增加 `run_id` 支持。
- 如果设置 `KG_RUN_ID`，Neo4j 后端使用该值作为本次 run；否则自动生成 `run-<uuid>`。
- Neo4j 会创建 `Run` 节点，并把 `Region`、`Drone`、`Task`、`Event` 通过 `IN_RUN` 关系挂到对应 run 下。
- `Region`、`Drone`、`Task` 节点合并时也包含 `run_id`，避免不同 run 中同名实体互相覆盖属性。
- `recent_agent_performance()` 和 `recent_events()` 已按当前 `run_id` 过滤，只返回本次 run 的历史事件。
- 内存后端 `TemporalKnowledgeStore` 行为保持不变。

### 执行过的操作

- 读取并分析 `simulation/knowledge.py`、`tests/knowledge_test.py`、`README.md`。
- 新增实现计划：`docs/superpowers/plans/2026-06-18-neo4j-run-isolation.md`。
- 先修改 `tests/knowledge_test.py`，增加 run 隔离行为测试。
- 运行 `python3 -m unittest tests.knowledge_test`，确认测试因缺少 `run_id` 支持失败。
- 修改 `simulation/knowledge.py`，实现 Neo4j run 隔离。
- 更新 `README.md`，补充 `KG_RUN_ID`、`Run` 节点和 `IN_RUN` 关系说明。
- 运行验证命令：
  - `python3 -m unittest tests.knowledge_test`
  - `python3 -m unittest discover -s tests -p '*_test.py'`
  - `bash -n run_local_sim.sh run_ray_sim.sh`
  - `node --check src/dynamic_app.mjs`

### 修改内容

- `simulation/knowledge.py`
  - `Neo4jKnowledgeStore.__init__()` 新增 `run_id` 参数。
  - 写 Neo4j 事件时创建或合并 `Run` 节点。
  - `Region`、`Drone`、`Task`、`Event` 均记录 `run_id` 并连接到 `Run`。
  - `ASSIGNED` 和 `COMPLETED` 关系匹配时限定 `run_id`。
  - Neo4j 查询近期表现和近期事件时限定当前 `run_id`。
  - `build_knowledge_store_from_env()` 新增读取 `KG_RUN_ID`。
- `tests/knowledge_test.py`
  - 增加对 `Run` 节点、`IN_RUN` 关系、查询参数 `run_id`、环境变量 `KG_RUN_ID` 的测试。
- `README.md`
  - 更新 Neo4j 启动示例、schema 和常用参数表。

### 后续建议

- 后续可以继续增加 Neo4j 约束和索引初始化，例如 `(Run.id)`、`(Task.run_id, Task.id)`、`(Drone.run_id, Drone.id)`。
- 如果需要长期实验管理，建议再增加按 `KG_RUN_ID` 清理某次 run 的脚本。
- 如果前端要展示图谱，可基于 `Run` 节点作为入口，只展示当前 run 的图谱子图。
