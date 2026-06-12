#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8010}
CITY=${CITY:-Jilin}
DAY=${DAY:-0925}
TASK_LIMIT=${TASK_LIMIT:-20}
DRONE_COUNT=${DRONE_COUNT:-3}
STEP_SECONDS=${STEP_SECONDS:-20}
MAX_STEPS=${MAX_STEPS:-1000}
FRAME_STRIDE=${FRAME_STRIDE:-1}
WALL_INTERVAL=${WALL_INTERVAL:-0.25}
USE_LLM=${USE_LLM:-0}
VLLM_API_BASE=${VLLM_API_BASE:-http://127.0.0.1:6666/v1}
VLLM_MODEL=${VLLM_MODEL:-qwen2.5-7b}
LLM_MAX_TOKENS=${LLM_MAX_TOKENS:-512}

args=(
  --host "$HOST"
  --port "$PORT"
  --backend local
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

if [ "$USE_LLM" = "1" ]; then
  args+=(--use-llm)
fi

python3 scripts/run_dynamic_sim.py "${args[@]}"
