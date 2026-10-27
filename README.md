# LaDe Drone Delivery Viewer

基于 LaDe delivery 数据的多 agent 无人机配送仿真。当前主要模拟 delivery：每个 region 使用基于该城市全量订单预计算的固定 depot，无人机从 region depot 取货，再飞往订单目的地。

## 启动入口

### 1. 普通本地仿真

不使用 Ray，所有 drone 在同一个 Python 进程里顺序推进。适合快速启动、调试和对照验证。

```bash
cd /data/zc/datasets/LaDe/viewer
./run_local_sim.sh
```

等价核心参数：

```bash
python3 scripts/run_dynamic_sim.py --backend local --host 127.0.0.1 --port 8010
```

### 2. Ray 并行仿真

每个 drone 是一个 Ray actor，并用 `num_cpus=1` 申请一个 Ray CPU slot。适合多 agent 并行实验。

```bash
cd /data/zc/datasets/LaDe/viewer
./run_ray_sim.sh
```

如需连接已经启动的 Ray 集群：

```bash
RAY_ADDRESS=auto ./run_ray_sim.sh
```

如需限制 Ray 可用 CPU：

```bash
RAY_NUM_CPUS=4 DRONE_COUNT=4 ./run_ray_sim.sh
```

如果没有安装 Ray：

```bash
pip install ray
```

### 3. 启用 LLM 调度

先启动 vLLM，并确认模型名：

```bash
curl http://127.0.0.1:6666/v1/models
```

再启动仿真：

```bash
USE_LLM=1 ./run_ray_sim.sh
```

可覆盖模型配置：

```bash
USE_LLM=1 VLLM_MODEL=qwen2.5-7b VLLM_API_BASE=http://127.0.0.1:6666/v1 ./run_ray_sim.sh
```

### 4. 启用 Neo4j 知识图谱

默认使用内存 KG。启用 Neo4j 前先安装依赖并启动 Neo4j：

```bash
pip install neo4j
```

用环境变量切换 KG 后端：

```bash
KG_BACKEND=neo4j \
KG_RUN_ID=jilin-0925-exp001 \
KG_RECORD_AGENT_STATE=1 \
NEO4J_URI=bolt://127.0.0.1:7687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD=your-password \
./run_ray_sim.sh
```

当前 Neo4j schema：

```text
节点：Run, Drone, Task, Region, Event, Step, AgentState
关系：IN_RUN, IN_STEP, OF_DRONE, HOME_REGION, CURRENTLY_IN, BELONGS_TO, ABOUT_DRONE, ABOUT_TASK, IN_REGION, ASSIGNED, CARRYING, COMPLETED, ASSISTS, HANDOVER_TO, TAKES_OVER
```

Neo4j KG 会记录任务派单和完成事件，并为 LLM/KG 查询提供 `recent_agent_performance` 和 `recent_events`。每次 Neo4j KG 运行都会绑定一个 `run_id`：如果设置 `KG_RUN_ID`，使用该值；否则自动生成 `run-<uuid>`。`Drone`、`Task`、`Region`、`Event` 都会通过 `IN_RUN` 关系挂到 `Run` 节点下，Neo4j 查询也会限定在当前 run 内，避免不同实验数据混淆。

当前仿真会额外写入电池、载货和 step 概要：Drone 节点包含 `battery_capacity_m`、`battery_remaining_m`、`battery_consumed_m`、`queue_capacity`、`cargo_capacity`、`onboard_count` 等字段；Task 节点包含 `picked_up`、`pickup_time`、`onboard_drone_id`、`assistance_reason`、`handover_from_drone_id`、`handover_to_drone_id` 和 `takeover_count`；Event 支持 `task_picked_up`、`drone_low_battery`、`drone_returning_to_charge`、`drone_charged`、`task_assistance_requested`、`assist_dispatched`、`task_handover_completed` 和 `task_taken_over`；每轮仿真会写入 `Step` 节点，并通过 `IN_RUN` 关联到当前 `Run`。如果设置 `KG_RECORD_AGENT_STATE=1`，每个 step 还会批量写入每个 drone 的 `AgentState` 快照节点，并关联到 `Run`、`Step` 和对应 `Drone`。

互助由中央调度器在每轮派单前执行：当无人机故障、载货航程电量不足，或预计无法在 deadline 前完成任务时，系统会选择队列、电量和时效可行的援助无人机。未取货任务直接重派；已取货任务以原无人机当前位置为交接点，援助无人机到达后才取得货物并继续配送。

启用 `KG_RECORD_AGENT_STATE=1` 时，仿真启动阶段默认检查三组 `AgentState` 复合索引。已有相同标签和属性定义的 RANGE 索引会直接复用；缺失时创建并等待索引变为 `ONLINE`，随后才开始 step 写入。后续新增的状态节点由 Neo4j 自动增量维护索引。设置 `KG_AUTO_CREATE_INDEXES=0` 可以关闭该行为；首次对已有大规模数据建索引时，可用 `KG_INDEX_WAIT_TIMEOUT_SECONDS` 调整启动等待上限。

### 5. 切换知识图谱写入位置

动态服务和 headless 入口支持 `--kg-write-mode main|ray|process`，启动脚本对应环境变量为 `KG_WRITE_MODE`：

| 模式 | 写入执行位置 | 适用执行后端 |
|---|---|---|
| `main` | 主进程，保持原实现，默认值 | local / ray |
| `ray` | 无人机 actor 写本机派单、取货、完成、运行事件与状态；专门的 Ray 写入 actor 写互助协调记录、全局 Step 和快照关联 | ray |
| `process` | 独立 Python 子进程按 FIFO 队列接管全部 KG 写入 | local / ray |

两个新模式均要求 `KG_BACKEND=neo4j`。主进程保留图查询、调度、仿真编排和页面数据；数据库写入在对应写入进程中执行。每次运行共用同一个 run ID，不在 actor 之间传递数据库 Driver。

多节点 Ray 集群中，`NEO4J_URI` 必须能从所有写入 actor 所在节点访问；`127.0.0.1` 仅适合数据库与这些进程位于同一台机器的情况。

先配置现有的 `NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD`，再分别启动。以下命令是两次独立运行，使用不同 run ID：

```bash
# 版本一：Ray 侧写入
KG_BACKEND=neo4j KG_RECORD_AGENT_STATE=1 \
KG_RUN_ID=writer-ray-demo KG_WRITE_MODE=ray \
./run_in_lade.sh bash run_ray_sim.sh

# 版本二：独立副程序写入
KG_BACKEND=neo4j KG_RECORD_AGENT_STATE=1 \
KG_RUN_ID=writer-process-demo KG_WRITE_MODE=process \
./run_in_lade.sh bash run_ray_sim.sh
```

也可以直接传命令行参数，例如：

```bash
KG_BACKEND=neo4j KG_RUN_ID=writer-ray-cli \
./run_in_lade.sh python scripts/run_dynamic_sim.py --backend ray --kg-write-mode ray

./run_in_lade.sh python scripts/run_headless_sim.py \
  --backend ray --kg-backend neo4j --kg-write-mode process \
  --run-id writer-process-headless
```

`process` 的副程序由主程序自动启动、确认就绪和关闭，无需手动运行 `simulation.knowledge_worker`。写入队列有上限；错误会回传，正常结束、关闭与需要读取已提交数据时会等待写入确认。headless 保存下一检查点前也会排空先前写入，保持现有“检查点最多领先数据库一个 Step”的恢复条件。

`ray` 模式下，各无人机的局部写入在其 actor 方法返回前完成；跨无人机互助记录由专门写入 actor 确认顺序，全局 Step 在本轮全部结果返回且检查点钩子完成后提交。无人机自己不会发布全局 Step。

新模式会在写入端初始化阶段检查/创建实体唯一约束，并按原配置初始化 AgentState 索引。如果现有库包含重复身份数据，启动会报错，不会自动删除或合并数据。`KG_AUTO_CREATE_INDEXES` 控制原有 AgentState 查询索引检查，唯一约束仍是新写入模式的初始化要求。建议先在隔离数据库或新的实验 run 验证。

这次切换的是 **KG 写入位置**，现有同步时间步与 Ray 快照获取仍然保留；它不自动把任务规划改为读取上一轮数据库快照的异步流水线，也未改变电量接单规则。

新增验证：

```bash
./run_in_lade.sh python -m unittest -v tests.knowledge_writer_test

# 启动独立的两 CPU 临时 Ray 集群；结束后关闭，不连接已有集群。
LADE_KG_RAY_INTEGRATION=1 ./run_in_lade.sh python -m unittest -v \
  tests.knowledge_writer_integration_test.KnowledgeWriterRayIntegrationTest

# 仅对隔离 Neo4j 测试库启用：会建约束，创建并清理自身 UUID run。
LADE_KG_WRITER_INTEGRATION_URI=bolt://127.0.0.1:7687 \
LADE_KG_WRITER_INTEGRATION_WRITE=1 \
./run_in_lade.sh python -m unittest -v \
  tests.knowledge_writer_integration_test.KnowledgeWriterNeo4jIntegrationTest
```

headless 的检查点能力仍以 Ray 后端为主；`process` 支持 local 指动态本地仿真入口。

### 6. 仿真约束

无人机默认电池容量为 `60_000m`，固定低电阈值为 `12_000m`，安全余量为 `5_000m`。每飞行 1 米消耗 1 米电量；接单前会检查“当前位置到取货点 + 取货点到送达点 + 送达点返回最近城市 depot + 安全余量”的电量需求。实际返航阈值为 `max(12_000m, 当前位置到最近城市 depot 的距离 + 5_000m)`；触发后锁定该最近 depot，抵达后立即充满。每架无人机的充电候选包含该城市固定表中的全部 depot，而不是仅包含当前任务样本涉及的区域。

容量分为两类：`queue_capacity=6` 表示最多接受 6 个未完成任务，`cargo_capacity=3` 表示最多同时携带 3 个已取货未送达任务。任务状态包括 `waiting`、`assigned`、`picked_up`、`completed`。

区域 depot 固定保存在 `data/fixed_region_depots.json`。坐标由五个城市各区域全部有效订单的 `lng/lat` 转为 EPSG:3857 后分别取算术平均得到，运行时不会按日期、任务数量或随机抽样结果重新计算。原始 delivery 文件更新后可重新生成：

```bash
./run_in_lade.sh python scripts/build_fixed_region_depots.py
```

## 页面

启动动态服务后打开：

```text
http://127.0.0.1:8010/dynamic.html
```

动态页面的 `Backend` 默认为“服务默认”：直接运行 `scripts/run_dynamic_sim.py` 或用 `./run_ray_sim.sh` 启动时默认是 `ray`；用 `./run_local_sim.sh` 会显式使用 `local`。也可以在页面手动选择 `local` 或 `ray` 后点击“应用参数并重启”。

动态页面通过 `/events` SSE 流实时接收仿真帧。轨迹图上方的时间轴会保存当前浏览器连接已接收的帧；拖动滑块可以回看地图、指标、任务、Agent 和事件在任意历史帧的状态，回看期间后端继续运行。将滑块拖到最右侧或点击“实时”即可恢复跟随最新帧，刷新或重启仿真后时间轴会清空。

其他页面：

```text
http://127.0.0.1:8010/index.html
http://127.0.0.1:8010/simulation.html
```

## 端口

| 端口 | 服务 | 作用 |
|---|---|---|
| `8010` | 本项目动态 HTTP 服务 | 提供页面、静态资源和 `/events` SSE 仿真流 |
| `6666` | vLLM API | 启用 LLM 时提供 OpenAI-compatible `/v1` 接口 |
| `8265` | Ray Dashboard | 使用 Ray 集群时查看 actor、worker 和资源 |

查看端口是否占用：

```bash
ss -ltnp | grep -E ':8010|:6666|:8265'
```

## 常用参数

脚本支持用环境变量覆盖默认值：

```bash
CITY=Jilin DAY=0925 TASK_LIMIT=100 DRONE_COUNT=10 ./run_local_sim.sh
CITY=Jilin DAY=0925 TASK_LIMIT=100 DRONE_COUNT=10 RAY_NUM_CPUS=10 ./run_ray_sim.sh
```

常用变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HOST` | `127.0.0.1` | HTTP 服务监听地址 |
| `PORT` | `8010` | HTTP 服务端口 |
| `CITY` | `Jilin` | 城市 |
| `DAY` | `0925` | 日期 |
| `TASK_LIMIT` | `20` | 任务数量 |
| `DRONE_COUNT` | `3` | drone 数量 |
| `STEP_SECONDS` | `20` | 仿真每步秒数 |
| `USE_LLM` | `0` | 是否启用 LLM 调度 |
| `RAY_ADDRESS` | 空 | Ray 集群地址，常用 `auto` |
| `RAY_NUM_CPUS` | 空 | Ray 本地 runtime 可用 CPU 数 |
| `KG_BACKEND` | `memory` | 知识图谱后端，支持 `memory` / `neo4j` |
| `KG_WRITE_MODE` | `main` | 写入位置：`main` / `ray` / `process`，后两者要求 Neo4j，`ray` 还要求 Ray 执行后端 |
| `KG_RUN_ID` | 自动生成 | Neo4j 单次实验运行 ID，用于隔离不同 run 的图谱数据 |
| `KG_RECORD_AGENT_STATE` | `0` | 是否在 Neo4j 中记录每个 step 下每个 drone 的 `AgentState` 快照，设置为 `1` 时开启 |
| `KG_AUTO_CREATE_INDEXES` | `1` | 记录 `AgentState` 时是否在启动阶段检查、创建并等待查询索引 `ONLINE` |
| `KG_INDEX_WAIT_TIMEOUT_SECONDS` | `600` | 等待 `AgentState` 索引变为 `ONLINE` 的最长秒数 |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | Neo4j Bolt 地址 |
| `NEO4J_USER` | `neo4j` | Neo4j 用户名 |
| `NEO4J_PASSWORD` | 空 | Neo4j 密码 |

## 静态 trace

生成静态 trace：

```bash
USE_LLM=0 CITY=Jilin DAY=0925 DRONE_COUNT=3 TASK_LIMIT=20 STEP_SECONDS=20 MAX_STEPS=1000 FRAME_STRIDE=1 bash generate_res.sh
```

然后打开：

```text
http://127.0.0.1:8010/simulation.html
```

## Ray 检查

查看 Ray 资源：

```bash
ray status
```

查看 Dashboard：

```text
http://127.0.0.1:8265
```

如果 `DRONE_COUNT=3` 且每个 actor 使用 `num_cpus=1`，运行期间 `ray status` 里应能看到 CPU 使用接近 `3.0/N CPU`。

## 验证

```bash
python3 -m unittest discover -s tests -p '*_test.py'
node --check src/dynamic_app.mjs
bash -n run_local_sim.sh run_ray_sim.sh
```
