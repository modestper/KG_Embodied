### 任务背景

用户想了解当前代码是如何使用本机 Neo4j 的，重点是连接方式、启用条件、写入内容和调用链。

### 关键结论

- 默认不会连接 Neo4j；默认知识图谱后端是 `memory`。
- 只有设置 `KG_BACKEND=neo4j` 或 `KG_BACKEND=graph` 时，`build_knowledge_store_from_env()` 才会创建 `Neo4jKnowledgeStore`。
- Neo4j 默认连接地址是 `bolt://127.0.0.1:7687`，用户名默认 `neo4j`，密码默认空字符串。
- 代码通过 Neo4j Python driver 的 `GraphDatabase.driver(uri, auth=(user, password))` 连接本机 Bolt 服务。
- 仿真运行时会在任务派单和任务完成两个时刻写入 Neo4j。
- 写入的节点包括 `Run`、`Region`、`Drone`、`Task`、`Event`，关系包括 `IN_RUN`、`HOME_REGION`、`CURRENTLY_IN`、`BELONGS_TO`、`ABOUT_DRONE`、`ABOUT_TASK`、`IN_REGION`、`ASSIGNED`、`COMPLETED`。
- 每次 Neo4j 后端运行都有 `run_id`：设置 `KG_RUN_ID` 则使用该值，否则自动生成 `run-<uuid>`，查询也按当前 `run_id` 过滤。
- 既有记录显示本机环境曾用过 `bolt://127.0.0.1:17687`、用户 `neo4j`、密码 `neo4j123456`，但当前代码默认仍是 `7687`，需要通过环境变量覆盖到 `17687`。

### 执行过的操作

- 读取 `simulation/knowledge.py`，确认 Neo4j 后端的连接、写入和查询逻辑。
- 读取 `simulation/delivery_sim.py`，确认本地仿真在派单和完成时调用知识图谱。
- 读取 `simulation/ray_delivery_sim.py`，确认 Ray 仿真同样在派单和完成时调用知识图谱。
- 读取 `scripts/run_dynamic_sim.py`，确认动态服务构建 runtime 时调用 `build_knowledge_store_from_env()` 并注入仿真环境。
- 读取 `simulation/planner.py`，确认 LLM coordinator 会通过 `knowledge_store.query_region_context()` 获取近期图谱上下文。
- 读取 `README.md`，确认 Neo4j 启用方式和文档化 schema。
- 读取 `run_local_sim.sh`、`run_ray_sim.sh`、`run_in_lade.sh`，确认启动脚本本身不设置 Neo4j 默认值。
- 读取 `docs/codex_notes/2026-06-18-neo4j-lade环境.md`，确认历史本机 Neo4j 端口和账号记录。

### 修改内容

- 新增本文档：`docs/codex_notes/2026-06-29-neo4j本机使用方式.md`。
- 未修改业务代码、测试代码或启动脚本。

### 后续建议

- 如果要使用历史本机 Neo4j 实例，运行时显式设置：

```bash
KG_BACKEND=neo4j \
KG_RUN_ID=jilin-0925-local001 \
NEO4J_URI=bolt://127.0.0.1:17687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD=neo4j123456 \
./run_in_lade.sh ./run_local_sim.sh
```

```bash
KG_BACKEND=neo4j \
KG_RUN_ID=jilin-0925-local001 \
NEO4J_URI=bolt://127.0.0.1:17687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD=neo4j123456 \
./run_in_lade.sh ./run_ray_sim.sh
```



- 如果本机 Neo4j 使用标准 Bolt 端口，则可保留默认 `NEO4J_URI=bolt://127.0.0.1:7687`。
- 若要避免不同实验混在一起，应始终设置有意义的 `KG_RUN_ID`。
