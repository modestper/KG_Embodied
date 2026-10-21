### 任务背景

用户想确认百万级节点写入 Neo4j 变慢的原因，尤其是否和存储介质有关，以及当前 Neo4j 图数据实际存放在哪里。

### 关键结论

- 当前项目访问的 Neo4j 地址主要是 `bolt://127.0.0.1:17687`，对应宿主机端口 `17687` 映射到容器内 `7687`。
- 进程信息显示该 Neo4j 是 Docker 容器启动的，容器内 Neo4j home 为 `/var/lib/neo4j`。
- Neo4j 默认图数据在容器内 `/var/lib/neo4j/data/databases/`，事务日志通常在 `/var/lib/neo4j/data/transactions/`。
- 由于当前账号无法访问 Docker API，暂时不能直接确认 `/var/lib/neo4j/data` 是否绑定到了宿主机目录，还是存放在 Docker volume/overlay 中。
- 百万级写入变慢可能和存储介质有关，但也和事务次数、`MERGE` 模式、索引约束、写入关系数量、Neo4j heap/page cache、容器 CPU/内存限制有关。
- `scripts/build_neo4j_scale_dataset.py` 已经使用 `UNWIND $tasks` 批量写入，默认 `batch-size=5000`，比逐条写入更合理。
- 动态仿真写图路径不是纯批量导入，会按 assignment、pickup、completion、step、agent state 多次写入，写入粒度更细，百万级时更容易慢。

### 执行过的操作

- 搜索项目内 Neo4j 连接、脚本和导入逻辑：
  - `rg -n "neo4j|NEO4J|17687|7474|data/databases|server.directories|dbms.directories|docker|compose" -S .`
  - `rg --files -g '*neo4j*' -g '*docker*' -g '*compose*' -g 'neo4j.conf' -g '*.sh'`
- 查看当前宿主机进程：
  - `ps -ef | rg -i 'neo4j|java|docker'`
- 尝试查看 Docker 容器：
  - `docker ps --format ...`
  - 由于当前账号无 Docker API 权限，命令失败。
- 读取写入逻辑：
  - `simulation/knowledge.py`
  - `scripts/build_neo4j_scale_dataset.py`

### 修改内容

本次没有修改代码，只新增了本排查总结文档。

### 后续建议

- 在有 Docker 权限的 shell 中执行 `docker ps` 和 `docker inspect <neo4j-container>`，确认 `/data` 是否挂载到了宿主机路径。
- 对百万级批量导入优先使用 `scripts/build_neo4j_scale_dataset.py --apply --create-schema`，不要用动态仿真逐事件写入来构造基准数据。
- 如果写入仍慢，优先检查 Neo4j 数据目录所在磁盘、Docker volume 位置、heap/page cache 配置、约束是否已创建。
