# 创建 lade 与 lade-index 查询库

### 任务背景

从源容器 `neo4j` 的内部数据库 `neo4j` 中，只复制 `run_id=hangzhou-0621-headless-002` 对应的百万级子图，建立两个独立查询容器：`lade` 为无三组 AgentState 属性索引的基线库，`lade-index` 为数据完全一致但带三组 AgentState 属性索引的实验库。

### 关键结论

- `lade`、`lade-index` 是 Docker 容器名；两个容器内部的 Neo4j database 名仍为 `neo4j`。
- `lade` 使用 HTTP/Bolt 端口 `57474/57687`，`lade-index` 使用 `61474/61687`。
- 两份数据使用 `/data/zc/datasets/LaDe/neo4j_ab/` 下的 bind mount，避免占用仅剩约 23 GB 的 Docker 根分区。
- 已新增 `scripts/create_lade_ab.sh`，将预检、复制、克隆、索引创建和最终一致性校验封装为一个安全脚本；默认只预检，必须显式传 `--apply` 才会修改 Docker 状态。
- 先通过现有 `scripts/copy_neo4j_runs.py` 只复制指定 run 到 `lade`，再删除三组 AgentState 属性索引。
- 停止 `lade` 后文件级复制得到 `lade-index`，然后只在 `lade-index` 中建立三组索引，因此两个实验库的数据起点一致。
- “无索引”准确含义是没有三组 AgentState 业务属性索引；Neo4j 默认的节点标签/关系类型 `LOOKUP` 索引应保留。
- 如果源 run 仍在写入，必须先停止仿真写入者，否则节点和关系可能不是同一时刻的快照。

### 执行过的操作

- 阅读并核对 `scripts/copy_neo4j_runs.py` 的预览、复制、schema 重建和逐类计数校验流程。
- 阅读并核对 `scripts/clone_neo4j_query_volume.sh` 的一致性 volume 克隆思路。
- 尝试只读查看 Docker 状态，但当前执行环境无法提供宿主机 sudo 密码，因此没有创建、停止或修改任何容器。
- 检查磁盘：`/data` 约有 3.3 TB 可用，根分区仅约有 23 GB 可用。
- 整理了下面可由用户在服务器终端逐段执行的完整命令。
- 新增并检查 `scripts/create_lade_ab.sh`；`bash -n` 通过，`--help` 输出正常。当前环境没有安装 `shellcheck`，因此未执行 ShellCheck。

### 修改内容

- 新增 `scripts/create_lade_ab.sh` 自动化脚本。
- 更新本操作说明，保留逐步命令作为人工核对和故障恢复参考。
- 没有修改业务代码、Neo4j 数据、Docker 容器或已有实验结果。

## 最简执行方式

不要使用 `sudo bash ...` 启动整个脚本；以普通用户运行即可，脚本内部会在需要 Docker 权限时执行 `sudo`，并在同一终端提示认证。

第一遍只做预检，不创建容器：

```bash
cd /data/zc/datasets/LaDe/viewer
bash scripts/create_lade_ab.sh
```

预检通过后正式执行，并保存完整日志：

```bash
cd /data/zc/datasets/LaDe/viewer
set -o pipefail
bash scripts/create_lade_ab.sh --apply 2>&1 | tee logs/create-lade-ab-20261008.log
```

脚本会依次提示：

```text
sudo 密码
源 Neo4j 密码
新查询库密码（lade 与 lade-index 共用）
```

正式复制可能持续较长时间。运行过程中不要关闭终端；如果通过 SSH 操作，建议先进入 `tmux` 或 `screen`。

## 一、只读预检

进入项目目录并刷新 sudo 凭证：

```bash
cd /data/zc/datasets/LaDe/viewer
sudo -v
```

查看现有容器，确认没有叫 `lade` 或 `lade-index` 的容器：

```bash
sudo docker ps -a \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

检查目标端口。无输出才表示没有进程监听：

```bash
sudo ss -ltnp | grep -E ':(57474|57687|61474|61687)\b'
```

查看 Docker 与 `/data` 空间：

```bash
sudo docker system df -v
df -h /data/zc/datasets/LaDe
```

确认源容器的版本、镜像和端口：

```bash
sudo docker inspect \
  --format 'name={{.Name}} image_id={{.Image}} running={{.State.Running}} ports={{json .NetworkSettings.Ports}}' \
  neo4j

sudo docker exec neo4j neo4j --version
```

若 `lade`、`lade-index` 已存在，或任一目标端口有输出，请先停下，不要执行后续命令，也不要删除旧容器。

## 二、准备变量和密码

下面不会把密码明文写入 shell 历史：

```bash
RUN_ID='hangzhou-0621-headless-002'
SOURCE_CONTAINER='neo4j'
SOURCE_URI='bolt://127.0.0.1:17687'
LADE_URI='bolt://127.0.0.1:57687'
LADE_INDEX_URI='bolt://127.0.0.1:61687'

LADE_DATA_DIR='/data/zc/datasets/LaDe/neo4j_ab/lade'
LADE_INDEX_DATA_DIR='/data/zc/datasets/LaDe/neo4j_ab/lade-index'

SOURCE_IMAGE_ID=$(sudo docker inspect --format '{{.Image}}' "$SOURCE_CONTAINER")

read -rsp '源 neo4j 密码: ' SOURCE_NEO4J_PASSWORD
echo
export SOURCE_NEO4J_PASSWORD

read -rsp '新查询库密码: ' LADE_NEO4J_PASSWORD
echo
export LADE_NEO4J_PASSWORD
```

新查询库密码至少应满足 Neo4j 的长度要求。`lade-index` 是从 `lade` 的 `/data` 完整复制出来的，因此沿用同一密码。

## 三、确认源 run 已停止变化

连续执行两次，中间间隔 30 秒：

```bash
sudo docker exec "$SOURCE_CONTAINER" cypher-shell \
  -u neo4j -p "$SOURCE_NEO4J_PASSWORD" -d neo4j \
  "MATCH (s:Step {run_id: '$RUN_ID'})
   RETURN count(s) AS steps,
          max(s.step_index) AS max_step,
          max(s.time_minute) AS max_time;"

sleep 30

sudo docker exec "$SOURCE_CONTAINER" cypher-shell \
  -u neo4j -p "$SOURCE_NEO4J_PASSWORD" -d neo4j \
  "MATCH (s:Step {run_id: '$RUN_ID'})
   RETURN count(s) AS steps,
          max(s.step_index) AS max_step,
          max(s.time_minute) AS max_time;"
```

两次结果必须相同。如果仍增长，应先停止对应仿真/supervisor，但保持源 Neo4j 容器运行；确认稳定后再复制。

查看目标 run 的节点分组：

```bash
sudo docker exec "$SOURCE_CONTAINER" cypher-shell \
  -u neo4j -p "$SOURCE_NEO4J_PASSWORD" -d neo4j \
  "MATCH (n)
   WHERE n.run_id = '$RUN_ID' OR (n:Run AND n.id = '$RUN_ID')
   RETURN labels(n) AS labels, count(*) AS node_count
   ORDER BY node_count DESC;"
```

## 四、在大容量 `/data` 分区创建 lade

读取源镜像中 Neo4j 用户的 UID/GID，并创建空目录：

```bash
NEO4J_UID=$(sudo docker run --rm --entrypoint sh "$SOURCE_IMAGE_ID" -c 'id -u neo4j')
NEO4J_GID=$(sudo docker run --rm --entrypoint sh "$SOURCE_IMAGE_ID" -c 'id -g neo4j')

sudo install -d -o "$NEO4J_UID" -g "$NEO4J_GID" "$LADE_DATA_DIR"
sudo install -d -o "$NEO4J_UID" -g "$NEO4J_GID" "$LADE_INDEX_DATA_DIR"
```

确认两个目录为空：

```bash
sudo find "$LADE_DATA_DIR" -mindepth 1 -maxdepth 1 -print
sudo find "$LADE_INDEX_DATA_DIR" -mindepth 1 -maxdepth 1 -print
```

两条命令都应无输出。随后启动空的 `lade`：

```bash
sudo docker run -d \
  --name lade \
  --restart unless-stopped \
  -p 57474:7474 \
  -p 57687:7687 \
  --mount "type=bind,src=$LADE_DATA_DIR,dst=/data" \
  -e NEO4J_AUTH="neo4j/$LADE_NEO4J_PASSWORD" \
  "$SOURCE_IMAGE_ID"
```

等待目标库就绪：

```bash
until sudo docker exec lade cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'RETURN 1 AS ok;' >/dev/null 2>&1
do
  sleep 2
done

echo 'lade 已就绪'
```

## 五、预览并复制指定 run

先预览；这一步会统计源子图、检查边界关系、确认版本一致和目标为空，但不会写入：

```bash
SOURCE_NEO4J_URI="$SOURCE_URI" \
SOURCE_NEO4J_PASSWORD="$SOURCE_NEO4J_PASSWORD" \
TARGET_NEO4J_URI="$LADE_URI" \
TARGET_NEO4J_PASSWORD="$LADE_NEO4J_PASSWORD" \
./run_in_lade.sh python scripts/copy_neo4j_runs.py \
  --run-id "$RUN_ID" \
  --batch-size 2000 \
  --fetch-size 1000 \
  --progress-every 50000 \
  --index-timeout-seconds 3600
```

必须看到目标库为 0 个节点，且没有未解释的跨 run 边界关系。不要为了绕过报错而直接增加 `--allow-boundary-relationships`。

确认预览无误后正式复制：

```bash
SOURCE_NEO4J_URI="$SOURCE_URI" \
SOURCE_NEO4J_PASSWORD="$SOURCE_NEO4J_PASSWORD" \
TARGET_NEO4J_URI="$LADE_URI" \
TARGET_NEO4J_PASSWORD="$LADE_NEO4J_PASSWORD" \
./run_in_lade.sh python scripts/copy_neo4j_runs.py \
  --run-id "$RUN_ID" \
  --batch-size 2000 \
  --fetch-size 1000 \
  --progress-every 50000 \
  --index-timeout-seconds 3600 \
  --apply
```

脚本完成时应明确输出“复制和校验完成”，并逐标签、逐关系类型核对源和目标数量。

## 六、把 lade 变成无 AgentState 属性索引基线

复制脚本会保留源 schema，因此先删除三组实验索引：

```bash
sudo docker exec lade cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'DROP INDEX lade_agent_state_run_drone_step IF EXISTS;'

sudo docker exec lade cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'DROP INDEX lade_agent_state_run_region_step IF EXISTS;'

sudo docker exec lade cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'DROP INDEX lade_agent_state_run_step IF EXISTS;'
```

确认三组索引为零，同时保留默认 LOOKUP 索引：

```bash
sudo docker exec lade cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  "SHOW INDEXES
   YIELD name, state, type, labelsOrTypes, properties
   RETURN name, state, type, labelsOrTypes, properties
   ORDER BY name;"
```

## 七、从 lade 制作完全一致的 lade-index

先停止 `lade`，获得一致的文件级快照：

```bash
sudo docker stop lade
```

再次确认目标目录为空，然后复制：

```bash
sudo find "$LADE_INDEX_DATA_DIR" -mindepth 1 -maxdepth 1 -print
sudo cp -a "$LADE_DATA_DIR/." "$LADE_INDEX_DATA_DIR/"
sudo chown -R "$NEO4J_UID:$NEO4J_GID" "$LADE_INDEX_DATA_DIR"
```

恢复 `lade`：

```bash
sudo docker start lade
```

启动 `lade-index`。认证数据已经包含在复制的 `/data` 中，所以这里不再传 `NEO4J_AUTH`：

```bash
sudo docker run -d \
  --name lade-index \
  --restart unless-stopped \
  -p 61474:7474 \
  -p 61687:7687 \
  --mount "type=bind,src=$LADE_INDEX_DATA_DIR,dst=/data" \
  "$SOURCE_IMAGE_ID"
```

等待就绪：

```bash
until sudo docker exec lade-index cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'RETURN 1 AS ok;' >/dev/null 2>&1
do
  sleep 2
done

echo 'lade-index 已就绪'
```

## 八、只在 lade-index 建立三组索引

```bash
sudo docker exec lade-index cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'CREATE RANGE INDEX lade_agent_state_run_drone_step IF NOT EXISTS
   FOR (n:AgentState) ON (n.run_id, n.drone_id, n.step_index);'

sudo docker exec lade-index cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'CREATE RANGE INDEX lade_agent_state_run_region_step IF NOT EXISTS
   FOR (n:AgentState) ON (n.run_id, n.region_id, n.step_index);'

sudo docker exec lade-index cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'CREATE RANGE INDEX lade_agent_state_run_step IF NOT EXISTS
   FOR (n:AgentState) ON (n.run_id, n.step_index);'
```

等待所有索引上线：

```bash
sudo docker exec lade-index cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'CALL db.awaitIndexes(3600);'
```

查看最终状态：

```bash
sudo docker exec lade-index cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  "SHOW INDEXES
   YIELD name, state, type, labelsOrTypes, properties, populationPercent
   WHERE name STARTS WITH 'lade_agent_state_'
   RETURN name, state, type, labelsOrTypes, properties, populationPercent
   ORDER BY name;"
```

预期三条均为 `RANGE / ONLINE / 100.0`。

## 九、最终一致性验证

确认两个容器都只含一个 run：

```bash
sudo docker exec lade cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'MATCH (r:Run) RETURN collect(r.id) AS run_ids, count(r) AS run_count;'

sudo docker exec lade-index cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  'MATCH (r:Run) RETURN collect(r.id) AS run_ids, count(r) AS run_count;'
```

使用现有迁移工具直接比较两份实验库的节点和关系分组：

```bash
SOURCE_NEO4J_URI="$LADE_URI" \
SOURCE_NEO4J_PASSWORD="$LADE_NEO4J_PASSWORD" \
TARGET_NEO4J_URI="$LADE_INDEX_URI" \
TARGET_NEO4J_PASSWORD="$LADE_NEO4J_PASSWORD" \
./run_in_lade.sh python scripts/copy_neo4j_runs.py \
  --run-id "$RUN_ID" \
  --verify-only
```

预期输出：

```text
校验通过：源和目标的节点/关系分组计数一致。
```

最后确认 A/B 的唯一预期差异：

```bash
sudo docker exec lade cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  "SHOW INDEXES YIELD name,state
   WHERE name STARTS WITH 'lade_agent_state_'
   RETURN name,state;"

sudo docker exec lade-index cypher-shell \
  -u neo4j -p "$LADE_NEO4J_PASSWORD" -d neo4j \
  "SHOW INDEXES YIELD name,state
   WHERE name STARTS WITH 'lade_agent_state_'
   RETURN name,state ORDER BY name;"
```

`lade` 应返回 0 条，`lade-index` 应返回 3 条且全部 `ONLINE`。

## 十、查询实验连接地址

无三组 AgentState 索引：

```text
容器：lade
HTTP：http://127.0.0.1:57474
Bolt：bolt://127.0.0.1:57687
database：neo4j
```

带三组 AgentState 索引：

```text
容器：lade-index
HTTP：http://127.0.0.1:61474
Bolt：bolt://127.0.0.1:61687
database：neo4j
```

不要同时对两个库做高并发压测，否则它们会争用同一宿主机 CPU、内存和磁盘，影响对照结果。建议使用完全相同的查询、并发、预热与重复次数，分时依次测试。

## 十一、结束后清理密码变量

```bash
unset SOURCE_NEO4J_PASSWORD
unset LADE_NEO4J_PASSWORD
```

### 后续建议

- 先只执行“一、只读预检”和“三、确认源 run 已停止变化”，确认名称、端口和 run 状态后再创建容器。
- 正式复制可能持续较长时间；不要关闭运行复制命令的终端，也不要中途重启源或目标 Neo4j。
- 保留复制脚本最后的统计输出，作为 A/B 数据一致性的实验凭据。
- 首轮性能测试前分别重启两个查询容器并采用相同预热流程，降低页缓存状态不同带来的偏差。
