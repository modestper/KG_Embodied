# Ray 自动新建集群的触发条件

### 任务背景

用户希望明确当前脚本什么时候会在启动时自己新建 Ray 集群，以及“不指定地址”“auto”“local”和环境变量之间的区别。本次只读核查当前脚本和已安装的 Ray 2.55.1 源码，没有启动服务。

### 关键结论

- 首先需要使用 `--backend ray`；`--backend local` 会跳过 Ray 初始化。
- 对通常尚未初始化 Ray 的新脚本进程，默认模式在没有配置连接地址、且默认发现没有得到已有实例地址时，会新建本地集群。
- **显式 `--ray-address local` 会要求新建本地实例**，即使本机另有 Ray 集群。这里的 `local` 是地址参数的特殊值，与 `--backend local` 含义不同。
- `--ray-address auto` 主要用于连接已有实例；没有找到时会报错，而不会默认新建。若设置了 `RAY_ADDRESS`，其值会覆盖传给 Ray 的空地址或 `auto`。
- 具体 `IP:端口` 表示连接已有集群；连接失败不会自动退回新建本地集群。
- 新建时 CPU 配额优先取 `--ray-num-cpus`，未指定则取 `--drone-count`。连接已有集群时，当前代码传入 CPU 参数的兼容问题仍未修改。
- 本机版本中，**显式 `local` 不会被 `RAY_ADDRESS` 覆盖**。前文建议同时清理环境变量属于较保守的操作，并非显式 `local` 模式的必要条件。

### 执行过的操作

读取了 `scripts/run_dynamic_sim.py:495` 至服务主入口，确认：

```python
if args.backend != "ray":
    return

requested_cpus = args.ray_num_cpus or getattr(args, "drone_count", None)
```

参数解析中，`--ray-address` 默认读取 `RAY_ADDRESS`，缺省为空；`main()` 在创建 HTTP 服务之前调用 `initialize_ray_if_needed()`。

通过 `./run_in_lade.sh python -c ...` 使用 `inspect.getsource` 检查了已安装 Ray 的：

- `ray.init`；
- `ray._private.services.canonicalize_bootstrap_address`；
- `ray._private.services.get_ray_address_from_environment`。

关键分支为：

```python
# Ray 2.55.1，ray.init 内部：
if address_env_var and (address is None or address == "auto"):
    address = address_env_var
```

```python
# 地址规范化：
if addr is None or addr == "auto":
    addr = get_ray_address_from_environment(addr, temp_dir)
if addr is None or addr == "local":
    return None
```

其中规范化结果为 `None`，表示后续应创建本地 Ray；具体地址则进入已有集群连接分支。

同时核对 [Ray 官方初始化规则](https://docs.ray.io/en/latest/ray-core/api/doc/ray.init.html)。具体版本行为以本机 2.55.1 源码为准。

#### 新脚本进程中的模式对照

| 生效模式 | 是否新建集群 |
| --- | --- |
| `backend=ray`，地址为空，无 `RAY_ADDRESS`，默认发现没有已有实例 | 新建本地 Ray |
| `backend=ray`，显式地址为 `local` | 新建本地 Ray |
| 地址留空，但 `RAY_ADDRESS` 指向已有集群 | 尝试连接已有集群 |
| 地址留空，默认发现找到已有实例 | 尝试连接已有集群 |
| 地址为 `auto`，没有环境变量覆盖 | 查找已有集群，找不到则报错 |
| 地址为具体 `IP:端口` | 尝试连接该集群，不自动新建 |
| `backend=local` | 不初始化 Ray，使用普通本地仿真后端 |

例如，希望新启动的脚本自己创建 5 CPU 的 Ray 时，可在底层 Python 脚本启动参数中使用：

```text
--backend ray --ray-address local --ray-num-cpus 5
```

这只是参数示例，本次没有执行。无人机数量还需与 actor 资源需求匹配；网页请求的 CPU 下限上调规则仍然存在。

#### 已经初始化的进程需要另看项目分支

`initialize_ray_if_needed()` 如果发现当前进程已经初始化 Ray，会先检查总 CPU 配额：

- 当前配额足够：直接复用，不调用 `ray.init()`；这种情况下即使新的请求写了 `local`，也不会经过 Ray 的新建分支。
- 当前配额不足：先 `ray.shutdown()`，再按有效地址重新初始化。

因此，“显式 local 要求新建”针对真正调用 `ray.init(address="local", ...)` 的情况；不能忽略项目函数可能提前返回的行为。

### 修改内容

仅新增本总结文件。没有修改脚本、启动配置、环境变量或已有前端工作文件，也没有启动、关闭、重启或连接实际 Ray 集群。

### 后续建议

1. 如果希望每次新启动服务都由脚本管理自己的 Ray，明确使用 `--backend ray --ray-address local`，并设置所需 CPU 配额。
2. 如果希望多个进程复用常驻 head，应先修正连接已有集群时传入 `num_cpus` 的兼容问题，再使用具体地址或 `auto`。
3. `s8010` 若从配置文件读取地址，需要确保最终传给 Python 脚本的参数符合所选模式；本次没有检查或改动该快捷命令。
