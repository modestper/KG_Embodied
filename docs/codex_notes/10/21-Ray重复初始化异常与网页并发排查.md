# Ray 重复初始化异常与网页并发排查

### 任务背景

用户添加 `--ray-address local` 后，服务在 2026-10-21 17:19:41 成功创建本地 Ray，但随后网页从 3 架无人机切换到 10 架时，出现新的 `AssertionError: Perhaps you called ray.init twice by accident?`。本次分析用户提供的日志及当前源码，未修改业务代码、重启服务或连接实际集群。

### 关键结论

- **有新的实际异常**：本次在 Ray 内部 `connect()` 的 `assert not worker.connected` 失败；与之前连接已有集群时不允许传 `num_cpus` 的 `ValueError` 不同。
- 17:19:41 的 `Started a local Ray instance` 说明启动阶段已经走到本地实例启动；异常出现在后续 `/events` 仿真请求处理中。
- 沿用前文“初始 CPU 配额为 5”的前提，先请求 3 架不会因 CPU 不足重启；随后请求 10 架会将本次需求上调到至少 10，并触发 `shutdown → init`。
- **代码存在明确的并发缺口**：`ThreadingHTTPServer` 并发处理请求，而 Ray 状态检查、关闭、初始化，以及环境构造没有统一串行保护；现有 `active_env_lock` 只保护活动环境指针。
- 根据日志时序和源码，优先怀疑“新请求重启 Ray 时，旧请求继续清理或调用 Ray，触发另一次初始化”的竞态。确切是哪个旧线程、哪一次 API 调用触发，当前日志没有线程 ID 和调用栈，尚不能最终确认。
- 当前 Ray 2.55.1 的 `ray.kill`、`ray.get` 等 API 支持自动初始化。在重启窗口中调用这些 API，可能执行不带参数的 `ray.init()`，自动发现并连接旧的 `222.20.99.83:6379`；显式调用中的 `address="local"` 不会约束另一处不带参数的调用。
- GPU `FutureWarning`、非主线程 SIGTERM 警告及 HTTP 304 不是本次导致请求失败的异常；HTTP 200 和之后的图查询成功，也不能证明 Ray 仿真请求已经成功完成。
- 本次只新增排查文档，没有修改现有前端、测试、仿真或 Ray 配置；没有运行真实并发仿真或强行重现故障。

### 执行过的操作

#### 1. 日志时序

| 时间 | 日志内容 | 可以确认或推断的含义 |
| --- | --- | --- |
| 17:19:41 | `Started a local Ray instance` | 启动阶段创建了本地实例 |
| 17:20:17 | `/events?...drone_count=3...` | 第一条网页仿真请求；按启动 5 CPU 前提，无需增加 CPU |
| 17:20:29 | `/events?...drone_count=10...` | 第二条请求需求至少为 10 CPU，会进入资源不足时的重启分支 |
| 17:20:31、17:20:33 | 两次非主线程 SIGTERM 警告 | 至少两次初始化相关代码在 HTTP 请求线程内执行，具有请求交错的迹象 |
| 17:20:33 | `Connecting to existing ... 222.20.99.83:6379` | 一次初始化选择了已有集群 |
| 17:20:41 | `Connected to Ray cluster` | 已有集群连接路径继续推进 |
| 17:20:54 | `Started a local Ray instance`，随后内部断言失败 | 另一条本地初始化路径推进到连接检查时，当前进程已经处于连接状态 |
| 17:20:58 | 相同的 10 架 `/events` 再次请求 | 与自动重连相符，也可能是手动重试；仅凭日志无法区分 |
| 17:27:49 | `/api/graph-query` 返回 200 | HTTP 服务及这一查询请求仍能响应，不代表前面的 Ray 仿真正常 |

日志中的 CPU 配额没有直接打印，因此“5 → 10”分析采用的是用户前文提供的启动参数前提。

#### 2. 并发请求入口及保护范围

`scripts/run_dynamic_sim.py:545` 使用：

```python
server = ThreadingHTTPServer((args.host, args.port), DynamicHandler)
```

`handle_events()` 在每个请求线程内执行：

```python
self.replace_active_env(None)
initialize_ray_if_needed(runtime_args)
env, config, depots = build_runtime(runtime_args)
self.replace_active_env(env)
```

`active_env_lock` 只在读取或替换 `active_env` 时短暂持有，旧环境的 `close_env(previous)` 在释放该锁后执行。`initialize_ray_if_needed()` 自身没有锁：

```python
if ray.is_initialized():
    current_cpus = ray.cluster_resources().get("CPU", 0)
    minimum_cpus = requested_cpus or current_cpus
    if current_cpus >= minimum_cpus:
        return
    ray.shutdown()
ray.init(**init_kwargs)
```

因此，“检查尚未初始化”与后续 `init` 之间，以及 `shutdown` 与 `init` 之间，都可能有其他请求线程调用 Ray。

此外，`simulation/ray_delivery_sim.py:353` 的环境构造函数也有一处 `is_initialized → init`，同样没有在项目层与其他初始化路径共享锁。

#### 3. 旧请求为何可能触发隐式初始化

`handle_events()` 在 `finally` 中调用 `clear_active_env(env)`；后者无论该环境是否仍是活动环境，都会调用 `close_env(env)`。新请求替换旧环境时也会关闭一次旧环境，因此旧请求退出与新请求初始化可能交错。

`simulation/ray_delivery_sim.py:402` 的关闭逻辑：

```python
def close(self):
    for actor in list(self.actors.values()):
        try:
            self.ray.kill(actor, no_restart=True)
        except Exception:
            pass
    self.actors = {}
```

已核对本机 `ray/__init__.py` 的 `AUTO_INIT_APIS` 列表，包含 `kill`、`get`、`wait` 等。它们由 `wrap_auto_init_for_all_apis()` 动态包裹，普通 `inspect.getsource(ray.kill)` 会因装饰器解包而不直接展示这层动态包装，因此同时检查了初始化模块源码。

本机 `ray/_private/auto_init_hook.py`：

```python
def auto_init_ray():
    if enable_auto_connect and not ray.is_initialized():
        with auto_init_lock:
            if not ray.is_initialized():
                ray.init()
```

该内部锁只用于这类自动初始化路径，项目显式调用 `ray.init(...)` 的函数并没有使用同一个保护范围。旧线程中的 `ray.kill`、`ray.get` 或 actor 调用，可能在重启窗口进入自动初始化路径。

这为“已明确使用 local，日志却又连接了 6379”提供了合理解释：显式新建与隐式默认发现是不同调用。此处是结合源码的高概率解释，尚未获得当时那次隐式调用的完整栈。

#### 4. 为什么判断为初始化竞态

本机 Ray 2.55.1 的 `ray.init()` 在入口阶段先检查 `global_worker.connected`，稍后创建或选择运行时，再进入 `connect()`。用户本次失败发生在后者：

```python
error_message = "Perhaps you called ray.init twice by accident?"
assert not worker.connected, error_message
```

与入口处已经初始化时抛出的普通 `RuntimeError` 相比，初始化推进到后段后才发现连接状态已改变，更符合其他初始化路径在中途改变全局状态的情形。日志中交错的本地实例启动与已有集群连接也支持这一判断。

仍不能仅凭这些日志排除其他修改全局 Ray 状态的路径，因此没有把具体线程交错顺序写成已证实的事实。

#### 5. 其他日志分类

| 内容 | 含义 |
| --- | --- |
| GPU 可见设备 `FutureWarning` | 提醒未来版本的环境变量处理将变化，本次仍继续启动服务 |
| `SIGTERM handler is not set...not the main thread` | 当前 Ray 初始化在工作线程内执行，不能在那里注册主线程信号处理；同时也是本次线程交错的线索 |
| 静态资源 HTTP 304 | 浏览器复用缓存，属于正常响应 |
| `/events` HTTP 200 | 仅说明 SSE 响应头已经发送，代码在此后才执行 Ray 初始化及仿真构造 |
| `AssertionError` | 使这一条仿真请求失败的实际异常 |
| 后续图查询 HTTP 200 | HTTP 服务仍在响应该端点，不能证明 Ray 状态已经恢复正常 |

前端 `src/dynamic_app.mjs` 的 EventSource 错误处理只显示 `reconnecting`，未关闭连接，所以失败后可能继续自动请求 `/events`，增加请求交错的机会。

#### 6. 主要核查命令

```bash
rg -n 'ray\.init|ray\.shutdown|ray\.kill|Lock|RLock|is_initialized' \
  scripts/run_dynamic_sim.py simulation/ray_delivery_sim.py

rg -n 'AUTO_INIT_APIS|wrap_auto_init_for_all_apis' \
  /data/zc/apps/miniconda3/envs/lade/lib/python3.13/site-packages/ray/__init__.py
```

还通过 `./run_in_lade.sh python -c ...` 使用 `inspect.getsource` 读取当前安装版本的 `connect`、`kill`、`get` 和自动初始化逻辑，没有调用实际的 `ray.init()` 或 `ray.shutdown()`。

辅助参考：[Ray 官方 worker 源码](https://docs.ray.io/en/master/_modules/ray/_private/worker.html)。该在线页面为主干版本，具体错误检查及 API 包装结论以本机 Ray 2.55.1 源码为准。

### 修改内容

仅新增本总结文件。没有更改 `run_dynamic_sim.py`、Ray 环境类、前端错误重连逻辑、现有测试或启动配置；没有启动、停止或重启真实 Ray 服务。

### 后续建议

1. 修复时应把旧仿真停止、旧请求退出、Ray 生命周期切换和新环境构造纳入统一的并发管理；可以使用统一管理线程或严格的生命周期同步，确保重启期间旧请求不会继续调用 Ray。
2. 清理旧环境时应避免在 Ray 已关闭后调用会自动初始化的 API，同时处理重复清理与运行时归属，不能只给 `init` 函数外层加一把锁就认定全部问题解决。
3. `ignore_reinit_error=True` 只影响入口处的重复初始化检查，不能可靠解决本次较晚发生的 `connect` 断言竞态，不应作为唯一修复。
4. 临时减少触发机会的方式是：启动时给足计划使用的逻辑 CPU 配额，并避免在旧仿真初始化、运行或退出期间反复启动新仿真；这只能降低概率，不能代替代码修复。
5. 后续定位可补充请求 ID、线程 ID、运行时代次和每次显式/隐式初始化的调用栈；本次未向运行中的服务添加日志或进行故障注入。
