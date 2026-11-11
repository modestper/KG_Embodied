### 任务背景

用户希望新建 Ray 查询测试代码，默认启动 100 个工作进程，并让每个进程使用独立的 CPU；保留现有线程版、普通多进程版和查询模板。

本次新增单机 Ray Actor 入口，复用现有 i0-i55 查询、参数构造、结果消费和统计逻辑。未对数据库执行 100 进程压测，未修改宿主机隔离配置。

### 关键结论

- 新入口为 `scripts/lade_llm_context_queries_ray.py`，默认创建 **100 个独立 Ray Actor 进程**；另有父进程及 Ray 控制服务进程，因此操作系统进程总数不止 100。
- 每个 Actor 显式申请 `num_cpus=1`，并绑定到一个选定的逻辑 CPU；选择 CPU 时按物理核心去重，避免两个 Actor 使用同核的超线程。当前机器默认可选 CPU 0—99，对应 100 个不同物理核心。
- 绑定覆盖 Actor 的执行线程与已经存在的 Ray 后台线程，并在就绪、执行时重新检查；每个 Actor 自行创建和复用 Neo4j Driver，包内查询继续顺序执行。
- **这里保证的是本脚本的 Actor 分别绑定到不同物理核心，不是宿主机级的排他独占。** Ray 的 CPU 资源是逻辑调度资源，affinity 约束执行位置，无法禁止数据库、Ray 控制服务或其他系统进程运行在相同核心及其超线程上。摘要明确记录 `system_cpu_exclusivity: false`。
- 默认 drone 范围为 `drone-1` 到 `drone-100`，进程数调整时自动对应 `drone-1` 到 `drone-N`；显式传入 drone 参数则保留该选择。默认 repeat 为 1，查询包仍为 `core`，需要全部 56 条时使用 `--queries all`。
- 所有 Actor 初始化和绑定校验完成后才提交查询；每个 Actor 最多一包任务在执行，主进程动态分配后续任务并统一写入日志。启动、查询阶段和清理分别计时，整轮 `wall_ms` 包含三者。
- 验证完成：普通验证 31 项通过；另有 1 项真实 Ray 双 Actor 离线测试通过，确认独立 PID、不同物理核心、每 Actor 分配 1 CPU、查询顺序、完整输出、失败记录、Driver 复用与关闭及父进程 affinity 不受改变。

### 执行过的操作

- 阅读 `AGENTS.md`、已有线程入口、普通多进程入口、相关测试及项目中现有 Ray 初始化代码。
- 核查运行环境：Python 3.13.13、Ray 2.55.1；检查 CPU affinity 与物理拓扑。当前允许范围内有 128 个物理核心，足够选择 100 个不同核心。
- 查阅 Ray 官方资源说明和 Actor 文档，核对逻辑资源、独立 Actor 进程及退出行为。官方明确说明 `num_cpus=1` 本身不提供物理 CPU 隔离：[Ray Resources](https://docs.ray.io/en/master/core/scheduling/resources.html)。
- 新增并运行以下验证：

```bash
PYTHONDONTWRITEBYTECODE=1 ./run_in_lade.sh python -m unittest -v \
  tests.lade_llm_context_queries_ray_test \
  tests.lade_llm_context_queries_test \
  tests.lade_llm_context_queries_process_test
```

上述命令共发现 32 项，其中 31 项通过，真实 Ray 测试默认跳过。随后单独启用并通过该项：

```bash
LADE_CONTEXT_RAY_TEST=1 PYTHONDONTWRITEBYTECODE=1 \
  ./run_in_lade.sh python -m unittest -v \
  tests.lade_llm_context_queries_ray_test.RayContextQueryIntegrationTest
```

- 真实测试最初在沙箱内因本地 socket 系统调用被拒绝失败（GCS 日志为 `Operation not permitted`），随后获得沙箱外执行许可并通过。测试使用模拟 Driver，不访问 Neo4j；没有对连接数据库执行额外授权操作。
- 双 Actor 测试执行 6 包上下文，每包 56 条内置查询及 1 条刻意失败的自定义查询，共 342 条记录；检查 336 条成功、6 条失败，以及实际执行线程的单 CPU affinity。
- 验证直接脚本的 `--help`、`--dry-run`；验证默认参数确实为 100 个 Actor / 100 个 drone，默认 CPU 选择确实对应 100 个不同物理核心。
- 对新增两个代码文件执行空白格式检查。未改动原查询文件。

### 修改内容

- 新增 `scripts/lade_llm_context_queries_ray.py`：单机 Ray Actor 压测入口、CPU 拓扑选择与线程 affinity、动态任务分发、日志与分阶段统计。
- 新增 `tests/lade_llm_context_queries_ray_test.py`：参数、CPU 拓扑选择、超线程冲突/CPU 不足拒绝、离线模式与真实 Actor 测试。
- 新增本总结文档。原线程、多进程和模板文件均未修改。

#### 执行流程

1. 父进程解析命令，加载并选择查询，校验每条查询参数。
2. 从当前允许使用的 CPU 中选择 N 个不同物理核心，每核只选一个逻辑 CPU；不足 N 个时明确报错，不静默重复分配。用户可用 `--cpu-ids` 显式选择相同数量的编号。
3. 启动独立本地 Ray runtime，显式使用 `address="local"` 和独立的 `/tmp/lade-context-ray-*` 目录，不加入已有仿真集群。
4. 创建 N 个 Actor，每个预留 1 个 Ray CPU；Actor 将自身线程绑定到指定 CPU，创建自己的 Driver。正常关闭时先调用 Driver.close，再销毁这些 Actor 并关闭本次 runtime。
5. 父进程等待全部 Actor 的 ready 信息，确认不同 PID、正确 CPU affinity，然后才发出查询任务。
6. 每个任务是一名 drone 的一次整包查询；同一个 Actor 串行执行包内查询。完成后由主进程取回结果、记录日志，再向该 Actor 分配下一包任务。

Actor 并不永久对应某个 drone。Ray 资源预留为每个 Actor 提供整个生命周期内的一个调度 CPU 配额；affinity 则将其线程限制在指定硬件 CPU 上。两者作用不同。

Linux 的 `sched_setaffinity(0, ...)` 只影响调用线程，Ray Actor 在构造函数执行前已经有后台线程，因此脚本遍历 `/proc/self/task` 设置现存线程的 affinity，并重复检查以覆盖启动期间的线程变化。新线程继承创建线程的 affinity。该设置仅作用于本次创建的 Actor 自身，不修改父进程、数据库或已有 Ray 服务。

#### 默认值与运行方式

默认关键参数：

| 参数 | 默认行为 |
|---|---|
| `--max-workers` | 100 个 Actor |
| 每 Actor Ray CPU | 固定 1 |
| CPU 选择 | 从允许范围内选择不同物理核心；当前机器为 CPU 0—99 |
| `--drone-ids` | 未显式指定 drone 时自动生成 drone-1 至 drone-N |
| `--queries` | core；使用 all 才是全部 56 条 |
| `--repeat` | 1 |
| `--max-connection-pool-size` | 每 Actor 为 1 |
| `--startup-timeout` | 等待全部 Actor 就绪最多 180 秒；不等于 Ray 内部初始化的总超时 |

沿用本次已有结果的 run、step 与查询组合，可在仓库根目录执行下面的命令。该命令在本任务中未实际运行：

```bash
./run_in_lade.sh python scripts/lade_llm_context_queries_ray.py \
  --uri bolt://127.0.0.1:61687 \
  --run-id hangzhou-0621-headless-002 \
  --step-index 2300 \
  --queries all \
  --repeat 20 \
  --query-log-jsonl ray_test100/query_logs.jsonl \
  --context-jsonl ray_test100/contexts.jsonl \
  --summary-json ray_test100/summary.json
```

这里不写 `--max-workers` 与 drone 参数时，默认就是 100 个 Actor、100 个 drone；`time_minute` 按 Step 自动获取。认证方式和默认数据库参数沿用原入口，也可通过原有环境变量或参数指定。

小规模检查：

```bash
./run_in_lade.sh python scripts/lade_llm_context_queries_ray.py \
  --uri bolt://127.0.0.1:61687 \
  --run-id hangzhou-0621-headless-002 \
  --step-index 2300 \
  --queries core \
  --max-workers 2 \
  --repeat 1
```

指定 CPU 示例：`--max-workers 2 --cpu-ids 4,5`。CPU 编号必须在当前允许范围内、数量必须和 Actor 数一致，且不能是同一物理核心的超线程。`--cpu-ids` 是逻辑 CPU 编号，不是插槽编号或物理 core_id。

只看参数与计划、不启动 Ray、不连接数据库：

```bash
./run_in_lade.sh python scripts/lade_llm_context_queries_ray.py \
  --dry-run --run-id offline-test --queries i0,i2 \
  --drone-ids drone-1,drone-2 --step-index 2300 --time-minute 634 \
  --max-workers 2
```

#### 输出与计时

默认输出与现有版本分开：

```text
data/lade_llm_context_ray_query_logs.jsonl
data/lade_llm_context_ray_contexts.jsonl
data/lade_llm_context_ray_summary.json
```

JSONL 沿用追加行为，summary 沿用覆盖行为；正式对照实验建议每次指定独立目录。

查询日志和上下文增加 `concurrency_mode="ray"`、`worker_pid`、`worker_index`、`cpu_id`、`affinity_cpus`。

summary 还记录全部启动的 worker 信息（包括没有实际查询返回的 Actor）、物理 socket/core、Ray 分配资源、绑定线程数、配置 CPU 列表、启动/观测进程数、连接池上限、临时日志目录及清理异常列表。

| 字段 | 计时范围 |
|---|---|
| 单查询 `elapsed_ms` | 沿用原入口，从 session.run 前至完整读取并转换结果 |
| 上下文 `total_elapsed_ms` | 沿用原入口，Actor 内一次整包查询 |
| `ray_startup_ms` | 本地 Ray 初始化、Actor 启动、Driver 创建及全部 ready 校验 |
| `query_phase_wall_ms` | 首次提交查询前至最后一包结果收集与日志写入结束，包含 Ray 通信和主进程写日志 |
| `ray_cleanup_ms` | Driver 关闭、Actor 销毁及本次 Ray runtime 关闭 |
| `wall_ms` | 上述三个 Ray 阶段之和，作为原统计函数的整轮耗时输入 |

Step 时间预查询、参数校验和 CPU 计划生成在整轮计时前，与原入口预处理的位置保持一致。Ray 启动和关闭可能使短实验整轮耗时增加，不能只比较单查询均值。

### 后续建议

- 代码及离线验证已完成，可用上面的 100 Actor 命令执行正式数据库实验；本任务没有替用户启动该负载。
- 如果目标是让这些核心对其他系统进程也完全排他，仍需在宿主机用 cpuset/CPU 分区等机制限制其他工作负载，并同时处理同核超线程。这会影响数据库及其他服务，未包含在本次新增脚本中。
- 对比线程、普通进程和 Ray 时，固定查询、参数、数据版本与输出目录，同时查看整轮时间、查询阶段时间、CPU 和服务端指标。Ray 和绑核是否提高吞吐需要实际对照，当前测试只证明实现行为正确。

无需用户额外确认。已获准的沙箱外操作仅为两个 Actor 的离线验证，不涉及数据库访问或系统级 CPU 隔离配置。
