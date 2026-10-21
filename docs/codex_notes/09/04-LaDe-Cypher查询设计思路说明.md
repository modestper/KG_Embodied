### 任务背景

用户希望理解当前 `scripts/lade_cypher_benchmark.py` 中 Q0-Q12 查询模板的设计思路，包括为什么这样设计、这样设计的好处，以及每类查询能从 Neo4j 知识图谱中读到什么信息。

### 关键结论

- 当前查询集不是为了只测一条最快查询，而是为了覆盖不同查询形状、不同业务语义和不同数据库执行压力。
- 所有查询都围绕 `run_id` 过滤，目的是保证每次只分析一个仿真 run，避免不同实验结果混在一起。
- Q0-Q2 是轻量基础查询，用来确认数据规模、连接状态和点查延迟。
- Q3-Q5 重点覆盖时间窗口和运行过程历史，适合模拟智能体查询近期状态或事件。
- Q6-Q9 重点覆盖业务统计，包括任务生命周期、状态聚合、区域负载和 Top-K 繁忙无人机。
- Q10-Q12 是较重查询，用于暴露复杂 join、路径遍历和反连接过滤的性能问题。
- 每次查询记录 `elapsed_ms`、`row_count`、`success`、`error`，可以同时分析查询延迟、返回规模和失败原因。

### 执行过的操作

- 读取 `scripts/lade_cypher_benchmark.py` 中 `DEFAULT_TEMPLATES` 定义。
- 确认模板位置：
  - Q0：`run_label_counts`
  - Q1：`run_point_lookup`
  - Q2：`drone_point_lookup`
  - Q3：`agent_state_trajectory`
  - Q4：`step_snapshot_star`
  - Q5：`event_time_window`
  - Q6：`drone_task_lifecycle`
  - Q7：`agent_state_timeseries_aggregate`
  - Q8：`region_load_heatmap`
  - Q9：`top_busy_drones`
  - Q10：`event_drone_region_complex_join`
  - Q11：`event_drone_task_region_path`
  - Q12：`unfinished_task_antijoin`

### 修改内容

本次没有修改运行代码，只新增本说明文档。

### 查询设计思路

#### 总体原则

这套查询模板主要参考图数据库 benchmark 的工作负载设计思想，而不是只写几条随机查询。设计时重点覆盖以下维度：

- 查询选择性：从只查一个 `Run` 或 `Drone`，到扫描一个时间窗口内的大量 `AgentState`。
- 查询形状：点查、星型展开、时间窗口、聚合、Top-K、复杂 join、路径遍历、反连接。
- 业务语义：既能看数据库性能，也能读出仿真中的 agent 状态、任务执行、区域负载、事件分布。
- 并发可测：模板都参数化，可以让多个 agent 带不同 `drone_id`、`step`、时间窗口并发执行。

#### Q0 run_label_counts

用途：统计某个 `run_id` 下不同 label 的节点数量。

能读到的信息：

- 当前 run 下有多少 `AgentState`、`Step`、`Event`、`Task`、`Drone`、`Region`。
- 判断是否真的生成了百万级节点。
- 判断某次仿真是否中途停止，例如 `AgentState = drone_count × step_count` 是否符合预期。

设计价值：

- 这是压测前的 sanity check。
- 查询结果可以验证后续延迟数据是否建立在正确规模上。

#### Q1 run_point_lookup

用途：按 `Run.id` 点查一个 run。

能读到的信息：

- 目标 run 是否存在。
- Neo4j 连接、认证、基本查询是否正常。

设计价值：

- 这是最轻量基线查询。
- 如果 Q1 都慢，说明问题可能在连接、driver、Bolt、Neo4j 服务状态，而不是查询复杂度。

#### Q2 drone_point_lookup

用途：查询某个 `Drone` 的当前属性。

能读到的信息：

- 当前状态 `status`。
- 所在区域 `region_id`。
- 队列长度 `queue_len`。
- 载货数量 `onboard_count`。
- 剩余电量/航程 `battery_remaining_m`。
- 累计飞行距离 `total_distance_m`。

设计价值：

- 模拟智能体查询自己的当前状态。
- 返回行数很小，适合测高并发点查。

#### Q3 agent_state_trajectory

用途：查询单个 agent 在一个 step 窗口内的历史轨迹。

能读到的信息：

- agent 在不同 step 的位置 `x/y`。
- 状态变化 `status`。
- 队列变化 `queue_len`。
- 电量变化 `battery_remaining_m`。

设计价值：

- 模拟智能体回顾自己的近期历史。
- 可以通过调节 `step_window` 控制返回规模。
- 能测试时间窗口过滤和 `ORDER BY step_index` 的成本。

#### Q4 step_snapshot_star

用途：以某一个 `Step` 为中心，展开该 step 下所有 `AgentState`。

能读到的信息：

- 某个 step 中不同状态的 agent 数量。
- 平均队列长度。
- 平均载货数量。
- 平均电量。

设计价值：

- 这是典型 star query。
- 对 100 个 agent 来说，一个 step 会展开约 100 个 `AgentState`。
- 适合观察某个时刻系统整体运行状态。

#### Q5 event_time_window

用途：查询某个时间段内发生的事件。

能读到的信息：

- 事件类型 `event_type`。
- 事件发生时间 `time_minute`。
- 关联 drone。
- 关联 task。
- 发生区域。

设计价值：

- 模拟“最近发生了什么”的查询。
- `OPTIONAL MATCH` 可以覆盖没有 task 的 drone 事件。
- 时间窗口越大，扫描和排序压力越大。

#### Q6 drone_task_lifecycle

用途：查询某个 drone 关联的任务分配和完成记录。

能读到的信息：

- 该 drone 被分配过哪些任务。
- 哪些任务完成了。
- 任务区域、发布时间、截止时间。
- 是否 late。

设计价值：

- 业务语义最直接。
- 可以用于分析单个 agent 的任务执行质量。
- 适合评估从 `Drone` 到 `Task` 的关系展开成本。

#### Q7 agent_state_timeseries_aggregate

用途：对一个 step 窗口内的全部 `AgentState` 做时间序列聚合。

能读到的信息：

- 每个 step 下不同状态的 agent 数量。
- 每个 step 的平均队列长度。
- 最大队列长度。
- 平均电量。

设计价值：

- 偏 BI 查询。
- 能观察系统状态如何随时间变化。
- 对百万级 `AgentState` 来说，这是很重要的大范围聚合压力测试。

#### Q8 region_load_heatmap

用途：按区域聚合 agent 活动情况。

能读到的信息：

- 哪些区域 agent 活动样本最多。
- 每个区域涉及多少不同 drone。
- 平均队列长度和平均载货数量。

设计价值：

- 可以看区域负载是否不均衡。
- 覆盖 `GROUP BY region_id`、`count(DISTINCT ...)`、`ORDER BY`、`LIMIT`。
- 适合发现热点区域。

#### Q9 top_busy_drones

用途：统计完成任务最多的 drone。

能读到的信息：

- 哪些 drone 完成任务最多。
- 每个 drone 的 late task 数量。

设计价值：

- 典型 Top-K 查询。
- 可以看任务是否集中在少数 agent 上。
- 能测试关系聚合和排序成本。

#### Q10 event_drone_region_complex_join

用途：同时关联 `Run`、`Event`、`Drone`、`Region`、`Task`，按区域、事件类型和 drone 状态聚合。

能读到的信息：

- 各区域不同事件类型的数量。
- 涉及多少 drone。
- 涉及多少 task。
- 事件发生时 drone 状态分布。

设计价值：

- 这是复杂 join 查询。
- 可以暴露 join 顺序、中间结果膨胀和索引不足问题。
- 比 Q0-Q9 更适合做数据库优化前后的对比。

#### Q11 event_drone_task_region_path

用途：查询 `Event -> Drone -> Task -> Region` 多跳路径。

能读到的信息：

- 某个时间窗口内，drone、task、region 之间的路径关联。
- 哪些 drone 在某些区域相关事件更多。
- 事件和任务之间是否符合区域一致性。

设计价值：

- 这是图数据库最核心的路径遍历场景。
- 能测试多跳关系展开和路径过滤。
- 数据窗口过大时可能明显变慢，适合测图遍历瓶颈。

#### Q12 unfinished_task_antijoin

用途：查没有 `COMPLETED` 关系的任务。

能读到的信息：

- 哪些任务状态仍未完成。
- 未完成任务按状态的数量分布。
- 当前 run 中任务闭环是否完整。

设计价值：

- 这是反连接/不存在性查询。
- 适合发现异常数据和未闭环任务。
- 可以测试 `NOT EXISTS` 模式过滤性能。

### 后续建议

- 日常快速检查：先跑 Q0、Q1、Q2。
- 模拟智能体查询：重点跑 Q2、Q3、Q5、Q6。
- 看系统运行状态：重点跑 Q4、Q7、Q8、Q9。
- 做数据库性能压测：重点跑 Q7、Q8、Q10、Q11、Q12。
- 做 100 agent 并发：不要只跑一条模板，应混合点查、轨迹、事件、聚合和路径查询。
