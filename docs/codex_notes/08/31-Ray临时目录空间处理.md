### 任务背景

用户在运行动态仿真时看到 Ray 报告 `/tmp/ray/session_...` 所在分区超过 95% 使用率，希望了解 Ray 的 `/tmp` 空间应该如何处理。

### 关键结论

- 当前 `/tmp` 挂载在 `/dev/nvme0n1p3`，容量约 906G，已用 828G，使用率 97%，剩余约 33G。
- 当前 `/data/zc/datasets/LaDe/viewer` 所在 `/dev/sda1` 容量约 15T，剩余约 5.7T，更适合作为大规模仿真的临时目录。
- 当前 `/tmp/ray` 本身占用约 17G，但 Ray 告警是因为整个 `/tmp` 所在分区超过 95%，不一定只是 Ray 自己占满。
- 当前项目代码里的 `ray.init(**init_kwargs)` 只接入了 `address` 和 `num_cpus`，还没有把 Ray 临时目录参数暴露到脚本或命令行。
- 短期处理可以在停止 Ray 后清理历史 `/tmp/ray/session_*`；长期处理建议把 Ray 临时目录迁移到 `/data` 这类大容量磁盘。

### 执行过的操作

- 读取 Ray 初始化相关代码：
  - `scripts/run_dynamic_sim.py`
  - `simulation/ray_delivery_sim.py`
  - `run_ray_sim.sh`
  - `run_million_data_sim.sh`
- 执行命令：
  - `rg -n "ray\\.init|_temp_dir|temp_dir|RAY_TMPDIR|object_spilling|RAY_" simulation scripts run_*.sh`
  - `du -sh /tmp/ray 2>/dev/null || true`
  - `df -h /tmp /data/zc/datasets/LaDe/viewer`

### 修改内容

本次没有修改运行代码，只新增该排查总结文件。

### 后续建议

- 先确认当前是否还有仿真在跑；如果没有，执行 `ray stop` 后再清理 `/tmp/ray` 的历史 session。
- 建议后续给 `run_dynamic_sim.py` 和 Ray 仿真环境补充 `--ray-temp-dir` 或环境变量支持，把临时目录指向 `/data/zc/datasets/LaDe/viewer/.ray_tmp`。
- 大规模写入 Neo4j 和 100 agent 并发仿真时，建议避免使用空间紧张的 `/tmp` 作为 Ray 默认临时目录。
