# i0–i11 的 Cypher 逐条讲解

### 任务背景

从 `scripts/lade_llm_context_queries.py` 的具体写法解释 12 条查询如何定位、过滤、关联、聚合和返回数据。以下示例省略部分重复的 RETURN 字段，但保留关键逻辑；i2 单独展示建议修正的语法。这里解释逻辑处理顺序，实际数据库可能通过索引或优化后的执行计划完成，不代表一定逐条扫描。

### 关键结论

- `MATCH` 描述要找的节点或关系模式，`WHERE` 进一步过滤，`RETURN` 决定返回字段。
- `$run_id` 等是 Python 传入的参数；`d`、`a`、`self` 是查询内部变量，`self` 没有 Python 中的特殊含义。
- `WITH` 将中间结果交给下一段，还能排序、截断或聚合；它不会创建节点或关系。
- i0 是等值点查；i1/i9 用上界过滤、倒序和 LIMIT 1 找最近记录；i2/i4/i10 查窗口。
- i3/i4 显式沿关系查；i6/i7/i8/i10/i11 主要用属性值关联节点，即使写了两个 MATCH，也不等于多跳遍历。
- i5/i6/i11 将多条明细压缩成统计；非聚合表达式决定分组，Cypher 不需要另写 SQL 的 GROUP BY。
- i2 当前 BETWEEN 写法需要修正；i0/i3/i8 的可变属性读取不保证还原历史 step，本文没有修改这些代码。

### 先看懂通用写法

```cypher
MATCH (d:Drone {run_id: $run_id, id: $drone_id})
RETURN d.status AS status
LIMIT 1
```

`( ... )` 表示节点，`d` 是给节点起的查询变量名，`:Drone` 是节点标签。花括号要求两个属性同时相等：节点的 run_id 等于传入的 `$run_id`，id 等于 `$drone_id`。例如参数为 run-A 和 drone-7，就只匹配这个 run 中的 drone-7。

`d.status` 读取属性，`AS status` 给返回列命名；不修改数据库字段。`LIMIT 1` 最多返回一行，不保证数据唯一，也不保证只访问一个节点。如果缺少唯一约束且匹配多个节点，没有排序的 LIMIT 1 不保证选择哪一个。

| 写法 | 含义 |
| --- | --- |
| `WHERE x <= $value` | 保留满足比较条件的候选行 |
| `AND` / `<>` | 同时满足 / 不等于 |
| `ORDER BY x DESC` | 从大到小排序 |
| `ORDER BY x ASC` | 从小到大排序 |
| `LIMIT $limit` | 截取前 limit 行；与排序搭配就是 Top-K |
| `(d)-[rel:ASSIGNED]->(t)` | 沿 d 指向 t 的 ASSIGNED 关系匹配，rel 是关系变量 |
| `WITH self` | 只把 self 这个变量传给后面的查询部分 |
| `coalesce(x, y, z)` | 按顺序返回第一个非 null 的值，不是第一个非零或非空字符串 |
| `count(*)` | 统计行数，不自动按 drone_id 去重 |
| `avg(x)` / `max(x)` | 平均值 / 最大值；缺失值不会当成零参与平均 |

### i0：读取单个 drone 当前属性

源代码：`scripts/lade_llm_context_queries.py:33`。

```cypher
MATCH (d:Drone {run_id: $run_id, id: $drone_id})
RETURN d.id AS drone_id,
       d.status AS status,
       d.queue_len AS queue_len,
       d.battery_remaining_m AS battery_remaining_m
LIMIT 1
```

实现过程：通过 run 和 drone ID 找到节点，再投影出状态、队列、电量等属性。这里没有关系模式，所以不用先遍历 Run 节点，run_id 是 Drone 自身保存的属性。

这条没有 step 条件，因此读取节点目前保存的值。即使命令传入 step 100，它也不会自动变成 step 100 的状态。

### i1：找指定 step 之前最近的自身状态

源代码：`scripts/lade_llm_context_queries.py:54`。

```cypher
MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
WHERE a.step_index <= $step_index
RETURN a.step_index AS step_index,
       a.time_minute AS time_minute,
       a.status AS status
ORDER BY a.step_index DESC
LIMIT 1
```

先限定同一 drone 的历史状态，排除指定 step 之后的记录，再按 step 倒序取第一条。假设状态为 98、99、101，传入 100，则过滤后是 98、99，最终返回 99。

所以它的含义是“不晚于指定 step 的最新状态”，不保证恰好在指定 step。返回 step_index 能让调用方知道实际取到了哪一步。

### i2：取最近一段自身轨迹

源代码：`scripts/lade_llm_context_queries.py:78`。当前第 86 行写的是：

```text
WHERE a.step_index BETWEEN $start_step AND $step_index
```

这是当前代码中的语法问题。下面用建议的 Cypher 范围表达式解释原本想实现的逻辑，脚本尚未改动：

```cypher
MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
WHERE a.step_index >= $start_step AND a.step_index <= $step_index
RETURN a.step_index AS step_index,
       a.x AS x,
       a.y AS y,
       a.status AS status
ORDER BY a.step_index DESC
LIMIT $limit
```

例如 start_step=81、step_index=100、limit=10，在每步都有状态的前提下，会返回 100、99、…、91 这 10 行。81–100 是允许匹配的范围，limit 再限制实际输出；不会保证把窗口里所有记录都返回。

返回的是最新到最旧的顺序，LLM 或轨迹展示如需正序，应在调用方反转，或另行调整查询。

范围写法参考：[Neo4j WHERE 文档](https://neo4j.com/docs/cypher-manual/4.4/clauses/where/)。

### i3：沿关系找自身未完成任务，再合并重复项

源代码：`scripts/lade_llm_context_queries.py:99`。

```cypher
MATCH (d:Drone {run_id: $run_id, id: $drone_id})
      -[rel:ASSIGNED|CARRYING]->(t:Task {run_id: $run_id})
WHERE coalesce(t.status, "") <> "completed"
WITH t,
     collect(DISTINCT type(rel)) AS relation_types,
     min(coalesce(rel.time_minute, rel.pickup_time_minute,
                  t.release_minute)) AS relation_time_minute
RETURN t.id AS task_id,
       t.deadline_minute AS deadline_minute,
       relation_types,
       relation_time_minute
ORDER BY t.deadline_minute ASC, relation_time_minute ASC
LIMIT $limit
```

1. `ASSIGNED|CARRYING` 表示关系类型二选一，即已分配或正在携带，并非必须同时有两种关系。
2. `coalesce(t.status, "") <> "completed"` 排除已完成任务。状态缺失时用空字符串代替，因此状态缺失的任务也会被保留。
3. 同一任务可能同时有 ASSIGNED 和 CARRYING 两条边，MATCH 会产生两行。`WITH t, ...聚合...` 按任务节点 t 分组，把它们合并成一行。
4. `type(rel)` 取关系类型名，`DISTINCT` 去重，`collect` 收集为列表，例如 `["ASSIGNED", "CARRYING"]`，列表顺序没有保证。
5. `coalesce` 为每条关系找一个可用时间；外层 `min` 取该任务各关系时间的最小值，并非当前动作发生时间。
6. 最后优先返回截止时间较早的任务；截止时间相同，再比较关系时间。

例如任务 T1 先在 590 分钟分配、595 分钟取件，最终是一行 T1，关系类型包含两种，relation_time_minute 为 590。

当前条件不检查 `rel.active`，也没有历史时间上界；Task.status 是当前值，不能直接还原过去的任务列表。

### i4：查询自身近期事件，顺带找任务

源代码：`scripts/lade_llm_context_queries.py:127`。

```cypher
MATCH (e:Event {run_id: $run_id, drone_id: $drone_id})
WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
OPTIONAL MATCH (e)-[:ABOUT_TASK]->(t:Task)
RETURN e.time_minute AS time_minute,
       e.type AS event_type,
       t.id AS task_id
ORDER BY e.time_minute DESC
LIMIT $limit
```

先按 drone 和闭区间时间窗口找事件，然后沿 ABOUT_TASK 找关联任务。OPTIONAL MATCH 的关键是：事件没有关联任务时，也保留这条事件，task_id 返回 null；如果用普通 MATCH，这类事件会被过滤掉。

例如充电类事件可以没有任务关联，但依然属于 drone 的近期经历。LIMIT 限制最终结果行数；如果数据里一个事件关联了多个任务，会产生多行，不能无条件把它理解成事件数限制。

### i5：把事件列表变成计数摘要

源代码：`scripts/lade_llm_context_queries.py:149`。

```cypher
MATCH (e:Event {run_id: $run_id, drone_id: $drone_id})
WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
RETURN sum(CASE WHEN e.type = "task_assigned" THEN 1 ELSE 0 END) AS assigned,
       sum(CASE WHEN e.type = "task_picked_up" THEN 1 ELSE 0 END) AS picked_up,
       sum(CASE WHEN e.type = "task_completed" THEN 1 ELSE 0 END) AS completed,
       sum(CASE WHEN coalesce(e.late, false) THEN 1 ELSE 0 END) AS late,
       count(e) AS total_events
```

`CASE WHEN ... THEN 1 ELSE 0 END` 将每条事件转成“符合条件记 1，否则记 0”，sum 将这些数累加。

假设窗口里 3 次分配、2 次取件、1 次完成且该完成超时，另有 1 次充电事件，则 assigned=3、picked_up=2、completed=1、late=1、total_events=7。late 是额外维度，不能与前三项相加当成总数。

RETURN 全部是聚合表达式，没有分组字段，所以得到整段窗口的一行统计。i4 给事件明细，i5 给压缩摘要；它们是两次独立查询，i5 没有复用 i4 的结果，也不受 i4 的 LIMIT 限制。

### i6–i8、i10 共用的前半段

```cypher
MATCH (self:AgentState {run_id: $run_id, drone_id: $drone_id})
WHERE self.step_index <= $step_index
WITH self
ORDER BY self.step_index DESC
LIMIT 1
```

这段先选出自身最近的一条状态。WITH 把这条状态绑定在变量 self 上交给后面；下一段就能读取 `self.region_id` 和 `self.step_index`。

这里 LIMIT 1 只限制中间自身状态，不会让整条查询最后只能返回一行。后面的 MATCH 可以再次扩展出许多行。没有找到 self 时，后面的普通 MATCH 也没有输入，通常不会得到区域结果。

### i6：按本区域、同一步的状态分组统计

源代码：`scripts/lade_llm_context_queries.py:165`。接在上述公共前缀后：

```cypher
MATCH (a:AgentState {
  run_id: $run_id,
  region_id: self.region_id,
  step_index: self.step_index
})
RETURN self.step_index AS step_index,
       self.region_id AS region_id,
       a.status AS status,
       count(*) AS agent_count,
       avg(a.queue_len) AS avg_queue_len,
       max(a.queue_len) AS max_queue_len,
       avg(a.battery_remaining_m) AS avg_battery_remaining_m
ORDER BY agent_count DESC
```

第二个 MATCH 用 self 的属性值匹配其他状态，并没有沿一条关系边走。若 self 是区域 R2、step 100，就匹配该 run 的 R2 区域在 step 100 的全部状态，包含自身。

RETURN 中 step、region、status 是非聚合字段，构成分组键。self 已固定为一行，所以实际是在同一区域和同一步内按 status 分组。例如 delivering 一行、idle 一行，每行带该组数量、平均队列和最大队列。

如果 self 回退到 step 99，第二个 MATCH 也查 step 99。count(*) 数的是状态记录，只有每个 drone 每步唯一时才等于 drone 数。

### i7：返回本区域其他 drone 的候选列表

源代码：`scripts/lade_llm_context_queries.py:188`。公共前缀之后：

```cypher
MATCH (a:AgentState {
  run_id: $run_id,
  region_id: self.region_id,
  step_index: self.step_index
})
WHERE a.drone_id <> $drone_id
RETURN a.drone_id AS drone_id,
       a.status AS status,
       a.queue_len AS queue_len,
       a.battery_remaining_m AS battery_remaining_m
ORDER BY a.queue_len ASC, a.battery_remaining_m DESC
LIMIT $limit
```

与 i6 的匹配范围类似，但排除了自己，不做聚合，直接返回其他 drone 的明细。

两个排序字段有先后优先级：先比较队列长度，只有队列相同才比较电量。例如队列 0、电量 1000 的 drone 排在队列 1、电量 5000 的前面，不是将两个指标加权打分。

这条只说明“同一区域、队列短者优先”，没有距离计算，没有保证候选处于可接单状态。

### i8：返回本区域已知的紧急未完成任务

源代码：`scripts/lade_llm_context_queries.py:213`。公共前缀之后：

```cypher
MATCH (t:Task {run_id: $run_id, region_id: self.region_id})
WHERE coalesce(t.status, "") <> "completed"
  AND t.release_minute <= $time_minute
  AND t.deadline_minute <= $time_minute + $deadline_horizon_minutes
RETURN t.id AS task_id,
       t.status AS task_status,
       t.deadline_minute AS deadline_minute,
       t.assigned_to AS assigned_to
ORDER BY t.deadline_minute ASC, t.release_minute ASC
LIMIT $limit
```

三个条件共同成立才保留：目前不是 completed、查询时刻已经释放、截止时间不晚于“查询时刻 + 预警窗口”。

例如查询时刻 600、预警窗口 60，则截止时间 650 的任务可能被选中，700 的不会；截止时间 590 的也会被选中，因为没有设置截止时间下界，逾期未完成任务也在范围内。

未完成不等于未分配，因此已交给其他 drone 的任务也可能出现。它使用 self 的区域，但 Task.status 是当前属性；历史回放时会有时间混用问题。

### i9：读取最近一条全局 Step 摘要

源代码：`scripts/lade_llm_context_queries.py:244`。

```cypher
MATCH (s:Step {run_id: $run_id})
WHERE s.step_index <= $step_index
RETURN s.step_index AS step_index,
       s.completed_tasks AS completed_tasks,
       s.unfinished_tasks AS unfinished_tasks,
       s.idle_drones AS idle_drones,
       s.total_distance_m AS total_distance_m
ORDER BY s.step_index DESC
LIMIT 1
```

与 i1 相同的“上界 + 倒序 + 取一条”结构，只是查询对象由单个 drone 的 AgentState 变成整个 run 的 Step。

RETURN 没有 count/sum 等聚合，它直接读取写入端提前计算好的属性，不是在查询时统计全部任务。当前 completed_tasks 的写入含义是本步完成数，不是累计完成数。

### i10：读取本区域近期事件

源代码：`scripts/lade_llm_context_queries.py:267`。公共前缀之后：

```cypher
MATCH (e:Event {run_id: $run_id, region_id: self.region_id})
WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
RETURN e.time_minute AS time_minute,
       e.type AS event_type,
       e.drone_id AS drone_id,
       e.task_id AS task_id
ORDER BY e.time_minute DESC
LIMIT $limit
```

i4 用 drone_id 限定“我自己的事件”；i10 用 self.region_id 限定“我所在区域的所有 drone 的事件”。自身事件也可能包含在内。

这里直接读 `e.task_id`，没有像 i4 那样沿 ABOUT_TASK 关系读取 `t.id`。两种写法依赖不同的数据表示；当前事件同时保存任务 ID 属性和关联关系，因而具有对照价值。

### i11：按区域汇总一个 step，再取负载排名

源代码：`scripts/lade_llm_context_queries.py:291`。

```cypher
MATCH (s:Step {run_id: $run_id})
WHERE s.step_index <= $step_index
WITH s
ORDER BY s.step_index DESC
LIMIT 1
MATCH (a:AgentState {run_id: $run_id, step_index: s.step_index})
RETURN a.region_id AS region_id,
       count(*) AS agent_count,
       sum(CASE WHEN a.status = "delivering" THEN 1 ELSE 0 END) AS delivering,
       sum(CASE WHEN a.status = "idle" THEN 1 ELSE 0 END) AS idle,
       avg(a.queue_len) AS avg_queue_len,
       max(a.queue_len) AS max_queue_len,
       avg(a.battery_remaining_m) AS avg_battery_remaining_m
ORDER BY max_queue_len DESC, agent_count DESC
LIMIT $limit
```

先选一条 Step，再匹配该 step 的全部 AgentState。RETURN 中只有 region_id 是非聚合字段，所以每个区域形成一行，其余字段在区域内统计。

与 i6 不同：i6 固定一个区域后按状态分组；i11 覆盖所有区域，按区域分组，并在一行内用 CASE 分别算 delivering 和 idle 数量。

末尾 LIMIT 限制返回区域数，不是匹配的 drone 数。第一个 LIMIT 1 选 Step，第二个 LIMIT 选区域排名，两者作用阶段不同。

排序依据是区域内的最大单机队列，其次才是 agent_count。例如一个区域只有一架 drone 队列特别长，也可能排第一；它是一个负载指标，不等于完整的区域拥堵判定。

### Python 如何传参和执行

`build_params()` 在脚本第 461 行开始构造参数。例如 step_index=100、history_steps=20，得到 start_step=81。实际执行需要时间的模板时，脚本先按 run 和精确 step 查询对应 time_minute，再减去事件窗口得到 start_time；step 编号本身不能直接当成分钟。

执行核心是：

```python
rows = [row_to_dict(row) for row in session.run(query.cypher, **params)]
```

Cypher 模板和参数分开传给驱动，`$drone_id` 等由参数绑定，不是手工字符串替换。每个 drone 的一包查询内部逐条执行，并分别计时；不同 drone 的包可以在线程池中并发。

各模板除共享传入参数外，没有自动共享查询结果。比如 i6 会自己查 self，不会自动使用 i1 已经返回的那条状态。

### 执行过的操作

- 使用 sed 阅读 `scripts/lade_llm_context_queries.py` 的 i0–i11 模板、查询包、参数构造及 Step 时间解析函数。
- 结合上一轮已核对的图谱写入语义，解释当前属性和历史快照的区别。
- 仅进行代码阅读和文档编写，未访问 Neo4j、未运行查询或压测。

### 修改内容

仅新增本文档；未修改查询脚本。文中省略字段的代码片段用于讲解，i2 的建议修正也尚未应用到源码。

### 后续建议

- 阅读时优先看 MATCH 和 WHERE 确定数据范围，再看 WITH 是否改变分组或候选数量，最后看 RETURN、ORDER BY 和 LIMIT。
- 正式运行包含 i2 的查询包前，先修正其范围语法并用目标数据库验证。
- 若将结果送入 LLM，保留实际 step/time，明确 current 属性与历史状态的时间语义。
