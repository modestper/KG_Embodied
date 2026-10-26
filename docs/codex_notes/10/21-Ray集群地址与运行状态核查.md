# Ray 集群地址与运行状态核查

### 任务背景

用户询问当前系统使用哪个 Ray 集群，以及是否有可以连接或访问的地址。本次只读核查进程、监听端口、会话记录与项目连接配置，没有调用 `ray.init()`，也没有启动或停止服务。

### 关键结论

- 本次检查时，宿主机没有匹配到运行中的 `gcs_server`、`raylet`、Ray Dashboard 或项目动态/无界面仿真进程；没有发现当前可用的本机 Ray 集群。
- 最新 Ray 会话是 `/tmp/ray/session_2026-10-21_11-30-15_407414_1687242`，由 `/tmp/ray/session_latest` 指向；日志最后输出在当天约 11:48:58，不能仅凭该目录存在认为服务仍在运行。
- 最近一次会话的节点 IP 为 `222.20.99.83`，GCS 端口为 `43915`，因此当时的集群连接地址为 **`222.20.99.83:43915`**。当前该端口无监听，不能作为可用连接地址。
- 最新会话日志显示为本机单节点资源，CPU 配额为 10，GPU 为 2，日志记录的 Ray 版本为 `2.55.1`；这是历史会话信息，不代表当前仍有这些 Ray 资源在线。
- 最新会话的 Dashboard 日志明确写着 `http server disabled`；当前 `8265` 也没有监听，因此没有可访问的 Ray Dashboard 网页地址。
- 仓库 `.ray_tmp/ray_current_cluster` 中的 **`222.20.99.83:6379` 是旧地址**，对应的 `.ray_tmp/session_latest` 指向 2026-08-31 会话；当前 `6379` 也无监听。
- 项目支持通过 `--ray-address` 或 `RAY_ADDRESS` 指定已有集群；本次检查的 shell 没有设置 `RAY_ADDRESS`，启动代码没有硬编码一个固定的远端 Ray 地址。

### 执行过的操作

#### 进程与端口

初次在沙箱内查询时，进程可见范围受限，`ss` 也提示 `Cannot open netlink socket: Operation not permitted`。随后获得只读检查授权，在宿主机视图核对进程及端口。

关键命令：

```bash
ps -eo pid,ppid,comm,args |
  rg '(^|/)(raylet|gcs_server)( |$)|[r]ay/dashboard|[r]ay_client_server.py|[s]cripts/run_headless_sim.py|[s]cripts/run_headless_supervisor.py|[s]cripts/run_dynamic_sim.py|[d]ynamic_server.py'

ss -ltnp
ss -ltnp '( sport = :43915 or sport = :6379 or sport = :8265 or sport = :10001 or sport = :8010 )'
```

进程过滤没有匹配结果，定向端口检查只有表头，没有监听记录。该结果是本次检查时点的状态，不保证之后没有其他人启动集群。

#### 最新与旧会话

读取了：

- `/tmp/ray/session_latest/` 的目录及其 `gcs_server_port_*` 文件，端口内容为 `43915`。
- `/tmp/ray/session_latest/logs/gcs_server.out`：记录 11:30:16 启动 GCS，监听 `43915`。
- `/tmp/ray/session_latest/logs/raylet.out`：记录节点地址 `222.20.99.83`、本机资源以及 11:48:58 左右 worker 断开连接。
- `/tmp/ray/session_latest/logs/dashboard.log`：记录 minimal mode、缺少完整 Dashboard 模块依赖，以及 `http server disabled`。
- `/tmp/ray/session_latest/logs/python-core-driver-01000000ffffffffffffffffffffffffffffffffffffffffffffffff_1687242.log` 的定向检索结果。
- `.ray_tmp/session_latest` 的旧会话链接及 `.ray_tmp/ray_current_cluster` 的旧地址。

另外确认 `/tmp/ray/ray_current_cluster` 文件当前不存在；不能将 `.ray_tmp/` 的旧记录当作 `/tmp/ray/` 的当前运行状态。

#### 项目配置

读取或检索了：

- `scripts/run_dynamic_sim.py:496`：`initialize_ray_if_needed` 在有 `args.ray_address` 时将其传入 `ray.init(address=...)`；未指定时调用 Ray 的默认初始化逻辑。
- `scripts/run_dynamic_sim.py:535`：`--ray-address` 默认读取 `RAY_ADDRESS`，缺省为空。
- `simulation/ray_delivery_sim.py:355`：环境初始化也只在指定 `ray_address` 时传递地址。
- `scripts/run_headless_sim.py:390`：无界面运行器同样支持 `--ray-address` / `RAY_ADDRESS`。
- `run_ray_sim.sh`：只有 `RAY_ADDRESS` 非空时才追加 `--ray-address` 参数。

### 修改内容

仅新增本总结文件。没有修改 Ray 配置、项目代码、现有前端改动或 Git 提交记录，没有创建 Ray 会话。

### 后续建议

1. 当前不要用 `222.20.99.83:43915` 或旧的 `222.20.99.83:6379` 当作在线集群地址；二者检查时均无监听。
2. 后续启动仿真时，如果需要连接已有集群，应明确传入当时实际在线的地址；如果需要固定地址或网页监控，应另行配置常驻 Ray head 和 Dashboard。
3. GCS 的 `IP:端口` 用于 Ray 连接，不是 Dashboard 的 HTTP 网页地址；网页地址需要 Dashboard 服务实际启用后另行核对。
