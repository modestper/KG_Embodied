### 任务背景

用户发现 Ray 后端仿真启动后只完成少量任务，之后大量 drone 显示空闲但任务不继续完成。排查后确认问题集中在 Ray actor 与主进程之间的派单状态同步：主进程把任务标成 assigned，但 actor 侧可能因为电量、状态或容量限制拒绝接单，导致任务进入“已分配但无人执行”的状态。

### 关键结论

- 本次修复让 Ray actor 的 `assign()` 返回真实接单结果，主进程只在 actor 确认接受后才记录任务分配。
- 如果首选 drone 接单失败，Ray 主进程会把该任务恢复为 waiting，并排除失败 drone 后重新选择其他可接单 drone。
- 本地 `AuctionCoordinator` 也改为使用 `can_accept_task()` 筛选候选 drone，避免低电量或不可用 drone 被选中。
- 调度器不再因为某个任务没有候选 drone 就中断整批派单，而是继续处理后面的任务。
- 已补充 Ray 回归测试，覆盖“首选 actor 拒单后改派其他 drone”的情况。

### 执行过的操作

- 读取并修改 `simulation/delivery_sim.py`、`simulation/ray_delivery_sim.py`、`simulation/planner.py`。
- 读取并修改 `tests/delivery_sim_test.py`、`tests/ray_delivery_sim_test.py`。
- 执行 `./run_in_lade.sh python -m unittest tests.delivery_sim_test`。
- 执行 `./run_in_lade.sh python -m unittest tests.ray_delivery_sim_test`。
- 执行 `./run_in_lade.sh python -m unittest tests.delivery_sim_test tests.ray_delivery_sim_test`。
- 执行 `./run_in_lade.sh python -m py_compile simulation/delivery_sim.py simulation/ray_delivery_sim.py simulation/planner.py`。
- 执行 `git diff --check -- simulation/delivery_sim.py simulation/ray_delivery_sim.py simulation/planner.py tests/delivery_sim_test.py tests/ray_delivery_sim_test.py`。

### 修改内容

- `simulation/delivery_sim.py`
  - `AuctionCoordinator.assign()` 改为通过 `candidates_for()` 获取可接单候选。
  - 候选筛选优先使用 `can_accept_task(task)`，同时支持排除已拒单 drone。
  - `winner.assign(task)` 返回成功后才写入 assignments。

- `simulation/ray_delivery_sim.py`
  - `DroneStateProxy.assign()` 增加接单前校验，并返回 `True/False`。
  - `_DroneActor.assign()` 返回 actor 侧真实接单结果、drone 快照和 task payload。
  - `RaySimulationEnv.release_and_assign()` 增加 actor 确认流程，避免只在 proxy 侧标记 assigned。
  - 新增 `_assign_task_with_retry()`，在 actor 拒单后重新选择其他候选 drone。
  - 新增 `_reset_task_assignment()`、`_apply_assigned_task()`、`_replace_drone_from_snapshot()` 等同步辅助方法。

- `simulation/planner.py`
  - `RegionLLMCoordinator.apply_assignments()` 改为尊重 `can_accept_task(task)`。
  - drone 实际接受任务后才记录 assignment，避免 planner 层制造状态不一致。

- `tests/delivery_sim_test.py`
  - 增加本地调度测试，验证同 region drone 电量不足时会派给其他可用 drone。

- `tests/ray_delivery_sim_test.py`
  - 增加 Ray 回归测试，验证 stale assignment 下首选 actor 拒单后能改派给其他 actor。

### 后续建议

- 重新启动当前 8010 仿真服务，新的 Ray actor 逻辑只有在进程重启后才会生效。
- 用原来的杭州大任务量参数再跑一次，观察 completed、assigned、waiting 和 idle drone 是否恢复正常变化。
- 如果后续仍出现长时间停滞，应继续检查电池约束是否过严，以及是否存在大量任务距离超过 50km 航程的情况。
