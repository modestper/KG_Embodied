### 任务背景

用户希望在很长的 Codex CLI 对话中快速返回较早的提问区域，例如定位到约 20 个问题之前，而不是逐屏向上翻找。

### 关键结论

- Codex CLI 当前没有文档化的“按问题序号跳到倒数第 N 个问题”功能。
- 在主界面按 `Ctrl+R` 可以搜索历史提问，适合通过关键词快速找到自己以前输入过的问题。
- `Ctrl+R` 找到的是历史输入，主要用于恢复或复用提示词，并不等同于跳到原对话中对应回答的位置。
- `/raw on` 可以开启原始滚动模式，使终端选择、复制和使用终端自身搜索功能更直接。
- `/keymap` 可以查看和修改当前 TUI 的快捷键绑定。
- 如果需要精确检索以前的完整问答，最可靠的方法是将完整 session 导出为 Markdown 后用编辑器搜索。

### 执行过的操作

- 使用 `openai-docs` 技能核对 Codex 会话管理能力。
- 读取官方 OpenAI Developer commands 文档的 Markdown 版本。
- 核对本机 Codex CLI 0.154.0 的 `resume`、`fork` 和 `--no-alt-screen` 帮助信息。

### 修改内容

- 新增本说明文档，未修改项目代码或 Codex 配置。

### 后续建议

- 只记得问题关键词时，优先使用 `Ctrl+R` 搜索。
- 需要查看问题前后的完整回答时，使用 `--no-alt-screen`、`/raw on` 和终端搜索；长远可扩展现有历史脚本以导出完整问答。
- 对重要结论及时生成 Markdown 总结，避免依赖超长会话的滚动记录。

官方参考：[Developer commands](https://learn.chatgpt.com/docs/developer-commands)
