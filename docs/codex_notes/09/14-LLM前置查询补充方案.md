# LLM 前置查询补充方案

### 任务背景

围绕 drone 调用 LLM 前的决策需求，补充当前 i0–i11 的查询类型和业务场景。本文提出 i12–i23 共 12 个新模板草案，包含具体 Cypher、参数和限制；编号尚未注册到脚本，不能直接用现有 `--queries i12` 调用。此次是方案设计，不修改仿真、现有查询或数据库。

### 关键结论

- 补充重点是状态完整性、存在性/反连接、空间候选、时间差分、异常识别、多跳追溯、截止风险、补能候选、冲突排查和历史案例。
- 当前图谱可以表达这些结构化查询，但某些字段可能尚未写入，任务释放记录也不完整，因此“空结果”不能一律解释为“没有任务/没有风险”。
- 所有历史场景应先固定已完整写入的 step，时间由该 Step 获取；查询草案优先使用精确 step，缺失时明确处理，不默默回退到不同历史时刻。
- 当前任务状态只表示最新值。i21 明确限定实时模式；历史方案尽量读截止时刻之前的 Event，而不使用最新 Task.status 推断过去。
- 路径搜索、充电排队、航路冲突预测、语义经验检索需要新增数据模型，不作为现有图谱已能可靠回答的问题。
- 不应每次调用 LLM 都执行全部模板；应按接单、配送、低电量、协同和异常场景选择查询包。
- 本文 Cypher 已对照代码字段和官方语法资料人工检查，未提交目标数据库进行 EXPLAIN/PROFILE 或结果测试，尚不能当作已验证的生产查询。

### 参数与时间约定

基础参数：`run_id`、`drone_id`、`step_index`、`time_minute`、`start_step`、`start_time`、`limit`。

- `time_minute` 应由所选完整 Step 读取，保持与 step_index 一致，不由调用者随意拼接。
- `region_id` 可从自身该步 AgentState 获取；`task_id` 来自本次要处理的任务。
- 额外阈值包括 `radius_m`、`min_battery_m`、`previous_step`、`movement_epsilon_m`、`safety_buffer_m`、`distance_tolerance_m` 等，来自业务配置，需校验范围与单位。
- 示例 point 使用 Cartesian x/y；项目距离逻辑使用欧氏距离并以米命名。接入时确认实际导入坐标确为米，不能把经纬度直接当平面米制坐标。
- Drone 上的容量、速度等配置在历史查询中使用的前提是同一 run 内不变；若配置会变化，应版本化或写入快照。
- 区间均明确上下界；缺失、空结果、查询失败、结果被 LIMIT 截断应作为不同状态交给调用方。

### 场景总览

| 编号 | 场景 | 类型 | 输出作用 |
| --- | --- | --- | --- |
| i12 | 调 LLM 前检查状态完整性 | 可选匹配、去重聚合 | 判断这一步是否缺无人机状态 |
| i13 | 接单前检查任务记录 | EXISTS、布尔判定 | 了解截止时刻之前是否有分配/完成记录 |
| i14 | 空闲时找待分配任务 | NOT EXISTS、反连接 | 找已知任务中未观察到分配/完成的候选 |
| i15 | 需要其他 drone 协助 | 空间距离、属性/关系关联、Top-K | 查真实距离较近的初步候选 |
| i16 | 判断状态变化 | 跨 step 关联、差分 | 比较位置、队列、续航和经过时间 |
| i17 | 怀疑停滞或异常 | 时间窗口聚合、聚合后过滤 | 筛出有任务但移动很少的候选 |
| i18 | 解释某任务发生了什么 | 固定多跳、可选匹配、时间窗口 | 返回任务事件及参与者 |
| i19 | 判断任务是否存在迟到风险 | 条件表达式、空间计算、派生指标 | 给出直达时间下界和截止余量 |
| i20 | 低电量寻找补能地点 | 空间范围、阈值过滤、Top-K | 找可达的已知仓库位置 |
| i21 | 实时任务执行者疑似冲突 | 多关系匹配、去重聚合、聚合后过滤 | 找当前记录中的多执行者不一致 |
| i22 | 决策前参考历史经验 | 多跳、时空过滤、相似条件 | 找同区域、相近任务距离的完成案例 |
| i23 | 区域调度与负载评估 | 多分支子查询、条件聚合 | 汇总区域人力及事件流量，避免行数相乘 |

### i12：当前 step 的状态是否齐全

参数：基础参数、`expected_drone_ids`。预期名单由运行配置提供，不从已有快照反推。

```cypher
WITH $expected_drone_ids AS expected
OPTIONAL MATCH (s:Step {run_id: $run_id, step_index: $step_index})
WITH expected, count(s) AS step_records
OPTIONAL MATCH (a:AgentState {run_id: $run_id, step_index: $step_index})
WHERE a.drone_id IN expected
WITH expected, step_records,
     collect(DISTINCT a.drone_id) AS observed,
     count(a) AS state_records
RETURN step_records,
       size(observed) AS observed_drone_count,
       [id IN expected WHERE NOT id IN observed] AS missing_drone_ids,
       state_records > size(observed) AS has_duplicate_states,
       step_records = 1 AND size(observed) = size(expected)
         AND state_records = size(expected) AS snapshot_complete;
```

用途：没有状态时也给出缺失名单，不能把缺失无人机当成不存在。expected_drone_ids 必须非空且去重；名单要考虑 agent 动态加入/退出。此检查不保证 Task、Event 与状态已原子提交，也不检查名单外多余状态，最终仍需写入端的完整 step 协议。

### i13：这个任务是否已经被分配或完成过

参数：run_id、task_id、time_minute。

```cypher
OPTIONAL MATCH (t:Task {run_id: $run_id, id: $task_id})
RETURN t IS NOT NULL AS task_known,
       EXISTS {
         MATCH (e:Event {run_id: $run_id, task_id: $task_id,
                         type: 'task_assigned'})
         WHERE e.time_minute <= $time_minute
       } AS assignment_observed,
       EXISTS {
         MATCH (e:Event {run_id: $run_id, task_id: $task_id,
                         type: 'task_completed'})
         WHERE e.time_minute <= $time_minute
       } AS completion_observed;
```

用途：用布尔事实辅助避免重复决策。assignment_observed 仅表示曾分配过，不表示当前归属；若支持撤销、转交，必须结合最新有效事件。task_known 只表示现在的库中有此节点，不证明历史时刻已经知道它。

### i14：找已知任务中未观察到分配的候选

参数：run_id、region_id、time_minute、limit。

```cypher
MATCH (t:Task {run_id: $run_id, region_id: $region_id})
WHERE t.release_minute <= $time_minute
  AND NOT EXISTS {
    MATCH (e:Event {run_id: $run_id, task_id: t.id})
    WHERE e.time_minute <= $time_minute
      AND e.type IN ['task_assigned', 'task_completed']
  }
RETURN t.id AS task_id, t.release_minute AS release_minute,
       t.deadline_minute AS deadline_minute,
       t.pickup_x AS pickup_x, t.pickup_y AS pickup_y
ORDER BY deadline_minute, task_id
LIMIT $limit;
```

用途：反连接找“缺少某种关系/事件”的对象。当前 Task 常在首次分配事件时才创建，实时空闲任务查询可能查不到本应等待的任务。要得到完整任务池，需任务释放时入图；严格历史回放还需记录 first_seen/released 事件，避免利用后来才出现的 Task 元数据。这条不能以空结果断言“所有任务都已分配”。

### i15：真正距离较近的初步协作候选

参数：run_id、drone_id、step_index、radius_m、min_battery_m、limit。

```cypher
MATCH (self:AgentState {run_id: $run_id, drone_id: $drone_id,
                        step_index: $step_index})
MATCH (a:AgentState {run_id: $run_id, step_index: $step_index})
      -[:OF_DRONE]->(d:Drone {run_id: $run_id})
WHERE a.drone_id <> $drone_id
  AND a.status IN ['idle', 'queued', 'delivering']
  AND a.queue_len < d.queue_capacity
  AND a.onboard_count < d.cargo_capacity
  AND a.battery_remaining_m >= $min_battery_m
WITH a, d, point.distance(
  point({x: self.x, y: self.y}), point({x: a.x, y: a.y})
) AS distance_m
WHERE distance_m <= $radius_m
RETURN a.drone_id AS drone_id, distance_m,
       a.status AS status, a.queue_len AS queue_len,
       a.battery_remaining_m AS battery_remaining_m
ORDER BY distance_m, queue_len, drone_id
LIMIT $limit;
```

用途：允许跨区域找附近候选，比“同一区域”更符合空间协作。这里限制剩余载荷是立即协助的保守策略，比 can_accept 的仅排队条件更严格；最终是否能接具体任务还需计算取件、送达、返仓距离和已有计划，不能只用 min_battery_m 判定。容量字段缺失时比较为 null，该候选会被排除，应在 i12 之外补配置完整性检查。动态构造 point 不等于已建立空间索引，但现有 run/step 索引可先缩小候选规模。

### i16：跨 step 比较自身变化

参数：run_id、drone_id、step_index、previous_step。

```cypher
OPTIONAL MATCH (now:AgentState {run_id: $run_id, drone_id: $drone_id,
                               step_index: $step_index})
OPTIONAL MATCH (prev:AgentState {run_id: $run_id, drone_id: $drone_id,
                                step_index: $previous_step})
RETURN now IS NOT NULL AND prev IS NOT NULL AS comparable,
       now.time_minute - prev.time_minute AS elapsed_minutes,
       now.queue_len - prev.queue_len AS queue_change,
       now.onboard_count - prev.onboard_count AS onboard_change,
       now.battery_remaining_m - prev.battery_remaining_m AS battery_change_m,
       now.total_distance_m - prev.total_distance_m AS travelled_m,
       prev.status AS previous_status, now.status AS current_status;
```

用途：LLM 直接读变化量，不必从一串轨迹自己计算。previous_step 必须小于 step_index；缺失任一快照时 comparable=false，差值为 null。电量差为正可能是充电，不应一律判异常。total_distance 差是累计路程变化，不是起终点直线位移。

### i17：窗口内有任务但移动很少的候选

参数：run_id、start_step、step_index、movement_epsilon_m、limit。假设每步记录一次且范围连续。

```cypher
MATCH (a:AgentState {run_id: $run_id})
WHERE a.step_index >= $start_step AND a.step_index <= $step_index
WITH a.drone_id AS drone_id,
     count(*) AS state_count, count(DISTINCT a.step_index) AS sampled_steps,
     max(a.queue_len) AS max_queue,
     max(a.onboard_count) AS max_onboard,
     max(a.total_distance_m) - min(a.total_distance_m) AS travelled_m,
     min(a.time_minute) AS first_time, max(a.time_minute) AS last_time
WHERE sampled_steps = $step_index - $start_step + 1
  AND state_count = sampled_steps
  AND (max_queue > 0 OR max_onboard > 0)
  AND travelled_m <= $movement_epsilon_m
RETURN drone_id, sampled_steps, travelled_m, first_time, last_time,
       max_queue, max_onboard
ORDER BY max_queue DESC, drone_id
LIMIT $limit;
```

用途：聚合后过滤，查停滞候选。等待取件、服务停留也可能满足条件，因此需 i18 的事件或调度状态解释，不能直接标记故障。此查询覆盖全体 drone 的时间窗口，比单机查状态更宽，适合异常触发或中央调度共享，不建议 100 个 drone 每次重复全量执行。

### i18：追溯一个任务的事件和参与者

参数：run_id、task_id、start_time、time_minute、limit。

```cypher
MATCH (t:Task {run_id: $run_id, id: $task_id})
OPTIONAL MATCH (t)<-[:ABOUT_TASK]-(e:Event {run_id: $run_id})
WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
OPTIONAL MATCH (e)-[:ABOUT_DRONE]->(d:Drone {run_id: $run_id})
RETURN t.id AS task_id, e.id AS event_id, e.type AS event_type,
       e.time_minute AS time_minute, d.id AS drone_id, e.late AS late
ORDER BY time_minute DESC, event_id
LIMIT $limit;
```

用途：Task ← Event → Drone 的固定两跳路径，保留无窗口内事件的已知 Task，并把事件事实与实体 ID 关联。历史查询不返回 d.status 或 t.status，避免混入它们的当前状态。同一分钟内事件的 ID 只提供稳定排序，UUID 大小不代表真实发生先后；完整因果顺序需写入事件序号。

### i19：某个任务的直达时间下界与截止余量

参数：run_id、drone_id、step_index、task_id、time_minute。

```cypher
MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id,
                     step_index: $step_index})-[:OF_DRONE]->(d:Drone {run_id: $run_id})
MATCH (t:Task {run_id: $run_id, id: $task_id})
WHERE t.release_minute <= $time_minute AND d.speed_m_per_minute > 0
WITH a, d, t, EXISTS {
  MATCH (e:Event {run_id: $run_id, task_id: $task_id,
                  drone_id: $drone_id, type: 'task_picked_up'})
  WHERE e.time_minute <= $time_minute
} AS pickup_observed
WITH a, d, t, pickup_observed,
  CASE WHEN pickup_observed THEN
    point.distance(point({x: a.x, y: a.y}),
                   point({x: t.dropoff_x, y: t.dropoff_y}))
  ELSE
    point.distance(point({x: a.x, y: a.y}),
                   point({x: t.pickup_x, y: t.pickup_y}))
    + point.distance(point({x: t.pickup_x, y: t.pickup_y}),
                     point({x: t.dropoff_x, y: t.dropoff_y}))
  END AS direct_distance_m
WITH t, pickup_observed, direct_distance_m,
     direct_distance_m / d.speed_m_per_minute AS travel_lower_bound_min
RETURN t.id AS task_id, pickup_observed, direct_distance_m,
       travel_lower_bound_min,
       t.deadline_minute - $time_minute - travel_lower_bound_min AS slack_upper_bound_min;
```

用途：形成算术派生风险指标。调用前必须用任务生命周期确认任务未完成、仍与本次 drone 决策相关；若发生转交，曾取件不代表现在仍携带，需额外事件模型。这是直线行驶时间下界，未计其他排队任务、装卸服务、绕行、充电；slack 为正不能保证准时，为负说明在这些距离/速度假设下连直达都无法按时。它不是精确 ETA，也不替代现有 planner。

### i20：低电量时寻找可达的已知仓库位置

参数：run_id、drone_id、step_index、safety_buffer_m、limit。

```cypher
MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id,
                     step_index: $step_index})
MATCH (r:Region {run_id: $run_id})
WHERE r.depot_x IS NOT NULL AND r.depot_y IS NOT NULL
WITH a, r, point.distance(
  point({x: a.x, y: a.y}), point({x: r.depot_x, y: r.depot_y})
) AS distance_m
WHERE distance_m + $safety_buffer_m <= a.battery_remaining_m
RETURN r.id AS region_id, r.depot_x AS depot_x, r.depot_y AS depot_y,
       distance_m,
       a.battery_remaining_m - distance_m - $safety_buffer_m AS remaining_margin_m
ORDER BY distance_m, region_id
LIMIT $limit;
```

用途：返回距离上可达的已知仓库候选。Region 来源于任务事件，不能保证覆盖仿真 depots 列表的全部仓库；图谱也没有充电能力、占用和排队状态，不能将结果描述为“现在可用的充电桩”。需要与运行配置中的 depot 清单核对，并在真实充电调度前补资源数据。

### i21：实时任务是否出现多执行者不一致

仅限实时最新模式；参数：run_id、region_id、limit，不声称对应历史 step。

```cypher
MATCH (d:Drone {run_id: $run_id})-[rel:ASSIGNED|CARRYING]->
      (t:Task {run_id: $run_id, region_id: $region_id})
WHERE t.status IN ['assigned', 'picked_up']
  AND ((type(rel) = 'ASSIGNED' AND t.assigned_to = d.id)
    OR (type(rel) = 'CARRYING' AND rel.active = true))
WITH t, collect(DISTINCT d.id) AS holders
WHERE size(holders) > 1
RETURN t.id AS task_id, t.status AS task_status, holders,
       size(holders) AS holder_count
ORDER BY holder_count DESC, task_id
LIMIT $limit;
```

用途：检查记录中的执行者不一致；排除已完成任务和 inactive 携带关系，避免把保留的历史边直接当成冲突。正常运行可能没有结果；在多事务写入的短暂中间状态也可能触发，需重复确认，不能直接以此自动撤销任务。历史冲突检查需要关系有效期或完整的转交事件。

### i22：相似任务的历史完成案例

参数：run_id、region_id、start_time、time_minute、target_distance_m、distance_tolerance_m、limit。

```cypher
MATCH (e:Event {run_id: $run_id, region_id: $region_id,
                type: 'task_completed'})-[:ABOUT_TASK]->(t:Task {run_id: $run_id})
WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
WITH e, t, point.distance(
  point({x: t.pickup_x, y: t.pickup_y}),
  point({x: t.dropoff_x, y: t.dropoff_y})
) AS task_distance_m
WITH e, t, task_distance_m,
     abs(task_distance_m - $target_distance_m) AS distance_difference_m
WHERE distance_difference_m <= $distance_tolerance_m
RETURN t.id AS task_id, e.drone_id AS drone_id,
       e.time_minute AS completed_time, e.late AS late,
       task_distance_m, distance_difference_m
ORDER BY distance_difference_m, completed_time DESC, task_id
LIMIT $limit;
```

用途：基于结构化特征检索案例，不需要 embedding。相似定义仅限区域、时间窗口、任务距离；没有考虑天气、载荷、策略，因此不能称为全面语义相似，更不能据此作因果推断。当前只记录事实，不包含当时的 LLM 决策理由。

### i23：独立汇总区域人力与事件流量

参数：run_id、region_id、step_index、start_time、time_minute。示例使用目标 Neo4j 支持的变量作用域 CALL 语法。

```cypher
MATCH (r:Region {run_id: $run_id, id: $region_id})
CALL (r) {
  MATCH (a:AgentState {run_id: $run_id, region_id: r.id,
                       step_index: $step_index})
  RETURN count(a) AS agent_count,
         sum(CASE WHEN a.status = 'idle' THEN 1 ELSE 0 END) AS idle_count,
         avg(a.queue_len) AS avg_queue_len
}
CALL (r) {
  MATCH (e:Event {run_id: $run_id, region_id: r.id})
  WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
  RETURN sum(CASE WHEN e.type = 'task_assigned' THEN 1 ELSE 0 END) AS assignments,
         sum(CASE WHEN e.type = 'task_completed' THEN 1 ELSE 0 END) AS completions
}
RETURN r.id AS region_id, agent_count, idle_count, avg_queue_len,
       assignments, completions;
```

用途：分别聚合状态分支和事件分支，再合并成一行。如果直接将 10 条状态与 100 条事件串成两个 MATCH，可能产生 1000 个组合行并重复计数；独立子查询避免这种错误。assignments 是分配流量，不是任务释放需求；缺少释放事件时，不应称为完整供需差。

### 暂不直接落地的类型

| 类型与场景 | 需要补充的模型 | 为什么不能只用现在的数据 |
| --- | --- | --- |
| 可达性、最短路径、受限绕行 | 航路点/区域通行边、方向、距离、禁行时段 | IN_RUN/OF_DRONE 等归属关系没有通行含义；跳数最短也不等于距离/时间最短 |
| 充电资源选择与排队 | Charger/Depot、充电能力、占用、预约和预计释放时间 | 仓库坐标不能说明是否有空闲充电资源 |
| 未来航路冲突与相遇预测 | PlannedRoute/Waypoint、预计到达时间、空间安全约束 | 历史位置快照不能唯一确定下一步计划 |
| 语义检索、决策经验复用 | Decision/Experience、文本/嵌入、执行结果与有效时间 | 当前没有可检索的决策理由或经验文本 |
| 多轮 LLM 决策反馈追溯 | Decision → Action → Outcome 关系、对应 context 与版本 | Event 记录事实，但无法自动推断是哪个 LLM 输出导致 |
| 规则与权限检查 | 能力、禁飞、策略版本和有效期 | 当前速度、容量字段不足以表达完整业务规则 |

### 按场景调用

下列组合为提议，不是现有脚本已注册的查询包。共享前置结果应复用，避免每条模板重复定位自身；性能实验是否允许共享需单独声明。

| 调用触发 | 候选查询组合 | 数据限制 |
| --- | --- | --- |
| 常规状态检查 | i12 + i1 + i3 + i4 | 历史回放需替换 i3 当前任务语义 |
| 空闲/准备接新任务 | i12 + i13 + i14 + i19 | i14 先补任务释放入图，再作为完整任务池使用 |
| 配送途中重规划 | i1 + i16 + i18 + i19 | 准确 ETA 仍交给仿真 planner |
| 电量不足 | i1 + i16 + i20 | 仓库可达不代表充电资源可用 |
| 协作/转交候选 | i12 + i15 + i18 | 再核对具体任务可行性和转交规则 |
| 异常排查 | i16 + i17 + i18 + i21 | i21 仅实时；候选异常需事件解释和再次确认 |
| 区域调度 | i11 + i23 | 全局/区域摘要优先共享，避免每个 drone 重复执行 |
| 参考历史案例 | i22 + 当前任务/状态上下文 | 特征相似仅提供参考，不保证策略有效 |

### 实验覆盖建议

查询类型以外，还应覆盖：空闲与繁忙 drone、低电量与正常电量、空队列与满队列、热点与冷门区域、早期/中期/后期 step、小窗口与大窗口、真实空结果、缺失状态、同时间多事件、正在写入与静态数据、1/10/50/100 并发。

每条查询保留 latency、success、row_count、requested_step、actual_step，以及数据是否完整等信息。复杂图查询适合作为单独压力组，不应为了覆盖类型而在每次实时决策里执行全部查询。

### 执行过的操作

- 阅读当前 i0–i11 模板和 `simulation/knowledge.py` 的实体属性/关系写入。
- 阅读 `simulation/delivery_sim.py` 中 can_accept、can_accept_task、required_battery_for 和欧氏距离计算，避免把简单电量阈值误写成完整任务可行性。
- 查阅 Neo4j 官方 [EXISTS](https://neo4j.com/docs/cypher-manual/current/subqueries/existential/)、[空间函数](https://neo4j.com/docs/cypher-manual/4.4/functions/spatial/)、[CALL 子查询语法](https://neo4j.com/docs/cypher-manual/5/cheat-sheet/)。
- 本轮未连接 Neo4j，未运行这些草案，未进行基准测试。

### 修改内容

仅新增本文档。i12–i23 是方案编号，尚未加入查询脚本。现有 i2 的 BETWEEN 问题也未在本轮修改。

### 后续建议

1. 优先实现并测试 i12、i13、i15、i16，建立数据检查、存在性、空间和差分四类能力。
2. 补任务释放事件/首次可见时间后，再将 i14 用于完整的任务接单场景。
3. i17/i18/i19/i20/i22/i23 按实际场景逐步接入，i21 单独标明实时语义。
4. 对模板逐条运行 EXPLAIN，然后构造有/无结果、字段缺失、时间边界和转交场景验证业务语义；不能只凭查询成功认定语义正确。
5. 后续增加路网、资源占用与决策记录时，再扩展路径类和语义经验检索。
