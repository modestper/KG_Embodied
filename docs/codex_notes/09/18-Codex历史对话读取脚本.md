### 任务背景

用户希望读取 `~/.codex/history.jsonl`，查看其中有哪些 Codex 对话，以及自己在每个对话中提出过哪些问题。

### 关键结论

- 已新增 `scripts/list_codex_history.py`，默认读取 `~/.codex/history.jsonl` 和同目录下的 `session_index.jsonl`。
- 脚本按 `session_id` 聚合对话，并按时间顺序列出每个会话中的全部用户输入。
- 优先显示 `session_index.jsonl` 中的真实对话名称；旧会话没有名称记录时，以首条用户输入生成名称。
- 支持使用 `--name` 按对话名称进行忽略大小写的部分匹配。
- 默认按最近更新的对话优先展示，支持限制数量或筛选指定会话。
- 支持终端文本、Markdown 和 JSON 三种输出格式，也可直接写入文件。
- 对空行、损坏的 JSON、字段缺失及无效时间戳会给出警告并跳过，不影响其余记录。

### 执行过的操作

- 读取 `AGENTS.md`，确认项目工作规则。
- 抽样读取 `~/.codex/history.jsonl`，确认实际字段为 `session_id`、`ts` 和 `text`。
- 读取 `~/.codex/session_index.jsonl`，确认对话名称字段为 `thread_name`，同一会话最后一条记录代表最新名称。
- 运行 `./run_in_lade.sh python -m py_compile scripts/list_codex_history.py` 检查语法。
- 使用真实历史文件分别验证 text、Markdown、JSON 输出。
- 验证 `--limit` 与 `--output` 参数可正常工作。

### 修改内容

- 新增并完善 `scripts/list_codex_history.py`：实现历史读取、名称索引读取、异常行容错、会话聚合、时间排序、ID/名称筛选以及多格式输出。
- 新增本总结文档，记录实现内容和使用方法。

常用命令：

```bash
# 在终端列出全部对话
./run_in_lade.sh python scripts/list_codex_history.py

# 只看最近 10 个对话
./run_in_lade.sh python scripts/list_codex_history.py --limit 10

# 输出为 Markdown 文件
./run_in_lade.sh python scripts/list_codex_history.py \
  --format markdown \
  --output codex_history.md

# 只看某个会话
./run_in_lade.sh python scripts/list_codex_history.py \
  --session 01a0b299-92c1-73e2-9229-45834af81bae

# 按对话名称筛选（支持部分匹配）
./run_in_lade.sh python scripts/list_codex_history.py \
  --name "编写对话历史"

# 查看完整参数说明
./run_in_lade.sh python scripts/list_codex_history.py --help
```

### 后续建议

- 历史内容可能包含命令、路径、账号等敏感信息，导出文件不建议提交到 Git 或公开分享。
- 如果后续需要关键词搜索、时间范围筛选或交互式选择会话，可以继续在此脚本上扩展。
