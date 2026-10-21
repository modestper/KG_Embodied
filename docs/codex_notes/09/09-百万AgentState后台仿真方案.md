### 任务背景

用户希望沿用 `run_million_data_sim.sh` 的 100 drone、10000 step 仿真参数，每个 step 将所有 drone 的 AgentState 快照写入 Neo4j，从真实仿真过程生成约 100 万个时序节点，并避免浏览器或外部网络断开导致仿真终止。用户要求只新增独立 headless 文件，不修改原有仿真文件。

### 关键结论

- 当前仿真循环位于 `/events` SSE 请求内，浏览器断开会触发连接异常，随后关闭 Ray 仿真环境。
- 已新增 `scripts/run_headless_sim.py`，直接驱动 local/Ray 仿真，不启动 HTTP 服务，也不依赖浏览器或 SSE 连接。
- 必须显式设置 `KG_BACKEND=neo4j` 和 `KG_RECORD_AGENT_STATE=1`，否则不会向 Neo4j 写入 AgentState。
- `100 drones * 10000 steps = 1000000 AgentState` 只在实际运行满 10000 step 时成立；当前逻辑会在全部任务完成后提前停止。
- headless 入口用轻量 StepCounter 替代 frame 列表，并跳过前端 snapshot 构造；step 编号仍连续递增，但不会在内存中保存完整帧。
- 保留任务全部完成后提前结束的原有语义，不提供继续补齐到 10000 step 的开关。
- `run_million_data_sim.sh`、`scripts/run_dynamic_sim.py` 和 `simulation/*` 均未修改。

### 执行过的操作

- 检查 `scripts/run_dynamic_sim.py` 的 `/events`、`stream_simulation()`、断连处理和 Ray 初始化逻辑。
- 检查 `run_million_data_sim.sh` 的默认参数及环境变量传递方式。
- 检查 `simulation/delivery_sim.py` 和 `simulation/ray_delivery_sim.py` 的 step、提前结束和 frame 保存逻辑。
- 检查 `simulation/knowledge.py` 中 `KG_BACKEND`、`KG_RECORD_AGENT_STATE`、Step 和 AgentState 写入实现。
- 检查服务器已安装的后台工具，确认 `screen`、`tmux` 和 `nohup` 均可用。
- 新增后运行 39 项 headless、local、Ray 和动态页面相关测试，全部通过。
- 运行 `py_compile`、`--help` 检查和 local/memory 冒烟仿真，均通过。

### 修改内容

- 新增 `scripts/run_headless_sim.py`：独立 headless 入口、周期进度日志、无 frame 留存和资源清理。
- 新增 `tests/run_headless_sim_test.py`：覆盖无 frame 累积、任务完成提前退出、最大 step 退出和百万实验默认参数。
- 未修改任何原有 Python、Shell 或仿真业务代码。

### 后续建议

- 使用 `screen` 或 `nohup` 启动新入口，并通过 Neo4j 查询观察 Step 和 AgentState 数量。
- 每次完整实验使用新的 `KG_RUN_ID`，避免重跑时 Event 重复积累。
- 若需要抵御服务器重启或进程崩溃，还应增加 checkpoint/resume；`screen` 只能抵御 SSH 和网络中断。
