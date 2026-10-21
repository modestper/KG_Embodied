### 任务背景

用户询问动态网页界面中的帧间隔 `20s` 是否固定，是否写死在代码里，能否在前端更改。

### 关键结论

- `20s` 不是完全写死，它是后端 `--step-seconds` 的默认值。
- 后端 `scripts/run_dynamic_sim.py` 支持从 URL query 中读取 `step_seconds` 并覆盖默认值。
- 当前 `dynamic.html` 没有提供 `step_seconds` 输入框。
- 当前 `src/dynamic_app.mjs` 的 `buildQuery()` 也没有把 `step_seconds` 传给后端。
- 所以从当前网页界面上不能直接改，但可以通过后端启动参数或手动 URL 参数改。

### 执行过的操作

- 搜索相关参数：`rg -n "step_seconds|step-seconds|frame_stride|wall_interval|max_steps" scripts/run_dynamic_sim.py dynamic.html src/dynamic_app.mjs -S`
- 阅读前端页面：`sed -n '1,220p' dynamic.html`
- 阅读前端逻辑：`sed -n '1,260p' src/dynamic_app.mjs`

### 修改内容

- 新增本说明文件：`docs/codex_notes/2026-08-25-step-seconds前端配置.md`。
- 未修改项目代码。

### 后续建议

- 如果希望在动态网页中直接调整帧间隔，需要给 `dynamic.html` 增加一个 `stepSeconds` 输入框，并在 `src/dynamic_app.mjs` 的 `buildQuery()` 中加入 `step_seconds` 参数。
