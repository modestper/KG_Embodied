### 任务背景

用户要求检查 `/data/zc/datasets/LaDe/viewer/run_million_data_sim.sh` 是否写得正确，尤其是否适合启动百万级数据仿真与 AgentState 写入。

### 关键结论

- `bash -n run_million_data_sim.sh` 通过，脚本语法没有问题。
- 脚本整体结构和 `run_ray_sim.sh` 一致，会启动 `scripts/run_dynamic_sim.py --backend ray`。
- 当前脚本第 38 行传入 `--kg-record-agent-state 1`，但 `scripts/run_dynamic_sim.py` 目前没有这个命令行参数，因此直接运行会报 `unrecognized arguments`。
- 当前脚本没有设置 `KG_BACKEND=neo4j`，因此默认仍使用内存知识图谱，不会写入 Neo4j。
- 当前脚本没有设置 `KG_RUN_ID`，如果启用 Neo4j 后未设置该变量，会自动生成随机 run id，不利于复现实验。
- 当前 `run_million_data_sim.sh` 没有可执行权限，直接 `./run_million_data_sim.sh` 会失败；可以用 `bash run_million_data_sim.sh` 或 `chmod +x`。
- 当前项目里尚未实现 `KG_RECORD_AGENT_STATE`/`AgentState` 功能，所以即使修正启动参数，也还不会生成每 step 每 agent 的节点。

### 执行过的操作

- 读取脚本：`sed -n '1,240p' run_million_data_sim.sh`
- 语法检查：`bash -n run_million_data_sim.sh`
- 查看权限：`ls -l run_million_data_sim.sh run_ray_sim.sh run_local_sim.sh run_in_lade.sh`
- 搜索 AgentState/记录开关：`rg -n "KG_RECORD_AGENT_STATE|AgentState|record_agent|agent_state|step_seconds|record_step|KG_BACKEND|KG_RUN_ID" simulation scripts tests run_million_data_sim.sh -S`
- 查看动态服务参数：`./run_in_lade.sh python scripts/run_dynamic_sim.py --help`
- 查看脚本行号：`nl -ba run_million_data_sim.sh | sed -n '1,120p'`
- 查看 `run_dynamic_sim.py` 参数定义：`nl -ba scripts/run_dynamic_sim.py | sed -n '420,455p'`

### 修改内容

- 新增本检查记录：`docs/codex_notes/2026-08-27-run-million-data-sim脚本检查.md`。
- 未修改 `run_million_data_sim.sh` 或其他业务代码。

### 后续建议

- 如果现在只想启动大规模 Ray 动态仿真，应先删掉或注释 `--kg-record-agent-state 1`。
- 如果目标是写入 Neo4j，需要在启动时设置 `KG_BACKEND=neo4j`、`KG_RUN_ID`、`NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD`。
- 如果目标是每 step 每 agent 写节点，需要先在 `simulation/knowledge.py`、`simulation/delivery_sim.py`、`simulation/ray_delivery_sim.py` 和 `scripts/run_dynamic_sim.py` 中实现该功能及参数开关。
