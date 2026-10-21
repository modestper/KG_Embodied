### 任务背景

核对无人机接单电量判断和低电返航的 depot 选择口径，并评估应统一为 home depot 还是最近 depot。

### 关键结论

- 当前口径确实不一致：`required_battery_for()`、队列投影电量检查和载货互助判断均按送达点附近的最近 depot 计算；`start_returning()` 最终通过 `resolve_charge_depot()` 优先选择 home depot。
- 最近 depot 的预留距离永远不大于 home depot 距离，因此当前逻辑可能允许无人机接单，但完成任务后按 home depot 返航时实际电量不足。
- `nearest_depot_from()` 当前使用的 `drone.depots` 只包含本次任务涉及的区域 depot，并不一定包含城市固定表中的全部 depot。
- 推荐统一为“最近可用 depot”：接单、队列投影、互助电量判断和实际低电返航都调用同一个 depot 选择函数；home depot 只在距离相同时优先，或在 depot 列表不可用时兜底。
- 该选择更符合当前模型：所有 depot 都是充电点、充电立即完成、没有充电容量限制，并且无人机会跨区域互助；强制返回 home depot 没有对应收益，反而增加耗电和任务拒绝。
- 若业务必须要求归属区域闭环，则应反向统一为 home depot，并让所有接单检查都按 home depot 计算；这会显著收紧跨区任务和互助能力。
- 低电触发也应补充距离条件：除固定 12 km 阈值外，当 `battery_remaining_m <= distance(current, selected_depot) + safety_buffer_m` 时应立即返航。
- 当前移动代码没有用剩余电量限制每步最大飞行距离，电量归零后仍可能继续移动；后续修复时应一并限制，否则 depot 选择一致后仍可能出现物理上不合理的轨迹。

### 执行过的操作

- 阅读 `DroneAgent.required_battery_for()`、`start_returning()`、`resolve_charge_depot()` 和 `nearest_depot_from()`。
- 阅读 `required_battery_for_projected_task()` 与 `needs_battery_assistance()` 的返航距离计算。
- 核对 Ray 后端，确认实际 Ray actor 使用同一个 `DroneAgent` 执行移动，调度代理也复制了最近 depot 电量判断。
- 核对现有低电返航和互助测试。

### 修改内容

本次仅完成逻辑分析，没有修改仿真代码。

### 后续建议

- 新增统一的 `select_charge_depot(position)`，返回完整 depot 对象，禁止各调用点分别实现选择规则。
- 将城市全部固定 depot 注入每个 drone 的 `depots`，而无人机初始部署仍可只使用当前任务涉及的区域。
- 补充“最近 depot 与 home depot 不同”的测试，验证接单预留目标和实际返航目标一致。
- 补充跨区援助完成后就近充电、距离触发返航以及电量归零不得继续移动的测试。
