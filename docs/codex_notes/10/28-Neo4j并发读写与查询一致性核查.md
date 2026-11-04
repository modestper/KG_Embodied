# Neo4j 并发读写与查询一致性核查

### 任务背景

用户询问主进程中的自助查询与无人机进程自主写入 Neo4j 是否产生读写冲突。本轮只读核查现有实现和官方事务说明，未连接数据库、运行模拟或修改功能代码。

### 关键结论

- 可以并发运行，不能简单理解为“一读一写就冲突”。自助查询为只读事务，不会覆盖无人机写入的数据，但只读不代表获得整场模拟的原子快照。
- `simulation/graph_query.py` 使用独立 driver/session、`READ_ACCESS`、显式事务、EXPLAIN 查询类型校验及最终 rollback。它与写入会话分离，查询路径没有向图中写状态。
- Neo4j 默认采用 read-committed 隔离。读取不会天然阻止其他事务更新同一数据，也不保证同一查询内多次读取可变属性都一致。数据库事务边界与一次模拟 Step 的边界不是同一个概念。
- 在无人机自主写入模式下，各 actor 分别提交状态；主进程等待 `ray.get()` 返回后再汇总记录 Step。直接扫描最新 `Drone`、`Task` 或尚未关联完整 Step 的 `AgentState`，可能看到不同推进阶段的数据。这些可以全部是已提交的数据，并非读取未提交事务。
- `record_drone_state()` 中更新 `Drone` 与保存 `AgentState` 使用两个 `session.run(...).consume()` 自动提交事务，也不构成单个原子更新。`record_step_data(..., external_agent_states=True)` 才将已有状态关联到 Step。
- 多个写入事务若修改共同节点或关系，仍可能出现锁等待乃至死锁；这属于写入之间的竞争，不能都归因于主进程自助查询。大型只读扫描也会争用 CPU、内存、页缓存和磁盘，使写入及模拟变慢。
- 推荐用固定 `run_id`、固定已提交 `step_index`，通过 `AgentState-[:IN_STEP]->Step` 查询历史快照并校验无人机状态数量。混入持续更新的 `Drone`/`Task` 属性，会削弱快照含义。
- 当前“暂停仿真”保证不再推进下一步，但未在暂停边界专门等待异步 writer 的 `flush()`。写入队列仍可能排空；不能将按钮提示“已暂停”直接解释为数据库已经完全静止。

### 执行过的操作

- 使用 `rg` 和 `sed` 阅读 `simulation/graph_query.py` 中查询会话、事务及结果消费方式。
- 阅读 `simulation/ray_delivery_sim.py` 的 actor `step()`、主进程 `step_once()` 及写入排序。
- 阅读 `simulation/knowledge.py` 的 `record_drone_state()` 和 `record_step_data()`。
- 阅读 `simulation/knowledge_writer.py` 的委托写入、`record_step()` 入队与 `flush()`。
- 核对启动脚本的 `KG_WRITE_MODE`、`KG_STATE_SOURCE` 参数入口，未推断当前运行实例必然启用了无人机写库模式。
- 查阅 [Neo4j 官方并发访问文档](https://neo4j.com/docs/operations-manual/current/database-internals/concurrent-data-access/)，核对隔离级别、非重复读与写锁。

### 修改内容

仅新增本核查记录。未修改查询、写入、暂停功能或现有实验配置。

### 后续建议

- 日常观察可以直接并发查询；需要可比结果时，先固定运行和已提交的 Step，查询历史状态，避免将当前可变属性当作该 Step 的属性。
- 如果需要“点暂停后数据库稳定可查”的严格语义，可在 Step 边界补充 writer 排空确认，再向前端确认数据库已同步；本轮没有实施该改动。
- 若已经出现异常，应区分结果不完整、查询变慢、锁等待和实际 deadlock/timeout，再结合事务与日志定位。当前核查没有证据证明运行中的数据库已经发生死锁。
