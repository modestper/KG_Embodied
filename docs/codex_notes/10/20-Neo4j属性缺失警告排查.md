### 任务背景

> 后续修订：本文记录旧目录告警的排查。当前 i 目录已移除这些未入库协作/故障字段的依赖，详见 [现有图谱结构修订说明](20-i查询按现有知识图谱结构修订.md)。本文的旧 i50/i51 含义不再代表当前版本。

用户执行 i 系列查询测试时，终端反复出现 `Received notification from DBMS server`，包含 `01N52`、`WARNING` 和 `helper_drone_id`、`source_drone_id`、`failure_mode`、`reason` 属性不存在的提示。本次解释告警含义，并只读核对查询、写入代码和当前数据库，不修改代码或数据库。

### 关键结论

- 这些文本是 Neo4j 服务端通知，由 Python 驱动记录为日志，不是查询返回的业务数据。`01N52` 是未知属性名警告，本身不等于查询执行异常。
- 这些属性在当前项目写入代码中有明确来源，并非不存在于项目设计的任意字段。但代码支持某个字段，不代表历史数据已经包含该字段。
- 当前 `bolt://127.0.0.1:57687` 的 `neo4j` 数据库已登记全部四个属性名；因此用户贴出的警告与本次检查时点的属性名登记状态不同。不能据此认定用户当时连接错误，也不能把当前状态反推为日志发生时的状态。
- 更关键的是，当前目标 run 的全部 26454 个 Event 中，没有 `task_assistance_requested`、`assist_dispatched`、`task_taken_over` 等协作事件，且所有 Event 都没有上述 source/helper/reason 字段的非空值。因此 i50/i51 在这个 run 上缺少对应业务数据。
- `failure_mode` 是可选故障标记。Neo4j 不存储值为 null 的节点属性，因此正常无人机可以没有该字段；旧数据未采集该字段也是可能原因。单凭告警不能区别这两种情况，也不能断定无人机已经故障。
- 当前脚本仅把执行异常标记为查询失败，没有把这些服务端警告计入失败。查询“成功”、返回空结果、字段返回 null、场景获得有效验证，是不同概念。
- `--print-context` 只控制正常上下文 JSON 的打印，不控制驱动告警。100 个 drone、多轮重复执行，会把相同警告反复输出。关闭日志不能补齐缺失数据，也不能使实验自动具有代表性。

### 一、如何阅读用户贴出的日志

| 日志字段 | 含义 |
| --- | --- |
| `Received notification from DBMS server` | 驱动收到服务端通知并记录日志 |
| `gql_status='01N52'` | 查询引用了当时数据库不认识的属性名 |
| `raw_severity='WARNING'` | 警告，不等同于抛出异常并终止查询 |
| `classification='UNRECOGNIZED'` | 该通知属于无法识别名称这一分类，不是说整个数据库不可识别 |
| `position` | 问题属性在后面所附 Cypher 中的位置 |
| `for query` | 产生通知的完整 Cypher，因而单条日志看起来很长 |

Neo4j 官方将 `01N52` 对应到 `Neo.ClientNotification.Statement.UnknownPropertyKeyWarning`，建议核对拼写以及属性是否确实应在未来数据中出现。[官方通知说明](https://neo4j.com/docs/status-codes/current/notifications/all-notifications/#_unknown_property_key)

服务端通知与查询失败不同，不会仅因通知本身而使语句失败。[Python 驱动通知 API](https://neo4j.com/docs/api/python-driver/current/api.html#neo4j.ResultSummary.notifications)

### 二、这些字段有什么用

| 字段 | 使用位置和业务含义 | 写入来源 |
| --- | --- | --- |
| `helper_drone_id` | Event 上的协助/接管无人机 ID；i51 用它查自己接管任务的完成反馈 | `Neo4jKnowledgeStore.record_assistance_event` |
| `source_drone_id` | Event 上原承担者/求助无人机 ID；i50 用它关联求助与派遣 | 同上 |
| `reason` | 协助事件发生原因，如电量、故障或任务风险 | 同上 |
| `failure_mode` | Drone/AgentState 的故障模式；i49 同时检查 status 是否为 failed | `agent_state_summary` 和 Drone 属性写入 |

当前仿真的 `_transfer_task_for_assistance` 会在真实触发时依次记录求助、派遣，以及符合条件时的接管事件。没有真实协作事件时，查询不会凭空构造协作历史。

如果在 `MATCH` 中要求一个缺失的属性值等于 drone ID，该节点无法匹配；直接返回缺失属性时通常得到 null。i49 的条件还包含 `a.status = "failed"`，所以不能笼统地把缺少 failure_mode 解释为该查询必然返回空，也不能把未读到该字段解释为无人机一定正常。

### 三、当前库的只读核查结果

连接：`bolt://127.0.0.1:57687`，数据库：`neo4j`，run：`hangzhou-0621-headless-002`。

#### 属性名登记

`CALL db.propertyKeys()` 返回的属性名中，四个名称均存在：

```text
failure_mode
reason
source_drone_id
helper_drone_id
```

这只证明数据库已登记这些名称，不能证明当前 run 的节点上有这些属性或有非空值。属性名登记不按 run 划分；属性名出现过，也不等于仍存在有代表性的业务记录。日志产生的具体连接、时刻、数据库状态或计划状态没有完整记录，本次不对状态变化原因作确定归因，也未清理查询缓存或写入占位数据。

#### 目标 run 的事件统计

对该 run 的全部 Event 按 type 分组，只得到：

| 类型 | 数量 |
| --- | ---: |
| `task_assigned` | 8272 |
| `task_picked_up` | 7923 |
| `task_completed` | 7907 |
| `drone_low_battery` | 787 |
| `drone_returning_to_charge` | 787 |
| `drone_charged` | 778 |
| 合计 | 26454 |

每组中 helper/source/reason 的非空属性值数量均为 0。没有协作请求、派遣、接管或实物交接类型，因此相关查询缺少数据基础，不只是所选 30 分钟窗口刚好没有协作事件。

#### AgentState 的检查边界

读取到 `drone-1` 的一个样本，step 为 1、status 为 idle，节点属性中没有 `failure_mode`。这里只能说明这个样本缺少故障属性，不能扩展为所有无人机、所有历史步都未发生过故障。

另一次针对 step 2300 的 100 个 drone 状态统计设置了 10 秒事务超时，未在限制内完成，数据库终止该只读事务；没有取得该项结果。本次未提高超时反复重试，不据此推断 step 2300 缺失数据或有无故障，也不把该超时当作用户原日志的原因。

### 四、为什么没开结果打印也会刷屏

本地安装的驱动源码 `neo4j/_sync/work/result.py` 使用 `neo4j.notifications` logger，将 WARNING 级通知记录为：

```text
Received notification from DBMS server: ...
```

脚本中的 `--print-context` 只控制正常上下文 JSON 的 `print`，不参与这个驱动日志分支。驱动告警通常经日志处理器输出到 stderr；具体去向仍取决于启动时的 logging 配置与重定向。

所以默认不打印业务返回行，与默认仍能看到数据库警告并不矛盾。添加 `--print-context` 能看到正常上下文，但不会解决字段/数据覆盖不足。

### 五、对本次测试的影响和建议

1. 原命令仍是合法的 i 系列测试入口，但“命令兼容”不代表该 run 对全部 56 条查询都有有效业务覆盖。
2. 对每条查询同时检查 success、error、row_count 和实际内容。聚合查询即使没有底层业务事件，也可能返回一行计数为 0，不能只看返回行数。
3. i50/i51 的协作场景应使用确实产生并写入协作事件的仿真 run 验证；不应在现有实验库随意补造事件或用无意义属性消除告警。
4. 基础任务、电量、普通历史查询与协作/故障查询的结果应分组解读。大量空结果的混合压测，不应直接代表真实协作负载性能。
5. 在确认缺失字段属于允许的可选数据后，可以独立设计告警统计和终端去噪；不能只屏蔽日志后宣称问题已解决。当前用户只要求解释，因此本次未实施日志配置修改。

### 执行过的操作

- 使用 `rg`、`sed` 阅读 `scripts/lade_llm_context_templates.py` 的 i49/i50/i51，以及相关字段引用。
- 阅读 `simulation/knowledge.py` 的协助事件写入、AgentState 字段构造和图谱写入，以及 `simulation/delivery_sim.py` 的协助触发逻辑。
- 阅读 `scripts/lade_llm_context_queries.py` 的异常、成功计数与驱动构建逻辑，确认没有将这些警告转换成失败或结构化查询告警指标。
- 阅读本地已安装的 Neo4j Python 驱动通知日志代码，并核对 Neo4j 官方通知/驱动文档。
- 通过 `./run_in_lade.sh python -c ...` 执行属性名登记、有限样本和目标 run 的事件分组只读查询。使用单连接池及客户端事务超时控制；step 2300 汇总超时情况已单独说明。
- 未运行完整 all 压测，未新增图谱节点、修改数据库属性或覆盖既有实验结果。

### 修改内容

仅新增本文档，未修改程序、查询模板、驱动日志设置或数据库。

### 后续建议

- 保留用户当次日志的启动命令、URI、数据库、时间和 summary，以便精确区分当次属性名状态与现在的状态。
- 在有协作/故障真实数据的 run 上验证相关查询，再讨论全量性能和索引效果。
- 若后续要求改进测试工具，可将通知结构化入日志，并区分执行成功、缺数据和场景覆盖；需作为独立修改任务处理。当前排查无需额外确认。
