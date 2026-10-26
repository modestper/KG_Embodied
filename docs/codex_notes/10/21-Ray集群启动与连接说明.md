# Ray 集群启动与连接说明

### 任务背景

用户询问如何启动 Ray 集群。结合当前服务器环境，默认提供在 `222.20.99.83` 上启动单机 head 的命令，并说明如何核查状态、独立连接及按需启用 Dashboard。本次提供教程，没有实际启动或停止服务，也没有安装依赖。

### 关键结论

- 已核对 `lade` 环境中的 Ray 版本为 **2.55.1**，`./run_in_lade.sh ray start` 和 `ray status` 均可用。
- 可先以固定 GCS 端口 `6379` 启动单机集群，连接地址为 `222.20.99.83:6379`；是否启动成功应以命令输出和 `ray status` 为准。
- 当前环境缺少完整 Dashboard 的部分依赖，最简启动命令明确关闭 Dashboard；这不妨碍 Ray 调度任务。
- 如需网页监控，可安装与现有版本一致的 `ray[default]==2.55.1`，再启用 Dashboard。Dashboard 使用 HTTP 端口 `8265`，与 GCS 的 `6379` 用途不同。
- **项目现有仿真入口存在连接已有集群的参数兼容问题**：同时向 `ray.init` 传入集群地址和 `num_cpus`，会被当前 Ray 明确拒绝。本教程不提供会触发该问题的仿真启动命令，也没有改动业务代码。
- 本次没有修改当前前端工作文件、仿真参数、Git 提交或运行中的服务，只新增此说明。

### 执行过的操作

执行了以下只读核查：

```bash
./run_in_lade.sh ray start --help
./run_in_lade.sh ray status --help
./run_in_lade.sh python -c 'import ray; print(ray.__version__)'
```

读取了 `run_in_lade.sh`、`run_ray_sim.sh`、`scripts/run_dynamic_sim.py`、`scripts/run_headless_sim.py`，并通过 `inspect.getsource(ray.init)` 核对当前已安装 Ray 的初始化实现。

本地实现中明确存在以下错误检查：

```text
When connecting to an existing cluster, num_cpus and num_gpus must not be provided.
```

同时参考 Ray 官方的启动、资源配置与 Dashboard 文档。尝试读取版本化的 2.55.1 文档页面未成功，因此具体 CLI 参数和禁止同时传参的结论以本地 2.55.1 实现为准。

### 修改内容

仅新增本教程。以下命令供用户选择执行，尚未实际运行。

#### 1. 启动最简集群

在服务器上的项目根目录 `/data/zc/datasets/LaDe/viewer` 执行：

```bash
./run_in_lade.sh ray start --head \
  --node-ip-address=222.20.99.83 \
  --port=6379 \
  --include-dashboard=false \
  --temp-dir=/tmp/ray
```

这会在本机启动 head；单机本身就是一个单节点 Ray 集群。命令返回后，Ray 服务进程继续在后台运行。日志保存在 `/tmp/ray`，不写入当前 Git 仓库。机器重启后的自动启动不在本次配置范围内。

这里未固定 CPU/GPU 数量，由 Ray 自动检测。若要限定逻辑调度资源，可在启动命令中追加如 `--num-cpus=10`；是否提供 GPU 资源可按任务需要通过 `--num-gpus` 设置。

资源需要满足项目的调度方式：`simulation/ray_delivery_sim.py` 为每个无人机 actor 声明 1 个 CPU，100 个同时常驻的无人机 actor 需要相应的可调度 CPU 配额。不要把示例中的 10 个 CPU 默认当作适合所有实验的设置。

#### 2. 查看启动是否成功

```bash
./run_in_lade.sh ray status --address=222.20.99.83:6379
```

正常情况下会显示节点和资源状态。如果端口已被其他实例使用，先核对正在运行的实例，不要重复占用同一个 head 端口。

#### 3. 用独立 Python 连接验证

```bash
./run_in_lade.sh python -c 'import ray; ray.init(address="222.20.99.83:6379"); print(ray.cluster_resources()); ray.shutdown()'
```

连接已有集群时不传 `num_cpus` / `num_gpus`；这些资源应在 `ray start` 时配置。此处 `ray.shutdown()` 只断开验证客户端，不停止用 CLI 启动的 head。

这一验证不启动无人机仿真实验。

#### 4. 可选：启用 Dashboard 网页监控

先在同一个环境补齐依赖，固定现有 Ray 版本：

```bash
./run_in_lade.sh python -m pip install 'ray[default]==2.55.1'
```

然后选择以下带 Dashboard 的启动方式，替代第一节的启动命令：

```bash
./run_in_lade.sh ray start --head \
  --node-ip-address=222.20.99.83 \
  --port=6379 \
  --include-dashboard=true \
  --dashboard-host=127.0.0.1 \
  --dashboard-port=8265 \
  --temp-dir=/tmp/ray
```

两种启动方式二选一，不能在相同端口重复启动。如果已经启动了不带 Dashboard 的 head，需要在其没有实验任务时停止并重启，才能应用新的启动参数。

Dashboard 只绑定服务器本机 `127.0.0.1`。在自己的电脑上建立 SSH 转发后访问：

```bash
ssh -N -L 8265:127.0.0.1:8265 chioe@222.20.99.83
```

SSH 用户名以实际账号为准；上例使用当前环境账号 `chioe`。在本地浏览器打开 `http://127.0.0.1:8265`。使用 SSH 转发无需将 Dashboard 改为对所有网卡监听。

#### 5. 项目连接已有集群前的兼容问题

`scripts/run_dynamic_sim.py:495` 的 `initialize_ray_if_needed` 会执行类似以下代码：

```python
requested_cpus = args.ray_num_cpus or args.drone_count
init_kwargs["address"] = args.ray_address
init_kwargs["num_cpus"] = requested_cpus
ray.init(**init_kwargs)
```

只要传入已有集群地址，`num_cpus` 就与当前 Ray 的连接规则冲突。`scripts/run_headless_sim.py` 也复用该初始化函数。

因此，集群本身可以按第一至三节独立启动并验证；要让当前动态/无界面仿真入口连接这个常驻集群，还需要调整初始化逻辑：连接已有集群时只传连接参数，只有确实创建本地集群时才设置节点资源。即使没有显式地址，也应考虑 Ray 默认发现已有集群的情况。本次尚未修改该逻辑。

#### 6. 按需停止

确认本机 Ray 实验已经结束、无需保留该实例后，可执行：

```bash
./run_in_lade.sh ray stop
```

该命令停止本机 Ray 服务，不只是退出某个 Python 客户端。本次没有执行。

### 后续建议

1. 仅需要 Ray 服务时，先按第一至三节启动和验证；无需先安装 Dashboard 依赖。
2. 若要让现有项目接入常驻 head，下一步应修正 `initialize_ray_if_needed` 的已有集群连接逻辑，并验证现有的本地启动路径。
3. 需要网页监控时再选择第四节的依赖安装及启动方式。

参考：[Ray 官方启动说明](https://docs.ray.io/en/latest/ray-core/starting-ray.html)、[资源与连接配置](https://docs.ray.io/en/latest/ray-core/configure.html)、[Dashboard 配置](https://docs.ray.io/en/latest/cluster/configure-manage-dashboard.html)。
