#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8010}
CITY=${CITY:-Hangzhou}
DAY=${DAY:-0621}
KG_RUN_ID=${KG_RUN_ID:-million-nodes-hangzhou-0621-100agents}
TASK_LIMIT=${TASK_LIMIT:-10424}
DRONE_COUNT=${DRONE_COUNT:-100}
STEP_SECONDS=${STEP_SECONDS:-6}
MAX_STEPS=${MAX_STEPS:-10000}
FRAME_STRIDE=${FRAME_STRIDE:-1}
WALL_INTERVAL=${WALL_INTERVAL:-0.25}
RAY_ADDRESS=${RAY_ADDRESS:-}
RAY_NUM_CPUS=${RAY_NUM_CPUS:-}
USE_LLM=${USE_LLM:-0}
KG_WRITE_MODE=${KG_WRITE_MODE:-main}
VLLM_API_BASE=${VLLM_API_BASE:-http://127.0.0.1:6666/v1}
VLLM_MODEL=${VLLM_MODEL:-qwen2.5-7b}
LLM_MAX_TOKENS=${LLM_MAX_TOKENS:-512}

args=(
  --host "$HOST"
  --port "$PORT"
  --backend ray
  --kg-write-mode "$KG_WRITE_MODE"
  --city "$CITY"
  --day "$DAY"
  --task-limit "$TASK_LIMIT"
  --drone-count "$DRONE_COUNT"
  --step-seconds "$STEP_SECONDS"
  --max-steps "$MAX_STEPS"
  --frame-stride "$FRAME_STRIDE"
  --wall-interval "$WALL_INTERVAL"
  --vllm-api-base "$VLLM_API_BASE"
  --vllm-model "$VLLM_MODEL"
  --llm-max-tokens "$LLM_MAX_TOKENS"
)

if [ -n "$RAY_ADDRESS" ]; then
  args+=(--ray-address "$RAY_ADDRESS")
fi

if [ -n "$RAY_NUM_CPUS" ]; then
  args+=(--ray-num-cpus "$RAY_NUM_CPUS")
fi

if [ "$USE_LLM" = "1" ]; then
  args+=(--use-llm)
fi

python scripts/run_dynamic_sim.py "${args[@]}"
