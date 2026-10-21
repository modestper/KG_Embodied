### 任务背景

headless 百万节点生成任务再次因 Ray OutOfMemory 退出。需要确认已生成数据是否丢失、OOM 是否由项目自身内存泄漏导致，并设计能够抵抗共享服务器内存波动的恢复方案。

### 关键结论

- 2026-09-16 16:06 的 Ray 日志明确记录：节点内存达到 `239.11 GB / 251.65 GB = 95.0159%`，超过默认阈值 `0.95`，Ray 主动杀死两个 Drone actor。被杀 actor 自身仅约 `0.05 GB`，Ray object store 使用量为 `0`。这是整台共享节点的全局内存压力，不是 object store 堆积，也没有证据表明单个 Drone actor 泄漏了 239 GB。
- 当前 actor 使用 `ray.remote(num_cpus=1)`，没有配置 `max_restarts`，因此默认 `max_restarts=0`。actor 被杀后，下一次 `_DroneActor.snapshot` 抛出错误并结束主程序。
- 不能只把 `max_restarts` 改成 `-1`。Ray 重启 actor 会重新运行构造函数；当前构造参数是初始无人机状态，会丢失位置、电池、队列、载货和完成任务状态。官方建议有状态 actor 使用 checkpoint 恢复。
- Neo4j 数据没有随进程退出而丢失。`hangzhou-0621-headless-002` 当前有 `2985` 个 Step 和 `298,500` 个 AgentState，每个完整 Step 正好 100 个状态。最后 Step 与最后 Event 的 `time_minute` 都为 `702.5`，没有检测到 Event 超前于最后完整 Step。
- 不应立即用相同 `run_id` 从 Step 0 重跑。Step/AgentState 使用确定 ID 和 `MERGE`，会覆盖已有编号；Event 使用 UUID，会重复新增，最终形成混合实验数据。
- 当前服务器仍是共享高压环境：检查时有约 69.6 GB 的 vLLM 进程、多个 Neo4j JVM、约 13 GB Milvus 以及多组约 3.9 GB Python 进程，Swap 9.5 GB 已用满。即使本项目内存很小，其他用户任务再次突增仍可能触发 Ray 的整机 95% 阈值。
- 如果目标只是生成百万级 Neo4j 图数据，优先使用 `--backend local`。100 智能体并发查询是后续查询客户端的并发度，与生成数据时是否使用 Ray actor 没有必然关系。local 后端只有一个仿真进程，不会因为 Ray 的节点级 OOM killer 随机牺牲 actor。

### 执行过的操作

- 阅读 `scripts/run_headless_sim.py`、`simulation/ray_delivery_sim.py`、`simulation/knowledge.py` 和相关测试。
- 搜索 `/tmp/ray/session_latest/logs` 中的 `NODE_OUT_OF_MEMORY`、`ActorDiedError` 和内存监控日志。
- 查看 Ray session 日志规模、系统内存、Swap、磁盘和当前宿主机高内存进程。
- 通过 `./run_in_lade.sh python` 运行只读 Neo4j 查询，统计杭州各 run 的 Step、AgentState、最大 step 和最后事件时间。
- 核对 Ray 版本为 `2.55.1`，并查阅 Ray 官方 OOM prevention 与 actor fault tolerance 文档。

### 修改内容

- 未修改仿真、Ray 或 Neo4j 生产代码，也未删除、覆盖任何实验数据。
- 新增本分析文档。
- 临时只读检查脚本位于 `/tmp/check_headless_run_progress.py`，不参与项目运行。

### 后续建议

- 第一优先级：为 headless 增加原子 checkpoint，至少保存 `step_index`、`now_minute`、全部 Task 状态以及每架 Drone 的完整 payload；新增 `--resume`，启动时校验 city/day/seed/task-limit/drone-count/step-seconds 等配置。
- 第二优先级：在每个完整 step 后写 checkpoint，并在恢复时比较 checkpoint step 与 Neo4j 最大 Step；不一致时拒绝盲目续跑。Event ID 应改为包含 run/step/type/task/drone 的确定 ID，保证重放幂等。
- 第三优先级：捕获 `RayActorError`/`OutOfMemoryError`，从最后完整 checkpoint 重建全部 actor 并重试当前 step；不能仅依赖 Ray 自动重启。
- 第四优先级：增加内存水位保护，例如内存使用超过 88% 暂停、回落到 85% 后继续，并限制重试次数。它只能降低风险，无法防止共享机器上其他任务瞬间占满内存。
- 对当前 `headless-002`，建议先备份或克隆 run，再实现一次性的 Step 2985 恢复。Neo4j 中现有 Task、Drone、ASSIGNED/CARRYING/COMPLETED 关系足以构造恢复状态，但队列顺序等信息需要谨慎还原并验证，因此不应直接在原 run 上试错。
- 不建议设置 `RAY_memory_monitor_refresh_ms=0`；这会把 Ray 主动保护变成 Linux OOM killer 风险。也不建议简单把阈值从 0.95 调到 0.99，因为当时 Swap 已满，额外余量不足。
