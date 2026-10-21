### 任务背景

`hangzhou-0621-headless-002` 的 Ray headless 仿真在 Step 2985 因内存压力中断。目标是先备份已有 Neo4j run，再实现可校验的 checkpoint/resume，并从 Step 2985 恢复后台运行，避免再次中断时丢失全部进度。

### 关键结论

- Step 2985 的逻辑备份包含 313,599 个节点和 949,523 条关系，两个压缩数据文件均通过 SHA-256 校验。
- 已从 Neo4j Step 2985 重建 100 个 drone、10,424 个任务、队列、载货、电量和位置状态，并成功恢复 Ray 仿真。
- checkpoint 使用带 SHA-256 的原子临时文件替换；每个 step 在提交 Neo4j 前先落盘 checkpoint。
- Step 与 100 条 AgentState 现在由同一条 Cypher 事务提交；中断后若 checkpoint 领先 Neo4j 1 步，恢复器会自动补交该 Step。
- Event ID 已确定化，Event 关系改用 `MERGE`，Ray 重试同一 step 不会重复写事件和关系。
- AgentState 的 `MERGE` 已改为使用现有 `(run_id, drone_id, step_index)` 复合索引，单 step 从数分钟降低到约数秒。
- 后台任务已在 `screen` 会话 `headless002_resume2` 中运行；核验时 checkpoint 与 Neo4j 均为 Step 3008，且该 Step 有 100 条 AgentState。

### 执行过的操作

- 检查 Ray OOM 日志、Neo4j Step/Task/Event/AgentState 一致性和系统内存。
- 运行 `scripts/backup_neo4j_run.py` 导出并校验 `headless-002` 的逻辑备份。
- 运行 `scripts/bootstrap_headless_checkpoint.py`，先生成 Step 2985 checkpoint，停止测试恢复后又从一致的 Step 2989 重建 checkpoint。
- 运行 38 项 checkpoint、Ray、Neo4j 和 headless 单元测试，全部通过。
- 使用 detached `screen` 会话恢复后台仿真，并核对 checkpoint、Neo4j 最新 Step、AgentState 数量和日志。

### 修改内容

- `simulation/checkpoint.py`：checkpoint 序列化、校验、原子写入和配置验证。
- `simulation/ray_delivery_sim.py`：Ray actor 完整状态导出/恢复，以及 Neo4j 提交前 checkpoint hook。
- `scripts/run_headless_sim.py`：`--resume`、自动 checkpoint、Ray actor 故障重建、checkpoint 领先一步的补提交。
- `simulation/knowledge.py`：Event 幂等写入、最新 Step 查询、Step 与 AgentState 原子提交、复合索引写入路径。
- `scripts/bootstrap_headless_checkpoint.py`：从最新 Neo4j Step 重建历史运行状态。
- `scripts/backup_neo4j_run.py`：按 run 导出 gzip JSONL 逻辑备份和 manifest/hash。
- `.gitignore`：忽略本地备份、checkpoint 和后台日志。
- 对应测试位于 `tests/checkpoint_test.py`、`tests/bootstrap_headless_checkpoint_test.py`、`tests/backup_neo4j_run_test.py`、`tests/ray_delivery_sim_test.py`、`tests/run_headless_sim_test.py` 和 `tests/knowledge_test.py`。

### 后续建议

- 用 `screen -r headless002_resume2` 查看会话，或用 `tail -f logs/headless-002-resume-20260917.log` 查看进度。
- 定期比较 checkpoint 与 Neo4j 最新 Step；运行期间两者允许短暂相差 1，稳定点应一致。
- 当前主机 swap 已用满且仍有其他大型进程；checkpoint 能保护进度，但应继续监控可用内存，必要时清理无关进程或重启旧 Ray 集群。
- 备份目录为 `backups/hangzhou-0621-headless-002-step2985-20260917/`，恢复文件为 `data/checkpoints/hangzhou-0621-headless-002.json`。
