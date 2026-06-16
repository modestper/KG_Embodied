# LaDe Drone Delivery Viewer

基于 LaDe delivery 数据的多 agent 无人机配送仿真。当前主要模拟 delivery：按 region 构造 depot，无人机从 region depot 取货，再飞往订单目的地。

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
NEO4J_URI=bolt://127.0.0.1:7687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD=your-password \
./run_ray_sim.sh
```

当前 Neo4j schema：

```text
节点：Drone, Task, Region, Event
关系：HOME_REGION, CURRENTLY_IN, BELONGS_TO, ABOUT_DRONE, ABOUT_TASK, IN_REGION, ASSIGNED, COMPLETED
```

Neo4j KG 会记录任务派单和完成事件，并为 LLM/KG 查询提供 `recent_agent_performance` 和 `recent_events`。

## 页面

启动动态服务后打开：

```text
http://127.0.0.1:8010/dynamic.html
```

动态页面的 `Backend` 默认为“服务默认”：用 `./run_local_sim.sh` 启动时默认是 `local`，用 `./run_ray_sim.sh` 启动时默认是 `ray`。也可以在页面手动选择 `local` 或 `ray` 后点击“应用参数并重启”。

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
