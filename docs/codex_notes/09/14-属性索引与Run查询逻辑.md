# 属性索引与 Run 查询逻辑

### 任务背景

解释当前查询是否先找 Run 再找 Drone，以及准备怎样通过属性索引加速 LLM 前置查询。本次仅说明方案，不创建索引或改变实验基线。

### 关键结论

- 查询在连接所选的一个 Neo4j 数据库中执行，不会自动搜索同一服务器上的所有数据库。
- Run 是普通节点，不是节点容器、数据库分区或文件夹；IN_RUN 表示图中的归属关系。
- 当前 i1 等模板直接匹配 AgentState 的 run_id/drone_id 属性，不经过 Run 节点，也不经过 Drone 节点。
- 上轮计划确认当前 i1 扫描该数据库全部 AgentState 标签节点后过滤；这不是扫描全部标签，也不是返回所有 run 的结果。
- 计划优先建立三组 AgentState 复合属性索引，分别支持自身历史、区域单步状态和全局单步状态。
- 索引不改变逻辑结果或图关系；优化器可自动选择索引访问路径，但必须通过 EXPLAIN/PROFILE 验证。
- 索引是一次建立、持续维护的数据库结构，不需要每个 run、每次查询或每次新增 step 重建；需要额外空间和写入维护成本。

### 当前查询实际怎样工作

```cypher
MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
WHERE a.step_index <= $step_index
RETURN a.step_index AS step_index, a.status AS status
ORDER BY a.step_index DESC
LIMIT 1
```

`a` 是 AgentState 节点；`a.run_id`、`a.drone_id`、`a.step_index` 都是这个节点上的属性。这里的 drone_id 不是需要再遍历一次才能找到的节点。Drone 标签节点通常有 id 属性；AgentState 用 drone_id 属性标识其对应 drone，并通过 OF_DRONE 关系连接它。标识相同不意味着查询自动沿关系访问。

没有合适属性索引时，上轮看到的实际计划为：

```text
所选数据库的全部 AgentState 标签记录
  -> 检查 run_id、drone_id、step_index
  -> Top-1 选最大 step
  -> 返回属性
```

因此要区分“结果范围”和“访问范围”：结果限定某 run 的某 drone，但为了找到它，当前计划可能读取其他 run 的状态。run_id 属性不会自动实现分区隔离。

### 与从 Run 沿关系查的区别

确实可以写沿归属关系匹配的另一种查询：

```cypher
MATCH (r:Run {id: $run_id})<-[:IN_RUN]-(a:AgentState)
WHERE a.drone_id = $drone_id AND a.step_index <= $step_index
RETURN a.step_index AS step_index, a.status AS status
ORDER BY a.step_index DESC
LIMIT 1
```

它描述的是 Run 与 AgentState 的关系模式。如果采用 Run 起点计划，可以先通过 Run.id 索引找到 Run，再扩展 IN_RUN 关系。实际物理起点由优化器选择，Cypher 从左往右书写不强制执行从左往右遍历。

仅对 Run.id 建索引，通常不能消除展开一个百万状态 run 的大量关系和候选过滤工作。当前直接使用 AgentState 复合索引，可以把 run、drone、step 条件放在同一个索引检索中。

属性过滤和 IN_RUN 关系过滤只有在写入数据一致时才表达同样的归属。属性与关系不同步时，结果可能不同，不能把两种写法视为数据库自动保证等价。

### 优先建立的三组索引

以下为计划命令，尚未执行。

```cypher
CREATE INDEX lade_agent_state_run_drone_step IF NOT EXISTS
FOR (a:AgentState) ON (a.run_id, a.drone_id, a.step_index);

CREATE INDEX lade_agent_state_run_region_step IF NOT EXISTS
FOR (a:AgentState) ON (a.run_id, a.region_id, a.step_index);

CREATE INDEX lade_agent_state_run_step IF NOT EXISTS
FOR (a:AgentState) ON (a.run_id, a.step_index);
```

| 索引字段 | 作用 | 主要查询 |
| --- | --- | --- |
| run_id, drone_id, step_index | 找某 run 中某 drone 的历史状态范围或最近状态 | i1/i2，以及 i6/i7/i8/i10 的 self 部分 |
| run_id, region_id, step_index | 找某 run 中某区域在某一步的状态 | i6/i7 的第二段 |
| run_id, step_index | 找某 run 在某一步的全部状态 | i11 |

第一组是一个包含三个属性的复合 RANGE 索引，不是三个单属性索引。可以把索引项理解为：

```text
(run-A, drone-7, 2299) -> 对应 AgentState
(run-A, drone-7, 2300) -> 对应 AgentState
(run-A, drone-7, 2301) -> 对应 AgentState
```

查询 run-A、drone-7、step<=2300 时，数据库可以定位相关索引范围，而无需先把整个 run 的状态读出来，再在内存里找 drone-7。如果计划利用索引顺序，还可以更有效地取得最大的合格 step；具体是否避免排序，以计划为准。

不把所有场景硬放进一个 (run_id, drone_id, region_id, step_index) 索引：i11 没有 drone_id 或 region_id 过滤，i6 第二段没有 drone_id 过滤，未必符合该复合索引的使用条件。索引应配合真实谓词组合，而不是字段越多越好。

第二阶段再根据剩余瓶颈评估 Step(run_id, step_index)、Drone(run_id, id)、Event(run_id, drone_id, time_minute)、Event(run_id, region_id, time_minute) 等，不一次性为所有字段建索引。

### 创建后如何使用与验证

通常保留现有 Cypher 和命令行参数即可，规划器会考虑匹配的属性索引，不需要 Python 先查询 Run，也不需要每条 Cypher 指定索引名称。

执行步骤：先保留无索引基线；在合适的实验时间创建索引并等待 ONLINE；检查 EXPLAIN 是否从标签扫描变为合适的索引检索；再以小样本 PROFILE 确认实际访问量，最后在相同参数下比较不同并发度的延迟和成功吞吐。

索引创建会对已有符合条件的节点建立条目，后续新增或修改数据时由数据库维护，不依赖重新生成数据集。复合索引不包含缺失任一被索引属性的节点；这些模板要求相应属性匹配，仍应核对实际数据完整性。

索引不缓存完整查询结果，也不建立唯一性约束。对于真正需要唯一的标识，是否增加约束属于另一个数据完整性决策，不和本次加速方案混为一谈。

官方依据：[Neo4j 索引对查询性能的影响](https://neo4j.com/docs/cypher-manual/current/indexes/search-performance-indexes/using-indexes/)。

### 执行过的操作

- 阅读 `scripts/lade_llm_context_queries.py` 的 i1/i2 模板。
- 结合上一轮已获取的 SHOW INDEXES、AgentState 计数和 EXPLAIN 结果解释现状，本轮没有重新访问数据库。
- 查阅 Neo4j 官方索引文档。

### 修改内容

仅新增本文档，未创建索引、修改查询或更改实验数据。

### 后续建议

- 先按三组 AgentState 索引做独立优化实验，验证结果一致性与访问量后再扩大并发。
- 保留查询的历史时间语义，不为了提速把 step<=2300 随意改成 step=2300；两者在状态缺失时结果不同。
- i2 语法问题仍需独立修复，索引不能解决语法错误。
