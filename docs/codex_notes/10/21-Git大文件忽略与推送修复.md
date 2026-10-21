# Git 大文件忽略与推送修复

### 任务背景

2026-10-21，向 GitHub 推送 `main` 时，远端因 Ray 运行日志和实验 JSONL 超过普通 Git 单文件限制而拒绝推送。用户希望学习如何忽略这些文件，同时保留本地实验数据。

### 关键结论

- 检查时本地 `main` 相对本地记录的 `origin/main` 领先 6 个提交、落后 0 个；未联网更新远端引用。
- 超限对象存在于这 6 个待推送提交中，最大为约 1056.33 MiB 的 Ray 日志；`test50/query_logs.jsonl` 约 155.20 MiB。
- `.gitignore` 只影响未跟踪文件。现有 `logs/` 规则已经能匹配 Ray 的日志子目录，但相关文件已经被 Git 跟踪，所以仍进入提交历史。
- 复查时 `.gitignore` 已新增 `test_single/`、`test_stage/`、`test50/`，其中 `test50/` 已覆盖该目录的 4 个大 JSONL；仍需补充 `/.ray_tmp/` 和 `/data/lade_llm_context_100drones_contexts.jsonl`。
- `git rm --cached` 从 Git 索引移除文件，保留磁盘文件；它本身不会清除已有提交中的大文件。
- 要让此次推送成功，还需要处理未推送历史。若接受将这 6 个提交合并成一次，可先备份分支，再以 `origin/main` 为基础重新提交；通常无需强制推送。
- 本次仅检查仓库并编写本教程，没有修改 `.gitignore`、暂存区、分支、提交历史或实验文件，也没有执行推送。

### 执行过的操作

读取了 `AGENTS.md`、`.gitignore`，检查了 `.gitignore` 的已有未提交修改。首次检查时修改仅为末尾增加的空行，复查时又出现 `test_single/`、`test_stage/`、`test50/` 规则；这些外部修改均予以保留。主要执行的只读命令如下：

```bash
git status --short --branch
git log --oneline --decorate -8
git diff -- .gitignore
git rev-list --left-right --count origin/main...main
git ls-files -- .gitignore .ray_tmp test50/index_contexts.jsonl test50/index_query_logs.jsonl data/lade_llm_context_100drones_contexts.jsonl test50/contexts.jsonl test50/query_logs.jsonl
git ls-tree -r --name-only origin/main -- .ray_tmp test50/index_contexts.jsonl test50/index_query_logs.jsonl data/lade_llm_context_100drones_contexts.jsonl test50/contexts.jsonl test50/query_logs.jsonl
git check-ignore -v --no-index -- .ray_tmp/session_2026-08-31_10-24-51_169034_2056166/logs/gcs_server.out test50/query_logs.jsonl test50/contexts.jsonl test50/index_contexts.jsonl test50/index_query_logs.jsonl data/lade_llm_context_100drones_contexts.jsonl
```

扫描待推送对象的命令：

```bash
git rev-list --objects origin/main..main |
  git cat-file --batch-check='%(objectname) %(objecttype) %(objectsize) %(rest)' |
  awk '$2 == "blob" && $3 >= 50 * 1024 * 1024 {printf "%.2f MiB\t%s\n", $3/1024/1024, substr($0,index($0,$4))}' |
  sort -nr
```

检查时这些路径均不存在于 `origin/main` 的文件树中。以下教程中的修改命令尚未执行。

### 修改内容

本次只新增此教程，未修改仓库配置或实验代码。

#### 第一步：补充 `.gitignore`

打开仓库根目录的 `.gitignore`，保留现有内容。复查时已有的 `test50/` 会忽略整个 `test50` 目录，因此只需在末尾追加：

```gitignore
# Ray runtime files
/.ray_tmp/

# Generated experiment export
/data/lade_llm_context_100drones_contexts.jsonl
```

开头的 `/` 将规则限定到仓库根目录下的对应路径。这里也排除了 50–100 MiB 的告警文件，以免实验导出继续增大后再次阻止推送。

如果不想忽略整个 `test50/`，而只想忽略日志中列出的 4 个大文件，可将现有 `test50/` 那一行替换为以下规则：

```gitignore
/test50/contexts.jsonl
/test50/query_logs.jsonl
/test50/index_contexts.jsonl
/test50/index_query_logs.jsonl
```

`test_single/`、`test_stage/` 属于你当前额外设置的忽略范围，本次没有调整。若保留整个 `test50/` 的规则，以下取消跟踪命令仍只移除此次指定的大文件；目录内其他已跟踪文件不会因为新增规则而自动停止跟踪。

#### 第二步：停止跟踪，保留本地文件

在仓库根目录执行：

```bash
git rm -r --cached --ignore-unmatch -- .ray_tmp
git rm --cached --ignore-unmatch -- \
  test50/contexts.jsonl \
  test50/query_logs.jsonl \
  test50/index_contexts.jsonl \
  test50/index_query_logs.jsonl \
  data/lade_llm_context_100drones_contexts.jsonl
git add -- .gitignore
```

必须保留 `--cached`：它表示只取消 Git 跟踪，不删除磁盘文件。`--ignore-unmatch` 允许重复执行这些命令。

此时 VS Code 显示相关文件为暂存的 `D`，表示下一次提交将不再包含它们，磁盘上的原文件仍保留。

#### 第三步：验证规则与跟踪状态

```bash
git check-ignore -v --no-index -- \
  .ray_tmp/session_2026-08-31_10-24-51_169034_2056166/logs/gcs_server.out \
  test50/contexts.jsonl \
  test50/query_logs.jsonl \
  test50/index_contexts.jsonl \
  test50/index_query_logs.jsonl \
  data/lade_llm_context_100drones_contexts.jsonl

git ls-files -- \
  .ray_tmp \
  test50/contexts.jsonl \
  test50/query_logs.jsonl \
  test50/index_contexts.jsonl \
  test50/index_query_logs.jsonl \
  data/lade_llm_context_100drones_contexts.jsonl

git diff --cached --stat
```

第一条应显示对应的忽略规则，第二条应无输出。文件已被跟踪时，普通 `git check-ignore` 可能不显示匹配，因此这里使用 `--no-index`。

完成以上步骤，后续 `git add` 就会忽略这些文件。如果只想配置今后的忽略行为，可以到这里停下；即使再提交一次删除，旧提交中的大文件仍然存在，当前推送仍可能失败。

#### 第四步：可选，解决这次推送的历史大文件

以下方案适用于：接受把尚未推送的 6 个提交合并成一个新提交，保留它们最终的代码内容，并从新提交中排除上述生成文件。旧的提交顺序和消息仍可在本地备份分支中查看。

**如果必须保留这 6 个提交在 `main` 上的逐次历史，不执行本节，应改为在备份后定向过滤待推送历史。**

先更新远端记录并确认状态：

```bash
git fetch origin
git branch --show-current
git rev-list --left-right --count origin/main...main
git status --short
```

预期当前分支为 `main`，计数仍为 `0 6`。状态应只包含你理解的修改、上述取消跟踪操作和本教程；若分支、计数或修改内容不符，先停止本节，重新核对。`git fetch` 只更新远端记录，不会改动工作文件。

创建备份后，将本地分支指针移回远端记录，但保留暂存区和磁盘文件：

```bash
git branch backup/main-before-large-file-cleanup-20261021
git reset --soft origin/main
```

如果备份分支已经存在，换一个尚未使用的名称后重新创建；必须确认备份创建成功，才执行下一条命令。备份分支保存已提交历史，不包含未提交修改。此处只使用 `--soft`，不要替换成 `--hard`。

再执行一次第二步的取消跟踪命令，确保生成文件不在新提交中：

```bash
git rm -r --cached --ignore-unmatch -- .ray_tmp
git rm --cached --ignore-unmatch -- \
  test50/contexts.jsonl \
  test50/query_logs.jsonl \
  test50/index_contexts.jsonl \
  test50/index_query_logs.jsonl \
  data/lade_llm_context_100drones_contexts.jsonl
git add -- .gitignore
git diff --cached --stat
git diff --cached --check
```

软重置后，之前 6 个提交相对 `origin/main` 的最终改动会留在暂存区，供重新提交。检查变更包含需要上传的代码，并确认 `.ray_tmp/` 和指定 JSONL 不在新增文件里。如需将本教程一起提交，另外执行：

```bash
git add -- docs/codex_notes/10/21-Git大文件忽略与推送修复.md
```

确认暂存内容正确后创建新提交：

```bash
git commit -m "更新项目代码并排除运行日志和实验大文件"
```

提交后扫描所有待推送对象，确认没有遗漏的超限文件：

```bash
git rev-list --objects origin/main..HEAD |
  git cat-file --batch-check='%(objectname) %(objecttype) %(objectsize) %(rest)' |
  awk '$2 == "blob" && $3 > 100 * 1024 * 1024 {print}'
```

预期无输出；如果有输出，先处理显示的路径，不要继续推送。

最后普通推送当前 `main`：

```bash
git push origin main:main
```

仅推送 `main`，不要使用 `--all` 或 `--mirror`，以免把含有旧大文件的备份分支也推上去。不要添加 `--force`；如果远端在操作期间有新提交导致推送被拒绝，应先核对远端变化。

### 后续建议

1. 先完成第一至第三步，确认本地实验文件还在、Git 已停止跟踪。
2. 若要立即修复此次推送，明确是否接受合并这 6 个未推送提交，再按第四步操作；本次未代为执行。
3. Ray 日志继续保留在本地；需要分享的大型实验数据可考虑独立存储或 Git LFS。已经进入普通 Git 历史的文件，需要迁移历史，不能只新增 LFS 跟踪规则。

参考：[GitHub 官方大文件限制及历史清理说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。
