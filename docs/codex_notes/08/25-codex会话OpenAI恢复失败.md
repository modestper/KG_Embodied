### 任务背景

用户想排查 Codex 会话 `019ed86e-f4b4-7340-951b-cf2e248415f0` 无法恢复的问题。报错为：`Model provider OpenAI not found`。用户说明之前给该会话或 provider 更改过别名。

### 关键结论

- 根因是 provider key 大小写和配置不一致：历史中保存了 `OpenAI`，当前生效配置只使用 `openai`。
- 目标 rollout 文件第 1 行的 `session_meta.payload.model_provider` 是 `openai`，但第 644、700、749、759、768、777、786、795、805、814、823 行的 `thread_settings.thread_settings.model_provider_id` 是 `OpenAI`。
- 当前 `/home/chioe/.codex/config.toml` 中 `model_provider = "openai"`，没有 `[model_providers.OpenAI]` 定义。
- `/home/chioe/.codex/config02.toml` 中曾经存在 `[model_providers.OpenAI]`，说明 `OpenAI` 是旧的自定义 provider key 或别名。
- `/home/chioe/.codex/state_5.sqlite` 的 `threads` 表中，只有该会话一行的 `model_provider` 是 `OpenAI`；其他 38 行是 `openai`。
- 当前 `migrate.py --target-provider openai --codex-home /home/chioe/.codex` 预览显示只会修 SQLite 这一行，不会修 rollout 里的 `model_provider_id` 事件。

### 执行过的操作

- 查看目标 rollout 中 provider 相关字段：`rg -n "session_meta|model_provider|OpenAI|openai" /home/chioe/.codex/sessions/2026/06/18/rollout-2026-06-18T09-53-30-019ed86e-f4b4-7340-951b-cf2e248415f0.jsonl`
- 查看当前生效配置：`sed -n '1,220p' /home/chioe/.codex/config.toml`
- 查找 state sqlite：`rg --files -g 'state_*.sqlite' /home/chioe/.codex`
- 查看 Codex Home 文件：`ls -la /home/chioe/.codex`
- 查看 SQLite 表结构：`sqlite3 /home/chioe/.codex/state_5.sqlite '.schema threads'`
- 统计 SQLite provider 分布：`sqlite3 /home/chioe/.codex/state_5.sqlite 'SELECT model_provider, COUNT(*) FROM threads GROUP BY model_provider ORDER BY COUNT(*) DESC, model_provider ASC;'`
- 查询目标会话 SQLite 行：`sqlite3 -header -column /home/chioe/.codex/state_5.sqlite "SELECT id, model_provider, model, name, title, rollout_path FROM threads WHERE id = '019ed86e-f4b4-7340-951b-cf2e248415f0';"`
- 精简列出 rollout 中 provider 字段：`rg -n -o '"model_provider_id":"[^"]+"|"model_provider":"[^"]+"' /home/chioe/.codex/sessions/2026/06/18/rollout-2026-06-18T09-53-30-019ed86e-f4b4-7340-951b-cf2e248415f0.jsonl`
- 查看旧配置文件：`sed -n '1,220p' /home/chioe/.codex/config02.toml`
- 预览迁移脚本效果：`./run_in_lade.sh python migrate.py --target-provider openai --codex-home /home/chioe/.codex`

### 修改内容

- 新增本排查总结：`docs/codex_notes/2026-08-25-codex会话OpenAI恢复失败.md`。
- 未修改 `/home/chioe/.codex/config.toml`、目标 rollout JSONL 或 SQLite。

### 后续建议

- 如果希望继续使用当前小写 `openai` provider，需要同时把目标 rollout 里的 `model_provider_id":"OpenAI"` 改成 `model_provider_id":"openai"`，并把 SQLite 中该会话的 `threads.model_provider` 改成 `openai`。
- 如果希望恢复旧的自定义 provider key，则需要把 `[model_providers.OpenAI]` 定义恢复到当前 `/home/chioe/.codex/config.toml`。
- 不建议只运行当前 `migrate.py --apply`，因为它不会处理 rollout 中的 `thread_settings_applied` 事件，可能仍然无法恢复该会话。
- 实际修复前应备份目标 rollout 文件和 `/home/chioe/.codex/state_5.sqlite`、`state_5.sqlite-wal`、`state_5.sqlite-shm`。
