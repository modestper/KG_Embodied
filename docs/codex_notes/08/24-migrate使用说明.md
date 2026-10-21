### 任务背景

用户想了解 `/data/zc/datasets/LaDe/viewer/migrate.py` 这个文件的用途和使用方式。

### 关键结论

- `migrate.py` 是一个独立的 Codex 本地历史迁移脚本，不是 LaDe 无人机仿真主流程的一部分。
- 它用于把 Codex 本地历史中的 `model_provider` 字段迁移到新的 provider key。
- 默认只做预览统计，不会写入文件；只有加 `--apply` 才会真正改写历史。
- 它会处理两类数据：`sessions/` 和 `archived_sessions/` 下的 rollout `*.jsonl`，以及 `state_*.sqlite` 中 `threads.model_provider`。
- 目标 provider 默认来自 `~/.codex/config.toml` 的 `model_provider`；读取不到时默认是 `openai`。
- 默认只保留目标 provider，其他 provider 都会被迁移到目标 provider；如果要保留某个旧 provider，需要显式传 `--keep-provider`。
- 当前 `migrate.py` 与 `codex_history.py` 内容一致；文件顶部示例里的 `scripts/tools/migrate_codex_provider_history.py` 是旧路径，在本仓库应使用 `migrate.py`。

### 执行过的操作

- 读取脚本前半部分：`sed -n '1,240p' /data/zc/datasets/LaDe/viewer/migrate.py`
- 读取脚本后半部分：`sed -n '241,560p' /data/zc/datasets/LaDe/viewer/migrate.py`
- 搜索项目内引用：`rg -n "migrate\\.py|migrate|run_in_lade|LaDe" -S /data/zc/datasets/LaDe/viewer`
- 查看项目根目录文件：`ls -la /data/zc/datasets/LaDe/viewer`
- 查看命令行帮助：`./run_in_lade.sh python migrate.py --help`
- 读取既有相关说明：`sed -n '1,140p' docs/codex_notes/2026-07-22-codex-history-usage.md`
- 对比 `migrate.py` 与 `codex_history.py`：`cmp -s migrate.py codex_history.py`

### 修改内容

- 新增本说明文件：`docs/codex_notes/2026-08-24-migrate使用说明.md`。
- 未修改 `/data/zc/datasets/LaDe/viewer/migrate.py`。

### 后续建议

- 先运行预览命令确认扫描范围和 provider 分布：`./run_in_lade.sh python migrate.py`
- 确认目标 provider 后再执行写入，并建议加备份目录：`./run_in_lade.sh python migrate.py --backup-dir /tmp/codex-provider-backup --apply`
- 如果 Codex 数据目录不是默认 `~/.codex`，需要传 `--codex-home`。
- 如果需要保留旧 provider，例如 `openai`，真实执行时要加：`--keep-provider openai`。
