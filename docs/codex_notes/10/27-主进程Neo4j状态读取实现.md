### 任务背景

用户希望在已有 `main|ray|process` 知识图谱写入方式上，增加主进程直接读取 Neo4j 中无人机状态的实现，用于派单和互助决策。本次实现状态来源切换，保留现有调度算法与同步时间步，不重启当前服务，不改动已有实验数据。

默认理解：主进程继续管理初始任务目录、操作确认和调度编排；决策所用的无人机状态与派单、取货、完成进度从 Neo4j 读回。完全异步调度、主进程操作系统优先级、强制中断后的持久消息恢复不属于本次实现。

### 关键结论

- 新增 `--state-source ray|neo4j`，环境变量为 `KG_STATE_SOURCE`，默认 `ray`，与 `KG_WRITE_MODE` 分别配置。Neo4j 读取要求 Ray 执行后端和 Neo4j 知识图谱。
- 新增 `DroneRuntimeState` 最新状态视图，保存完整队列、载货任务、电量、位置、目标、depot、故障、互助和操作中的任务更新，弥补原 `AgentState` 历史摘要的信息缺口；不要求开启 `KG_RECORD_AGENT_STATE`。
- 主进程批量查询本次执行的状态，校验预期操作版本，再还原为规划器使用的内存对象；Neo4j 模式不再调用 `actor.snapshot.remote()`，缺失或版本错误不会回退到 Ray。
- `ray` 写入模式中 actor 先提交完整状态，RPC 仅返回操作确认、事件和任务 ID；`process` 模式仍经主进程转交状态给独立写入进程，提交后查询。两个卸载模式中主进程数据库连接只执行读取查询。
- 每次启动和检查点恢复使用新的 `execution_id`，以 `(run_id, execution_id, drone_id)` 唯一标识运行状态。此隔离只适用于新增视图，不解决旧 Task/Event/Step 在固定 run ID 重启时的覆盖问题。
- 保留“本轮执行与写入完成 → 查询对应版本 → 下一轮规划”的同步逻辑，尚未实现异步流水线；实际耗时是否下降需要测量。
- 新增状态单元测试 11 项及已有写入测试 14 项通过；全量 224 项中 210 项通过、11 项跳过、3 项为原有电量准入失败。真实 Neo4j 两条语句的只读 EXPLAIN 及独立真实 Ray 回归通过；真实 Neo4j 落库集成尚未执行。

### 执行过的操作

读取并检查了 `AGENTS.md`，以及以下关键实现：

- `simulation/ray_delivery_sim.py`：actor 派单、step、互助释放/回滚、快照同步和检查点恢复。
- `simulation/knowledge.py`、`simulation/knowledge_writer.py`、`simulation/state_codec.py`：历史图谱写入、写入进程协议、主进程读取连接与状态编码。
- `scripts/run_dynamic_sim.py`、`scripts/run_headless_sim.py`、三个启动脚本和现有测试、README。
- `simulation/checkpoint.py`：确认恢复仍以原检查点格式为准。
- 现有数据库说明：确认 `57687/61687` 是实验查询库，未对其进行建约束或数据写入。

运行的关键检查：

```bash
PYTHONDONTWRITEBYTECODE=1 ./run_in_lade.sh python -m unittest -v \
  tests.knowledge_state_test.KnowledgeStateTest tests.knowledge_writer_test

PYTHONDONTWRITEBYTECODE=1 ./run_in_lade.sh python -m unittest discover \
  -s tests -p '*_test.py'

LADE_KG_RAY_INTEGRATION=1 PYTHONDONTWRITEBYTECODE=1 \
./run_in_lade.sh python -m unittest -v \
  tests.knowledge_writer_integration_test.KnowledgeWriterRayIntegrationTest

# 已使用现有凭据执行，凭据不写入此文档。
LADE_KG_STATE_EXPLAIN_URI=bolt://127.0.0.1:61687 \
PYTHONDONTWRITEBYTECODE=1 ./run_in_lade.sh python -m unittest -v \
  tests.knowledge_state_test.KnowledgeStateExplainIntegrationTest

git diff --check
bash -n run_ray_sim.sh run_local_sim.sh run_million_data_sim.sh
```

还执行了 `git status --short`、`rg` 定位、文件分段读取和监听端口只读检查。真实 Ray 验证启动独立临时实例，结束后关闭，不连接已有 Ray 集群。

验证说明：

- 新测试禁止调用 actor 快照接口，比较普通配送与故障交接在三种写入模式下的页面帧、任务状态和事件，与原 Ray 读取结果一致。
- 覆盖图中状态影响主进程、任务通知只使用图中完整数据、缺失/过期/损坏/重复状态报错、检查点恢复隔离、互助拒绝回滚和入口参数。
- 单元测试中的数据库和 Ray 是替身；`process` 的状态转交逻辑使用同样的写入执行器验证，并非真实 Neo4j 子进程落库验证。
- 真实 Ray 回归覆盖原三种写入模式的跨进程运行；Ray 自身输出了 ResourceWarning/FutureWarning，测试断言通过。
- 真实 Neo4j 仅执行 `EXPLAIN`，检查新增发布和查询语句的编译，不执行它们。现有库尚无新标签与属性，因此查询规划报告对应的未登记标签/属性提示，测试通过。
- 已增加显式启用的真实 Ray + Neo4j 状态读取测试，但未对实验库执行；需要隔离测试库才能验证真实提交、索引和端到端耗时。

全量测试的三个原有失败：

1. `delivery_sim_test.DeliverySimulationTest.test_battery_insufficient_for_task_and_return_prevents_assignment`
2. `delivery_sim_test.DeliverySimulationTest.test_auction_retries_other_region_when_same_region_drone_lacks_battery`
3. `ray_delivery_sim_test.RayDeliverySimulationTest.test_ray_reassigns_task_when_preferred_actor_rejects_assignment`

三项均与当前被注释的电量接单硬限制有关，本次未修改电量规则。

### 修改内容

| 文件 | 修改目的 |
|---|---|
| `simulation/knowledge_state.py` | 定义状态来源、运行状态编码、完整性与版本校验、读取错误类型 |
| `simulation/knowledge.py` | 增加新状态视图唯一约束、批量发布和批量读取；使用读模式会话查询 |
| `simulation/knowledge_writer.py` | 在既有写入协议中增加建状态约束和发布状态命令；主进程通过 reader 查询 |
| `simulation/ray_delivery_sim.py` | 在派单、互助、step 后发布状态；主进程从图中刷新代理和任务进度；初始化/恢复隔离运行视图 |
| `scripts/run_dynamic_sim.py` | 接入参数、环境变量、请求参数和配置输出，提前拒绝不支持的组合 |
| `scripts/run_headless_sim.py` | 接入 headless 参数与启动日志 |
| `run_ray_sim.sh`、`run_local_sim.sh`、`run_million_data_sim.sh` | 转发状态来源参数；local 与 neo4j 读取组合会明确拒绝 |
| `tests/knowledge_state_test.py` | 新增行为、读取连接、恢复、错误校验与只读 EXPLAIN 验证 |
| `tests/knowledge_writer_integration_test.py` | 增加可显式启用的真实 Neo4j 三种写入模式状态读取集成测试 |
| `README.md` | 说明参数、结构、数据路径、同步边界和验证命令 |

后续核查发现，当前 `s8010` 函数已使用第二个参数设置 `KG_WRITE_MODE`，会覆盖同名环境变量。因此按当前函数定义，下次启动应使用以下命令：

```bash
# Ray 写入，主进程从 Neo4j 读取；使用新的实验 run ID。
KG_STATE_SOURCE=neo4j s8010 1027_graph_read_ray ray

# 独立副程序写入，主进程从 Neo4j 读取。
KG_STATE_SOURCE=neo4j s8010 1027_graph_read_process process

# 切回原 Ray 快照读取。
KG_STATE_SOURCE=ray s8010 1027_ray_read ray
```

这些命令是分别启动的示例，不要同时占用同一端口。本次没有重启当前服务；运行中的旧进程需要在后续启动时加载新代码。

运行状态视图结构：

| 属性 | 用途 |
|---|---|
| `run_id`、`execution_id`、`drone_id` | 查询范围与复合唯一身份 |
| `version` | 本次执行的全局操作序号；每架无人机校验其最近一次操作序号 |
| `schema_version` | JSON 协议版本，目前为 1 |
| `step_index`、`time_minute`、`updated_at` | 状态所属时间和发布时间 |
| `x`、`y`、`status`、`region_id`、`battery_remaining_m` | 便于直接查询的标量属性 |
| `payload_json` | 完整无人机快照与本次操作的完整任务更新 |

该视图按每架无人机覆盖最新状态，历史 AgentState/Step 继续沿用原结构；不会自动删除旧 execution 的视图。已有历史数据库本身不足以提供完整新状态，需要由新代码运行时发布。

### 后续建议

1. 下次启动选择新的 run ID，按需要启用 `KG_STATE_SOURCE=neo4j`；当前代码默认保持原读取来源，无需额外确认。
2. 在隔离 Neo4j 测试库执行 `KnowledgeWriterNeo4jIntegrationTest`，验证真实落库、读后可见性与三种写入路径；不要将实验对比库当成可写测试库。
3. 比较同场景 Ray 与 Neo4j 的单步总耗时、状态提交耗时、主进程查询耗时与调度耗时，再决定是否减少重复查询或拆分 JSON 读取视图。
4. 如继续实现无人机运行与主进程规划并行，需要明确接受的状态滞后、任务预留/冲突检测、派单二次校验和决策提交协议，单独改造调度循环。
5. 固定 run ID 的完整重启隔离、旧写入者退出、强制中断时未提交写入恢复等问题仍需后续处理；新增执行 ID 不代表这些问题已经全部解决。
