### 任务背景

用户执行 `scripts/create_lade_ab.sh --apply` 后看到了“操作未完成”，希望检查运行状态并定位失败原因。

### 关键结论

- “操作未完成”来自 `scripts/create_lade_ab.sh:170` 的统一 EXIT trap，不是 Neo4j 返回的具体错误；脚本内任意步骤非零退出或被中断都会打印这句话。
- 当前没有 `create_lade_ab.sh` 或 `copy_neo4j_runs.py` 进程，任务不是仍在后台执行。
- `/data/zc/datasets/LaDe/neo4j_ab/lade` 和 `lade-index` 均已存在，大小都约为 3.0 GB，说明数据库数据已复制并完成目录克隆。
- 文件时间线显示：`lade-index` 的数据库文件复制于 11:32:08 左右；随后 `lade` 在 11:32:28—11:32:38 被重新启动并更新文件。这与脚本第 386—409 行的“停止 lade、克隆、重启 lade、启动 lade-index”阶段吻合。
- 因此失败点高度集中在 `lade-index` 的 `docker run`、容器启动或 `wait_for_neo4j` 连接检查阶段；没有证据表明百万级节点复制本身失败。
- 用户随后确认 `sudo docker ps -a | grep lade` 只能看到 `lade`、看不到 `lade-index`。读取 `/tmp/lade-startup.log` 后确认，`lade` 于 03:32:30 开始启动、03:32:39 启用 Bolt、03:32:40 完成启动，只耗时约 12 秒，没有启动错误，因此排除第 398 行等待超时。
- 最终根因是脚本默认把 `lade-index` 映射到主机端口 `67474` 和 `67687`，两者均超过 TCP/UDP 端口最大值 `65535`。第 401 行的 `docker run` 因非法 host port 在容器对象创建前失败，所以数据目录已经存在，但 `docker ps -a` 中没有 `lade-index`。
- 当前 Codex 执行终端无法继承用户终端的 sudo 凭据，`sudo -n docker ...` 返回 `sudo: a password is required`，所以还不能读取容器的精确退出原因。
- 脚本本身没有将标准输出自动保存到日志文件；终端中具体错误通常位于“操作未完成”前面几行，容器启动错误则需要 `docker logs lade-index`。

### 执行过的操作

- 搜索项目日志与“操作未完成”字符串，定位到 `scripts/create_lade_ab.sh:170`。
- 阅读脚本第 130—482 行，梳理失败 trap、复制、克隆、容器启动、索引创建和最终校验流程。
- 检查相关进程，未发现仍在运行的复制任务。
- 检查两个数据目录的大小、内容和文件时间戳，确认它们均约 3.0 GB，并已执行克隆步骤。
- 尝试只读查看 Docker 容器状态和日志，但当前执行终端没有可用的 sudo 认证。
- 读取用户导出的 `/tmp/lade-startup.log`，确认 `lade` 正常关闭、正常重启，且 Bolt 和 HTTP 均成功启用；日志中的两条 X-Forward 安全警告与本次失败无关。

建议用户在已经执行过 `sudo -v` 的同一个终端运行：

```bash
{
  date
  sudo docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  sudo docker inspect --format 'name={{.Name}} running={{.State.Running}} exit={{.State.ExitCode}} error={{.State.Error}} started={{.State.StartedAt}} finished={{.State.FinishedAt}}' lade lade-index
  sudo docker logs --timestamps --tail 200 lade
  sudo docker logs --timestamps --tail 200 lade-index
} > /tmp/lade-ab-diagnose.log 2>&1
```

执行后，Codex 可以直接读取 `/tmp/lade-ab-diagnose.log` 继续定位。

### 修改内容

- 新增排查记录：`docs/codex_notes/10/08-lade复制未完成排查.md`。
- 将 `scripts/create_lade_ab.sh` 中 `lade-index` 的默认 HTTP/Bolt 端口修正为合法的 `61474/61687`。
- 为四个目标映射端口增加 `1..65535` 范围校验，使非法配置在执行 Docker 和复制数据之前直接失败。
- 同步修正 `docs/codex_notes/10/08-创建lade与lade-index查询库.md` 中的端口和命令示例。
- 未修改数据库文件或 Docker 容器。

### 后续建议

- 暂时不要重新运行 `create_lade_ab.sh --apply`，因为两个目标容器或数据目录已经存在，脚本的防覆盖检查会直接拒绝，也可能掩盖原始故障。
- 将 `LADE_INDEX_HTTP_PORT` 和 `LADE_INDEX_BOLT_PORT` 改为不超过 `65535` 且未占用的端口，例如 `61474` 和 `61687`，再仅补做容器启动、索引创建和数据校验，不能整段重跑复制脚本。
- 后续应在脚本预检中增加 `1..65535` 端口范围校验，避免在耗时的数据复制完成后才由 Docker 拒绝非法端口。
- 若后续修改脚本，可为每次执行自动保存带时间戳的日志，并在 EXIT trap 中打印失败命令和行号，避免只看到笼统的“操作未完成”。
