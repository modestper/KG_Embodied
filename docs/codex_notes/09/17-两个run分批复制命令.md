### 任务背景

用户要使用 Neo4j Python 驱动，从当前 `bolt://127.0.0.1:17687` 源库分批复制以下两个 run 到独立查询库：

- `million-nodes-hangzhou-0621-100agents`
- `million-hangzhou-0831-agents03`

目标库计划使用独立容器、独立 volume 和 `37474/37687` 端口。本次提供可直接运行的工具和完整命令，但不替用户启动容器或写数据库。

### 关键结论

- 已新增 `scripts/copy_neo4j_runs.py`，默认仅预览；显式添加 `--apply` 才会写入目标库。
- 工具一次复制两个 run 的全部目标节点，以及起点和终点都属于这两个 run 的全部关系，并保留节点标签、节点属性、关系类型、方向和关系属性。
- 使用临时唯一键把源节点映射到目标节点；关系复制完成后会删除临时标签和属性。
- 默认要求目标 `neo4j` database 节点数为 0，避免覆盖或混入已有数据。
- 如果发现目标子图与其他 run/共享节点存在边界关系，默认中止；只有人工核对后才能显式允许跳过这些边界关系。
- 工具自动复制 Neo4j 5 中常用的节点唯一性约束，以及非约束所有的 RANGE/TEXT/POINT 索引；LOOKUP 索引由目标库默认提供。遇到其他 schema 类型会打印警告，必须人工核对。
- 完成后会比较源和目标按标签组合、关系类型统计的数量，并检查目标库总节点数。

### 第零步：进入项目目录

```bash
cd /data/zc/datasets/LaDe/viewer
```

建议在 `tmux` 中执行正式迁移，避免 SSH/终端断开：

```bash
tmux new -s lade-neo4j-copy
```

如果没有使用 `tmux`，可跳过这一条。

### 第一步：确认容器名和端口未被占用

```bash
sudo docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
```

确认不存在名为 `lade-query-neo4j` 的容器，并确认宿主机端口 `37474`、`37687` 未被占用：

```bash
sudo ss -ltnp | grep -E ':(37474|37687)\b' || true
```

如果已经存在同名容器或端口有输出，不要继续执行创建命令，应先确认它们的用途。

### 第二步：取得源容器的精确镜像

查看源库版本：

```bash
sudo docker exec neo4j neo4j --version
```

取得源容器当前使用的不可变 image ID：

```bash
LADE_SOURCE_NEO4J_IMAGE_ID=$(sudo docker inspect --format '{{.Image}}' neo4j)
```

检查结果：

```bash
sudo docker image inspect "$LADE_SOURCE_NEO4J_IMAGE_ID" \
  --format 'image={{.Id}} tags={{json .RepoTags}}'
```

后续使用 image ID 启动目标容器，可确保源、目标使用同一套 Neo4j 二进制，而不是重新解析可能变化的 `neo4j:latest`。

### 第三步：交互读取密码

不要把真实密码直接写进命令历史：

```bash
read -rsp '源 Neo4j 密码: ' SOURCE_NEO4J_PASSWORD
echo
export SOURCE_NEO4J_PASSWORD
```

为新查询库设置一个不少于 8 个字符的新密码：

```bash
read -rsp '目标 Neo4j 新密码: ' TARGET_NEO4J_PASSWORD
echo
export TARGET_NEO4J_PASSWORD
```

工具还支持这些环境变量，但下面的默认地址已经符合当前方案：

```text
SOURCE_NEO4J_URI=bolt://127.0.0.1:17687
SOURCE_NEO4J_USER=neo4j
SOURCE_NEO4J_DATABASE=neo4j
TARGET_NEO4J_URI=bolt://127.0.0.1:37687
TARGET_NEO4J_USER=neo4j
TARGET_NEO4J_DATABASE=neo4j
```

### 第四步：创建独立目标容器

创建独立数据卷：

```bash
sudo docker volume create lade-query-neo4j-data
```

启动目标容器：

```bash
sudo docker run -d \
  --name lade-query-neo4j \
  --restart unless-stopped \
  -p 37474:7474 \
  -p 37687:7687 \
  -v lade-query-neo4j-data:/data \
  -e NEO4J_AUTH="neo4j/${TARGET_NEO4J_PASSWORD}" \
  "$LADE_SOURCE_NEO4J_IMAGE_ID"
```

查看启动日志：

```bash
sudo docker logs --tail 100 lade-query-neo4j
```

等待日志出现数据库启动完成信息，再测试：

```bash
sudo docker exec -it lade-query-neo4j cypher-shell \
  -u neo4j \
  -p "$TARGET_NEO4J_PASSWORD" \
  -d neo4j \
  'RETURN 1 AS ok;'
```

目标 Browser 地址为：

```text
http://127.0.0.1:37474
```

### 第五步：先运行预览

不要先加 `--apply`。运行：

```bash
./run_in_lade.sh python scripts/copy_neo4j_runs.py \
  --run-id million-nodes-hangzhou-0621-100agents \
  --run-id million-hangzhou-0831-agents03
```

预览会执行只读检查：

- 验证源、目标连接；
- 验证源、目标 Neo4j 版本完全一致；
- 确认两个 `Run` 节点都存在；
- 按标签统计目标节点；
- 按关系类型统计子图内部关系；
- 检查与其他 run/共享节点相连的边界关系；
- 读取源库约束和索引；
- 打印目标库当前节点数；
- 不写入任何节点或 schema。

正常结尾应包含：

```text
预览完成：未写入目标库。追加 --apply 才会执行复制。
```

如果提示“源子图存在跨 run/共享节点关系”，不要直接追加 `--allow-boundary-relationships`。先保存输出并检查这些关系是否应随 run 一起复制。

### 第六步：正式分批复制

确认预览中两个 run 正确、边界关系为 0、目标节点数为 0 后运行：

```bash
./run_in_lade.sh python scripts/copy_neo4j_runs.py \
  --run-id million-nodes-hangzhou-0621-100agents \
  --run-id million-hangzhou-0831-agents03 \
  --batch-size 2000 \
  --fetch-size 1000 \
  --progress-every 50000 \
  --index-timeout-seconds 900 \
  --apply
```

执行顺序为：

```text
检查目标为空
  -> 建立临时映射约束
  -> 分批复制节点
  -> 分批复制关系
  -> 删除临时标签和属性
  -> 复制源 schema
  -> 等待索引 ONLINE
  -> 比较源/目标统计
```

脚本从源库流式读取，不会一次把百万节点全部装入 Python 内存。`batch-size=2000` 是偏保守的初始值；第一次建议不要调大。

如果两个 run 仍在被写入，应先停止对它们的写入再开始复制，否则源统计和实际流式读取可能来自不同状态，最终校验会失败。

### 第七步：再次独立校验

即使正式复制末尾已经自动校验，也建议再执行一次：

```bash
./run_in_lade.sh python scripts/copy_neo4j_runs.py \
  --run-id million-nodes-hangzhou-0621-100agents \
  --run-id million-hangzhou-0831-agents03 \
  --verify-only
```

成功结果应包含：

```text
校验通过：源和目标的节点/关系分组计数一致。
```

再直接查看目标库的 run：

```bash
sudo docker exec -it lade-query-neo4j cypher-shell \
  -u neo4j \
  -p "$TARGET_NEO4J_PASSWORD" \
  -d neo4j \
  'MATCH (r:Run) RETURN r.id AS run_id ORDER BY run_id;'
```

预期只返回：

```text
million-hangzhou-0831-agents03
million-nodes-hangzhou-0621-100agents
```

检查目标索引：

```bash
sudo docker exec -it lade-query-neo4j cypher-shell \
  -u neo4j \
  -p "$TARGET_NEO4J_PASSWORD" \
  -d neo4j \
  'SHOW INDEXES YIELD name,state,type,labelsOrTypes,properties RETURN name,state,type,labelsOrTypes,properties ORDER BY name;'
```

所有用于测试的索引都应是 `ONLINE`。如果复制工具打印“schema 未自动复制”警告，应先处理这些警告，再进行性能测试。

### 第八步：在新库运行查询脚本

对第一个 run 测试：

```bash
NEO4J_URI=bolt://127.0.0.1:37687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD="$TARGET_NEO4J_PASSWORD" \
./run_in_lade.sh python scripts/lade_llm_context_queries.py \
  --run-id million-nodes-hangzhou-0621-100agents \
  --drone-id drone-1 \
  --step-index 2300 \
  --queries core
```

`step-index` 和 `drone-id` 必须替换为该 run 实际存在的值。第二个 run 同理，只需替换 `--run-id` 及其有效参数。

为了避免默认日志混入旧测试，正式测试建议显式指定新文件：

```bash
NEO4J_URI=bolt://127.0.0.1:37687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD="$TARGET_NEO4J_PASSWORD" \
./run_in_lade.sh python scripts/lade_llm_context_queries.py \
  --run-id million-nodes-hangzhou-0621-100agents \
  --drone-id drone-1 \
  --step-index 2300 \
  --queries core \
  --query-log-jsonl data/query_store_run1_query_logs.jsonl \
  --context-jsonl data/query_store_run1_contexts.jsonl \
  --summary-json data/query_store_run1_summary.json
```

### 失败后的处理

工具不支持在部分写入的目标库上无条件续传，因为直接重跑可能复制重复关系。若正式复制中途失败：

1. 保留完整错误输出；
2. 不要再次直接加 `--apply`；
3. 如果确认目标容器是本次新建、其中没有其他有价值的数据，可以删除目标容器和 volume 后重建。

下面两条命令会不可恢复地删除目标查询库，只能在核对名称完全正确后执行：

```bash
sudo docker rm -f lade-query-neo4j
sudo docker volume rm lade-query-neo4j-data
```

删除后重新从“第四步”开始。源容器 `neo4j` 和源 volume 不应执行任何删除命令。

### 清理 shell 中的密码

全部操作完成后：

```bash
unset SOURCE_NEO4J_PASSWORD
unset TARGET_NEO4J_PASSWORD
unset LADE_SOURCE_NEO4J_IMAGE_ID
```

注意：Docker 容器初始化使用的 `NEO4J_AUTH` 环境配置可能被具有 Docker 管理权限的用户查看。不要把生产级敏感密码用于本地测试容器。

### 工具实现说明

`scripts/copy_neo4j_runs.py` 的选择条件为：

```text
普通节点：node.run_id 属于目标 run 列表
Run 节点：Run.id 属于目标 run 列表
关系：起点和终点都满足以上条件
```

工具使用源 `elementId()` 作为迁移期间的临时映射值，但不会把它作为长期业务标识；复制结束后会移除临时属性和标签。源节点的原始 `id`、`run_id`、其他属性与标签保持不变。

它不会复制以下实例级内容：

- Neo4j 用户和角色；
- 容器配置、内存配置；
- 查询缓存和 page cache 状态；
- 事务日志；
- 不属于这两个 run 的节点和关系。

因此性能对比前还应让源/目标使用相同资源限制，并采用一致的预热与并发参数。

### 执行过的操作

- 新增 `scripts/copy_neo4j_runs.py`。
- 新增 `tests/copy_neo4j_runs_test.py`。
- 执行：
  - `./run_in_lade.sh python -m py_compile scripts/copy_neo4j_runs.py tests/copy_neo4j_runs_test.py`
  - `./run_in_lade.sh python -m unittest tests/copy_neo4j_runs_test.py`
  - `./run_in_lade.sh python scripts/copy_neo4j_runs.py --help`
- 单元测试结果：7 项全部通过。
- 未连接源或目标 Neo4j，未创建容器，未写入或删除数据库数据。

### 修改内容

- `scripts/copy_neo4j_runs.py`
  - 支持多个 `--run-id`；
  - 默认 dry-run 预览；
  - 支持 `--apply` 和 `--verify-only`；
  - 分批复制节点与关系；
  - 自动复制常用约束/索引并等待 ONLINE；
  - 检测跨边界关系和非空目标库；
  - 自动执行源/目标统计校验。
- `tests/copy_neo4j_runs_test.py`
  - 覆盖 run 去重、标识符转义、约束/索引语句生成、统计差异和默认预览模式。
- `docs/codex_notes/2026-09-17-两个run分批复制命令.md`
  - 保存本次完整操作命令与安全注意事项。

### 后续建议

- 严格按“创建目标 → 预览 → apply → verify-only”的顺序执行。
- 将预览输出中的节点数、关系数和边界关系数保存下来；若边界关系不为 0，先分析再决定是否允许跳过。
- 正式复制期间不要向这两个源 run 继续写数据。
- 复制完成并校验通过后，再开始查询性能测试，不要让复制写入和压测同时运行。
