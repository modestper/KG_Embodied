### 任务背景

用户希望基于 WatDiv、LDBC SNB、FEASIBLE、LSQ、gMark、FinBench 等查询工作负载设计思想，为当前 LaDe/Neo4j 知识图谱设计一组 Cypher 查询模板，并说明各类查询的特点。

### 关键结论

- 当前第一版查询集建议覆盖 12 类：规模统计、点查、单 agent 轨迹、step 快照、事件时间窗口、任务生命周期、时间序列聚合、区域热力、Top-K、复杂 join、路径查询、异常/反连接查询。
- 这些查询分别对应 WatDiv 的结构多样性、LDBC 的交互式/BI 工作负载、FEASIBLE/LSQ 的特征筛选、gMark 的路径复杂度、FinBench 的时间窗口和热点访问。
- 每条查询都应带 `run_id`，避免跨 run 混查。
- 每类查询至少准备低/中/高三档参数，例如单 step、小窗口、大窗口、全 run。
- 100 agent 并发测试时，不建议 100 个 agent 都跑同一条查询；应混合点查、轨迹、聚合、路径和 Top-K 查询。

### 执行过的操作

- 读取 `simulation/knowledge.py`，确认当前 Neo4j 写入的 label、关系和字段：
  - Label：`Run`、`Step`、`AgentState`、`Drone`、`Task`、`Event`、`Region`
  - 关系：`IN_RUN`、`IN_STEP`、`OF_DRONE`、`ABOUT_DRONE`、`ABOUT_TASK`、`IN_REGION`、`BELONGS_TO`、`ASSIGNED`、`CARRYING`、`COMPLETED`
  - 关键字段：`run_id`、`step_index`、`time_minute`、`drone_id`、`region_id`、`status`、`queue_len`、`onboard_count`、`battery_remaining_m`、`total_distance_m`
- 参考前面调研的查询工作负载设计思路，整理当前项目可用的 Cypher 查询模板。

### 修改内容

本次没有修改运行代码，只新增本查询设计笔记。

### 查询模板

#### Q0 规模统计查询

```cypher
MATCH (n)-[:IN_RUN]->(:Run {id: $run_id})
RETURN labels(n) AS labels, count(*) AS cnt
ORDER BY cnt DESC
```

特点：用于确认某个 run 下各类节点规模，适合作为压测前的 sanity check。它不代表业务查询，但能帮助判断百万级数据是否真的写入到了目标 run。

#### Q1 Run 点查

```cypher
MATCH (r:Run {id: $run_id})
RETURN r.id AS run_id
```

特点：最轻量查询，用来测基础连接、Bolt 往返、driver 开销和索引命中情况。

#### Q2 Drone 点查

```cypher
MATCH (d:Drone {run_id: $run_id, id: $drone_id})-[:IN_RUN]->(:Run {id: $run_id})
RETURN d.id AS drone_id,
       d.status AS status,
       d.region_id AS region_id,
       d.queue_len AS queue_len,
       d.onboard_count AS onboard_count,
       d.battery_remaining_m AS battery_remaining_m,
       d.total_distance_m AS total_distance_m
```

特点：典型交互式短读查询，返回结果很小。适合测试高选择性点查和索引效果。

#### Q3 单 agent 时间窗口轨迹查询

```cypher
MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
WHERE a.step_index BETWEEN $start_step AND $end_step
RETURN a.step_index AS step_index,
       a.time_minute AS time_minute,
       a.x AS x,
       a.y AS y,
       a.status AS status,
       a.queue_len AS queue_len,
       a.battery_remaining_m AS battery_remaining_m
ORDER BY a.step_index
```

特点：对应 FinBench 的时间窗口思想，也对应真实 agent 查询历史状态。窗口越大，返回行数越多，能测时间过滤和排序成本。

#### Q4 单 step 快照星型查询

```cypher
MATCH (s:Step {run_id: $run_id, step_index: $step_index})<-[:IN_STEP]-(a:AgentState)
RETURN s.step_index AS step_index,
       a.status AS status,
       count(*) AS agent_count,
       avg(a.queue_len) AS avg_queue_len,
       avg(a.onboard_count) AS avg_onboard_count,
       avg(a.battery_remaining_m) AS avg_battery_remaining_m
ORDER BY agent_count DESC
```

特点：对应 WatDiv 的 star query。中心是一个 `Step`，向外展开多个 `AgentState`。如果是 100 agent，一个 step 大约 fan-out 100 条。

#### Q5 事件时间窗口查询

```cypher
MATCH (e:Event {run_id: $run_id})-[:ABOUT_DRONE]->(d:Drone)
WHERE e.time_minute >= $start_time AND e.time_minute < $end_time
OPTIONAL MATCH (e)-[:ABOUT_TASK]->(t:Task)
RETURN e.time_minute AS time_minute,
       e.type AS event_type,
       d.id AS drone_id,
       t.id AS task_id,
       e.region_id AS region_id
ORDER BY e.time_minute
LIMIT $limit
```

特点：时间窗口 + 可选任务关联，适合测事件日志查询。`OPTIONAL MATCH` 会覆盖存在无任务事件的情况。

#### Q6 单 drone 任务生命周期查询

```cypher
MATCH (d:Drone {run_id: $run_id, id: $drone_id})-[rel:ASSIGNED|COMPLETED]->(t:Task {run_id: $run_id})
RETURN t.id AS task_id,
       t.region_id AS region_id,
       type(rel) AS relation_type,
       rel.time_minute AS event_time_minute,
       t.release_minute AS release_minute,
       t.deadline_minute AS deadline_minute,
       t.status AS task_status,
       t.late AS late
ORDER BY event_time_minute
LIMIT $limit
```

特点：业务语义强，适合 agent 查询自己处理过哪些任务。它测的是从 agent 到任务的关系展开，以及按时间排序。

#### Q7 AgentState 时间序列聚合查询

```cypher
MATCH (a:AgentState {run_id: $run_id})
WHERE a.step_index BETWEEN $start_step AND $end_step
RETURN a.step_index AS step_index,
       a.status AS status,
       count(*) AS cnt,
       avg(a.queue_len) AS avg_queue_len,
       max(a.queue_len) AS max_queue_len,
       avg(a.battery_remaining_m) AS avg_battery
ORDER BY step_index, status
```

特点：对应 LDBC BI 的聚合类查询。窗口越大，扫描的 `AgentState` 越多，适合测试大范围扫描、group by 和排序。

#### Q8 区域负载热力查询

```cypher
MATCH (a:AgentState {run_id: $run_id})
WHERE a.step_index BETWEEN $start_step AND $end_step
RETURN a.region_id AS region_id,
       count(*) AS samples,
       count(DISTINCT a.drone_id) AS drones,
       avg(a.queue_len) AS avg_queue_len,
       avg(a.onboard_count) AS avg_onboard_count
ORDER BY samples DESC
LIMIT $limit
```

特点：区域维度聚合，适合分析哪些区域无人机活动更密集。它能覆盖 group by、distinct、top-k 和时间窗口。

#### Q9 Top-K 繁忙 drone 查询

```cypher
MATCH (d:Drone {run_id: $run_id})-[c:COMPLETED]->(t:Task {run_id: $run_id})
RETURN d.id AS drone_id,
       count(t) AS completed_tasks,
       sum(CASE WHEN coalesce(c.late, false) THEN 1 ELSE 0 END) AS late_tasks
ORDER BY completed_tasks DESC, late_tasks DESC
LIMIT $limit
```

特点：典型 Top-K 查询，适合测试排序、聚合和热点 agent。若任务分布不均，热点节点会更明显。

#### Q10 事件-无人机-区域复杂 join 查询

```cypher
MATCH (:Run {id: $run_id})<-[:IN_RUN]-(e:Event)-[:ABOUT_DRONE]->(d:Drone)
MATCH (e)-[:IN_REGION]->(region:Region)
WHERE e.time_minute >= $start_time AND e.time_minute < $end_time
OPTIONAL MATCH (e)-[:ABOUT_TASK]->(t:Task)
RETURN region.id AS region_id,
       e.type AS event_type,
       d.status AS drone_status,
       count(e) AS events,
       count(DISTINCT d) AS drones,
       count(DISTINCT t) AS tasks
ORDER BY events DESC
LIMIT $limit
```

特点：对应 WatDiv complex query 和 LDBC BI join-heavy 查询。它会同时触碰 `Run/Event/Drone/Region/Task`，适合测多关系 join。

#### Q11 路径查询

```cypher
MATCH p=(e:Event {run_id: $run_id})-[:ABOUT_DRONE]->(d:Drone)-[:ASSIGNED|COMPLETED]->(t:Task)-[:BELONGS_TO]->(region:Region)
WHERE e.time_minute >= $start_time
  AND e.time_minute < $end_time
  AND e.region_id = t.region_id
RETURN d.id AS drone_id,
       region.id AS region_id,
       count(DISTINCT t) AS related_tasks,
       count(DISTINCT e) AS events,
       min(length(p)) AS path_len
ORDER BY events DESC
LIMIT $limit
```

特点：对应 gMark 的路径查询思想。它测试多跳路径展开、路径过滤和中间结果大小。时间窗口越大，成本越高。

#### Q12 未完成或异常任务反连接查询

```cypher
MATCH (t:Task {run_id: $run_id})
WHERE NOT EXISTS {
  MATCH (:Drone {run_id: $run_id})-[:COMPLETED]->(t)
}
RETURN t.status AS task_status,
       count(*) AS task_count
ORDER BY task_count DESC
```

特点：反连接/不存在性查询，用来找没有 `COMPLETED` 关系的任务。它适合测试 `NOT EXISTS`、模式过滤和任务完整性检查。

### 查询类别特点

- 点查类：Q1、Q2。结果少，延迟主要受索引、连接池和 Bolt 往返影响。
- 时间窗口类：Q3、Q5、Q7、Q8、Q10、Q11。最适合模拟 agent 查询近期历史；窗口大小决定查询选择性。
- star 类：Q4。中心节点向外展开，适合测试单个 step 下 100 个或更多 agent 状态的读取。
- 轨迹类：Q3。面向单 agent 历史，常用于智能体回顾自身状态。
- 聚合类：Q7、Q8、Q9、Q10。偏 BI，适合测试大范围扫描、group by、distinct、order by、limit。
- Top-K 类：Q8、Q9、Q10、Q11。适合测试排序和热点数据分布。
- 复杂 join 类：Q10。覆盖多个 label 和关系，能暴露 join 顺序和中间结果膨胀问题。
- 路径类：Q11。覆盖多跳遍历，适合测试图数据库核心路径能力。
- 反连接类：Q12。用于查异常或缺失关系，能测试 `NOT EXISTS` 这类模式过滤。

### 参数档位建议

- 低选择性成本小：`step_index` 单点，`drone_id` 单点，`limit=20`。
- 中等规模：`start_step=1000`、`end_step=1500`，窗口 500 step。
- 高成本：`start_step=1`、`end_step=10000`，全 run 或接近全 run。
- 时间窗口可以按 `step_seconds` 换算：如果 `step_seconds=6`，1000 step 约等于 100 分钟。

### 后续建议

- 第一轮先跑 Q0-Q9，确认基本延迟分布。
- 第二轮再跑 Q10-Q12，观察复杂 join、路径查询和反连接是否成为慢查询。
- 100 agent 并发测试时，可以按比例混合：点查 30%、轨迹 25%、step 快照 15%、事件窗口 10%、聚合 10%、Top-K 5%、路径/反连接 5%。
