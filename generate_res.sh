cd /data/zc/datasets/LaDe/viewer
# 重庆 delivery_cq.csv: 1026 当天 14172
# 杭州 delivery_hz.csv: 1026 当天 17800
# 上海 delivery_sh.csv: 1027 当天 17381
# 吉林 delivery_jl.csv: 0529 当天 434
# 烟台 delivery_yt.csv: 0618 当天 2252

# 之前的大规模规则调度参数，按需取消注释：
# USE_LLM=0
# CITY=Chongqing
# DAY=1026
# DRONE_COUNT=100
# TASK_LIMIT=5000
# STEP_SECONDS=5
# MAX_STEPS=17280
# FRAME_STRIDE=12



#是否开启 LLM 推理
USE_LLM=${USE_LLM:-1}

CITY=${CITY:-Jilin}
DAY=${DAY:-0925}
DRONE_COUNT=${DRONE_COUNT:-5}
TASK_LIMIT=${TASK_LIMIT:-50}
STEP_SECONDS=${STEP_SECONDS:-5}
MAX_STEPS=${MAX_STEPS:-5000}
FRAME_STRIDE=${FRAME_STRIDE:-1}

if [ "$USE_LLM" = "1" ]; then
  export DRONE_COORDINATOR=${DRONE_COORDINATOR:-llm-region}
  export VLLM_API_BASE=${VLLM_API_BASE:-http://127.0.0.1:6666/v1}
  #这里要改模型的调用
  #export VLLM_MODEL=${VLLM_MODEL:-qwen2.5-14b}
  export VLLM_MODEL=${VLLM_MODEL:-qwen2.5-7b}
  export LLM_MAX_TASKS_PER_REGION=${LLM_MAX_TASKS_PER_REGION:-20}
  export LLM_MAX_DRONES_PER_REGION=${LLM_MAX_DRONES_PER_REGION:-10}
fi

python3 scripts/build_delivery_sim.py \
  --city "$CITY" \
  --day "$DAY" \
  --drone-count "$DRONE_COUNT" \
  --task-limit "$TASK_LIMIT" \
  --step_seconds "$STEP_SECONDS" \
  --max-steps "$MAX_STEPS" \
  --frame_stride "$FRAME_STRIDE"

python3 scripts/build_trace_index.py
