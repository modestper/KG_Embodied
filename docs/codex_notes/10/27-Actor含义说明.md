### 任务背景

解释本项目中 actor 的含义。

### 关键结论

- Ray 后端中，每架无人机对应一个 actor，内部持有一个 DroneAgent。
- actor 保存无人机状态，提供接单、推进仿真和查询快照等方法。
- 主进程通过 remote() 调用 actor，通过 ray.get() 收集结果。
- 当前代码通过 ray.remote(num_cpus=1) 声明每个 actor 的逻辑 CPU 需求；这不代表绑定某个物理核心。
- 当前仿真每一步等待所有无人机 actor 返回，再汇总状态、记录事件和写入知识存储。
- 当前没有专门产生事件或写入事件的 Ray actor。DroneAgent 在无人机 actor 内产生取货、低电、返航、充电等 step_events，_DroneActor.step() 将其随结果返回；主进程调用 record_drone_step_events() 记录事件。完成任务另外通过 completed_tasks 返回并由主进程记录完成事件。

### 执行过的操作

- 使用 rg 搜索 actor 定义和调用。
- 使用 sed 阅读 simulation/ray_delivery_sim.py 中的 _DroneActor、actor 创建和 step_once。
- 追查 simulation/delivery_sim.py 中 step_events 的产生，以及 simulation/knowledge.py 的事件存储定义。

### 修改内容

仅新增本说明文件，未修改代码或运行服务。

### 后续建议

阅读代码时，将 DroneAgent 理解为无人机行为逻辑，将 Ray actor 理解为承载该逻辑并保存状态的独立执行单元。
