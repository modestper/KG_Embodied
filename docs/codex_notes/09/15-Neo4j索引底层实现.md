# Neo4j 索引底层实现说明

### 任务背景

用户希望了解当前 LaDe 仿真使用的 Neo4j 原生属性索引在底层采用什么数据结构，以及复合索引如何完成查询和增量维护。

### 关键结论

- 当前三个 `AgentState` 索引均为 `RANGE` 类型，实际 provider 是 `range-1.0`。
- Neo4j 官方资料将 `range-1.0` 描述为内部 B-tree；Neo4j 5 的官方技术演示进一步说明其为支持范围扫描的 B+Tree。
- Neo4j 2026.05 开源代码包含通用 `GBPTree` 组件，即带 generation-safe 机制的 Neo4j 原生 B+Tree 实现。`RANGE` 是对外能力类型，`GBPTree` 是内部存储组件，不能把两者当成同一层概念。
- B+Tree 的内部页保存分隔键和子页指针，叶子页按键排序保存索引项；精确查询从根逐层定位到叶子，范围查询定位起点后按顺序扫描叶子项。
- 复合索引按声明顺序形成有序元组。例如 `(run_id, drone_id, step_index)` 先按 `run_id`，再按 `drone_id`，最后按 `step_index` 排序，因此适合先限定 run 和 drone，再按 step 精确查询或范围扫描。
- 新增 `AgentState` 时，Neo4j 在事务提交过程中同步维护相关索引；叶子页空间不足时会发生页分裂并向上更新分隔键。应用代码不需要自行维护树。
- 属性索引主要负责快速找到图遍历的起始节点；找到节点后的关系扩展仍使用 Neo4j 的图存储和邻接访问机制，并非所有遍历都在 B+Tree 中完成。

### 执行过的操作

- 连接本机 `bolt://127.0.0.1:17687`，执行 `SHOW INDEXES` 并读取 `name`、`type`、`indexProvider`、标签和属性。
- 核对 Neo4j 官方 Operations Manual、Cypher Manual、GraphAcademy 和 Neo4j 2026.05 开源仓库中的 `gbptree` 目录。
- 确认当前索引：
  - `lade_agent_state_run_drone_step`: `(run_id, drone_id, step_index)`
  - `lade_agent_state_run_region_step`: `(run_id, region_id, step_index)`
  - `lade_agent_state_run_step`: `(run_id, step_index)`

### 修改内容

未修改仿真代码或 Neo4j 数据库结构，仅新增本说明文档。

### 后续建议

- 使用 `PROFILE` 检查查询计划是否出现 `NodeIndexSeek`、`NodeIndexRangeSeek` 等算子，并确认 `Details` 中列出预期索引属性。
- 复合索引查询尽量满足左前缀：优先提供 `run_id`，再提供 `drone_id` 或 `region_id`，最后对 `step_index` 做精确或范围条件。
- 百并发测试时同时观察 page cache 命中率、Bolt 连接池和磁盘 I/O，索引只能减少扫描量，不能消除并发排队和结果集传输成本。
