### 任务背景

用户再次询问 `/data/zc/datasets/LaDe/viewer/run_million_data_sim.sh` 当前是否正确。此前该脚本包含用于百万级 AgentState 的启动参数，当前项目代码中 AgentState 功能已经部分实现。

### 关键结论

- `bash -n run_million_data_sim.sh` 通过，脚本语法正确。
- 当前 `simulation/knowledge.py` 已经实现 `record_agent_state` 功能，并通过环境变量 `KG_RECORD_AGENT_STATE=1` 开启。
- 当前 `scripts/run_dynamic_sim.py` 仍不支持 `--kg-record-agent-state` 或 `--kg-run-id` 命令行参数。
- 因此脚本中的 `--kg-record-agent-state 1` 和 `--kg-run-id "$KG_RUN_ID"` 仍然是错误参数，直接运行会失败。
- 正确做法是把 `KG_RECORD_AGENT_STATE=1`、`KG_RUN_ID=...`、`KG_BACKEND=neo4j`、`NEO4J_*` 作为环境变量传给 `run_dynamic_sim.py`。
- 当前 `MAX_STEPS=1000`、`DRONE_COUNT=100`，最多生成约 `100,000` 个 AgentState 节点，不是百万级；若目标是百万 AgentState，通常需要 `MAX_STEPS=10000`。
- 当前脚本仍没有可执行权限，直接 `./run_million_data_sim.sh` 会失败；可用 `bash run_million_data_sim.sh` 或加执行权限。

### 执行过的操作

- 读取脚本：`sed -n '1,260p' run_million_data_sim.sh`
- 语法检查：`bash -n run_million_data_sim.sh`
- 查看动态服务参数：`./run_in_lade.sh python scripts/run_dynamic_sim.py --help`
- 搜索 AgentState 开关和实现：`rg -n "kg-record-agent-state|KG_RECORD_AGENT_STATE|AgentState|record_agent_states|agent_state" simulation scripts tests run_million_data_sim.sh -S`
- 阅读 AgentState 写入代码：`sed -n '170,320p' simulation/knowledge.py`
- 阅读环境变量构建 KG 逻辑：`sed -n '560,595p' simulation/knowledge.py`
- 阅读相关测试：`sed -n '210,325p' tests/knowledge_test.py`
- 查看权限：`ls -l run_million_data_sim.sh`

### 修改内容

- 新增本复查记录：`docs/codex_notes/2026-08-27-run-million-data-sim复查.md`。
- 未修改 `run_million_data_sim.sh` 或业务代码。

### 后续建议

- 删除 `args` 中的 `--kg-record-agent-state 1` 和 `--kg-run-id "$KG_RUN_ID"`。
- 在执行 `python3 scripts/run_dynamic_sim.py` 前用 `export` 设置：
  - `KG_BACKEND=neo4j`
  - `KG_RECORD_AGENT_STATE=1`
  - `KG_RUN_ID=...`
  - `NEO4J_URI=...`
  - `NEO4J_USER=...`
  - `NEO4J_PASSWORD=...`
- 若要百万 AgentState，把 `MAX_STEPS` 默认值从 `1000` 调到 `10000`，并确认任务不会提前完成导致仿真提前结束。
