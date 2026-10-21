#!/usr/bin/env bash

set -euo pipefail

SOURCE_CONTAINER="${SOURCE_CONTAINER:-lade-query-neo4j}"
SOURCE_VOLUME="${SOURCE_VOLUME:-lade-query-neo4j-data}"
TARGET_CONTAINER="${TARGET_CONTAINER:-lade-query-neo4j-index}"
TARGET_VOLUME="${TARGET_VOLUME:-lade-query-neo4j-data-index}"
TARGET_HTTP_PORT="${TARGET_HTTP_PORT:-47474}"
TARGET_BOLT_PORT="${TARGET_BOLT_PORT:-47687}"
TARGET_NEO4J_USER="${TARGET_NEO4J_USER:-neo4j}"
TARGET_NEO4J_DATABASE="${TARGET_NEO4J_DATABASE:-neo4j}"
INDEX_WAIT_SECONDS="${INDEX_WAIT_SECONDS:-900}"

APPLY=0
CREATE_AGENT_STATE_INDEXES=0
SOURCE_WAS_RUNNING=0

usage() {
  cat <<'EOF'
用法:
  bash scripts/clone_neo4j_query_volume.sh [选项]

默认仅打印计划，不修改 Docker 状态。

选项:
  --apply                       执行 volume 克隆并启动目标容器
  --create-agent-state-indexes  在目标库创建三组 AgentState 索引
  -h, --help                    显示帮助

可用环境变量:
  SOURCE_CONTAINER       默认 lade-query-neo4j
  SOURCE_VOLUME          默认 lade-query-neo4j-data
  TARGET_CONTAINER       默认 lade-query-neo4j-index
  TARGET_VOLUME          默认 lade-query-neo4j-data-index
  TARGET_HTTP_PORT       默认 47474
  TARGET_BOLT_PORT       默认 47687
  TARGET_NEO4J_USER      默认 neo4j
  TARGET_NEO4J_DATABASE  默认 neo4j
  TARGET_NEO4J_PASSWORD  创建索引时必需；克隆后密码与源查询库相同
  INDEX_WAIT_SECONDS     默认 900
EOF
}

while (($#)); do
  case "$1" in
    --apply)
      APPLY=1
      ;;
    --create-agent-state-indexes)
      CREATE_AGENT_STATE_INDEXES=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "错误: 未知参数 $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

DOCKER=(sudo docker)

container_exists() {
  "${DOCKER[@]}" inspect "$1" >/dev/null 2>&1
}

volume_exists() {
  "${DOCKER[@]}" volume inspect "$1" >/dev/null 2>&1
}

port_is_published() {
  local port="$1"
  "${DOCKER[@]}" ps --format '{{.Ports}}' | grep -Eq ":${port}->"
}

restore_source_if_needed() {
  if ((SOURCE_WAS_RUNNING)); then
    echo "恢复源查询容器: $SOURCE_CONTAINER"
    "${DOCKER[@]}" start "$SOURCE_CONTAINER" >/dev/null || true
  fi
}

if ! container_exists "$SOURCE_CONTAINER"; then
  echo "错误: 找不到源容器 $SOURCE_CONTAINER" >&2
  exit 1
fi

if ! volume_exists "$SOURCE_VOLUME"; then
  echo "错误: 找不到源 volume $SOURCE_VOLUME" >&2
  exit 1
fi

MOUNTED_DATA_VOLUME=$(
  "${DOCKER[@]}" inspect \
    --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' \
    "$SOURCE_CONTAINER"
)

if [[ "$MOUNTED_DATA_VOLUME" != "$SOURCE_VOLUME" ]]; then
  echo "错误: 源容器 /data 挂载的 volume 不是预期值" >&2
  echo "预期: $SOURCE_VOLUME" >&2
  echo "实际: ${MOUNTED_DATA_VOLUME:-<bind-mount-or-none>}" >&2
  exit 1
fi

SOURCE_IMAGE_ID=$(
  "${DOCKER[@]}" inspect --format '{{.Image}}' "$SOURCE_CONTAINER"
)
SOURCE_RUNNING=$(
  "${DOCKER[@]}" inspect --format '{{.State.Running}}' "$SOURCE_CONTAINER"
)

echo "Neo4j 查询库克隆计划"
echo "===================="
echo "源容器:       $SOURCE_CONTAINER"
echo "源 volume:     $SOURCE_VOLUME"
echo "源运行状态:   $SOURCE_RUNNING"
echo "源 image ID:   $SOURCE_IMAGE_ID"
echo "目标容器:     $TARGET_CONTAINER"
echo "目标 volume:   $TARGET_VOLUME"
echo "目标 HTTP:     http://127.0.0.1:$TARGET_HTTP_PORT"
echo "目标 Bolt:     bolt://127.0.0.1:$TARGET_BOLT_PORT"
echo "创建索引:     $CREATE_AGENT_STATE_INDEXES"

if container_exists "$TARGET_CONTAINER"; then
  echo "错误: 目标容器已存在，拒绝覆盖: $TARGET_CONTAINER" >&2
  exit 1
fi

if volume_exists "$TARGET_VOLUME"; then
  echo "错误: 目标 volume 已存在，拒绝覆盖: $TARGET_VOLUME" >&2
  exit 1
fi

if port_is_published "$TARGET_HTTP_PORT"; then
  echo "错误: HTTP 端口已被 Docker 容器占用: $TARGET_HTTP_PORT" >&2
  exit 1
fi

if port_is_published "$TARGET_BOLT_PORT"; then
  echo "错误: Bolt 端口已被 Docker 容器占用: $TARGET_BOLT_PORT" >&2
  exit 1
fi

if ((CREATE_AGENT_STATE_INDEXES)) && [[ -z "${TARGET_NEO4J_PASSWORD:-}" ]]; then
  echo "错误: 创建索引需要环境变量 TARGET_NEO4J_PASSWORD" >&2
  exit 1
fi

if ((!APPLY)); then
  echo
  echo "预览完成：未停止容器、未创建 volume、未复制数据。"
  echo "确认计划后追加 --apply。"
  exit 0
fi

echo
echo "创建空目标 volume: $TARGET_VOLUME"
"${DOCKER[@]}" volume create "$TARGET_VOLUME" >/dev/null

if [[ "$SOURCE_RUNNING" == "true" ]]; then
  SOURCE_WAS_RUNNING=1
  trap restore_source_if_needed EXIT INT TERM
  echo "停止源查询容器以获得一致副本: $SOURCE_CONTAINER"
  "${DOCKER[@]}" stop "$SOURCE_CONTAINER" >/dev/null
fi

echo "复制 Docker volume 数据..."
"${DOCKER[@]}" run --rm \
  --user 0:0 \
  --entrypoint /bin/sh \
  --mount "type=volume,src=$SOURCE_VOLUME,dst=/source,readonly" \
  --mount "type=volume,src=$TARGET_VOLUME,dst=/target" \
  "$SOURCE_IMAGE_ID" \
  -c 'set -eu
      if find /target -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
        echo "target volume is not empty" >&2
        exit 1
      fi
      cp -a /source/. /target/'

if ((SOURCE_WAS_RUNNING)); then
  echo "重新启动源查询容器: $SOURCE_CONTAINER"
  "${DOCKER[@]}" start "$SOURCE_CONTAINER" >/dev/null
  SOURCE_WAS_RUNNING=0
  trap - EXIT INT TERM
fi

echo "启动目标索引版容器: $TARGET_CONTAINER"
"${DOCKER[@]}" run -d \
  --name "$TARGET_CONTAINER" \
  --restart unless-stopped \
  -p "$TARGET_HTTP_PORT:7474" \
  -p "$TARGET_BOLT_PORT:7687" \
  -v "$TARGET_VOLUME:/data" \
  "$SOURCE_IMAGE_ID" >/dev/null

echo "目标容器已启动。克隆数据库沿用源查询库的用户名和密码。"

if ((CREATE_AGENT_STATE_INDEXES)); then
  echo "等待目标 Neo4j 接受连接..."
  READY=0
  for _attempt in $(seq 1 90); do
    if "${DOCKER[@]}" exec "$TARGET_CONTAINER" cypher-shell \
      -u "$TARGET_NEO4J_USER" \
      -p "$TARGET_NEO4J_PASSWORD" \
      -d "$TARGET_NEO4J_DATABASE" \
      'RETURN 1 AS ok;' >/dev/null 2>&1; then
      READY=1
      break
    fi
    sleep 2
  done
  if ((!READY)); then
    echo "错误: 目标 Neo4j 在 180 秒内未就绪" >&2
    exit 1
  fi

  echo "创建三组 AgentState 索引..."
  "${DOCKER[@]}" exec "$TARGET_CONTAINER" cypher-shell \
    -u "$TARGET_NEO4J_USER" \
    -p "$TARGET_NEO4J_PASSWORD" \
    -d "$TARGET_NEO4J_DATABASE" \
    'CREATE INDEX lade_agent_state_run_drone_step IF NOT EXISTS
     FOR (n:AgentState) ON (n.run_id, n.drone_id, n.step_index);'

  "${DOCKER[@]}" exec "$TARGET_CONTAINER" cypher-shell \
    -u "$TARGET_NEO4J_USER" \
    -p "$TARGET_NEO4J_PASSWORD" \
    -d "$TARGET_NEO4J_DATABASE" \
    'CREATE INDEX lade_agent_state_run_region_step IF NOT EXISTS
     FOR (n:AgentState) ON (n.run_id, n.region_id, n.step_index);'

  "${DOCKER[@]}" exec "$TARGET_CONTAINER" cypher-shell \
    -u "$TARGET_NEO4J_USER" \
    -p "$TARGET_NEO4J_PASSWORD" \
    -d "$TARGET_NEO4J_DATABASE" \
    'CREATE INDEX lade_agent_state_run_step IF NOT EXISTS
     FOR (n:AgentState) ON (n.run_id, n.step_index);'

  echo "等待所有索引 ONLINE，超时 ${INDEX_WAIT_SECONDS}s..."
  "${DOCKER[@]}" exec "$TARGET_CONTAINER" cypher-shell \
    -u "$TARGET_NEO4J_USER" \
    -p "$TARGET_NEO4J_PASSWORD" \
    -d "$TARGET_NEO4J_DATABASE" \
    "CALL db.awaitIndexes(${INDEX_WAIT_SECONDS});"

  echo "目标索引状态:"
  "${DOCKER[@]}" exec "$TARGET_CONTAINER" cypher-shell \
    -u "$TARGET_NEO4J_USER" \
    -p "$TARGET_NEO4J_PASSWORD" \
    -d "$TARGET_NEO4J_DATABASE" \
    "SHOW INDEXES
     YIELD name, state, type, labelsOrTypes, properties
     WHERE name STARTS WITH 'lade_agent_state_'
     RETURN name, state, type, labelsOrTypes, properties
     ORDER BY name;"
fi

echo
echo "克隆完成"
echo "源查询库 Bolt: bolt://127.0.0.1:37687"
echo "索引版 Bolt:   bolt://127.0.0.1:$TARGET_BOLT_PORT"
echo "请勿同时压测两个容器，否则会竞争同一宿主机资源。"
