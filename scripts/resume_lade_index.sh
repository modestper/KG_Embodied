#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)

RUN_ID="${RUN_ID:-hangzhou-0621-headless-002}"

LADE_CONTAINER="${LADE_CONTAINER:-lade}"
LADE_URI="${LADE_URI:-bolt://127.0.0.1:57687}"

LADE_INDEX_CONTAINER="${LADE_INDEX_CONTAINER:-lade-index}"
LADE_INDEX_HTTP_PORT="${LADE_INDEX_HTTP_PORT:-61474}"
LADE_INDEX_BOLT_PORT="${LADE_INDEX_BOLT_PORT:-61687}"
LADE_INDEX_URI="${LADE_INDEX_URI:-bolt://127.0.0.1:${LADE_INDEX_BOLT_PORT}}"
LADE_INDEX_DATA_DIR="${LADE_INDEX_DATA_DIR:-/data/zc/datasets/LaDe/neo4j_ab/lade-index}"

INDEX_WAIT_SECONDS="${INDEX_WAIT_SECONDS:-3600}"
READY_ATTEMPTS="${READY_ATTEMPTS:-90}"

APPLY=0
CURRENT_STAGE="初始化"
TARGET_CREATED_BY_SCRIPT=0

usage() {
  cat <<'EOF'
用法:
  bash scripts/resume_lade_index.sh
  bash scripts/resume_lade_index.sh --apply

用途:
  从已经复制完成的 lade-index 数据目录继续执行：
  1. 创建并启动 lade-index 容器；
  2. 创建三组 AgentState RANGE 索引；
  3. 等待索引 ONLINE；
  4. 比较 lade 与 lade-index 的目标 run 数据。

默认只执行安全预检并打印计划；传入 --apply 才会创建容器和索引。
脚本不会重新复制数据，也不会删除或覆盖容器、目录。

常用可覆盖环境变量:
  RUN_ID, LADE_CONTAINER, LADE_URI
  LADE_INDEX_CONTAINER, LADE_INDEX_HTTP_PORT, LADE_INDEX_BOLT_PORT
  LADE_INDEX_URI, LADE_INDEX_DATA_DIR
  INDEX_WAIT_SECONDS, READY_ATTEMPTS, LADE_NEO4J_PASSWORD
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

stage() {
  CURRENT_STAGE="$1"
  info "$CURRENT_STAGE"
}

report_exit() {
  local exit_code=$?
  if ((exit_code != 0)); then
    echo "续跑未完成，停止阶段: $CURRENT_STAGE" >&2
    if ((TARGET_CREATED_BY_SCRIPT)); then
      echo "已创建的 $LADE_INDEX_CONTAINER 容器会保留，便于查看 docker logs。" >&2
    fi
    echo "脚本不会自动删除或覆盖任何容器和数据。" >&2
  fi
  return "$exit_code"
}

trap report_exit EXIT

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1"
}

container_exists() {
  sudo docker inspect "$1" >/dev/null 2>&1
}

container_running() {
  [[ "$(sudo docker inspect --format '{{.State.Running}}' "$1")" == "true" ]]
}

directory_is_nonempty() {
  local directory="$1"
  [[ -d "$directory" ]] &&
    [[ -n "$(sudo find "$directory" -mindepth 1 -maxdepth 1 -print -quit)" ]]
}

port_is_listening() {
  local port="$1"
  sudo ss -ltnH | awk '{print $4}' | grep -Eq ":${port}$"
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

[[ -x "$PROJECT_ROOT/run_in_lade.sh" ]] ||
  die "找不到可执行文件: $PROJECT_ROOT/run_in_lade.sh"
[[ -f "$PROJECT_ROOT/scripts/copy_neo4j_runs.py" ]] ||
  die "找不到校验工具: $PROJECT_ROOT/scripts/copy_neo4j_runs.py"
[[ "$RUN_ID" =~ ^[A-Za-z0-9._:-]+$ ]] ||
  die "RUN_ID 包含不安全字符: $RUN_ID"

validate_positive_integer INDEX_WAIT_SECONDS "$INDEX_WAIT_SECONDS"
validate_positive_integer READY_ATTEMPTS "$READY_ATTEMPTS"
validate_port LADE_INDEX_HTTP_PORT "$LADE_INDEX_HTTP_PORT"
validate_port LADE_INDEX_BOLT_PORT "$LADE_INDEX_BOLT_PORT"
[[ "$LADE_INDEX_HTTP_PORT" != "$LADE_INDEX_BOLT_PORT" ]] ||
  die "HTTP 和 Bolt 不能使用同一个主机端口: $LADE_INDEX_HTTP_PORT"

stage "验证 sudo 和现有资源"
sudo -v

container_exists "$LADE_CONTAINER" || die "找不到基准容器: $LADE_CONTAINER"
container_running "$LADE_CONTAINER" || die "基准容器未运行: $LADE_CONTAINER"

if container_exists "$LADE_INDEX_CONTAINER"; then
  die "目标容器已存在，拒绝覆盖: $LADE_INDEX_CONTAINER"
fi

directory_is_nonempty "$LADE_INDEX_DATA_DIR" ||
  die "续跑数据目录不存在或为空: $LADE_INDEX_DATA_DIR"

for target_port in "$LADE_INDEX_HTTP_PORT" "$LADE_INDEX_BOLT_PORT"; do
  if port_is_listening "$target_port"; then
    die "目标端口已被占用: $target_port"
  fi
done

SOURCE_IMAGE_ID=$(sudo docker inspect --format '{{.Image}}' "$LADE_CONTAINER")
[[ -n "$SOURCE_IMAGE_ID" ]] || die "无法读取 $LADE_CONTAINER 的 image ID"

if [[ -z "${LADE_NEO4J_PASSWORD:-}" ]]; then
  read -rsp 'lade 与 lade-index 共用的 Neo4j 密码: ' LADE_NEO4J_PASSWORD
  echo
fi

stage "验证基准库连接、run 和索引状态"
wait_for_neo4j "$LADE_CONTAINER" "$LADE_NEO4J_PASSWORD"

RUN_COUNT=$(query_scalar "$LADE_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  "MATCH (r:Run {id: '$RUN_ID'}) RETURN count(r) AS count;")
[[ "$RUN_COUNT" == "1" ]] ||
  die "$LADE_CONTAINER 中的 Run 节点数量不是 1：run_id=$RUN_ID count=$RUN_COUNT"

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
[[ "$BASE_AGENT_INDEX_COUNT" == "0" ]] ||
  die "$LADE_CONTAINER 中存在 $BASE_AGENT_INDEX_COUNT 个实验目标索引，不再是无索引基准库"

stage "续跑计划"
cat <<EOF
基准容器:        $LADE_CONTAINER
基准 Bolt:       $LADE_URI
目标 run_id:     $RUN_ID
Neo4j image ID:  $SOURCE_IMAGE_ID

目标容器:        $LADE_INDEX_CONTAINER
目标 HTTP:       http://127.0.0.1:$LADE_INDEX_HTTP_PORT
目标 Bolt:       $LADE_INDEX_URI
现有数据目录:    $LADE_INDEX_DATA_DIR

数据目录磁盘:
EOF
df -h "$LADE_INDEX_DATA_DIR"

if ((!APPLY)); then
  echo
  echo "续跑预检通过：未创建容器、未修改数据库。"
  echo "确认后执行: bash scripts/resume_lade_index.sh --apply"
  exit 0
fi

stage "从现有数据目录创建 $LADE_INDEX_CONTAINER"
sudo docker run -d \
  --name "$LADE_INDEX_CONTAINER" \
  --restart unless-stopped \
  -p "$LADE_INDEX_HTTP_PORT:7474" \
  -p "$LADE_INDEX_BOLT_PORT:7687" \
  --mount "type=bind,src=$LADE_INDEX_DATA_DIR,dst=/data" \
  "$SOURCE_IMAGE_ID" >/dev/null
TARGET_CREATED_BY_SCRIPT=1

stage "等待 $LADE_INDEX_CONTAINER 接受 Bolt 连接"
wait_for_neo4j "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD"

stage "创建三组 AgentState RANGE 索引"
run_cypher "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  'CREATE RANGE INDEX lade_agent_state_run_drone_step IF NOT EXISTS
   FOR (n:AgentState) ON (n.run_id, n.drone_id, n.step_index);'
run_cypher "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  'CREATE RANGE INDEX lade_agent_state_run_region_step IF NOT EXISTS
   FOR (n:AgentState) ON (n.run_id, n.region_id, n.step_index);'
run_cypher "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  'CREATE RANGE INDEX lade_agent_state_run_step IF NOT EXISTS
   FOR (n:AgentState) ON (n.run_id, n.step_index);'

stage "等待全部索引 ONLINE"
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
[[ "$INDEXED_AGENT_INDEX_COUNT" == "3" ]] ||
  die "$LADE_INDEX_CONTAINER 中 ONLINE 的目标索引数不是 3，而是 $INDEXED_AGENT_INDEX_COUNT"

stage "比较 lade 与 lade-index 的目标 run 数据"
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

stage "最终索引状态"
run_cypher "$LADE_INDEX_CONTAINER" "$LADE_NEO4J_PASSWORD" \
  "SHOW INDEXES
   YIELD name, state, type, labelsOrTypes, properties
   RETURN name, state, type, labelsOrTypes, properties
   ORDER BY name;"

cat <<EOF

续跑完成
========
run_id:   $RUN_ID
无索引库: $LADE_CONTAINER  $LADE_URI
索引库:   $LADE_INDEX_CONTAINER  $LADE_INDEX_URI
HTTP:     http://127.0.0.1:$LADE_INDEX_HTTP_PORT
数据目录: $LADE_INDEX_DATA_DIR
EOF
