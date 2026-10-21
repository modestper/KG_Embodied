### 任务背景

用户补充了通过网页端运行 `run_million_data_sim.sh` 的日志，指出网页端请求同样使用 `Hangzhou`、约一万任务、100 个 drone、Ray 后端和 Neo4j AgentState 写入，因此需要重新解释为什么 headless 仍会触发 Ray OOM。

### 关键结论

- `run_million_data_sim.sh` 确实显式传入 `--backend ray`，且默认参数是大规模：`TASK_LIMIT=10424`、`DRONE_COUNT=100`、`MAX_STEPS=10000`。
- 用户给出的网页端大规模 `/events` 请求与 headless 规模基本一致；此前“网页端默认小规模”的判断只适用于 `run_local_sim.sh` / 普通动态页面默认请求，不适用于 `run_million_data_sim.sh`。
- 网页端 SSE 连接返回 `200` 只代表连接建立，不代表 10000 steps 已完整跑完。
- 网页端每步后会执行 `time.sleep(wall_interval)`，默认 `0.25s`，即至少约 41.7 分钟才能完成 10000 steps；headless 没有这个节流，会全速推进 Ray 同步和 Neo4j 写入。
- 网页端刷新、切换参数或重新打开页面会触发新的 `/events` 请求，并通过 `replace_active_env(None)` 关闭旧环境；因此日志中多次请求可能代表旧 run 被中断，而不是一个 run 顺利完整跑完。
- Ray OOM 日志显示的是节点总内存超过阈值，不一定是被杀 worker 自身占用很大；Neo4j、Ray 历史 session、系统缓存、其他进程和之前 run 的残留都会影响当次 headless 是否触发 OOM。

### 执行过的操作

- 读取 `run_million_data_sim.sh`，确认其启动 `scripts/run_dynamic_sim.py --backend ray`。
- 搜索 `run_million`、`headless`、`KG_RECORD_AGENT_STATE`、`--backend` 相关实现和既有文档记录。
- 查看 `git status --short`，确认本次未改业务代码，仅新增说明文档。
- 结合此前已读取的 `scripts/run_dynamic_sim.py`、`scripts/run_headless_sim.py`、`simulation/ray_delivery_sim.py`、`simulation/knowledge.py` 进行对比。

### 修改内容

- 未修改业务代码。
- 新增本说明文件：`docs/codex_notes/2026-09-11-Ray-OOM网页端同规模复查.md`。

### 后续建议

- 判断网页端是否真的完整跑完时，不要只看 HTTP `200`，应查询 Neo4j 中该 `KG_RUN_ID` 的最大 `Step.step_index`。
- 对比 headless 和网页端时，应记录启动前后的 `free -h`、`ray status`、`ray logs raylet.out` 和 Neo4j 进程内存。
- 如需让 headless 更接近网页端压力，可增加进度节流参数，或分批运行，避免全速写入导致瞬时内存压力过高。
- 每次大规模实验前建议清理旧 Ray：确认没有其他仿真后执行 `ray stop -f`，并确认 Neo4j/系统内存处于可接受状态。
