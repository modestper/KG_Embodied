# AgentState 索引实现原理

### 任务背景

用户希望了解当前项目中的 AgentState 索引是怎样建立的、具体代码实现、使用了什么库，以及该库底层采用什么数据结构。本文以当前工作区尚未提交的 `simulation/knowledge.py` 实现和已有建索引证据为准，同时区分项目代码、Python 驱动和 Neo4j 服务端三层职责。

### 关键结论

- 项目建立的是 Neo4j 节点属性的复合 `RANGE` 索引，不是 Python 字典、查询结果缓存、向量索引或全文索引。
- 项目使用官方 `neo4j` Python Driver；当前环境版本为 `6.2.0`。Driver 只负责连接、Bolt 通信和发送 Cypher，并不在 Python 进程里构造索引树。
- 真正的索引创建、已有数据填充、磁盘持久化、事务内增量更新和查询选择都由 Neo4j 服务端完成。
- 代码定义三组索引：`(run_id, drone_id, step_index)`、`(run_id, region_id, step_index)` 和 `(run_id, step_index)`，分别适配“某无人机历史”“某区域某步”“某 run 某步全部状态”三类查询。
- `CREATE INDEX` 没有显式写 `RANGE`，因为 Neo4j 默认创建 `RANGE` 索引；已有实际证据也显示三组索引均为 `RANGE / ONLINE / 100%`。
- Neo4j 的 RANGE 属性索引属于原生有序 B+Tree/GB+Tree 实现路线：复合属性形成按字段顺序排列的键，叶子记录再指向匹配的节点实体；因此很适合“前几列等值、最后一列范围”的查询。
- 这些索引是非唯一索引，只负责加速，不能替代唯一约束；同一键理论上可以对应多个节点。
- 索引只建立一次。创建时 Neo4j 扫描已有 `AgentState` 完成 population；以后每个事务写入或修改相关属性时，Neo4j 自动同步维护索引，不需要每个 step 重建。

### 具体实现

#### 1. 索引定义

`simulation/knowledge.py` 中的 `AGENT_STATE_INDEXES` 声明了三组复合键：

```python
AGENT_STATE_INDEXES = (
    ("lade_agent_state_run_drone_step", ("run_id", "drone_id", "step_index")),
    ("lade_agent_state_run_region_step", ("run_id", "region_id", "step_index")),
    ("lade_agent_state_run_step", ("run_id", "step_index")),
)
```

第一组可以概念化成下面这种有序目录：

```text
(run-A, drone-1, 2299) -> AgentState 节点
(run-A, drone-1, 2300) -> AgentState 节点
(run-A, drone-2, 2300) -> AgentState 节点
```

当查询条件为 `run_id = run-A AND drone_id = drone-1 AND step_index <= 2300` 时，Neo4j 可以直接定位一个连续键区间，而不用扫描全部 AgentState。字段顺序不是随意的：等值过滤字段放在前面，时间/step 范围字段放在最后，正好符合复合 RANGE 索引的使用方式。

不能只建一个包含所有字段的索引，例如 `(run_id, drone_id, region_id, step_index)`。区域查询没有 `drone_id` 条件，全局单步查询既没有 `drone_id` 也没有 `region_id` 条件，都会缺少复合键的前缀，因此分别准备三组索引更符合真实查询谓词。

#### 2. 启动触发条件

环境构造函数只有在下面两个条件同时成立时才自动检查索引：

```text
KG_RECORD_AGENT_STATE=1
KG_AUTO_CREATE_INDEXES=1   # 默认值为 1
```

调用链为：

```text
build_knowledge_store_from_env()
  -> Neo4jKnowledgeStore(..., auto_create_indexes=True)
    -> __init__()
      -> ensure_agent_state_indexes()
```

`KG_INDEX_WAIT_TIMEOUT_SECONDS` 控制最长等待时间，默认 600 秒；轮询间隔在构造器中默认 1 秒。若不记录 AgentState，项目不会为它自动建索引。

#### 3. 检查、复用和创建

`ensure_agent_state_indexes()` 首先执行：

```cypher
SHOW INDEXES
YIELD name, state, type, entityType, labelsOrTypes, properties, failureMessage
RETURN name, state, type, entityType, labelsOrTypes, properties, failureMessage
```

它不是只按名字判断，而是按以下完整定义寻找等价索引：

- `type == RANGE`
- `entityType == NODE`
- 标签为 `AgentState`
- 属性名称及顺序完全相同

因此数据库中即使已经有一个不同名称但定义相同的索引，也会直接复用。反过来，如果预定名称已经被一个不同定义占用，代码会报错，防止 `IF NOT EXISTS` 悄悄跳过后留下错误结构。等价索引若处于 `FAILED`，也会把 Neo4j 的 `failureMessage` 带入异常。

真正创建时，Python 只是动态拼出并发送下面三条 Cypher：

```cypher
CREATE INDEX lade_agent_state_run_drone_step IF NOT EXISTS
FOR (a:AgentState) ON (a.run_id, a.drone_id, a.step_index);

CREATE INDEX lade_agent_state_run_region_step IF NOT EXISTS
FOR (a:AgentState) ON (a.run_id, a.region_id, a.step_index);

CREATE INDEX lade_agent_state_run_step IF NOT EXISTS
FOR (a:AgentState) ON (a.run_id, a.step_index);
```

Neo4j 中省略 `RANGE` 关键字时，普通 `CREATE INDEX` 默认就是 RANGE。`IF NOT EXISTS` 让重复执行具有幂等性，但项目仍先做定义核验，因为仅靠名称级的 `IF NOT EXISTS` 无法证明现有索引就是所需结构。

#### 4. Neo4j 服务端如何建立

收到 `CREATE INDEX` 后，Neo4j 创建 schema 索引元数据，并对已有的 `AgentState` 节点执行 population。可以把服务端阶段简化成：

```text
扫描已有 AgentState
  -> 读取被索引属性
  -> 跳过缺少索引属性/值为 null 的记录
  -> 编码复合有序键
  -> 批量写入原生 B+Tree/GB+Tree 索引页
  -> 更新统计信息
  -> POPULATING 变为 ONLINE
```

项目不会假设 `CREATE INDEX` 发出后立刻可用，而是重复 `SHOW INDEXES`：全部为 `ONLINE` 才允许构造函数返回；遇到 `FAILED` 立即失败；超过截止时间则抛 `TimeoutError`。这保证仿真开始大量 step 写入前，目标索引已经可供查询和 `MERGE` 使用。

首次在百万级已有数据上创建会花时间，是因为 Neo4j 要为历史节点补齐索引项。索引上线后，新节点和相关属性修改会在写事务中自动更新索引，代价是额外写放大和磁盘空间，但不再需要全量重建。

#### 5. 写入为什么也会受益

每个 step 使用一条 Cypher，通过 `UNWIND $agent_states` 批量写入状态：

```cypher
MERGE (a:AgentState {
  run_id: $run_id,
  drone_id: state.drone_id,
  step_index: state.step_index
})
SET a = state
```

这三个 `MERGE` 字段恰好与第一组复合索引一致。因此数据库在判断“这个 run、drone、step 的快照是否已经存在”时可以进行索引 seek，而不是扫描不断增长的 AgentState 标签。索引随后也在同一事务中加入或更新对应条目。

需要注意：这是普通非唯一索引，不是唯一约束。它显著加快查找，但数据库 schema 并未从根本上禁止重复键；若未来出现多个并发写者且必须强制唯一，应单独评估复合唯一约束。

#### 6. 查询怎样使用

业务 Cypher 不需要写索引名。Neo4j 的 cost planner 根据标签、谓词、索引统计和代价自动决定访问路径。已有 `EXPLAIN/PROFILE` 证据显示目标访问从 `NodeByLabelScan` 变成：

```text
NodeIndexSeek
RANGE INDEX a:AgentState(run_id, drone_id, step_index)
WHERE run_id = $run_id
  AND drone_id = $drone_id
  AND step_index <= $step_index
```

同理，区域查询会使用 `(run_id, region_id, step_index)`，某一步的全局状态会使用 `(run_id, step_index)`。索引只缩小候选集合，不缓存最终查询结果；后续聚合、排序、关系展开仍由执行计划完成。

### 使用的库及底层关系

完整技术栈可以分成五层：

```text
LaDe Python 代码
  -> neo4j Python Driver 6.2.0
    -> Bolt 协议 + Cypher
      -> Neo4j DBMS 的 RANGE index provider
        -> 原生有序 B+Tree/GB+Tree + page cache + 事务日志
```

各层职责：

- 项目代码：决定建哪些索引、检查定义、等待状态和处理错误。
- Python Driver：连接池、认证、参数编码、Bolt 请求/响应和结果消费。
- Cypher：声明 schema 索引以及查询/写入意图。
- Neo4j planner/runtime：决定查询是否使用 `NodeIndexSeek`，执行检索、过滤、排序和图遍历。
- Neo4j storage/index provider：构建并持久化 B+Tree 类索引页，在事务提交时维护条目，借助 page cache 读写磁盘页。

这里没有用 Elasticsearch，也没有用 Faiss/HNSW，更不是 Neo4j 的 full-text/vector index。RANGE 索引按值的有序比较工作；Neo4j 官方资料把原生属性索引描述为 key-value 映射上的 B+Tree，并以 `range-1.0` 展示 RANGE provider。当前项目的历史证据采集了 `type=RANGE`，但没有采集 `indexProvider` 字段；如需对当前运行库严格确认 provider，可执行：

```cypher
SHOW INDEXES
YIELD name, type, state, indexProvider, labelsOrTypes, properties
WHERE name STARTS WITH 'lade_agent_state_'
RETURN *;
```

官方参考：

- [Neo4j Search-performance indexes](https://neo4j.com/docs/cypher-manual/5/indexes/search-performance-indexes/)
- [Neo4j Index configuration](https://neo4j.com/docs/operations-manual/current/performance/index-configuration/)
- [Neo4j 5 index implementation presentation](https://dist.neo4j.com/nodes-20202-slides/076%20Index%20Changes%20in%20Neo4j%205%20-%20NODES2022%20-%20EMEA%20Beginner%203%20-%20Anton%20Persson.pdf)

### 与前端 trace index 的区别

仓库中还有 `scripts/build_trace_index.py`，它会遍历 `data/*.json`，抽取文件名、城市、任务数等元信息，生成 `data/traces.json` 供前端下拉框加载。这个“index”只是 JSON 文件目录，不是树索引，也不参与 Neo4j 查询。本次重点解释的是当前新增并用于百万级 AgentState 查询和写入的 Neo4j 索引。

### 执行过的操作

- 阅读 `simulation/knowledge.py` 的索引常量、`Neo4jKnowledgeStore.__init__()`、`ensure_agent_state_indexes()`、`record_step()` 和环境构造逻辑。
- 阅读 `tests/knowledge_test.py` 的索引模拟 Driver 和四组索引行为测试。
- 阅读 `README.md`、历史建索引说明及 `before/apply/after` 证据。
- 查看当前 Python 驱动版本：`./run_in_lade.sh python -c "import neo4j; print(neo4j.__version__)"`，结果为 `6.2.0`。
- 运行四个索引专项单元测试，结果 `Ran 4 tests ... OK`。
- 查阅 Neo4j 官方 RANGE 索引、复合索引、自动规划和原生 B+Tree 实现资料。
- 尝试只读查看当前 Docker 容器版本，但当前账号无 Docker socket 权限，因此没有把容器镜像版本作为结论。

### 修改内容

仅新增本说明文档，没有修改索引实现、业务代码、数据库 schema 或已有实验结果。工作区中的 `simulation/knowledge.py`、`README.md` 和测试改动在本次任务开始前已经存在，未被覆盖。

### 后续建议

- 如果希望把“底层库版本”精确到当前数据库实例，补跑上面的 `SHOW INDEXES ... indexProvider`，并查询 `CALL dbms.components()`；当前已经能确认索引类型和实际命中方式，但历史证据未保存 provider 字段。
- 使用 `EXPLAIN` 检查规划器选择，使用 `PROFILE` 检查实际 `NodeIndexSeek`、Rows 和 DB Hits；不能只用索引为 `ONLINE` 来证明某条查询真正命中。
- 若业务要求 `(run_id, drone_id, step_index)` 绝对唯一，应评估唯一约束，而不是把普通 RANGE 索引当作数据完整性保障。
- 索引越多写入维护成本越高，后续新增索引应基于真实查询谓词和 PROFILE 证据，而不是为所有属性机械建索引。
