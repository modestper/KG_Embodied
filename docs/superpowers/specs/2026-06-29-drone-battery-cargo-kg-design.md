# Drone Battery Cargo KG Design

## 目标

在当前 LaDe viewer 仿真中实现第一阶段真实约束：无人机电池续航、低电返航、depot 即时充电、最多同时携带 3 个货物、任务取货状态、Neo4j 扩展记录和动态页面状态展示。local 与 Ray 后端必须保持一致行为。故障与接管只预留状态字段，不实现行为。

## 数据模型

`DeliveryTask` 增加：

- `pickup_time: int | None`
- `onboard_drone_id: str | None`

任务状态支持 `waiting`、`assigned`、`picked_up`、`completed`。

`DroneAgent` 增加：

- `queue_capacity: int = 6`
- `cargo_capacity: int = 3`
- `onboard_tasks: list[DeliveryTask]`
- `battery_capacity_m: float = 50_000`
- `battery_remaining_m: float = 50_000`
- `low_battery_threshold_m: float = 12_000`
- `safety_buffer_m: float = 5_000`
- `target_depot: dict | None`
- `failure_mode: str | None`
- `takeover_mode: str | None`

`capacity` 保留为兼容字段，但新逻辑使用 `queue_capacity`。`battery_range_m` 保留兼容，实际电池逻辑使用新字段。

## 运动与电池

每飞行 1 米消耗 1 米 `battery_remaining_m`，`battery_consumed_m = battery_capacity_m - battery_remaining_m`。

接单前判断：

```text
battery_remaining_m >=
  distance(current_position, task.pickup)
+ distance(task.pickup, task.dropoff)
+ distance(task.dropoff, nearest_depot)
+ safety_buffer_m
```

不满足时该 drone 不能接该任务。若 `battery_remaining_m <= low_battery_threshold_m` 且没有 `onboard_tasks`，drone 进入 `returning`，目标为自己的 region depot；找不到时使用最近 depot。到达 depot 后立即充满并变为 `idle`。

## 多货物

`queue_capacity` 限制已接收未完成任务数，`cargo_capacity` 限制已取货未送达任务数。到达 pickup 后，如果 `onboard_tasks` 未满，任务状态变为 `picked_up` 并加入 `onboard_tasks`；如果已满，不再取第 4 个货。

目标选择第一版：

- 如果 `onboard_tasks` 非空，送 `deadline_minute` 最早的任务；
- 否则取 `queue` 中第一个未取货任务。

完成任务后从 `onboard_tasks` 和 `queue` 移除。

## Neo4j

Drone 节点新增电池与载货字段。Task 节点新增 `picked_up`、`pickup_time`、`onboard_drone_id`。

新增事件类型：

- `task_picked_up`
- `drone_low_battery`
- `drone_returning_to_charge`
- `drone_charged`

保留已有 `task_assigned`、`task_completed`。

新增关系：

- `(Drone)-[:CARRYING {active: true}]->(Task)`，完成时设置 `active=false`
- `(Step)-[:IN_RUN]->(Run)`

Step 节点每轮记录实验概要，包括 step index、时间、完成/未完成任务数、各状态 drone 数、低电 drone 数和总飞行距离。

## 前端

动态页面 Agent 表增加：

- 电量：`remaining_km / capacity_km`
- 载货：`onboard_count / cargo_capacity`
- 状态继续显示 `failed`、`returning`、`charging`、`delivering`、`idle` 等。

## 验收

必须覆盖测试：

- 电池不足时不能接单。
- 低电且无载货时返航。
- 到达 depot 后立即充满并 idle。
- 最多同时 onboard 3 个任务。
- 到 pickup 后任务变 `picked_up`，到 dropoff 后变 `completed`。
- Neo4j 写入电池/载货字段、任务取货字段、新事件、Step 节点。
- Ray payload 与 snapshot 保留新增字段。
