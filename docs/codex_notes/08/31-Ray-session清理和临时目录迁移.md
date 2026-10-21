### 任务背景

用户想了解如何删除 Ray 原有的 `/tmp/ray/session_*` 临时会话目录，以及如何把 Ray 默认临时目录从 `/tmp` 改到 `/data` 大容量磁盘。

### 关键结论

- `/tmp/ray/session_*` 是 Ray 启动会话产生的运行时目录，正在运行的 session 不应该直接删除。
- 删除 Ray session 前应先停止 Ray，并确认没有 `raylet`、`gcs_server`、Ray worker 等进程仍在运行。
- 如果确认没有 Ray 任务在跑，可以删除 `/tmp/ray/session_*` 和 `/tmp/ray/session_latest`。
- 更适合当前百万级仿真的做法是把 Ray 临时目录迁移到 `/data/zc/datasets/LaDe/viewer/.ray_tmp`。
- 当前项目脚本还没有接入 `--ray-temp-dir`，不改代码时推荐手动用 `ray start --head --temp-dir=...` 启动 Ray，然后脚本用 `RAY_ADDRESS=auto` 连接。

### 执行过的操作

- 本次没有执行清理命令。
- 本次只整理操作步骤，避免误删正在运行的 Ray session。

### 修改内容

- 新增总结文件：`docs/codex_notes/2026-08-31-Ray-session清理和临时目录迁移.md`

### 后续建议

- 清理前先确认当前没有重要仿真正在运行。
- 后续可以把 `RAY_TEMP_DIR` 接入 `run_dynamic_sim.py`、`run_ray_sim.sh` 和 `run_million_data_sim.sh`，这样每次启动时自动使用 `/data` 目录。
