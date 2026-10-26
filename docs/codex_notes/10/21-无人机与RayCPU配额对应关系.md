# 无人机与 Ray CPU 配额对应关系

### 任务背景

用户询问：脚本自动创建 Ray 集群时，是否可以指定 CPU 数量；当前代码中的无人机数量与 CPU 数量是一对一还是允许多对一。本次核查启动入口、网页参数处理、无人机 actor 创建及同步逻辑，只作分析，不修改仿真代码或启动实验。

### 关键结论

- **有 CPU 数量参数：`--ray-num-cpus`。** 动态和无界面入口均支持；未指定时，初始化函数默认使用 `drone_count`。
- `run_ray_sim.sh` 和 `run_million_data_sim.sh` 支持将环境变量 `RAY_NUM_CPUS` 转换为 `--ray-num-cpus`；动态 Python 入口本身的该参数默认值为 `None`，无界面入口则会直接读取这个环境变量。
- **当前实现为一架无人机对应一个 Ray actor，每个 actor 显式申请 1 个逻辑 CPU 配额。** 该值写死在 `ray.remote(num_cpus=1)(_DroneActor)`，没有每架无人机 CPU 配额的启动参数。
- 网页请求还有额外规则：将 `ray_num_cpus` 上调为至少 `drone_count`。因此，网页请求中设置“100 架无人机、16 个 CPU”，实际用于初始化的配额会被上调到至少 100。
- 显式声明的 actor CPU 资源在 actor 存活期间占用逻辑配额。若实际集群资源不足以容纳全部 actor，部分 actor 会等待；当前初始化同步等待所有 actor 的快照，可能持续阻塞，不会自动将这些常驻 actor 按小批次轮转。
- **逻辑 CPU 配额不等于物理 CPU 核绑定。** 多个 actor 进程仍可能由操作系统安排在相同物理 CPU 上分时运行；Ray 不会因声明 100 个逻辑 CPU 就创造 100 个物理 CPU。
- Ray 支持小数 CPU 资源。若希望“100 架无人机共享 16 个逻辑 CPU”，可以设计为每个 actor 申请 `0.1` CPU，总申请量为 10；但当前项目需要修改 actor 资源声明和网页 CPU 下限逻辑后才支持这种配置。

### 执行过的操作

读取或检索了以下文件：

- `scripts/run_dynamic_sim.py`：`build_runtime`、`args_from_request`、`initialize_ray_if_needed` 和 `parse_args`。
- `scripts/run_headless_sim.py`：`run_headless`、`parse_args`。
- `simulation/ray_delivery_sim.py`：`RaySimulationEnv.__init__`、`_create_actors`、`_sync_drones`、`step_once`。
- `run_ray_sim.sh`、`run_million_data_sim.sh`：环境变量与 CLI 参数转发。
- `tests/dynamic_server_test.py`：已有参数处理测试及初始化 mock 测试，仅阅读，未执行。

关键检索命令：

```bash
rg -n 'ray-num-cpus|ray_num_cpus|drone-count|drone_count|num_cpus|\.options\(|_create_actors|self\.actors|ray\.get' \
  scripts/run_dynamic_sim.py scripts/run_headless_sim.py \
  simulation/ray_delivery_sim.py run_ray_sim.sh \
  run_million_data_sim.sh tests/dynamic_server_test.py
```

同时核对 [Ray 官方资源说明](https://docs.ray.io/en/latest/ray-core/scheduling/resources.html)，确认显式 actor 资源需求、逻辑资源与物理资源的区别，以及 fractional CPU 的支持情况。

#### 1. 启动参数及默认值

`scripts/run_dynamic_sim.py:536` 定义：

```python
parser.add_argument("--ray-num-cpus", type=int, default=None)
```

`scripts/run_dynamic_sim.py:500` 计算本次请求的配额：

```python
requested_cpus = args.ray_num_cpus or getattr(args, "drone_count", None)
```

因此，对于有效的正整数：

| 参数情况 | 脚本新建 Ray 时请求的逻辑 CPU 配额 |
| --- | ---: |
| `--drone-count 10`，未指定 CPU | 10 |
| `--drone-count 10 --ray-num-cpus 16` | 16 |
| `--drone-count 100 --ray-num-cpus 128` | 128 |

以上说明参数含义，不表示本次执行了这些命令。是否连接已有集群仍受 `--ray-address`、`RAY_ADDRESS` 及 Ray 默认发现逻辑影响；连接已有集群时不能传节点 CPU 数量，之前发现的兼容问题仍未修改。

#### 2. 网页路径会强制抬高 CPU 下限

`scripts/run_dynamic_sim.py:475`：

```python
if values["backend"] == "ray":
    values["ray_num_cpus"] = max(
        values.get("ray_num_cpus") or 0,
        values["drone_count"],
    )
```

该规则在网页 `/events` 请求解析时生效；不能把它当作所有 CLI 入口都具有的保护。例如无界面入口直接使用启动参数，不经过该网页请求处理函数。

#### 3. 每架无人机创建一个常驻 actor

`simulation/ray_delivery_sim.py:376`：

```python
self.actor_class = self.ray.remote(num_cpus=1)(_DroneActor)
```

`simulation/ray_delivery_sim.py:410`：

```python
def _create_actors(self, drone_payloads):
    return {
        payload["drone_id"]: self.actor_class.remote(payload)
        for payload in drone_payloads
    }
```

对应关系是：

```text
每架无人机 → 一个 Ray actor → 申请 1 个逻辑 CPU 配额
```

如果 `D` 是无人机数量、`c` 是每个 actor 的 CPU 配额、`C` 是可用逻辑 CPU，在只有这些 actor 且其他资源充足的单机情况下，需要满足：

```text
D × c ≤ C
```

当前 `c = 1`，所以 `C` 需要至少为 `D`。将集群配额从 100 提高到 128，不会让每架无人机自动改为使用多个逻辑 CPU；actor 声明仍然是 1。

#### 4. CPU 不足的实际后果

初始化时，`simulation/ray_delivery_sim.py:457` 会同步等待所有 actor 的快照：

```python
snapshots = self.ray.get([
    actor.snapshot.remote()
    for actor in self.actors.values()
])
```

如果只有 16 个可用逻辑 CPU，却尝试创建 100 个各占 1 个 CPU 的常驻 actor，部分 actor 无法启动，而已启动的 actor 仍持有 CPU 配额。由此推断，初始化可能一直等待剩余 actor，不能将其理解为仅降低并发、让 100 架无人机自动轮流完成。

每个仿真步也会并发提交所有无人机的 `step`，并同步等待全部结果。这是按仿真步同步推进的设计。

#### 5. 多对一需要先明确指哪种 CPU

| “多对一”的含义 | 当前情况 |
| --- | --- |
| 多个无人机 actor 共享 1 个逻辑 CPU 配额 | 当前每个 actor 显式申请 1 个，不支持这种配额配置 |
| 多个 actor 进程在同一个物理 CPU 上分时运行 | 操作系统可以这样调度，当前代码没有固定核心绑定 |
| 100 架无人机在总配额为 16 的 Ray 集群中同时存活 | 需降低每个 actor 的逻辑 CPU 申请量，或改变 actor 组织方式 |

Ray 允许把 actor 声明改为 `num_cpus=0.5`、`0.1` 等。例如 `100 × 0.1 = 10`，在其他资源充足、无其他任务占用时，可放入总配额为 16 的节点中。该配置仅表示调度预算；实际吞吐量、延迟和内存使用需要针对实验负载验证。

### 修改内容

仅新增本总结文件，未修改 actor CPU 声明、启动参数、网页处理规则或已有前端工作文件；未创建、停止或重启 Ray 集群，也未运行实验或测试。

### 后续建议

1. 保持当前“每架无人机一个 actor、每个 actor 申请 1 个 CPU”的设计时，设置足够的 CPU 配额，避免在实际资源不足的集群中等待 actor 初始化。
2. 如需让无人机数量与逻辑 CPU 配额独立配置，可新增“每个无人机 actor 的 CPU 配额”参数，调整 `ray.remote(num_cpus=...)`，并同步修改网页的 CPU 下限计算；这一参数当前尚不存在。
3. 无论是否引入小数 CPU，都应修复连接已有集群时传入 `num_cpus` 的已知兼容问题，并验证 CPU 不足时能够给出明确提示。
