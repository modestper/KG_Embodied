### 任务背景

用户希望新增一个 Python 查询脚本，在没有 `cypher-shell` 的情况下查看本机 Neo4j 图谱规模和本项目实验 run 数据。

### 关键结论

- 已新增 `scripts/query_neo4j.py`。
- 脚本默认连接 `bolt://127.0.0.1:17687`，默认用户 `neo4j`，默认密码 `neo4j123456`。
- 可通过 `NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD` 覆盖连接信息。
- 可通过 `--run-id` 或 `KG_RUN_ID` 查询指定实验 run。
- 默认输出总节点数、总关系数、按节点标签统计、按关系类型统计和 `Run` 列表。
- 指定 run 后会额外输出该 run 下的节点统计和最近事件。

### 执行过的操作

- 新增测试：`tests/query_neo4j_test.py`。
- 运行测试确认缺少脚本时失败：`./run_in_lade.sh python -m unittest tests.query_neo4j_test`。
- 新增实现：`scripts/query_neo4j.py`。
- 运行测试确认通过：`./run_in_lade.sh python -m unittest tests.query_neo4j_test`。
- 验证命令行入口：`./run_in_lade.sh python scripts/query_neo4j.py --help`。

### 修改内容

- `scripts/query_neo4j.py`：新增 Neo4j 查询工具脚本。
- `tests/query_neo4j_test.py`：新增不依赖真实 Neo4j 的单元测试，覆盖全局统计、run 列表、指定 run 统计和最近事件输出。
- `docs/codex_notes/2026-06-29-neo4j查询脚本.md`：新增本次工作总结。

### 后续建议

- 在用户终端里如果 `run_in_lade.sh` 仍没有执行权限，可使用 `bash ./run_in_lade.sh ...`，或先执行 `chmod +x run_in_lade.sh`。
- 查询默认本机 Neo4j：

```bash
bash ./run_in_lade.sh python scripts/query_neo4j.py
```

- 查询指定实验：

```bash
bash ./run_in_lade.sh python scripts/query_neo4j.py --run-id jilin-0925-local001
```
