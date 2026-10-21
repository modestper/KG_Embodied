#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)

RUN_ID="${RUN_ID:-hangzhou-0621-headless-002}"
SOURCE_CONTAINER="${SOURCE_CONTAINER:-neo4j}"
SOURCE_URI="${SOURCE_URI:-bolt://127.0.0.1:17687}"
SOURCE_NEO4J_USER="${SOURCE_NEO4J_USER:-neo4j}"
SOURCE_NEO4J_DATABASE="${SOURCE_NEO4J_DATABASE:-neo4j}"

LADE_CONTAINER="${LADE_CONTAINER:-lade}"
LADE_HTTP_PORT="${LADE_HTTP_PORT:-57474}"
LADE_BOLT_PORT="${LADE_BOLT_PORT:-57687}"
LADE_URI="${LADE_URI:-bolt://127.0.0.1:${LADE_BOLT_PORT}}"

LADE_INDEX_CONTAINER="${LADE_INDEX_CONTAINER:-lade-index}"
LADE_INDEX_HTTP_PORT="${LADE_INDEX_HTTP_PORT:-61474}"
LADE_INDEX_BOLT_PORT="${LADE_INDEX_BOLT_PORT:-61687}"
LADE_INDEX_URI="${LADE_INDEX_URI:-bolt://127.0.0.1:${LADE_INDEX_BOLT_PORT}}"

DATA_ROOT="${DATA_ROOT:-/data/zc/datasets/LaDe/neo4j_ab}"
LADE_DATA_DIR="${LADE_DATA_DIR:-${DATA_ROOT}/lade}"
LADE_INDEX_DATA_DIR="${LADE_INDEX_DATA_DIR:-${DATA_ROOT}/lade-index}"

BATCH_SIZE="${BATCH_SIZE:-2000}"
FETCH_SIZE="${FETCH_SIZE:-1000}"
PROGRESS_EVERY="${PROGRESS_EVERY:-50000}"
INDEX_WAIT_SECONDS="${INDEX_WAIT_SECONDS:-3600}"
STABILITY_WAIT_SECONDS="${STABILITY_WAIT_SECONDS:-30}"
READY_ATTEMPTS="${READY_ATTEMPTS:-90}"

APPLY=0
LADE_STOPPED_BY_SCRIPT=0

AGENT_STATE_INDEXES=(
  lade_agent_state_run_drone_step
  lade_agent_state_run_region_step
  lade_agent_state_run_step
)

usage() {
  cat <<'EOF'
用法:
  bash scripts/create_lade_ab.sh
  bash scripts/create_lade_ab.sh --apply

默认只执行只读预检并打印计划；传入 --apply 才会：
  1. 创建无 AgentState 属性索引的查询容器 lade；
  2. 从源 neo4j 只复制 hangzhou-0621-headless-002；
  3. 文件级克隆得到 lade-index；
  4. 只在 lade-index 创建三组 AgentState RANGE 索引；
  5. 比较两份库的节点和关系分组计数。

安全行为:
  - 不删除任何容器或目录；
  - 不覆盖同名容器；
  - 不写入非空目标目录；
  - 端口占用、源 run 变化、复制或校验失败时立即停止；
  - 克隆阶段即使失败，也会尝试恢复 lade 容器。

常用可覆盖环境变量:
  RUN_ID, SOURCE_CONTAINER, SOURCE_URI
  LADE_HTTP_PORT, LADE_BOLT_PORT
  LADE_INDEX_HTTP_PORT, LADE_INDEX_BOLT_PORT
  DATA_ROOT, STABILITY_WAIT_SECONDS, INDEX_WAIT_SECONDS
  SOURCE_NEO4J_PASSWORD, LADE_NEO4J_PASSWORD
EOF
}

die() {
  echo "错误: $*" >&2
  exit 1
}

info() {
  echo
  echo "==> $*"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1"
}

container_exists() {
  sudo docker inspect "$1" >/dev/null 2>&1
}

container_running() {
  [[ "$(sudo docker inspect --format '{{.State.Running}}' "$1")" == "true" ]]
}

port_is_listening() {
  local port="$1"
  sudo ss -ltnH | awk '{print $4}' | grep -Eq ":${port}$"
}

directory_is_nonempty() {
  local directory="$1"
  [[ -d "$directory" ]] &&
    [[ -n "$(sudo find "$directory" -mindepth 1 -maxdepth 1 -print -quit)" ]]
}

validate_positive_integer() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "$name 必须是正整数，当前值: $value"
}

validate_port() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[0-9]+$ ]] || die "$name 必须是整数，当前值: $value"
  ((10#$value >= 1 && 10#$value <= 65535)) ||
    die "$name 必须在 1..65535 范围内，当前值: $value"
}

run_cypher() {
  local container="$1"
  local password="$2"
  local query="$3"
  sudo docker exec "$container" cypher-shell \
    -u neo4j -p "$password" -d neo4j "$query"
}

run_cypher_plain() {
  local container="$1"
  local password="$2"
  local query="$3"
  sudo docker exec "$container" cypher-shell --format plain \
    -u neo4j -p "$password" -d neo4j "$query"
}

query_scalar() {
  local container="$1"
  local password="$2"
  local query="$3"
  run_cypher_plain "$container" "$password" "$query" |
    tail -n 1 |
    tr -d '[:space:]"'
}

source_signature() {
  run_cypher_plain "$SOURCE_CONTAINER" "$SOURCE_NEO4J_PASSWORD" \
    "MATCH (r:Run {id: '$RUN_ID'})
     OPTIONAL MATCH (s:Step {run_id: '$RUN_ID'})
     RETURN count(DISTINCT r) AS run_count,
            count(DISTINCT s) AS step_count,
            coalesce(max(s.step_index), -1) AS max_step,
            coalesce(max(s.time_minute), -1) AS max_time;"
}

wait_for_neo4j() {
  local container="$1"
  local password="$2"
  local attempt
  for ((attempt = 1; attempt <= READY_ATTEMPTS; attempt++)); do
    if sudo docker exec "$container" cypher-shell \
      -u neo4j -p "$password" -d neo4j \
      'RETURN 1 AS ok;' >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  die "容器 $container 在 $((READY_ATTEMPTS * 2)) 秒内未能接受 Bolt 连接"
}

restore_lade_if_needed() {
  local exit_code=$?
  if ((LADE_STOPPED_BY_SCRIPT)); then
    echo "尝试恢复容器 $LADE_CONTAINER ..." >&2
    sudo docker start "$LADE_CONTAINER" >/dev/null 2>&1 || true
    LADE_STOPPED_BY_SCRIPT=0
  fi
  if ((exit_code != 0)); then
    echo "操作未完成。脚本不会自动删除已创建的容器或数据，请先检查日志。" >&2
  fi
  return "$exit_code"
}

trap restore_lade_if_needed EXIT

while (($#)); do
  case "$1" in
    --apply)
      APPLY=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知参数: $1"
      ;;
  esac
  shift
done

require_command sudo
require_command docker
require_command ss
require_command awk
require_command grep
require_command tail
require_command tr
require_command df

[[ -x "$PROJECT_ROOT/run_in_lade.sh" ]] || die "找不到可执行文件: $PROJECT_ROOT/run_in_lade.sh"
[[ -f "$PROJECT_ROOT/scripts/copy_neo4j_runs.py" ]] || die "找不到复制工具 scripts/copy_neo4j_runs.py"
[[ "$RUN_ID" =~ ^[A-Za-z0-9._:-]+$ ]] || die "RUN_ID 包含不安全字符: $RUN_ID"

validate_positive_integer BATCH_SIZE "$BATCH_SIZE"
validate_positive_integer FETCH_SIZE "$FETCH_SIZE"
validate_positive_integer PROGRESS_EVERY "$PROGRESS_EVERY"
validate_positive_integer INDEX_WAIT_SECONDS "$INDEX_WAIT_SECONDS"
validate_positive_integer STABILITY_WAIT_SECONDS "$STABILITY_WAIT_SECONDS"
validate_positive_integer READY_ATTEMPTS "$READY_ATTEMPTS"

validate_port LADE_HTTP_PORT "$LADE_HTTP_PORT"
validate_port LADE_BOLT_PORT "$LADE_BOLT_PORT"
validate_port LADE_INDEX_HTTP_PORT "$LADE_INDEX_HTTP_PORT"
validate_port LADE_INDEX_BOLT_PORT "$LADE_INDEX_BOLT_PORT"

info "验证 sudo"
sudo -v

container_exists "$SOURCE_CONTAINER" || die "找不到源容器: $SOURCE_CONTAINER"
container_running "$SOURCE_CONTAINER" || die "源容器未运行: $SOURCE_CONTAINER"

for target_container in "$LADE_CONTAINER" "$LADE_INDEX_CONTAINER"; do
  if container_exists "$target_container"; then
    die "目标容器已存在，拒绝覆盖: $target_container"
  fi
done

for target_port in \
  "$LADE_HTTP_PORT" "$LADE_BOLT_PORT" \
  "$LADE_INDEX_HTTP_PORT" "$LADE_INDEX_BOLT_PORT"; do
  if port_is_listening "$target_port"; then
    die "目标端口已被占用: $target_port"
  fi
done

for target_directory in "$LADE_DATA_DIR" "$LADE_INDEX_DATA_DIR"; do
  if [[ -L "$target_directory" ]]; then
    die "目标路径是符号链接，拒绝使用: $target_directory"
  fi
  if directory_is_nonempty "$target_directory"; then
    die "目标目录非空，拒绝覆盖: $target_directory"
  fi
done

SOURCE_IMAGE_ID=$(sudo docker inspect --format '{{.Image}}' "$SOURCE_CONTAINER")
[[ -n "$SOURCE_IMAGE_ID" ]] || die "无法读取源容器 image ID"

if [[ -z "${SOURCE_NEO4J_PASSWORD:-}" ]]; then
  read -rsp '源 Neo4j 密码: ' SOURCE_NEO4J_PASSWORD
  echo
fi

if ((APPLY)); then
  if [[ -z "${LADE_NEO4J_PASSWORD:-}" ]]; then
    read -rsp '新查询库密码（lade 与 lade-index 共用）: ' LADE_NEO4J_PASSWORD
    echo
  fi
  [[ ${#LADE_NEO4J_PASSWORD} -ge 8 ]] || die "新查询库密码长度必须至少为 8"
fi

info "验证源库连接和 run 稳定性"
SOURCE_RUN_COUNT=$(query_scalar "$SOURCE_CONTAINER" "$SOURCE_NEO4J_PASSWORD" \
  "MATCH (r:Run {id: '$RUN_ID'}) RETURN count(r) AS count;") || die \
  "无法查询源库；请检查密码和源容器"
[[ "$SOURCE_RUN_COUNT" == "1" ]] || die \
  "源库中的 Run 节点数量不是 1：run_id=$RUN_ID count=$SOURCE_RUN_COUNT"

FIRST_SIGNATURE=$(source_signature) || die "无法查询源库；请检查密码和源容器"
echo "$FIRST_SIGNATURE"

echo "等待 ${STABILITY_WAIT_SECONDS} 秒后复查源 run 是否仍在变化..."
sleep "$STABILITY_WAIT_SECONDS"
SECOND_SIGNATURE=$(source_signature) || die "第二次源 run 检查失败"
echo "$SECOND_SIGNATURE"
[[ "$FIRST_SIGNATURE" == "$SECOND_SIGNATURE" ]] || die \
  "源 run 在检查间隔内发生变化。请先停止仿真写入者，再重新运行脚本"

info "执行计划"
cat <<EOF
源容器:          $SOURCE_CONTAINER
源地址:          $SOURCE_URI/$SOURCE_NEO4J_DATABASE
源 image ID:     $SOURCE_IMAGE_ID
目标 run_id:     $RUN_ID

无索引容器:      $LADE_CONTAINER
无索引 HTTP:     http://127.0.0.1:$LADE_HTTP_PORT
无索引 Bolt:     $LADE_URI
无索引数据目录:  $LADE_DATA_DIR

索引容器:        $LADE_INDEX_CONTAINER
索引 HTTP:       http://127.0.0.1:$LADE_INDEX_HTTP_PORT
索引 Bolt:       $LADE_INDEX_URI
索引数据目录:    $LADE_INDEX_DATA_DIR

数据目录磁盘:
EOF
df -h "$DATA_ROOT" 2>/dev/null || df -h "$(dirname "$DATA_ROOT")"

if ((!APPLY)); then
  echo
  echo "预检通过：未创建容器、未创建目录、未复制数据。"
  echo "确认后执行: bash scripts/create_lade_ab.sh --apply"
  exit 0
fi

info "创建两个空数据目录"
NEO4J_UID=$(sudo docker run --rm --entrypoint sh "$SOURCE_IMAGE_ID" -c 'id -u neo4j')
NEO4J_GID=$(sudo docker run --rm --entrypoint sh "$SOURCE_IMAGE_ID" -c 'id -g neo4j')
[[ "$NEO4J_UID" =~ ^[0-9]+$ ]] || die "无法读取 Neo4j UID"
[[ "$NEO4J_GID" =~ ^[0-9]+$ ]] || die "无法读取 Neo4j GID"

sudo install -d -o "$NEO4J_UID" -g "$NEO4J_GID" "$LADE_DATA_DIR"
sudo install -d -o "$NEO4J_UID" -g "$NEO4J_GID" "$LADE_INDEX_DATA_DIR"

directory_is_nonempty "$LADE_DATA_DIR" && die "创建后 lade 目录不是空目录"
directory_is_nonempty "$LADE_INDEX_DATA_DIR" && die "创建后 lade-index 目录不是空目录"

info "启动空的 $LADE_CONTAINER 容器"
sudo docker run -d \
  --name "$LADE_CONTAINER" \
  --restart unless-stopped \
  -p "$LADE_HTTP_PORT:7474" \
  -p "$LADE_BOLT_PORT:7687" \
  --mount "type=bind,src=$LADE_DATA_DIR,dst=/data" \
  -e NEO4J_AUTH="neo4j/$LADE_NEO4J_PASSWORD" \
  "$SOURCE_IMAGE_ID" >/dev/null

wait_for_neo4j "$LADE_CONTAINER" "$LADE_NEO4J_PASSWORD"

COPY_COMMON_ARGS=(
  --run-id "$RUN_ID"
  --batch-size "$BATCH_SIZE"
  --fetch-size "$FETCH_SIZE"
  --progress-every "$PROGRESS_EVERY"
  --index-timeout-seconds "$INDEX_WAIT_SECONDS"
)

info "预览指定 run 的子图复制"
(
  cd "$PROJECT_ROOT"
  SOURCE_NEO4J_URI="$SOURCE_URI" \
  SOURCE_NEO4J_USER="$SOURCE_NEO4J_USER" \
  SOURCE_NEO4J_PASSWORD="$SOURCE_NEO4J_PASSWORD" \
  SOURCE_NEO4J_DATABASE="$SOURCE_NEO4J_DATABASE" \
  TARGET_NEO4J_URI="$LADE_URI" \
  TARGET_NEO4J_USER=neo4j \
  TARGET_NEO4J_PASSWORD="$LADE_NEO4J_PASSWORD" \
  TARGET_NEO4J_DATABASE=neo4j \
  ./run_in_lade.sh python scripts/copy_neo4j_runs.py \
    "${COPY_COMMON_ARGS[@]}"
)

info "正式复制指定 run 到 $LADE_CONTAINER"
(
  cd "$PROJECT_ROOT"
  SOURCE_NEO4J_URI="$SOURCE_URI" \
  SOURCE_NEO4J_USER="$SOURCE_NEO4J_USER" \
  SOURCE_NEO4J_PASSWORD="$SOURCE_NEO4J_PASSWORD" \
  SOURCE_NEO4J_DATABASE="$SOURCE_NEO4J_DATABASE" \
  TARGET_NEO4J_URI="$LADE_URI" \
  TARGET_NEO4J_USER=neo4j \
  TARGET_NEO4J_PASSWORD="$LADE_NEO4J_PASSWORD" \
  TARGET_NEO4J_DATABASE=neo4j \
  ./run_in_lade.sh python scripts/copy_neo4j_runs.py \
    "${COPY_COMMON_ARGS[@]}" \
    --apply
)

info "删除 $LADE_CONTAINER 中三组 AgentState 属性索引"
for index_name in "${AGENT_STATE_INDEXES[@]}"; do
  run_cypher "$LADE_CONTAINER" "$LADE_NEO4J_PASSWORD" \
    "DROP INDEX ${index_name} IF EXISTS;"
done

BASE_AGENT_INDEX_COUNT=$(query_scalar "$LADE_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  "SHOW INDEXES
   YIELD type, labelsOrTypes, properties
   WHERE type <> 'LOOKUP'
     AND labelsOrTypes = ['AgentState']
     AND properties IN [
       ['run_id', 'drone_id', 'step_index'],
       ['run_id', 'region_id', 'step_index'],
       ['run_id', 'step_index']
     ]
   RETURN count(*) AS count;")
[[ "$BASE_AGENT_INDEX_COUNT" == "0" ]] || die \
  "$LADE_CONTAINER 中仍有 $BASE_AGENT_INDEX_COUNT 个目标 AgentState 索引"

info "停止 $LADE_CONTAINER 并复制一致的数据目录"
sudo docker stop "$LADE_CONTAINER" >/dev/null
LADE_STOPPED_BY_SCRIPT=1

directory_is_nonempty "$LADE_INDEX_DATA_DIR" && die \
  "克隆前 lade-index 目录非空，拒绝覆盖"

sudo cp -a "$LADE_DATA_DIR/." "$LADE_INDEX_DATA_DIR/"
sudo chown -R "$NEO4J_UID:$NEO4J_GID" "$LADE_INDEX_DATA_DIR"

sudo docker start "$LADE_CONTAINER" >/dev/null
LADE_STOPPED_BY_SCRIPT=0
wait_for_neo4j "$LADE_CONTAINER" "$LADE_NEO4J_PASSWORD"

info "启动 $LADE_INDEX_CONTAINER"
sudo docker run -d \
  --name "$LADE_INDEX_CONTAINER" \
  --restart unless-stopped \
  -p "$LADE_INDEX_HTTP_PORT:7474" \
  -p "$LADE_INDEX_BOLT_PORT:7687" \
  --mount "type=bind,src=$LADE_INDEX_DATA_DIR,dst=/data" \
  "$SOURCE_IMAGE_ID" >/dev/null

wait_for_neo4j "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD"

info "只在 $LADE_INDEX_CONTAINER 创建三组 AgentState RANGE 索引"
run_cypher "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  'CREATE RANGE INDEX lade_agent_state_run_drone_step IF NOT EXISTS
   FOR (n:AgentState) ON (n.run_id, n.drone_id, n.step_index);'
run_cypher "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  'CREATE RANGE INDEX lade_agent_state_run_region_step IF NOT EXISTS
   FOR (n:AgentState) ON (n.run_id, n.region_id, n.step_index);'
run_cypher "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  'CREATE RANGE INDEX lade_agent_state_run_step IF NOT EXISTS
   FOR (n:AgentState) ON (n.run_id, n.step_index);'

run_cypher "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  "CALL db.awaitIndexes(${INDEX_WAIT_SECONDS});"

INDEXED_AGENT_INDEX_COUNT=$(query_scalar "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  "SHOW INDEXES
   YIELD state, type, labelsOrTypes, properties
   WHERE state = 'ONLINE'
     AND type = 'RANGE'
     AND labelsOrTypes = ['AgentState']
     AND properties IN [
       ['run_id', 'drone_id', 'step_index'],
       ['run_id', 'region_id', 'step_index'],
       ['run_id', 'step_index']
     ]
   RETURN count(*) AS count;")
[[ "$INDEXED_AGENT_INDEX_COUNT" == "3" ]] || die \
  "$LADE_INDEX_CONTAINER 中 ONLINE 的目标索引数不是 3，而是 $INDEXED_AGENT_INDEX_COUNT"

info "比较 lade 与 lade-index 的节点和关系分组"
(
  cd "$PROJECT_ROOT"
  SOURCE_NEO4J_URI="$LADE_URI" \
  SOURCE_NEO4J_USER=neo4j \
  SOURCE_NEO4J_PASSWORD="$LADE_NEO4J_PASSWORD" \
  SOURCE_NEO4J_DATABASE=neo4j \
  TARGET_NEO4J_URI="$LADE_INDEX_URI" \
  TARGET_NEO4J_USER=neo4j \
  TARGET_NEO4J_PASSWORD="$LADE_NEO4J_PASSWORD" \
  TARGET_NEO4J_DATABASE=neo4j \
  ./run_in_lade.sh python scripts/copy_neo4j_runs.py \
    --run-id "$RUN_ID" \
    --verify-only
)

info "最终索引状态"
echo "[$LADE_CONTAINER]"
run_cypher "$LADE_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  "SHOW INDEXES
   YIELD name, state, type, labelsOrTypes, properties
   RETURN name, state, type, labelsOrTypes, properties
   ORDER BY name;"

echo "[$LADE_INDEX_CONTAINER]"
run_cypher "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  "SHOW INDEXES
   YIELD name, state, type, labelsOrTypes, properties
   RETURN name, state, type, labelsOrTypes, properties
   ORDER BY name;"

cat <<EOF

完成
====
run_id:     $RUN_ID
无索引库:   $LADE_CONTAINER  $LADE_URI
索引库:     $LADE_INDEX_CONTAINER  $LADE_INDEX_URI
数据目录:   $LADE_DATA_DIR
索引目录:   $LADE_INDEX_DATA_DIR

请勿同时对两个容器进行高并发压测，以免争用同一宿主机资源。
EOF
