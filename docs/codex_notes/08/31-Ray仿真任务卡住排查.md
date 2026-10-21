### 任务背景

用户反馈当前 LaDe 项目启动仿真后运行很久，但只完成了少量任务，页面上 100 个 drone 大多保持 idle，怀疑仿真卡住。

### 关键结论

- 当前 8010 服务进程为 `scripts/run_dynamic_sim.py --backend ray --city Hangzhou --day 0621 --task-limit 10424 --drone-count 100 --step-seconds 6 --max-steps 10000`。
- 进程启用了 `KG_BACKEND=neo4j`、`KG_RECORD_AGENT_STATE=1`，会把 Step 和 AgentState 写入 Neo4j，运行会比纯仿真慢很多。
- Neo4j 中 `million-hangzhou-0831-agents` 的 Step 记录显示，大规模 run 在早期完成少量任务后，后续大量 drone 变为 idle。
- 同参数 local 对照实验不写 Neo4j、不走 Ray 时，2000 step 可完成大量任务，说明数据本身和基本调度逻辑不是根因。
- 根因集中在 Ray 路径：`DroneStateProxy.assign()` 会无条件把任务标成 `assigned`，但 actor 内部真实 `DroneAgent.assign()` 可能因电量/容量拒绝；Ray 环境没有把 actor 拒单结果回滚到主任务表。
- 结果是部分任务在主进程里变成 `assigned`，但没有进入任何 actor 的真实队列，后续不会再被调度，drone 看起来 idle。
- 另外，前端 `/events` 会用页面控件参数覆盖命令行默认参数；如果页面里 `max_steps/task_limit` 没改，实际运行参数可能和启动命令不一致。

### 执行过的操作

- 查看进程与端口：
  - `pgrep -af "run_dynamic_sim|run_ray_sim|ray|python"`
  - `ss -ltnp`
  - `ps -p 2184981 -o pid,ppid,etime,stat,pcpu,pmem,cmd`
- 查看进程环境：
  - `strings /proc/2184981/environ`
- 查询 Ray：
  - `./run_in_lade.sh ray status --address=222.20.99.83:43167`
- 查询 Neo4j 当前 run：
  - Step 数量、最新 Step、Event 类型统计、Task 状态、Drone 状态、AgentState 状态。
- 运行 local 对照实验：
  - Hangzhou 0621，100 drones，step_seconds=6，分别测试 task_limit 20、1000、10000。
- 运行最小复现：
  - 构造电量不足任务，使用 `FakeRay` 证明 Ray 主任务表会被标成 `assigned`，但 actor 队列为空。

### 修改内容

本次没有修改项目代码，只新增本排查总结文件。

### 后续建议

- 修复 `DroneStateProxy.assign()`，使其与 `DroneAgent.assign()` 一样调用 `can_accept_task()`，不能接单时返回 `False` 且不修改任务状态。
- 修改 Ray actor 的 `assign()` 返回值，显式返回 `accepted`，主进程只有在 actor 确认接单后才记录 assignment。
- 如果 actor 拒单，主进程应把任务恢复为 `waiting`，避免任务永久卡在 `assigned`。
- `AuctionCoordinator` 候选阶段应直接过滤 `can_accept_task(task)`，不能只过滤 `can_accept()`。
- 大规模实验建议先关闭 `KG_RECORD_AGENT_STATE=1`，或降低 `frame_stride`、增大 `step_seconds`，避免每步 100 个 AgentState 写入 Neo4j。
