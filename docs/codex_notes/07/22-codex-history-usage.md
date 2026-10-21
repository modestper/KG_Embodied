### 任务背景

用户想了解 `/data/zc/datasets/LaDe/viewer/codex_history.py` 这个文件的用途和使用方式。

### 关键结论

- `codex_history.py` 是一个独立的 Codex 历史迁移脚本。
- 它用于迁移 Codex 本地历史中的 `model_provider` 字段，包括 rollout `jsonl` 和 `state_*.sqlite`。
- 默认只预览统计，不写入文件。
- 只有加 `--apply` 才会真正修改历史数据。
- 推荐真实执行时同时加 `--backup-dir` 做备份。
- 文件顶部示例里的 `scripts/tools/migrate_codex_provider_history.py` 是旧路径，当前仓库应使用 `codex_history.py`。

### 执行过的操作

- 读取 `codex_history.py` 前半部分：`sed -n '1,260p' codex_history.py`
- 读取 `codex_history.py` 后半部分：`sed -n '261,620p' codex_history.py`
- 查看文件权限：`ls -l codex_history.py`
- 搜索项目内引用：`rg -n "codex_history|migrate_codex_provider_history|model_provider|CODEX_HOME|CODEX_SQLITE_HOME" .`
- 查看命令帮助：`./run_in_lade.sh python codex_history.py --help`

### 修改内容

- 新增本说明文件：`docs/codex_notes/2026-07-22-codex-history-usage.md`
- 未修改 `codex_history.py`。

### 后续建议

- 先运行预览命令确认扫描结果。
- 确认目标 provider 正确后，再用 `--backup-dir` 和 `--apply` 执行真实迁移。
- 如果 Codex 数据目录不是默认的 `~/.codex`，需要显式传入 `--codex-home`。
