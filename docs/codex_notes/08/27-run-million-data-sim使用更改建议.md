### 任务背景

用户询问如果使用 `/data/zc/datasets/LaDe/viewer/run_million_data_sim.sh` 启动百万级 AgentState 仿真，还需要怎么更改。

### 关键结论

- 当前脚本已经删除了无效的 `--kg-record-agent-state` 和 `--kg-run-id` 参数，这是正确方向。
- 还需要把 `KG_BACKEND=neo4j`、`KG_RECORD_AGENT_STATE=1`、`KG_RUN_ID` 和 `NEO4J_*` 明确导出为环境变量。
- 建议把最后的 `python3 scripts/run_dynamic_sim.py` 改为 `./run_in_lade.sh python scripts/run_dynamic_sim.py`，保证使用项目指定的 `lade` 环境。
- 当前 `DRONE_COUNT=100`、`MAX_STEPS=10000` 理论最多生成 `1,000,000` 个 AgentState 节点。
- 但动态服务只有在 `/events` 被连接后才真正开始仿真；只运行 shell 脚本只是启动 HTTP 服务。
- 如果直接打开 `dynamic.html`，前端会发送页面默认参数，可能覆盖脚本里的大规模参数；要用脚本默认参数，可以请求不带 query 的 `/events`。

### 执行过的操作

- 读取脚本：`sed -n '1,180p' run_million_data_sim.sh`
- 查看动态服务参数：`./run_in_lade.sh python scripts/run_dynamic_sim.py --help`
- 搜索 AgentState 和 KG 环境变量实现：`rg -n "KG_RECORD_AGENT_STATE|record_agent_state|AgentState|KG_RUN_ID|KG_BACKEND" simulation/knowledge.py tests/knowledge_test.py -S`

### 修改内容

- 新增本说明文件：`docs/codex_notes/2026-08-27-run-million-data-sim使用更改建议.md`。
- 未修改 `run_million_data_sim.sh`。

### 后续建议

- 在脚本中新增并导出 Neo4j 相关环境变量。
- 使用唯一 `KG_RUN_ID` 避免重复运行覆盖同一个 run 的 AgentState。
- 用 `curl -N http://127.0.0.1:8010/events` 或前端手动设置大规模参数来触发真实仿真。
