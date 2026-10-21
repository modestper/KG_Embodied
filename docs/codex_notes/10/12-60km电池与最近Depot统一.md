### 任务背景

根据五个城市全量配送距离统计，将无人机默认最大续航从 50 km 调整为 60 km，并统一低电返航、派单校验、互助判断和实际返航使用的 depot 口径，避免“派单按最近 depot、返航却固定回 home depot”的不一致。

### 关键结论

- 默认参数调整为 `battery_capacity_m=60_000`、`battery_remaining_m=60_000`、`low_battery_threshold_m=12_000`、`safety_buffer_m=5_000`。
- 动态低电阈值统一为 `max(12_000, distance(current_position, nearest_depot) + 5_000)`，在运行过程中按无人机当前位置重新计算。
- 派单电量要求统一为“当前位置到取货点 + 取货点到送达点 + 送达点到最近 depot + 5 km”，不再固定使用所属区域的 home depot。
- 实际返航在触发时选择当前位置最近的 depot 并锁定为本次返航目标；充电完成后无人机的原始 `region_id` 不变。
- 每架无人机的候选充电点使用该城市固定 depot 表中的全部区域，而不是仅使用当前任务抽样涉及的区域；local 与 Ray 后端行为一致。
- 飞行距离现在受剩余电量限制，电量归零后不能继续无消耗移动；Step 的 `low_battery_drones` 也改为动态阈值口径。
- 重庆 `0615 / 10 单 / 3 架无人机` 回归完成 10/10 个任务，使用 48 个城市固定 depot，产生低电、返航、充电事件各 1 次。

### 执行过的操作

- 阅读并检查 `simulation/delivery_sim.py`、`simulation/ray_delivery_sim.py`、`simulation/knowledge.py` 及三个运行入口中的电池、派单、返航和 Step 统计逻辑。
- 运行核心测试：`./run_in_lade.sh python -m unittest tests.delivery_sim_test tests.ray_delivery_sim_test tests.knowledge_test`，59 项通过。
- 运行周边测试：`./run_in_lade.sh python -m unittest tests.dynamic_server_test tests.bootstrap_headless_checkpoint_test tests.checkpoint_test tests.planner_test`，31 项通过。
- 运行 `py_compile` 检查核心仿真与脚本文件，并执行 `git diff --check`，均通过。
- 运行真实数据回归：`./run_in_lade.sh python scripts/build_delivery_sim.py --city Chongqing --day 0615 --task-limit 10 --drone-count 3 --max-steps 3000 --step-seconds 20 --seed 13 --out /tmp/delivery_sim_cq0615_battery_verify.json`。

### 修改内容

- `simulation/delivery_sim.py`：新增统一的最近充电点选择函数，调整默认电池容量、动态低电判断、派单回程校验、返航目标与飞行耗电约束，并允许环境注入全城市 depot。
- `simulation/ray_delivery_sim.py`：同步 Ray 状态代理与环境的 60 km 默认值、动态阈值和全城市 depot 逻辑。
- `simulation/knowledge.py`：Step 低电无人机计数优先调用无人机的动态低电判断。
- `scripts/run_dynamic_sim.py`、`scripts/build_delivery_sim.py`、`scripts/bootstrap_headless_checkpoint.py`：为 local、Ray、离线生成和断点恢复路径加载完整城市固定 depot。
- `tests/delivery_sim_test.py`、`tests/ray_delivery_sim_test.py`、`tests/knowledge_test.py`：补充最近 depot、动态阈值、60 km 默认值、电量限制及 local/Ray 一致性测试。
- `README.md`、`HANDOFF.md`：更新当前参数、公式和运行行为说明。

### 后续建议

- 当前正在运行的 `s8010` 进程不会热加载 Python 修改，需要停止后重新启动，并创建新仿真流才能看到 60 km 与新阈值生效。
- 旧 checkpoint 会保留创建时写入的电池容量和剩余电量，继续旧实验时不会强制改成 60 km；验证新参数应启动新 run，以免破坏历史实验可复现性。
- 可在后续大规模实验中继续记录低电返航率、充电次数、未完成任务率和逾期率，验证 60/12/5 参数在不同城市与不同无人机数量下的稳定性。
