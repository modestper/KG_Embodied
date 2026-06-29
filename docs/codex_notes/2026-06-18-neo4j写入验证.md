### 任务背景

用户要求基于当前 Neo4j 接入实现，执行小规模仿真写入，并查询 Neo4j 验证节点和关系是否真实落库。

### 关键结论

- 已使用 `lade` 环境执行本地后端仿真写入验证。
- 本次测试 run_id 为 `jilin-0925-test001`。
- 直接调用后端仿真逻辑后，Neo4j 中产生了 `Run / Region / Drone / Task / Event` 节点。
- 查询结果显示：`runs=1`、`regions=1`、`drones=1`、`tasks=3`、`events=6`。
- 关系查询显示：`ASSIGNED=3`、`COMPLETED=3`、`IN_RUN=11`。
- 最终验证查询显示：当前 run 下有 `nodes_in_run=11`、`events=6`。
- `conda run -n lade python -m unittest tests.knowledge_test` 通过。

### 执行过的操作

- 启动过一次本地仿真 HTTP 服务：

```bash
KG_BACKEND=neo4j KG_RUN_ID=jilin-0925-test001 NEO4J_URI=bolt://127.0.0.1:17687 NEO4J_USER=neo4j NEO4J_PASSWORD=neo4j123456 TASK_LIMIT=5 DRONE_COUNT=2 MAX_STEPS=20 PORT=8011 WALL_INTERVAL=0 conda run -n lade ./run_local_sim.sh
```

- 请求 `/events` 时 HTTP 服务未在 20 秒内返回内容，因此改为直接调用后端 runtime 和 stream 逻辑。
- 执行直接后端仿真：

```bash
KG_BACKEND=neo4j KG_RUN_ID=jilin-0925-test001 NEO4J_URI=bolt://127.0.0.1:17687 NEO4J_USER=neo4j NEO4J_PASSWORD=neo4j123456 conda run -n lade python -c "..."
```

- 查询 Neo4j 节点和关系数量。
- 停止了临时启动的 `8011` HTTP 服务。
- 运行最终验证：

```bash
conda run -n lade python -m unittest tests.knowledge_test
```

### 修改内容

- 新增本文档：`docs/codex_notes/2026-06-18-neo4j写入验证.md`。
- 未修改项目运行代码。
- Neo4j 数据库中新增了测试 run `jilin-0925-test001` 对应的图谱数据。

### 后续建议

- 如果希望避免测试数据留在 Neo4j，可按 `run_id='jilin-0925-test001'` 删除该 run 相关节点和关系。
- 下一步可以把 Neo4j 启动参数整理成 `run_local_neo4j_sim.sh`，便于之后一条命令运行带知识图谱写入的仿真。
- 当前 HTTP `/events` 路径曾出现超时，后续如果要通过浏览器触发仿真，应单独排查服务响应路径。
