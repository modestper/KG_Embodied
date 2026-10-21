# LaDe Viewer 中知识图谱与 LLM 的使用说明

本文说明 `LaDe/viewer` 仿真项目中所谓“知识图谱”和 LLM 的实际实现、数据流、Prompt 结构、回退机制，以及它们之间的关系。

## 1. 先给出结论

当前项目中的“知识图谱”并不是 Neo4j、RDF、属性图或向量数据库意义上的知识图谱。

它的实际实现是：

```python
class TemporalKnowledgeStore:
    def __init__(self):
        self.events = []
```

也就是一个保存在内存中的时序事件列表。它记录两种历史事件：

1. `task_assigned`：某个任务被分配给某个 Drone。
2. `task_completed`：某个 Drone 完成了某个任务，以及任务是否迟到。

它的主要作用有两个：

1. 根据当前区域、任务截止时间和 Drone 队列长度，筛选进入 LLM Prompt 的任务与 Drone。
2. 从最近 30 分钟的事件中，生成 Drone 的近期表现和近期事件，作为 `kg_context` 加入 LLM Prompt。

当前项目中 LLM 有两种可选用法：

1. `LLMPlanner`：针对“一个 Drone 和一个任务”返回一个成本值。
2. `RegionLLMCoordinator`：针对“一个区域中的一批任务和一批 Drone”返回批量分配结果。

动态仿真页面的 `use_llm` 开关和 `generate_res.sh` 中的 `DRONE_COORDINATOR=llm-region`，实际启用的是第二种 `RegionLLMCoordinator`。默认情况下，每个 Drone 自己的成本规划器仍然是 `RuleBasedPlanner`。

## 2. 相关代码文件

| 文件 | 作用 |
| --- | --- |
| `simulation/delivery_sim.py` | 定义任务、Drone、规则拍卖协调器、仿真循环，并记录知识事件 |
| `simulation/knowledge.py` | 定义 `TemporalKnowledgeStore`，查询区域上下文 |
| `simulation/planner.py` | 定义规则规划器、LLM 规划器、区域 LLM 协调器和 Prompt |
| `scripts/build_delivery_sim.py` | 离线生成仿真 trace |
| `scripts/run_dynamic_sim.py` | 动态仿真服务，通过 SSE 向前端发送帧、知识事件和 LLM 调用 |
| `src/dynamic_app.mjs` | 动态页面展示最新知识事件、LLM 输入和 LLM 输出 |
| `tests/knowledge_test.py` | 验证知识事件记录、紧急任务筛选和候选 Drone 筛选 |
| `tests/planner_test.py` | 验证 LLM Prompt、知识上下文、回退机制和日志 |

## 3. 仿真的基础对象

### 3.1 DeliveryTask

任务定义在 `simulation/delivery_sim.py`：

```python
@dataclass
class DeliveryTask:
    task_id: str
    city: str
    x: float
    y: float
    release_minute: int
    deadline_minute: int
    aoi_id: str
    region_id: str
    pickup_x: float | None = None
    pickup_y: float | None = None
    picked_up: bool = False
    status: str = "waiting"
    assigned_to: str | None = None
    completed_at: int | None = None
```

其中：

| 字段 | 含义 |
| --- | --- |
| `task_id` | 订单 ID |
| `city` | 城市 |
| `x`, `y` | 配送目的地坐标 |
| `release_minute` | 任务可被分配的时间，来自数据集的 `accept_time` |
| `deadline_minute` | 配送截止时间，来自数据集的 `delivery_time` |
| `aoi_id` | AOI 标识 |
| `region_id` | 区域标识，是区域 LLM 调度的重要分组键 |
| `pickup_x`, `pickup_y` | 取货点坐标，在当前实现中通常是区域 depot |
| `picked_up` | Drone 是否已经到达取货点 |
| `status` | `waiting`、`assigned` 或 `completed` |
| `assigned_to` | 被分配到的 Drone ID |
| `completed_at` | 完成时间 |

任务从 CSV 中加载时，项目读取：

```python
order_id
city
lng
lat
accept_time
delivery_time
aoi_id
region_id
```

`build_region_depots()` 从 `data/fixed_region_depots.json` 读取每个区域的固定 depot。该文件预先使用城市全量订单计算：

```python
depot_x = 区域内所有任务目的地 x 的平均值
depot_y = 区域内所有任务目的地 y 的平均值
```

运行时只选择当前任务涉及的区域，不再根据当前日期、任务上限或随机抽样子集重新计算坐标。每个任务的 `pickup_x` 和 `pickup_y` 都会被设置为它所属区域的固定 depot 坐标。因此，当前仿真把任务理解为：

```text
从区域 depot 取货 -> 飞到任务目的地
```

### 3.2 DroneAgent

Drone 定义同样位于 `simulation/delivery_sim.py`：

```python
@dataclass
class DroneAgent:
    drone_id: str
    x: float
    y: float
    capacity: int = 5
    speed_m_per_minute: float = 1200
    battery_range_m: float = 80_000
    queue: list[DeliveryTask] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    total_distance_m: float = 0
    status: str = "idle"
    region_id: str | None = None
    planner: object | None = None
```

Drone 的重要状态包括：

| 字段 | 含义 |
| --- | --- |
| `x`, `y` | 当前坐标 |
| `capacity` | 队列最多能接收多少任务 |
| `queue` | 当前等待执行的任务队列 |
| `completed` | 已完成任务 ID 列表 |
| `total_distance_m` | 累计飞行距离 |
| `region_id` | Drone 所属区域 |
| `planner` | 用于估算任务成本的规划器 |

在 `depot_mode=region` 时，Drone 会被放置到某个区域 depot，并绑定对应的 `region_id`。

## 4. 知识存储到底保存了什么

### 4.1 它保存的是事件，不保存完整实体图

`TemporalKnowledgeStore` 只有一个列表：

```python
self.events = []
```

它不会长期保存完整的 Task 对象、Drone 对象、区域对象，也没有显式的节点和边。

当前状态中的任务和 Drone 是在查询时作为参数传入的：

```python
query_region_context(
    region_id,
    tasks,
    drones,
    now_minute,
)
```

因此要区分：

```text
知识存储中真正持久存在的内容：events 列表
查询时临时传入的内容：当前 tasks、当前 drones、当前时间
```

### 4.2 task_assigned 事件

当一个任务成功分配后，`SimulationEnv.release_and_assign()` 会调用：

```python
self.knowledge_store.record_assignment(self.now_minute, task, drone)
```

记录的数据格式为：

```json
{
  "time_minute": 480,
  "time": "08:00:00",
  "type": "task_assigned",
  "region_id": "10",
  "task_id": "task-1",
  "drone_id": "drone-1",
  "queue_len": 3
}
```

字段说明：

| 字段 | 来源 | 含义 |
| --- | --- | --- |
| `time_minute` | 仿真当前时间 | 用于时间窗口过滤 |
| `time` | `format_minute(time_minute)` | 人类可读时间 |
| `type` | 固定值 | 表示任务分配事件 |
| `region_id` | `task.region_id` | 事件所属区域 |
| `task_id` | `task.task_id` | 被分配任务 |
| `drone_id` | `drone.drone_id` | 接收任务的 Drone |
| `queue_len` | `len(drone.queue)` | 分配完成后 Drone 的队列长度 |

注意，`queue_len` 是在 `drone.assign(task)` 已经执行之后记录的，因此包含刚刚分配的任务。

### 4.3 task_completed 事件

当 Drone 在移动步骤中完成任务后，仿真会调用：

```python
self.knowledge_store.record_completion(next_time, task, drone)
```

记录的数据格式为：

```json
{
  "time_minute": 500,
  "time": "08:20:00",
  "type": "task_completed",
  "region_id": "10",
  "task_id": "task-1",
  "drone_id": "drone-1",
  "late": false
}
```

`late` 的计算方式是：

```python
time_minute > task.deadline_minute
```

也就是完成时间严格晚于截止时间时才算迟到。

### 4.4 哪些信息没有传给知识存储

当前事件中没有保存：

- Drone 的实时坐标轨迹
- Drone 的电量或剩余航程
- Drone 的速度
- 任务的目的地坐标
- 任务的截止时间
- 任务从分配到完成的耗时
- 任务的实际飞行距离
- LLM 给出的 `reason`
- LLM 的原始响应
- 区域之间的关系
- AOI 之间的关系

这些信息有些存在于仿真对象或 LLM 日志中，但不在 `TemporalKnowledgeStore.events` 中。

## 5. 知识存储如何生成区域上下文

区域 LLM 调度器在每次调度前调用：

```python
context = self.knowledge_store.query_region_context(
    region_id=region_id,
    tasks=tasks,
    drones=drones,
    now_minute=now_minute,
    max_tasks=self.max_tasks_per_region,
    max_drones=self.max_drones_per_region,
)
```

`query_region_context()` 返回四类信息：

```json
{
  "region_id": "10",
  "time": "08:00:00",
  "urgent_tasks": [],
  "candidate_drones": [],
  "recent_agent_performance": {},
  "recent_events": []
}
```

### 5.1 urgent_tasks：筛选紧急任务

首先只保留当前区域的任务：

```python
region_tasks = [task for task in tasks if task.region_id == region_id]
```

然后按照下面的顺序排序：

```python
(task.deadline_minute, task.release_minute)
```

也就是：

1. 截止时间越早，优先级越高。
2. 截止时间相同时，释放时间越早，优先级越高。

最后最多取 `max_tasks` 个任务，默认是 20：

```python
urgent_tasks = sorted(... )[:max_tasks]
```

每个紧急任务会被摘要成：

```json
{
  "task_id": "task-1",
  "deadline": "09:00:00",
  "pickup": {"x": 100.0, "y": 200.0},
  "dropoff": {"x": 1200.0, "y": 900.0},
  "distance_from_depot_m": 1303.8
}
```

### 5.2 candidate_drones：筛选候选 Drone

首先只保留：

1. 属于当前区域的 Drone。
2. 队列尚未达到容量上限的 Drone。

代码为：

```python
region_drones = [
    drone
    for drone in drones
    if drone.region_id == region_id and drone.can_accept()
]
```

然后按下面的顺序排序：

```python
(
    len(drone.queue),
    euclidean(drone.position, urgent_tasks[0].pickup_position),
)
```

也就是：

1. 队列更短的 Drone 优先。
2. 队列长度相同时，离最紧急任务取货点更近的 Drone 优先。

最后最多取 `max_drones` 个，默认是 10。

每个候选 Drone 会被摘要成：

```json
{
  "drone_id": "drone-1",
  "position": {"x": 100.0, "y": 200.0},
  "queue_len": 2,
  "distance_to_depot_m": 350.5,
  "completed": 8
}
```

### 5.3 recent_agent_performance：最近 30 分钟表现

默认窗口为最近 30 分钟：

```python
start = now_minute - window_minutes
```

它只统计当前区域内、时间不早于 `start` 的事件。

聚合结果格式为：

```json
{
  "drone-1": {
    "assigned": 4,
    "completed": 3,
    "late": 1
  },
  "drone-2": {
    "assigned": 2,
    "completed": 2,
    "late": 0
  }
}
```

这里的三个计数分别来自：

- `assigned`：`task_assigned` 事件数量
- `completed`：`task_completed` 事件数量
- `late`：`task_completed` 中 `late=true` 的数量

### 5.4 recent_events：最近 30 分钟原始事件

它同样只返回当前区域最近 30 分钟的事件，并最多保留最后 20 条：

```python
return events[-limit:]
```

这让 LLM 可以看到近期发生过哪些具体分配和完成事件，而不仅是聚合统计。

## 6. 知识存储如何影响 LLM Prompt

知识存储对 Prompt 的影响分为两层。

### 6.1 第一层：决定哪些任务和 Drone 能进入 Prompt

`RegionLLMCoordinator` 不会把所有任务和 Drone 都发给 LLM。

它先从知识上下文中拿到：

```python
context["urgent_tasks"]
context["candidate_drones"]
```

然后只保留这些 ID 对应的原始对象：

```python
batch_tasks = self.tasks_from_context(context, region_tasks)
batch_drones = self.drones_from_context(context, region_drones)
```

因此，即使 `urgent_tasks` 和 `candidate_drones` 没有直接放在 `kg_context` 字段中，它们已经决定了顶层 `tasks` 和 `drones` 的内容。

### 6.2 第二层：把近期历史加入 kg_context

构造 Prompt 时，只有下面两个知识字段被直接加入 `kg_context`：

```python
payload["kg_context"] = {
    "recent_agent_performance": context["recent_agent_performance"],
    "recent_events": context["recent_events"],
}
```

所以最终传给 LLM 的 Prompt 中：

```text
顶层 tasks    <- 由 urgent_tasks 筛选后的当前任务
顶层 drones   <- 由 candidate_drones 筛选后的当前 Drone
kg_context    <- 最近表现和最近事件
```

## 7. 当前主要使用的 LLM 调用：RegionLLMCoordinator

### 7.1 它什么时候被启用

当环境变量满足：

```bash
DRONE_COORDINATOR=llm-region
```

或：

```bash
DRONE_COORDINATOR=region-llm
```

`build_coordinator_from_env()` 会返回 `RegionLLMCoordinator`。

动态仿真页面开启 LLM 时，`planner_environment` 会设置：

```python
os.environ["DRONE_COORDINATOR"] = "llm-region"
os.environ["VLLM_API_BASE"] = self.args.vllm_api_base
os.environ["VLLM_MODEL"] = self.args.vllm_model
os.environ["LLM_MAX_TOKENS"] = str(self.args.llm_max_tokens)
```

需要注意：这里没有设置 `DRONE_PLANNER=llm`。因此动态页面启用 LLM 时：

```text
Coordinator: RegionLLMCoordinator
Drone planner: RuleBasedPlanner
```

### 7.2 每次调度的调用链

每个仿真步骤会执行：

```text
SimulationEnv.run()
  -> release_and_assign()
    -> 找到已释放且仍 waiting 的任务
    -> coordinator.assign(...)
      -> 按 region_id 对任务分组
      -> 找到同区域且可接单的 Drone
      -> 查询知识上下文
      -> 筛选紧急任务与候选 Drone
      -> 构造 LLM 请求
      -> 调用 /chat/completions
      -> 解析 assignments
      -> 把任务加入 Drone 队列
    -> 记录 task_assigned 知识事件
  -> Drone 移动并完成任务
  -> 记录 task_completed 知识事件
```

### 7.3 区域分组和限制

任务会先按 `region_id` 分组：

```python
for region_id, region_tasks in self.group_tasks(tasks).items():
```

只会选择同区域 Drone：

```python
region_drones = [
    drone for drone in drones
    if drone.can_accept() and drone.region_id == region_id
]
```

如果某个区域没有 Drone，代码会直接跳过：

```python
if not region_drones:
    continue
```

这意味着 `RegionLLMCoordinator` 当前不支持跨区域调度。某个区域没有 Drone 时，该区域任务不会交给 LLM，也不会自动使用其他区域 Drone。

## 8. RegionLLMCoordinator 的 Prompt 结构

### 8.1 System Prompt

系统提示词为：

```text
You are a regional drone delivery coordinator.
Return only JSON with assignments:[{task_id:string,drone_id:string,reason:string}].
Use only provided task_id and drone_id values.
Do not include markdown.
```

它要求模型：

1. 充当区域 Drone 配送协调器。
2. 只返回 JSON。
3. 返回 `assignments` 数组。
4. 只能使用 Prompt 中提供的任务 ID 和 Drone ID。
5. 不要输出 Markdown。

### 8.2 User Prompt

User Prompt 是一个紧凑 JSON 字符串，结构如下：

```json
{
  "time": "08:30:00",
  "objective": "Assign waiting delivery tasks to drones. Minimize late delivery and balance queues.",
  "region_id": "53",
  "depot": {
    "x": 14088982.457,
    "y": 5437141.175
  },
  "tasks": [
    {
      "task_id": "3282885",
      "deadline": "10:46:00",
      "dropoff": {
        "x": 14087076.357,
        "y": 5436667.746
      },
      "distance_from_depot_m": 1964.0
    }
  ],
  "drones": [
    {
      "drone_id": "drone-5",
      "position": {
        "x": 14086991.244,
        "y": 5438159.152
      },
      "queue_len": 5,
      "distance_to_depot_m": 2236.3,
      "completed": 11
    }
  ],
  "kg_context": {
    "recent_agent_performance": {
      "drone-5": {
        "assigned": 8,
        "completed": 3,
        "late": 0
      }
    },
    "recent_events": [
      {
        "time_minute": 518.0,
        "time": "08:38:00",
        "type": "task_completed",
        "region_id": "53",
        "task_id": "113965",
        "drone_id": "drone-5",
        "late": false
      }
    ]
  }
}
```

### 8.3 Prompt 中各字段的来源

| Prompt 字段 | 来源 |
| --- | --- |
| `time` | 当前仿真时间 |
| `objective` | 代码中固定字符串 |
| `region_id` | 当前任务分组的区域 |
| `depot` | 第一条候选任务的取货点，即区域 depot |
| `tasks` | 知识上下文筛选后的紧急任务 |
| `tasks[].task_id` | `DeliveryTask.task_id` |
| `tasks[].deadline` | `DeliveryTask.deadline_minute` |
| `tasks[].dropoff` | `DeliveryTask.x`, `DeliveryTask.y` |
| `tasks[].distance_from_depot_m` | 取货点到目的地的欧氏距离 |
| `drones` | 知识上下文筛选后的候选 Drone |
| `drones[].position` | Drone 当前坐标 |
| `drones[].queue_len` | Drone 当前任务队列长度 |
| `drones[].distance_to_depot_m` | Drone 当前坐标到区域 depot 的欧氏距离 |
| `drones[].completed` | Drone 累计完成任务数量 |
| `kg_context.recent_agent_performance` | 知识事件最近 30 分钟聚合 |
| `kg_context.recent_events` | 知识事件最近 30 分钟明细，最多 20 条 |

### 8.4 Prompt 中没有包含的重要信息

当前区域 Prompt 没有包含：

- Drone 速度
- Drone 电池容量或剩余航程
- Drone 队列中具体有哪些任务
- 已排队任务的截止时间
- 候选任务的释放时间
- 候选任务的 AOI
- 任务优先级的显式评分
- 跨区域 Drone 信息
- 区域之间的距离或关系

因此，LLM 主要依据：

```text
任务截止时间 + 配送距离 + Drone 队列长度 + Drone 到 depot 距离
+ 最近分配/完成/迟到历史
```

做出分配。

## 9. LLM 应该返回什么

模型应该返回：

```json
{
  "assignments": [
    {
      "task_id": "3282885",
      "drone_id": "drone-5",
      "reason": "short queue and close to depot"
    }
  ]
}
```

解析代码只读取：

```python
json.loads(content).get("assignments", [])
```

之后应用分配时只使用：

```python
decision.get("task_id")
decision.get("drone_id")
```

`reason` 目前不会被保存到知识存储，也不会影响仿真，只是要求模型输出以便解释。

系统还会再次校验：

1. `task_id` 必须属于本次候选任务。
2. 任务仍然必须是 `waiting`。
3. `drone_id` 必须属于本次候选 Drone。
4. Drone 仍然必须有容量。

无效 ID 或已满队列不会被应用。

## 10. LLM 调用失败时如何处理

`RegionLLMCoordinator.assign()` 把请求、响应解析和分配放在 `try` 中。

以下情况都会进入异常路径：

- vLLM 无法连接
- 请求超时
- 模型返回非 JSON
- JSON 被截断
- JSON 顶层不是对象
- 返回数据结构不符合预期
- 代码解析时发生其他异常

异常后会：

1. 记录一次 `llm_event`，其中 `used_fallback=true`。
2. 使用 `AuctionCoordinator` 做规则分配。

规则回退协调器会优先选择同区域 Drone，并调用 Drone 的 `estimate_cost()`。

默认 Drone 使用 `RuleBasedPlanner`，成本公式为：

```text
总成本 =
  Drone 到取货点距离
  + 取货点到目的地距离
  + 队列长度 * 1500
  + 预计迟到分钟数 * 250
  + 跨区域惩罚 25000
  + 电池风险惩罚 10000
```

因此，即使 LLM 返回错误 JSON，仿真通常仍能继续，只是该次分配使用规则算法。

## 11. 另一种 LLM 用法：LLMPlanner

`LLMPlanner` 与 `RegionLLMCoordinator` 是不同层级的组件。

### 11.1 它解决的问题

`LLMPlanner` 不直接批量分配任务。它只回答：

```text
把这个任务交给这个 Drone，成本是多少？
```

它返回：

```json
{
  "accept": true,
  "cost": 123.5,
  "reason": "near region depot"
}
```

规则拍卖协调器可以比较多个 Drone 的成本，并选择成本最低者。

### 11.2 它的 Prompt

`LLMPlanner` 的 Prompt 包含单个 Drone 和单个任务：

```json
{
  "now": "08:00:00",
  "drone": {
    "id": "drone-1",
    "region_id": "10",
    "queue_len": 2,
    "position": {"x": 0, "y": 0},
    "speed_m_per_minute": 1200,
    "total_distance_m": 5000
  },
  "task": {
    "id": "task-1",
    "region_id": "10",
    "pickup": {"x": 100, "y": 0},
    "dropoff": {"x": 1200, "y": 0},
    "release": "08:00:00",
    "deadline": "09:00:00"
  }
}
```

### 11.3 它不读取知识存储

`LLMPlanner` 当前不会查询 `TemporalKnowledgeStore`，也没有 `kg_context`。

只有 `RegionLLMCoordinator` 使用知识存储上下文。

### 11.4 当前默认没有启用它

只有设置：

```bash
DRONE_PLANNER=llm
```

`build_planner_from_env()` 才会创建 `LLMPlanner`。

动态页面的 `use_llm` 开关默认只设置 `DRONE_COORDINATOR=llm-region`，不会设置 `DRONE_PLANNER=llm`。

## 12. 知识事件、LLM 日志和前端展示的区别

项目中有三类容易混淆的数据。

### 12.1 knowledge_store.events

这是知识存储的核心数据，内容只有任务分配和任务完成事件。

离线 trace 最多保存最后 200 条：

```python
"kg_events": self.knowledge_store.events[-200:]
```

### 12.2 coordinator.llm_events

这是内存中的 LLM 调用记录，最多保留最后 20 条：

```python
self.llm_events = self.llm_events[-20:]
```

每条包含：

```json
{
  "time": "08:30:00",
  "region_id": "53",
  "model": "qwen2.5-7b",
  "max_tokens": 512,
  "elapsed_ms": 1520,
  "request": {},
  "response": {},
  "decisions": [],
  "error": null,
  "used_fallback": false
}
```

### 12.3 data/llm_logs/*.jsonl

动态仿真开启 LLM 时，会创建 JSONL 日志文件：

```text
data/llm_logs/llm_<时间>_<城市>_<任务数>_<Drone数>_...jsonl
```

日志第一条通常是 `run_start`，后续每次 LLM 调用写一条 `llm_call`。

它保存完整请求、完整响应、解析结果、错误和是否回退，是检查 Prompt 和模型行为最直接的材料。

### 12.4 动态前端展示

动态服务通过 SSE 每帧发送：

```python
"kg_events": env.knowledge_store.events[-10:]
"llm_events": llm_events(env)[-5:]
```

前端：

- 展示最近知识事件。
- 展示最新一次 LLM 请求。
- 展示最新一次 LLM 响应、解析结果、错误和 fallback 状态。

因此页面看到的是截断后的近期数据，不是所有历史。

## 13. 完整数据流示例

假设当前仿真时间为 `08:30:00`，区域 `53` 有两个已释放任务和一个可接单 Drone。

### 第一步：寻找可分配任务

`SimulationEnv.release_and_assign()` 选择：

```python
task.status == "waiting"
task.release_minute <= now_minute
```

### 第二步：按区域分组

`RegionLLMCoordinator` 将区域 `53` 的任务放在一起，并只选择区域 `53` 的可用 Drone。

### 第三步：查询知识上下文

`TemporalKnowledgeStore.query_region_context()`：

1. 按截止时间排序任务。
2. 按队列长度和到 depot 距离排序 Drone。
3. 统计区域 `53` 最近 30 分钟的分配数、完成数、迟到数。
4. 读取区域 `53` 最近 20 条事件。

### 第四步：构造 Prompt

顶层 `tasks` 和 `drones` 是筛选后的当前状态，`kg_context` 是历史状态。

### 第五步：调用 vLLM

请求发送到：

```text
http://127.0.0.1:6666/v1/chat/completions
```

模型名称来自：

```bash
VLLM_MODEL
```

### 第六步：解析模型输出

模型返回任务和 Drone ID 对，协调器校验 ID 和容量后调用：

```python
drone.assign(task)
```

### 第七步：记录知识事件

仿真环境根据分配结果记录：

```json
{
  "type": "task_assigned",
  "region_id": "53",
  "task_id": "...",
  "drone_id": "...",
  "queue_len": 3
}
```

### 第八步：Drone 移动并完成任务

Drone 到达取货点和目的地后，记录：

```json
{
  "type": "task_completed",
  "region_id": "53",
  "task_id": "...",
  "drone_id": "...",
  "late": false
}
```

这些新事件会影响下一次 LLM 调用中的 `kg_context`。

## 14. 当前实现的局限与注意事项

### 14.1 它不是严格意义上的知识图谱

当前实现更准确的名称是：

```text
Temporal Event Store
```

或者：

```text
时序配送事件记忆
```

如果要成为真正的知识图谱，通常需要显式建模：

- Task 节点
- Drone 节点
- Region 节点
- AOI 节点
- `ASSIGNED_TO` 关系
- `BELONGS_TO_REGION` 关系
- `DELIVERS_TO_AOI` 关系
- `COMPLETED_BY` 关系
- 时间属性、距离属性和状态属性

并支持图查询或图推理。

### 14.2 知识存储只存在于单次仿真内存中

每次创建 `SimulationEnv` 都会创建新的 `TemporalKnowledgeStore`。

不同仿真运行之间不会共享经验。JSONL 日志虽然会落盘，但下一次仿真不会读取这些日志重新构建知识。

### 14.3 LLM Prompt 中的历史信息有限

历史表现只有计数，没有：

- 平均配送耗时
- 平均配送距离
- 准时率
- 当前负载变化趋势
- 区域拥堵情况
- Drone 故障或电池历史

因此 LLM 能做的“知识增强”仍然比较基础。

### 14.4 小模型可能输出非法 JSON

项目日志中已经出现过：

```text
Unterminated string
'list' object has no attribute 'get'
Expecting property name enclosed in double quotes
```

这说明模型有时会：

- 输出被截断的 JSON
- 返回数组而不是对象
- 返回不符合 JSON 语法的文本

当前代码会自动 fallback，不会直接让仿真崩溃，但会降低 LLM 实际参与调度的比例。

### 14.5 reason 没有形成反馈闭环

Prompt 要求 LLM 返回 `reason`，但代码不使用它，也不写入知识事件。

所以当前知识存储无法学习“为什么上次这样分配”。

### 14.6 区域没有 Drone 时不会跨区域调度

`RegionLLMCoordinator` 在某区域没有 Drone 时直接跳过。知识存储也不会推荐其他区域的 Drone。

这可能导致某些区域任务长期保持 `waiting`。

## 15. 如何查看真实 Prompt 和知识上下文

最直接的方法是查看：

```text
LaDe/viewer/data/llm_logs/*.jsonl
```

每个 `llm_call` 记录中的：

```text
request.messages[1].content
```

就是实际发给模型的 User Prompt。它是一个 JSON 字符串，需要再做一次 JSON 解析。

可以用下面的命令查看第一条 LLM 调用的 Prompt：

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path("LaDe/viewer/data/llm_logs/你的日志文件.jsonl")

for line in path.read_text(encoding="utf-8").splitlines():
    record = json.loads(line)
    if record.get("event") != "llm_call":
        continue
    content = record["request"]["messages"][1]["content"]
    print(json.dumps(json.loads(content), ensure_ascii=False, indent=2))
    break
PY
```

查看某次调用是否使用 fallback：

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path("LaDe/viewer/data/llm_logs/你的日志文件.jsonl")

for line in path.read_text(encoding="utf-8").splitlines():
    record = json.loads(line)
    if record.get("event") == "llm_call":
        print(record["time"], record["region_id"], record["used_fallback"], record["error"])
PY
```

## 16. 最简洁的理解方式

可以把当前系统理解成下面的闭环：

```text
原始配送任务
  -> 仿真当前状态
  -> 任务释放
  -> 知识存储筛选紧急任务和候选 Drone
  -> 知识存储汇总最近 30 分钟历史
  -> RegionLLMCoordinator 构造 Prompt
  -> vLLM 返回任务分配
  -> Drone 执行任务
  -> 记录分配和完成事件
  -> 新事件进入下一次 Prompt
```

其中：

```text
当前状态决定“现在有什么可选”
知识事件决定“最近发生了什么”
LLM 决定“这次如何分配”
规则 fallback 保证“LLM 出错时仿真仍能继续”
```
