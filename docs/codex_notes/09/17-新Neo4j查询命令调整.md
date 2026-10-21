### 任务背景

用户准备在复制后的独立 Neo4j 查询库中，继续使用 `scripts/lade_llm_context_queries.py` 对 `million-nodes-hangzhou-0621-100agents` 的 100 架 drone 执行全部即时查询，希望确认原命令是否需要修改。

### 关键结论

- `i0-i11` 的 Cypher 查询模板不需要因为迁移到新库而修改，因为复制工具保留了节点标签、属性、关系类型、方向和关系属性。
- 必须把 `NEO4J_URI` 从源库的 `17687` 改为目标查询库的 `37687`。
- 必须使用创建目标容器时设置的目标密码，不一定仍是源库密码 `neo4j123456`。
- 建议显式设置 `NEO4J_DATABASE=neo4j`，减少数据库选择歧义。
- 必须更换三个输出文件名；查询日志和 context JSONL 使用追加模式，继续使用旧文件会把源库与目标库实验混在一起。
- `--queries all` 包含 `i2`；当前 `i2` 使用 `BETWEEN`，此前在现有 Neo4j 环境中出现过不支持该语法的失败。复制数据库不会修复该查询语法。
- `--repeat 20`、100 个 drone、12 个模板理论上会产生 `100 × 20 × 12 = 24000` 次查询尝试。

### 推荐命令

先通过交互方式读取目标密码：

```bash
read -rsp '目标查询库密码: ' TARGET_NEO4J_PASSWORD
echo
export TARGET_NEO4J_PASSWORD
```

生成 100 个 drone ID：

```bash
DRONE_IDS=$(printf "drone-%s," $(seq 1 100))
DRONE_IDS=${DRONE_IDS%,}
```

第一行生成的文本末尾会多一个逗号；第二行 `${DRONE_IDS%,}` 删除末尾的一个逗号。最终格式为：

```text
drone-1,drone-2,...,drone-100
```

在目标查询库执行：

```bash
NEO4J_URI=bolt://127.0.0.1:37687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD="$TARGET_NEO4J_PASSWORD" \
NEO4J_DATABASE=neo4j \
./run_in_lade.sh python scripts/lade_llm_context_queries.py \
  --run-id million-nodes-hangzhou-0621-100agents \
  --drone-ids "$DRONE_IDS" \
  --step-index 2300 \
  --queries all \
  --repeat 20 \
  --max-workers 100 \
  --max-connection-pool-size 100 \
  --query-log-jsonl data/query_store_100drones_query_logs.jsonl \
  --context-jsonl data/query_store_100drones_contexts.jsonl \
  --summary-json data/query_store_100drones_summary.json
```

结束后清除密码变量：

```bash
unset TARGET_NEO4J_PASSWORD
```

### 各项变化

原源库地址：

```text
bolt://127.0.0.1:17687
```

新目标库地址：

```text
bolt://127.0.0.1:37687
```

原日志名包含的是此前源库结果：

```text
data/lade_llm_context_100drones_query_logs.jsonl
data/lade_llm_context_100drones_contexts.jsonl
data/lade_llm_context_100drones_summary.json
```

新命令使用独立文件：

```text
data/query_store_100drones_query_logs.jsonl
data/query_store_100drones_contexts.jsonl
data/query_store_100drones_summary.json
```

其中两个 JSONL 文件会追加写入；summary JSON 会覆盖写入。如果重复进行多轮独立实验，最好每轮继续使用不同文件名或加入时间戳。

### 正式压测前的检查

先确认目标容器运行：

```bash
sudo docker ps --filter name=lade-query-neo4j
```

确认复制校验通过：

```bash
./run_in_lade.sh python scripts/copy_neo4j_runs.py \
  --run-id million-nodes-hangzhou-0621-100agents \
  --run-id million-hangzhou-0831-agents03 \
  --verify-only
```

确认目标索引均为 `ONLINE`：

```bash
sudo docker exec -it lade-query-neo4j cypher-shell \
  -u neo4j \
  -p "$TARGET_NEO4J_PASSWORD" \
  -d neo4j \
  'SHOW INDEXES YIELD name,state RETURN name,state ORDER BY name;'
```

确认 `drone-1` 到 `drone-100` 和 step 2300 确实存在。可先进行小规模 dry-run 参数检查：

```bash
NEO4J_URI=bolt://127.0.0.1:37687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD="$TARGET_NEO4J_PASSWORD" \
NEO4J_DATABASE=neo4j \
./run_in_lade.sh python scripts/lade_llm_context_queries.py \
  --run-id million-nodes-hangzhou-0621-100agents \
  --drone-id drone-1 \
  --step-index 2300 \
  --time-minute 634 \
  --queries all \
  --dry-run
```

`--dry-run` 不连接 Neo4j，只确认模板选择和参数生成。由于 dry-run 无法自动从 Step 节点取得时间，所以示例显式传入了 `--time-minute`；正式运行不必传，脚本会从目标库读取 step 2300 对应时间。

### 关于 i2

当前 `i2` 查询包含：

```cypher
WHERE a.step_index BETWEEN $start_step AND $step_index
```

项目此前的真实日志中，该模板在当前 Neo4j 环境出现过不支持 `BETWEEN` 的语法错误。数据库复制后 Neo4j 版本保持相同，因此该错误不会自动消失。

如果暂时不修改代码，可以显式列出除 `i2` 外的查询：

```text
--queries i0,i1,i3-i11
```

如果要求 `all` 的 12 个模板全部成功，应把 `i2` 条件调整为：

```cypher
WHERE a.step_index >= $start_step AND a.step_index <= $step_index
```

本次没有修改该查询，因为用户当前只询问新库连接命令。

### 性能结果解释

`--max-workers 100` 表示最多同时执行 100 个 context；同一 context 内 `i0-i11` 仍然顺序执行。

`--max-connection-pool-size 100` 表示 Python Neo4j driver 的连接池上限为 100，并不保证数据库服务端一定能无等待地处理 100 个请求。

第一次运行时 page cache、查询计划和连接池可能较冷。若要做正式对比，应统一源库和目标库的预热方法、后台负载、线程数、连接池大小和日志配置。

### 执行过的操作

- 阅读用户给出的完整查询命令。
- 对照 `scripts/lade_llm_context_queries.py` 的默认 URI、数据库参数、日志写入方式、查询包和并发参数。
- 核对复制后目标容器的端口规划和密码来源。
- 未运行查询或修改 Neo4j 数据。

### 修改内容

- 未修改查询脚本或迁移脚本。
- 新增说明：`docs/codex_notes/2026-09-17-新Neo4j查询命令调整.md`。

### 后续建议

- 先使用新地址运行一条 `i0` 或少量 drone，确认连接和数据正确，再执行 24000 次混合查询。
- 正式测试使用新的日志文件，避免与源库旧实验混合。
- 如果要求 `--queries all` 零失败，先修正并单独验证 `i2` 的范围条件。
