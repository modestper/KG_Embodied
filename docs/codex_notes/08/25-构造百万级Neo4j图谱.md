### 任务背景

用户说明 LaDe 数据集总任务量有百万级，但按天分摊后单日只有万级，想知道如何在 Neo4j 数据库中构造真实存在的百万级存储量，用于后续查询性能和 100 智能体并发压测。

### 关键结论

- 原始 CSV 确实有百万级：五城合计约 `4,514,666` 行，杭州约 `1,861,601` 行，上海约 `1,483,865` 行，重庆约 `931,352` 行。
- 当前 Neo4j 规模小，是因为现有仿真只按 `city/day/task_limit` 抽取一部分任务写图谱。
- 构造百万级存储不建议靠完整仿真慢慢跑，推荐直接从 LaDe CSV 批量导入 benchmark 图谱。
- 已新增脚本 `scripts/build_neo4j_scale_dataset.py`，默认只 dry-run 估算，不写数据库。
- 默认导入 `333,334` 条订单时，会构造 `333,334` 个 Task、`666,668` 个 Event，最少 `1,000,003 + Region/Courier 去重节点`，关系最少约 `4,333,342 + Region/Courier 去重关系`。
- 真正写入 Neo4j 时必须显式加 `--apply`，建议同时加 `--create-schema`。

### 执行过的操作

- 阅读 Neo4j 写入逻辑：`sed -n '160,540p' simulation/knowledge.py`
- 阅读仿真构造脚本：`sed -n '1,260p' scripts/build_delivery_sim.py`
- 阅读动态仿真脚本：`sed -n '1,620p' scripts/run_dynamic_sim.py`
- 查找原始 CSV：`find /data/zc/datasets/LaDe -maxdepth 3 -type f -name 'delivery_*.csv'`
- 统计 CSV 行数：`wc -l /data/zc/datasets/LaDe/delivery/delivery_*.csv`
- 查看 CSV 字段：`head -n 2 /data/zc/datasets/LaDe/delivery/delivery_jl.csv`
- 新增脚本：`scripts/build_neo4j_scale_dataset.py`
- 新增测试：`tests/build_neo4j_scale_dataset_test.py`
- 运行测试：`./run_in_lade.sh python -m unittest tests.build_neo4j_scale_dataset_test`
- 查看脚本帮助：`./run_in_lade.sh python scripts/build_neo4j_scale_dataset.py --help`
- dry-run 估算百万级规模：`./run_in_lade.sh python scripts/build_neo4j_scale_dataset.py --city all --limit 333334 --dry-run`
- 运行相关测试集合：`./run_in_lade.sh python -m unittest tests.build_neo4j_scale_dataset_test tests.concurrent_neo4j_query_test tests.timed_neo4j_query_test tests.query_neo4j_test`
- shell 语法检查：`bash -n run_in_lade.sh bash/time_neo4j_query.sh`

### 修改内容

- 新增 `scripts/build_neo4j_scale_dataset.py`：
  - 从 LaDe delivery CSV 直接批量构造 Neo4j benchmark KG。
  - 支持 `--city`、`--csv-path`、`--run-id`、`--limit`、`--batch-size`、`--create-schema`、`--dry-run`、`--apply`。
  - 为每条订单构造 Task 节点、2 个 Event 节点、Region/Courier 去重节点，以及 IN_RUN、BELONGS_TO、DELIVERED、WORKS_IN、ABOUT_TASK、ABOUT_COURIER、IN_REGION 等关系。
  - 默认 `--limit 333334`，约生成百万级节点。
- 新增 `tests/build_neo4j_scale_dataset_test.py`：
  - 验证时间解析、CSV 行转 Task、批次读取、dry-run 输出和规模估算。
- 新增本总结文件：`docs/codex_notes/2026-08-25-构造百万级Neo4j图谱.md`。

### 后续建议

- 先只做 dry-run：
  `./run_in_lade.sh python scripts/build_neo4j_scale_dataset.py --city all --limit 333334 --dry-run`
- 确认磁盘、内存和 Neo4j 运行状态后，再写入：
  `./run_in_lade.sh python scripts/build_neo4j_scale_dataset.py --city all --limit 333334 --run-id lade-scale-1m --create-schema --apply`
- 如果只想要关系达到百万级而非节点百万级，可以把 `--limit` 降到 `80000` 到 `100000`。
- 如果要百万 Task 节点，则使用：
  `./run_in_lade.sh python scripts/build_neo4j_scale_dataset.py --city all --limit 1000000 --run-id lade-scale-1m-tasks --create-schema --apply`
- 导入完成后，应重新运行 `scripts/concurrent_neo4j_query.py`，并分别测试点查、事件窗口、Step 汇总、1-2 跳邻域查询。
