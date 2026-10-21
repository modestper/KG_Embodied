### 任务背景

用户希望理解 `scripts/lade_llm_context_queries.py` 中的代码结构，以及这份脚本在 LaDe 无人机配送仿真中的具体功能。本次仅做代码阅读、关联文档核对和测试验证，不修改程序逻辑。

### 关键结论

- 该脚本不是 LLM 客户端，也不会向 vLLM/OpenAI 接口发送请求；它负责在调用 LLM 之前，从 Neo4j 知识图谱提取决策所需的即时上下文。
- 脚本内置 `i0` 到 `i11` 共 12 个 Cypher 查询，覆盖无人机自身状态、历史轨迹、任务、事件、区域负载、协同对象和全局态势。
- 默认执行 `core` 查询包：`i0,i1,i3,i4,i5,i6,i9`，为每架无人机生成一份结构化 context JSON。
- 同一个 context 内的查询顺序执行；不同无人机和重复轮次之间由线程池并发执行，因此它也可用于测量 Neo4j 在“多无人机同时请求 LLM 上下文”场景下的延迟和吞吐。
- 输出包括逐查询日志 JSONL、逐 context JSONL 和汇总统计 JSON；单条查询失败会被记录，但不会阻止同一 context 的其他查询继续执行。
- 该脚本目前是独立 CLI/基准工具，项目仿真主流程没有直接导入它；若要真正参与 LLM 调度，还需由调用方读取生成的 context 或直接复用其中的函数。

### 代码整体功能

可以把脚本理解为 LLM 决策前的“情报收集器”：

```text
命令行参数
  -> 选择查询模板/查询包
  -> 补齐 run、drone、step、时间窗口等参数
  -> 从 Neo4j 查询状态、任务、事件和全局态势
  -> 拼成每架 drone 的结构化 context
  -> 写入日志并统计 avg/p50/p95/p99、QPS 等指标
```

它同时承担两个用途：

1. **上下文构造**：为后续 LLM 决策准备 JSON 数据。
2. **查询压测**：模拟多架无人机同时构造上下文时的 Neo4j 性能。

### 主要数据结构

`ContextQuery` 是一个不可变 dataclass，一条查询模板包含：

- `id`：短编号，例如 `i4`。
- `name`：写入 context 时使用的可读名称，例如 `recent_drone_events`。
- `category`：查询类别，如 `self`、`task`、`event`。
- `required_params`：执行 Cypher 前必须具备的参数。
- `description`：查询用途说明。
- `cypher`：真正发给 Neo4j 的查询语句。

`CONTEXT_QUERIES` 保存全部内置模板；`QUERY_PACKS` 把常用模板组合成不同使用场景。

### 12 个内置查询的作用

| ID | 名称 | 作用 |
| --- | --- | --- |
| `i0` | `current_drone_state` | 从 `Drone` 节点读取当前状态、位置、队列、载货、电量和累计里程。 |
| `i1` | `latest_agent_state` | 找到不晚于目标 step 的最新 `AgentState`，获得该时刻的状态快照。 |
| `i2` | `recent_self_trajectory` | 查询最近若干 step 的自身轨迹，作为短期记忆；结果按新到旧排列。 |
| `i3` | `active_tasks_for_drone` | 通过 `ASSIGNED`/`CARRYING` 关系读取已分配或正在携带、但尚未完成的任务，优先返回截止时间较早的任务。 |
| `i4` | `recent_drone_events` | 查询时间窗口内该无人机发生的近期事件，并可关联具体任务。 |
| `i5` | `recent_self_performance` | 聚合近期分配、取件、完成、迟到和总事件数，形成紧凑表现摘要。 |
| `i6` | `current_region_load` | 先确定该无人机最新所在区域，再按状态聚合该区域同一步的无人机数量、平均队列和电量。 |
| `i7` | `nearby_drones_same_region` | 返回同一区域的其他无人机，按队列短、电量高排序，为协调或转派提供信息。这里的“nearby”实际表示同区域，不是按欧氏距离筛选。 |
| `i8` | `known_open_tasks_in_region` | 查询当前区域内已释放、未完成且在截止时间窗口内的任务。它只能看到已经写入 Neo4j 的任务。 |
| `i9` | `latest_global_step_summary` | 读取不晚于目标 step 的最新全局 `Step` 摘要，如完成/未完成任务数和各种状态的无人机数。 |
| `i10` | `region_recent_events` | 查询当前区域时间窗口内的近期事件，帮助判断局部拥堵或异常。 |
| `i11` | `global_region_bottlenecks` | 在最新 step 上按区域聚合无人机数量、配送/空闲数量、队列和电量，并优先返回最大队列较高的区域。 |

查询都带 `run_id` 限制，用于隔离不同仿真实验的数据。

### 查询包

- `core`：默认轻量上下文，兼顾自身、任务、事件、区域和全局信息。
- `decision`：在 `core` 基础上增加自身轨迹和区域临期任务，适合做下一动作决策。
- `coordination`：关注区域负载、其他无人机、区域事件和全局瓶颈。
- `diagnostic`：关注历史、事件、表现和全局瓶颈，适合问题诊断。
- `all`：执行全部 12 个查询。

`select_queries()` 的输入很灵活，既支持查询包，也支持单个 ID、范围和类别，例如：

```text
core
i0,i3,i9
i0-i5
self,task
```

函数会保持第一次出现的顺序并去重；无法识别的选择会直接以 `SystemExit` 报错。

### 参数解析和自动补参

`parse_args()` 定义 CLI 参数，并允许部分值来自环境变量：

- Neo4j：`NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD`、`NEO4J_DATABASE`。
- 仿真定位：`KG_RUN_ID`、`KG_STEP_INDEX`、`KG_TIME_MINUTE`。
- 查询控制：无人机列表、查询包、历史步数、事件时间窗口、截止时间窗口、返回条数等。
- 并发控制：`--repeat`、`--max-workers`、`--max-connection-pool-size`。
- 输出控制：日志路径、是否打印 context、是否包含 Cypher、dry-run 等。

`build_params()` 负责为每条查询构造参数：

```text
start_step = max(0, step_index - history_steps + 1)
start_time = max(0.0, time_minute - event_window_minutes)
```

例如 `step_index=100`、`history_steps=20` 时，历史窗口是 `81..100`，共 20 个 step。命令行 `--param key=value` 的值优先级最高，因为代码先用它建立字典，再通过 `setdefault()` 填默认值。

如果查询需要时间参数，但用户没有给 `--time-minute`，`resolve_time_args()` 会通过 `run_id + step_index` 查 `Step` 节点并取得 `time_minute`。如果查询还要求 `step_index` 而用户没有提供，则在参数校验阶段报错。

### 自定义查询

`load_custom_queries()` 支持从 JSON 文件加载查询。JSON 可以直接是数组，也可以是带 `queries` 字段的对象。每个查询至少要有 `id` 和 `cypher`。若没有显式提供 `required_params`，`infer_required_params()` 会用正则从 Cypher 中提取 `$param` 参数名。

默认情况下，自定义查询会覆盖同 ID 的内置查询或追加新查询；使用 `--replace-default-queries` 后只保留自定义查询。

### 核心执行流程

`main()` 的顺序如下：

1. 解析命令行参数，并处理关闭日志的开关。
2. 加载内置和自定义查询。
3. 若指定 `--list-queries`，只列出模板后退出。
4. 根据 `--queries` 选择模板。
5. 若指定 `--dry-run`，只打印每条查询的参数计划，不连接 Neo4j。
6. 校验 `run_id`，创建 Neo4j driver。
7. 必要时从 `Step` 节点解析 `time_minute`。
8. 调用 `run_context_benchmark()` 并发构造 context。
9. 写日志和 summary，在终端打印汇总，最后关闭 driver。

`run_context_benchmark()` 会把每个 `(repeat, drone_id)` 组合成一个 job。默认线程数等于无人机 ID 数量。每个 job 调用一次 `execute_context()`：

- 为该无人机打开一个 Neo4j session。
- 在同一 session 中依次执行选中的查询。
- 每条查询分别计时、记录行数和错误。
- 用查询的 `name` 作为键，把返回数据写入 `context["queries"]`。
- 计算整份 context 的总耗时。

多个 job 由 `ThreadPoolExecutor` 并发执行；主线程按实际完成顺序收集结果，所以 JSONL 的 context 顺序不保证与输入无人机顺序一致。

### 输出文件结构

默认输出为：

- `data/lade_llm_context_query_logs.jsonl`：一行一条查询指标，含查询 ID、无人机、参数、成功状态、耗时、行数和错误。
- `data/lade_llm_contexts.jsonl`：一行一份完整 context，包含全部查询结果和精简指标。
- `data/lade_llm_context_summary.json`：本轮总体、按查询、按类别汇总的统计结果。

查询日志和 context 日志使用追加模式 `a`，不会自动清空旧内容；summary 使用覆盖写入。`append_jsonl()` 还通过线程锁保证日志写入不会互相穿插。

`summarize()` 统计：

- context 数、总查询数、成功数和失败数；
- 总墙钟时间和整体 QPS；
- 成功查询的平均、P50、P95、P99 延迟；
- context 的平均和 P95 延迟；
- 按查询 ID、按类别的聚合指标。

失败查询不参与延迟分位数计算，但仍计入总查询数和吞吐分子。

### 一个典型用法

先查看查询模板：

```bash
./run_in_lade.sh python scripts/lade_llm_context_queries.py --list-queries
```

不连接数据库，查看默认 `core` 包会生成哪些参数：

```bash
./run_in_lade.sh python scripts/lade_llm_context_queries.py \
  --run-id jilin-0925-exp001 \
  --drone-ids drone-1,drone-2 \
  --step-index 100 \
  --time-minute 600 \
  --queries core \
  --dry-run
```

真实查询时去掉 `--dry-run`，并确保 Neo4j 连接信息正确。若不给 `--time-minute`，脚本会尝试从对应的 `Step` 节点读取，但仍需提供 `--step-index`。

### 容易误解或需要注意的地方

- 文件名含 `llm`，但代码没有 LLM API 调用；它只准备 LLM 可消费的数据。
- 脚本默认的 Neo4j 地址是 `bolt://127.0.0.1:17687`，而 README 示例使用 `7687`，实际运行时应以当前部署端口为准。
- `i0` 读的是 `Drone` 当前属性，`i1` 读的是某个 step 对应的 `AgentState` 历史快照，两者用途不同。
- `i7` 的“附近”只是同一个 `region_id`，并没有根据 `x/y` 算空间距离。
- 一份 context 内的 Cypher 并非并行查询；并行发生在不同 context/job 之间。
- 每次 `repeat` 都使用同样的输入参数，主要用于压测和采样延迟，不会自动推进 step。
- `context["queries"]` 以查询名称为键；如果自定义查询使用重复名称，后一个结果会覆盖前一个名称对应的结果。
- `AUTO_PARAMS` 常量和 `execute_one()` 函数当前没有被主流程引用，属于保留或冗余代码。
- `scripts/lade_llm_context_queries_single.py` 当前与目标脚本内容完全相同，并不是真正的单线程版本；是否保留两份需要由项目维护者决定。

### 执行过的操作

- 完整读取 `scripts/lade_llm_context_queries.py`。
- 阅读 `README.md` 中 LLM 调度、Neo4j schema、任务状态和 AgentState 的说明。
- 阅读 `tests/lade_llm_context_queries_test.py`，核对查询选择、参数窗口、context、日志、summary 和自定义查询行为。
- 阅读已有设计记录 `docs/codex_notes/2026-09-04-LLM前置即时Neo4j查询脚本.md`。
- 搜索项目内调用关系，确认该脚本没有被仿真主流程导入。
- 对比 `scripts/lade_llm_context_queries.py` 与 `scripts/lade_llm_context_queries_single.py`，确认两者当前完全相同。
- 执行：
  - `./run_in_lade.sh python -m unittest tests/lade_llm_context_queries_test.py`
  - `./run_in_lade.sh python scripts/lade_llm_context_queries.py --list-queries`

测试结果：8 项测试全部通过。

### 修改内容

- 未修改任何程序代码。
- 新增本说明文件：`docs/codex_notes/2026-09-16-LLM前置上下文查询脚本讲解.md`。

### 后续建议

- 若要让这些查询真正影响 LLM 调度，应在仿真调用 LLM 前复用查询函数，并把 `context["queries"]` 序列化到 prompt 或结构化输入中。
- 正式接入前，先用 `core` 包测试线上 Neo4j 延迟，再根据决策需求增加 `decision` 或 `coordination` 查询。
- 若长期做基准测试，建议每轮指定独立日志文件，或在确认后清理旧 JSONL，避免多轮数据混在一起。
- 可以补充一项真实 Neo4j 集成测试，验证现有 Cypher 与当前数据库 schema、索引和实际数据字段完全一致。
