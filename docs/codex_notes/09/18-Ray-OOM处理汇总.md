### 任务背景

杭州 0621 的 `hangzhou-0621-headless-002` 使用 Ray 运行 100 个 drone，并逐 Step 向 Neo4j 写入 100 个 AgentState。仿真先后在 Step 2985 和 Step 4157 附近因整机内存压力触发 Ray OOM，需要保证历史数据不丢失，并让任务能够自动恢复和继续运行。

### 关键结论

- 两次故障都不是 Neo4j 数据损坏，而是共享服务器整机内存超过 Ray 的 95% 保护阈值，Ray 主动杀死 drone actor。
- 已实现原子 checkpoint，完整保存任务状态、100 个 drone 的位置、电量、队列、载货、累计里程和仿真时间。
- checkpoint 与 Neo4j Step 进行严格一致性校验，已经从 Step 2985 和 Step 4157 成功恢复。
- Ray step 执行和 actor 重建阶段的 OOM 都已纳入重试，并增加内存门控和指数退避。
- 新增常驻 supervisor；仿真子进程退出后 screen 不再立即消失，而是等待内存恢复并自动执行 `--resume`。
- Ray CPU 和预启动 worker 从默认 256 限制为 100，和 100 个 drone 一一对应，降低空闲 worker 开销。
- 当前常驻会话为 `headless002_guard`，本次整理时 checkpoint 已推进到 Step 4322。

### 执行过的操作

#### 第一阶段：9 月 17 日首次 OOM 后保护已有数据

1. 检查 Ray OOM 日志。
   - 当时整机使用量约为 239.11/251.65 GB，刚超过 Ray 默认 95% 阈值。
   - Object Store 使用率接近 0，说明问题不是 Ray 对象存储堆积，而是整机进程总内存过高。
   - 被杀死的单个 drone actor 约占 40 至 50 MB，但它被 Ray 选作内存压力下的牺牲 worker。

2. 备份 Neo4j 历史子图。
   - Run ID：`hangzhou-0621-headless-002`。
   - 备份点：Step 2985。
   - 备份节点 313,599 个、关系 949,523 条。
   - 保存为 gzip JSONL，并记录文件数量、字节数和 SHA-256。

3. 实现完整 checkpoint。
   - 保存 `step_index`、`now_minute` 和运行配置。
   - 保存所有任务的 waiting/assigned/picked_up/completed 等可变状态。
   - 保存每个 drone 的位置、电池、状态、queue、onboard_tasks、completed、累计距离和 depot 信息。
   - checkpoint 使用临时文件、`fsync` 和 `os.replace` 原子替换。
   - 文件外层保存 SHA-256，损坏或只写入一半时会拒绝恢复。

4. 从 Neo4j Step 2985 重建 checkpoint。
   - 使用 AgentState 还原 100 个 drone。
   - 使用 Task、ASSIGNED、CARRYING、COMPLETED 关系还原任务队列和载货状态。
   - 校验 queue、onboard 和 completed 数量一致后才生成 checkpoint。

5. 实现 Ray 状态恢复。
   - 从 checkpoint 重新创建 100 个 Ray actors。
   - 将主进程中的任务对象、Ray actor 内部任务和 drone proxy 同步到同一状态。
   - 恢复后从原 Step 继续计数，而不是从 Step 0 重新开始。

6. 处理重放幂等性。
   - Event ID 改为由 run、事件类型、时间、drone 和 task 确定。
   - Event 关系由 `CREATE` 改为 `MERGE`。
   - 同一个 Step 因 OOM 重试时，不会生成重复事件和重复关系。

7. 处理 checkpoint 与 Neo4j 的提交一致性。
   - 每个 Step 先原子保存 checkpoint，再提交 Neo4j。
   - Step 和该 Step 的 100 条 AgentState 改为同一事务提交。
   - 如果进程恰好在两次提交之间退出，恢复器允许 checkpoint 领先 Neo4j 1 步，并自动补交缺失 Step。

8. 优化 AgentState 写入速度。
   - 原逻辑按 `AgentState.id` 做 MERGE，但数据库没有该字段索引，单 Step 可能耗时数分钟。
   - 改为按现有复合索引 `(run_id, drone_id, step_index)` MERGE。
   - 单 Step 写入时间降低到数秒级。

#### 第二阶段：9 月 18 日再次 OOM 后增强自动恢复

1. 定位第二次退出原因。
   - 仿真在 Step 4157 遇到 OOM时，整机内存为 240.32/251.65 GB。
   - 原恢复逻辑开始重建 actors，但此时内存进一步达到 248.85/251.65 GB。
   - `step_once()` 的 OOM 已被捕获，但 `restore_checkpoint_state()` 内发生的第二次 OOM 未被捕获，导致 Python 直接退出。
   - screen 中只有该 Python 命令，因此主进程退出后 `headless002_resume2` 会话自然消失。

2. 扩大 Ray OOM 捕获范围。
   - actor restore 自身发生 OOM 时也进入恢复循环。
   - 每次恢复前主动关闭残余 actors，避免旧 actor 和新 actor 同时占用内存。
   - step 失败、actor 创建失败、actor snapshot 失败均可按同一 checkpoint 重新恢复。

3. 增加恢复前内存门控。
   - Ray 进程内恢复要求宿主机 `MemAvailable >= 32 GiB`。
   - 不满足时每 30 秒检查一次，不再立即批量创建 100 个 actors。
   - 保留 5 次指数退避恢复，延迟从 30 秒开始逐步增加。

4. 增加常驻 supervisor。
   - 新增 `scripts/run_headless_supervisor.py`。
   - supervisor 是 screen 中的常驻父进程，真正的 headless 仿真作为子进程运行。
   - 子进程非正常退出后，父进程等待 120 秒，并等待宿主机至少有 48 GiB 可用内存。
   - 内存满足条件后，自动追加 `--resume`，从最新 checkpoint 重新启动。
   - `--max-restarts 0` 表示不限重启次数。

5. 限制 Ray worker 规模。
   - 原 Ray 根据机器识别到 256 个 CPU，raylet 参数显示会预启动最多 256 个 Python workers。
   - 当前显式传入 `--ray-num-cpus 100`。
   - raylet 已确认 `CPU,100`、`maximum_startup_concurrency=100` 和 `num_prestart_python_workers=100`。
   - 100 个 drone 仍各使用一个 actor，不影响仿真逻辑和生成节点内容。

6. 重新恢复并验证。
   - checkpoint 和 Neo4j 在 Step 4157 完全一致，且有 100 条 AgentState。
   - 新会话 `headless002_guard` 从 Step 4157 恢复。
   - checkpoint 与 Neo4j 后续在 Step 4183 再次核验一致。
   - 本次汇总时 checkpoint 已继续推进到 Step 4322。
   - 相关单元测试共 41 项，全部通过。

### 修改内容

- `simulation/checkpoint.py`：checkpoint 数据结构、SHA-256 校验和原子写入。
- `simulation/ray_delivery_sim.py`：Ray actor 状态导出、重建和提交前 checkpoint hook。
- `simulation/knowledge.py`：Event 幂等写入、Step/AgentState 事务和复合索引写入路径。
- `scripts/run_headless_sim.py`：resume、一致性校验、Ray 故障恢复、内存门控和恢复重试。
- `scripts/bootstrap_headless_checkpoint.py`：从 Neo4j 最新 Step 重建 checkpoint。
- `scripts/backup_neo4j_run.py`：按 Run 导出完整逻辑备份。
- `scripts/run_headless_supervisor.py`：常驻父进程、内存检查和异常退出自动重启。
- `tests/`：补充 checkpoint、Ray restore OOM、supervisor、Neo4j 和备份测试。

### 后续建议

- 使用 `screen -r headless002_guard` 查看当前常驻会话。
- 使用 `tail -f logs/headless-002-resume-20260917.log` 查看进度；旧 OOM traceback 会保留作为历史记录。
- checkpoint 能保证进度，supervisor 能自动恢复，但共享服务器其他用户仍可能造成整机内存峰值。
- 当前 swap 长期处于满载状态，建议后续协调清理无关高内存任务，或将该仿真放到有独立内存配额的节点。
- 不建议将 Ray OOM 阈值从 95% 直接提高到 99%，否则可能从“Ray 主动杀 actor”升级为 Linux OOM killer 杀死 Neo4j 或其他关键进程。
