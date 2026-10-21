# AgentState 索引建立与验证

### 任务背景

用户授权在 Neo4j 建立适合当前 LLM 前置查询的属性索引，并验证查询是否自动使用索引。目标为 `bolt://127.0.0.1:17687` 的 `neo4j` 数据库，优先处理前次排查确认的 AgentState 大范围扫描。

### 关键结论

- 索引建立在 Neo4j 数据库中，作为数据库的 schema 对象持久保存；不是 Python 内存中的字典或查询结果缓存。
- 本次限定三组 AgentState 复合 RANGE 索引；不改变原模板、业务节点或关系。
- 建索引前后的原始记录分别保存在同目录的 `2026-09-14-agent-state-index-evidence/`，不覆盖原有压测日志。
- 验证使用原始 Cypher，没有添加 USING INDEX，也没有将时间范围查询改写为等值查询。
- ONLINE 只证明索引可用；EXPLAIN 验证规划器的选择，PROFILE 验证实际执行的索引算子和数据库访问次数。
- 实际查询期间仍有仿真写入，单次串行 PROFILE 的耗时仅作参考，不能与旧的 100 并发延迟直接计算加速倍数。
- 三组索引均已 ONLINE 100%；六条目标模板的 AgentState 访问均自动变为 NodeIndexSeek，七条 PROFILE 查询的结果逐行内容在忽略顺序后与建立前一致。
- i1 仍从索引检索 2300 条合格历史记录后进行 Top-1，并不是只访问一条索引项；已有优化消除了百万级标签扫描，但还存在后续优化空间。

### 索引定义

```cypher
CREATE INDEX lade_agent_state_run_drone_step IF NOT EXISTS
FOR (a:AgentState) ON (a.run_id, a.drone_id, a.step_index);

CREATE INDEX lade_agent_state_run_region_step IF NOT EXISTS
FOR (a:AgentState) ON (a.run_id, a.region_id, a.step_index);

CREATE INDEX lade_agent_state_run_step IF NOT EXISTS
FOR (a:AgentState) ON (a.run_id, a.step_index);
```

第一组对应 i1/i2 和其他查询中寻找 self 状态的部分；第二组对应 i6/i7 的区域状态查找；第三组对应 i11 的单步状态查找。索引对整个数据库中符合定义的 AgentState 生效，不只针对测试的这个 run。

### 实际验证结果

创建阶段时间为 2026-09-14 15:40:47 至 15:47:43（本机时间）。三组索引最终均为 RANGE / ONLINE / 100.0%。已有仿真写入未被终止，创建耗时包含等待和后台填充时间，未进一步分解各阶段耗时。

测试参数为 run `million-nodes-hangzhou-0621-100agents`、drone-1、step 2300、limit 10。time_minute 从对应 Step 解析，事件窗口 30 分钟，deadline 预警窗口 60 分钟。没有修改原查询文本，仅分别添加 EXPLAIN 或 PROFILE 前缀。

| 查询 | 建立前 DB Hits | 建立后 DB Hits | 建立前单次 PROFILE 耗时 | 建立后单次 PROFILE 耗时 |
| --- | ---: | ---: | ---: | ---: |
| i1 | 3,334,744 | 2,310 | 1337.856 ms | 27.338 ms |
| i6 | 7,105,825 | 2,313 | 2934.328 ms | 9.340 ms |
| i7 | 7,097,105 | 2,313 | 2685.481 ms | 8.848 ms |
| i8 | 3,387,532 | 55,100 | 1182.618 ms | 57.861 ms |
| i9 | 41,128 | 41,136 | 42.948 ms | 39.188 ms |
| i10 | 3,534,442 | 202,034 | 1288.580 ms | 84.789 ms |
| i11 | 3,803,780 | 41,628 | 1448.799 ms | 30.219 ms |

这些是一次串行、带 PROFILE 统计的客户端耗时；不是平均值或 P95，不是优化后的 100 并发测试。建索引会改变缓存状态，原仿真仍持续写入，因此应优先依据计划和 DB Hits 判断效果，不能把上述耗时比例直接用于预测并发加速比。

实际 PROFILE 中的关键算子：

```text
i1:
  NodeIndexSeek
  RANGE INDEX a:AgentState(run_id, drone_id, step_index)
  WHERE run_id = $run_id AND drone_id = $drone_id
        AND step_index <= $step_index
  Rows = 2300, DB Hits = 2301
  后续仍有 Top(step_index DESC LIMIT 1)

i6 / i7 的第二段:
  NodeIndexSeek
  RANGE INDEX a:AgentState(run_id, region_id, step_index)
  Rows = 2, DB Hits = 4

i11 的 AgentState 部分:
  NodeIndexSeek
  RANGE INDEX a:AgentState(run_id, step_index)
  Rows = 100, DB Hits = 101
```

因此可以确认原始查询会自动索引定位 AgentState，但不能声称每一条查询的所有分支都只用索引：i8 仍有 Task 标签扫描，i10 仍有 Event 标签扫描，i9/i11 仍有 Step 标签扫描。此次没有给这些标签建索引。

生成证据：

- `2026-09-14-agent-state-index-evidence/before.json`：原有索引、12 条模板计划或错误、7 条实际 PROFILE 及结果。
- `2026-09-14-agent-state-index-evidence/apply.json`：执行过的 CREATE 语句和最终索引状态。
- `2026-09-14-agent-state-index-evidence/after.json`：索引上线后的计划、实际访问次数、耗时与结果。

已通过结构化断言检查：3 个目标索引全部 ONLINE；i1/i6/i7/i8/i10/i11 均有 AgentState 的 NodeIndexSeek，且无 AgentState 的 NodeByLabelScan；7 条 PROFILE 查询的结果多重集合前后一致。

### 用户如何自行验证

在同一个 Neo4j 数据库中查看索引状态：

```cypher
SHOW INDEXES
YIELD name, state, populationPercent, labelsOrTypes, properties
WHERE name STARTS WITH 'lade_agent_state_'
RETURN name, state, populationPercent, labelsOrTypes, properties;
```

预期三个索引均为 ONLINE，populationPercent 为 100。

下面的语句只查看计划，不实际读取目标状态：

```cypher
EXPLAIN
MATCH (a:AgentState {
  run_id: 'million-nodes-hangzhou-0621-100agents',
  drone_id: 'drone-1'
})
WHERE a.step_index <= 2300
RETURN a.step_index AS step_index, a.status AS status
ORDER BY a.step_index DESC
LIMIT 1;
```

查看访问 AgentState 的数据源算子。如果为 NodeIndexSeek / NodeIndexSeekByRange 等，并且 Details 标明 AgentState(run_id, drone_id, step_index) 与相关条件，说明从属性索引定位。若仍为 NodeByLabelScan(a:AgentState)，则这一分支仍在扫描标签。

执行计划通常以树展示，界面顶端的 ProduceResults 只是输出算子，不能据此判断“起点”。应查看叶子或底部的数据访问算子；多分支查询需要分别检查。

将 EXPLAIN 换成 PROFILE 可以实际执行并查看 DB Hits 和行数。DB Hits 是节点、属性、索引条目等底层数据库访问操作的计数，不等于返回节点数，也不等于磁盘物理读取次数。

原理参考：[Neo4j 执行计划说明](https://neo4j.com/docs/cypher-manual/current/planning-and-tuning/execution-plans/)、[索引检索算子说明](https://neo4j.com/docs/cypher-manual/25/planning-and-tuning/operators/operators-detail/)。

### 执行过的操作

- 阅读 AGENTS.md、`scripts/lade_llm_context_queries.py` 并检查 git 工作区，保留原有变更。
- 创建临时执行工具 `/tmp/lade_agent_state_indexes_20260914.py`，分 before/apply/after 三阶段记录结果；JSON 证据不包含数据库密码。
- 所有 Python 操作通过 `./run_in_lade.sh python ...` 执行。
- before 阶段保存原有索引、12 条模板的 EXPLAIN 结果，以及 i1/i6/i7/i8/i9/i10/i11 各一次串行 PROFILE。i2 的已知 BETWEEN 语法错误单独记录。
- apply 阶段逐个创建索引并轮询到 ONLINE，未终止已有仿真事务。
- 创建等待期间只读检查事务状态，发现数据库仍有 UNWIND AgentState 仿真写入。
- after 阶段重复相同计划检查和七条实际查询，解析前后 JSON 进行索引状态、访问算子与返回内容断言验证，全部通过。

### 修改内容

数据库变更限定为上面的三组索引。项目新增本说明及索引操作/执行计划 JSON 证据，未修改现有压测脚本和实验结果。

### 后续建议

- 将之后的测试作为“有索引”实验保存到新文件，避免与原无索引基线混合。
- i2 的 BETWEEN 语法错误不属于索引问题，运行完整 all 包之前仍需修正。
- 本次只针对 AgentState，其他标签是否需要索引，应根据剩余执行计划和真实负载另行评估。
