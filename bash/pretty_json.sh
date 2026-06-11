
cd /data/zc/datasets/LaDe/viewer/scripts


input="/data/zc/datasets/LaDe/viewer/data/llm_logs/llm_Jilin_0925_20tasks_3agents_1000steps_1stride_512tok_qwen2.5-14b_20260603_165432.jsonl"

output="/data/zc/datasets/LaDe/viewer/data/llm_logs/01.jsonl"
# 用法示例：

#   cd /data/zc/datasets/LaDe/viewer

#   # 格式化 .json 到终端
#   python3 scripts/pretty_json.py data/traces.json

#   # 覆盖原 .json 文件
#   python3 scripts/pretty_json.py data/traces.json --in-place

#   # 格式化 .jsonl 到终端
#   python3 scripts/pretty_json.py data/llm_logs/xxx.jsonl

#   # 把 jsonl 展开成更适合阅读的记录块    没有输出
#   python3 scripts/pretty_json.py data/llm_logs/xxx.jsonl --jsonl-readable

#   # 输出到另一个文件
#   python3 scripts/pretty_json.py data/llm_logs/xxx.jsonl --jsonl-readable --output /tmp/
#   llm_readable.txt



#变成更适合阅读的。
python3 pretty_json.py ${input} --jsonl-readable --output ${output}