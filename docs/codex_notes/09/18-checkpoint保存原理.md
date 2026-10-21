### 任务背景

解释当前 Ray headless 仿真的 checkpoint 保存了哪些内容、如何写入文件、如何恢复 Ray actors，以及这种设计为什么能够在 OOM 后继续运行。

### 关键结论

- checkpoint 是项目自定义的“仿真动态状态快照”，不是 Ray 自带快照，也不是 Neo4j 数据库备份。
- 当前文件约 2.1 MB，包含 10,424 个任务的动态状态和 100 个 drone 的完整运行状态。
- 静态任务数据先从 LaDe CSV 按相同参数重新加载，checkpoint 只负责覆盖会随仿真变化的状态。
- 文件使用规范化 JSON、SHA-256、临时文件、`fsync` 和原子替换，避免 OOM 或断电留下半个文件。
- 每个 Step 在写 Neo4j 前先写 checkpoint，因此最新 checkpoint 可能短暂领先 Neo4j 1 步；恢复器能够识别并补交该 Step。
- 当前只保留一个最新 checkpoint，不保留每个 Step 的历史 checkpoint。

### 执行过的操作

- 阅读 `simulation/checkpoint.py` 的构建、写入、读取和校验代码。
- 阅读 `simulation/ray_delivery_sim.py` 的 drone 序列化和 actor 恢复代码。
- 阅读 `scripts/run_headless_sim.py` 的 Step 提交与 resume 流程。
- 检查当前 checkpoint 的字段、任务数、drone 数、队列数和文件大小。

### 修改内容

本次只整理说明，没有修改仿真代码。

#### 一、checkpoint 顶层结构

文件外层结构为：

```text
{
  sha256: 对 payload 计算的校验值,
  payload: 实际 checkpoint 数据
}
```

`payload` 包含：

```text
version
run_id
step_index
now_minute
config
state
```

- `version`：checkpoint 格式版本，当前为 1，用于防止未来格式变化后错误读取旧文件。
- `run_id`：对应 Neo4j Run，例如 `hangzhou-0621-headless-002`。
- `step_index`：已完成并保存的 Step 编号。
- `now_minute`：当前仿真时刻。
- `config`：重新构建同一个仿真场景所需的参数。
- `state`：任务和 drone 的动态状态。

#### 二、保存的运行配置

`config` 保存：

```text
run_id
backend
city
day
task_limit
drone_count
step_seconds
depot_mode
seed
use_llm
```

这些参数用于保证恢复时加载的仍是同一批杭州 0621 数据、同样的 10,424 个任务、100 个 drone、相同随机种子和 Step 长度。如果启动参数与 checkpoint 不一致，恢复器会直接拒绝运行，避免把旧状态套到另一个实验上。

#### 三、任务状态

`state.task_states` 为每个任务保存：

```text
task_id
status
picked_up
assigned_to
completed_at
pickup_time
onboard_drone_id
```

含义如下：

- `status`：waiting、assigned、picked_up 或 completed。
- `picked_up`：货物是否已经取走。
- `assigned_to`：任务分配给哪个 drone。
- `completed_at`：完成时间。
- `pickup_time`：取货时间。
- `onboard_drone_id`：货物当前在哪个 drone 上。

任务坐标、发布时间、deadline、AOI、region 等静态字段由恢复时重新读取 LaDe CSV 获得，不需要在全局任务状态中重复保存。drone 的 queue 和 onboard_tasks 中仍包含对应任务的完整 payload，便于直接重建 actor 内部队列。

#### 四、drone 状态

`state.drones` 为每个 drone 保存：

```text
drone_id
x, y
status
region_id
speed_m_per_minute
queue_capacity
cargo_capacity
queue
onboard_tasks
completed
total_distance_m
battery_capacity_m
battery_remaining_m
low_battery_threshold_m
safety_buffer_m
home_depot
depots
target_depot
failure_mode
takeover_mode
```

这些字段足以恢复：

- drone 当前所在位置；
- 当前是 idle、queued、delivering 或 returning；
- 还剩多少电量；
- 已接受但未完成哪些任务；
- 当前携带哪些货物；
- 已完成任务列表；
- 累计飞行距离；
- 返航或充电目标；
- 预留的故障和接管状态。

checkpoint 不保存 Ray actor PID、ObjectRef、worker 进程地址等运行时对象，因为这些对象在 Ray 集群重启后已经无效。恢复时会根据上述普通 JSON 数据重新创建 actor。

#### 五、当前 Step 的补充信息

`state.completed_this_step_ids` 保存当前 Step 内刚完成的任务 ID。

其作用是处理以下提交窗口：

```text
checkpoint 已写完
        ↓
进程在 Neo4j Step 提交前退出
```

恢复器发现 checkpoint 比 Neo4j 领先 1 步时，会利用完整任务/drone 状态和 `completed_this_step_ids` 补写该 Step 的概要和 100 条 AgentState。

#### 六、保存过程

每个 Step 的顺序为：

```text
100 个 Ray actors 执行动作
        ↓
主进程收集 actor snapshot
        ↓
更新任务、drone、时间和完成状态
        ↓
调用 checkpoint_state() 转换成普通 dict/list
        ↓
写入 checkpoint
        ↓
提交 Neo4j Step + 100 条 AgentState
```

具体写文件过程：

1. 将 payload 按 key 排序并压缩成规范化 JSON。
2. 对规范化字节计算 SHA-256。
3. 先写入同目录临时文件。
4. 调用 `flush()` 和 `fsync()`，确保内容进入文件系统。
5. 使用 `os.replace()` 原子替换正式 checkpoint。
6. 再对目录执行 `fsync()`，确保文件名替换持久化。

因此任何时刻正式路径指向的要么是上一个完整 checkpoint，要么是新 checkpoint，不会指向只写了一半的 JSON。

#### 七、恢复过程

恢复顺序为：

1. 读取 JSON 外层 envelope。
2. 重新计算 payload 的 SHA-256，与文件中记录值比较。
3. 校验 checkpoint 版本、run_id 和所有运行参数。
4. 从 LaDe CSV 使用相同 city、day、limit 和 seed 重建静态任务和初始 drones。
5. 查询 Neo4j 最新 Step，确认与 checkpoint 相同，或只落后 1 步。
6. 按 task_id 将任务动态字段覆盖到静态任务对象。
7. 关闭已失效或残余的 Ray actors。
8. 使用每个 drone payload 创建新的 `_DroneActor`。
9. 将 queue、onboard_tasks、位置、电池和累计距离恢复到 actor 内部。
10. 从 actors 拉取 snapshot，重建主进程中的 DroneStateProxy。
11. 从 checkpoint 的下一 Step 继续运行。

#### 八、该方案的原理

仿真在某个 Step 边界上的未来行为，由以下内容共同决定：

```text
静态场景参数
+ 当前仿真时间
+ 所有任务动态状态
+ 所有 drone 动态状态
+ 队列与载货顺序
```

只要保存并还原这些“决定下一步行为的状态”，就不需要保存 Python 进程和 Ray worker 本身。新进程会从相同状态开始计算下一个 Step，这就是 checkpoint/restart 的核心原理。

### 后续建议

- 当前只保留最新 checkpoint；如需防止逻辑错误覆盖正确快照，可以增加每 100 或 500 Step 的归档 checkpoint。
- checkpoint 不包含 Neo4j 全图；Neo4j 历史备份仍使用 `backups/.../nodes.jsonl.gz` 和 `relationships.jsonl.gz`。
- 当前 `use_llm=false`，恢复后的调度可保持较强确定性。未来启用 LLM 或增加运行期随机故障时，应额外保存随机数生成器状态或模型决策记录。
- 可以增加 checkpoint 内部交叉校验，例如检查所有 queue task 都存在、onboard 数不超过 3、同一任务不能同时属于两个 drones。
