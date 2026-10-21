### 任务背景

`hangzhou-0621-headless-002` 从 Step 2985 恢复后再次因 Ray OOM 退出，原 `screen` 会话 `headless002_resume2` 随 Python 进程结束而消失。任务是确认 checkpoint 是否完整、定位自动恢复失效原因，并增加不会随子进程退出的常驻恢复机制。

### 关键结论

- OOM 发生前已成功运行到 Step 4157；checkpoint、Neo4j 最新 Step 和 100 条 AgentState 完全一致，没有丢失已完成进度。
- 首次 OOM 时整机内存为 240.32/251.65 GB，恢复 actor 时进一步达到 248.85/251.65 GB，均超过 Ray 的 95% 内存阈值。
- 原代码能捕获 `step_once()` 的 OOM，但 `restore_checkpoint_state()` 重建 actor 时的第二次 OOM 位于捕获范围之外，因此第一次恢复失败后主进程直接退出。
- `headless002_resume2` 只承载一个 Python 命令；该命令退出后 screen 会话正常关闭，因此不是 screen 自身丢失。
- 新代码会先关闭残余 actors，等待宿主机至少有 32 GiB 可用内存，再重建 actors；actor 重建自身失败也会进入指数退避重试。
- 新增常驻 supervisor：子仿真异常退出后，父进程继续留在 screen 中，等待至少 48 GiB 可用内存后自动用 `--resume` 重启。
- Ray CPU 数由默认 256 限制为 100，预启动 Python worker 也从 256 降为 100，不改变 100 个 drone 的仿真和 Neo4j 数据内容。
- 新会话为 `headless002_guard`；最终核验时 checkpoint 与 Neo4j 均已推进到 Step 4183。

### 执行过的操作

- 读取 `logs/headless-002-resume-20260917.log` 和 `/tmp/ray/session_*/logs` 中的 OOM、actor 和 GCS 日志。
- 检查宿主机内存、主要进程、screen 会话、checkpoint 及 Neo4j 最新 Step。
- 修改 Ray step 恢复逻辑并增加恢复前内存门控。
- 新增 headless 常驻 supervisor 及自动重启测试。
- 使用 `--ray-num-cpus 100` 从 Step 4157 启动 `headless002_guard`。
- 运行 41 项 checkpoint、Ray、supervisor 和 Neo4j 相关测试，全部通过。

### 修改内容

- `scripts/run_headless_sim.py`
  - actor restore OOM 也纳入恢复重试。
  - 每次恢复前清理残余 Ray actors。
  - 新增 `--ray-min-available-memory-gb` 和 `--ray-memory-poll-interval`。
- `scripts/run_headless_supervisor.py`
  - 常驻运行 headless 子进程。
  - 子进程异常退出时等待内存并自动重启。
  - 自动补充 `--resume`，避免误从头运行。
- `tests/run_headless_sim_test.py`
  - 覆盖 step OOM 后 actor restore 再次 OOM 的场景。
- `tests/run_headless_supervisor_test.py`
  - 覆盖自动补充 resume 和失败后重启行为。

### 后续建议

- 查看常驻会话：`screen -r headless002_guard`。
- 查看运行日志：`tail -f logs/headless-002-resume-20260917.log`。旧 traceback 是 9 月 17 日失败记录，新运行从 `[supervisor] starting headless child` 开始。
- 当前 swap 仍为 100% 占用，服务器又有多个十 GiB 级共享任务；supervisor 可以保住进度并自动恢复，但不能消除其他用户造成的全机内存峰值。
- screen 会话在仿真正常完成到 Step 10000 后会退出；只有运行期间子进程异常退出时，supervisor 才会继续保留并重启。
