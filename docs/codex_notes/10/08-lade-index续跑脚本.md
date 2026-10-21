### 任务背景

完整复制脚本已经成功生成 `lade` 数据库并克隆出约 3 GB 的 `lade-index` 数据目录，但由于原端口 `67474/67687` 超过 `65535`，Docker 未能创建 `lade-index` 容器。用户需要一个不重复复制数据、能够从失败点继续执行的脚本。

### 关键结论

- 新增 `scripts/resume_lade_index.sh`，只处理已有 `lade-index` 数据目录，不再读取原始 `neo4j` 容器或重新复制百万级节点。
- 默认端口为 HTTP `61474`、Bolt `61687`，均处于合法范围。
- 脚本默认只执行预检；只有传入 `--apply` 才会创建容器和索引。
- 续跑前会检查 `lade` 正在运行、`lade-index` 容器尚不存在、数据目录非空、端口未占用、目标 run 存在且 `lade` 仍是无实验索引基准库。
- 执行阶段会创建 `lade-index` 容器，建立三组 AgentState RANGE 索引，等待其全部变为 `ONLINE`，再比较两份库中目标 run 的节点和关系分组。
- 任何失败都不会自动删除容器或数据；输出会标明停止阶段，方便继续查看 Docker 日志。

### 执行过的操作

- 新建并赋予可执行权限：`scripts/resume_lade_index.sh`。
- 执行 `bash -n scripts/resume_lade_index.sh`，语法检查通过。
- 执行 `bash scripts/resume_lade_index.sh --help`，帮助输出正常。
- 使用非法 Bolt 端口 `67687` 测试预检，脚本在访问 Docker 前正确拒绝并返回退出码 1。
- 检查 ShellCheck 是否可用；当前环境未安装，因此没有运行 ShellCheck。
- 未执行 `--apply`，未创建容器、未建立索引、未修改现有 Neo4j 数据。

### 修改内容

- 新增 `scripts/resume_lade_index.sh`：
  - 复用 `/data/zc/datasets/LaDe/neo4j_ab/lade-index`；
  - 从 `lade` 容器读取完全相同的 Neo4j image ID；
  - 创建 `lade-index` 并映射 `61474:7474`、`61687:7687`；
  - 创建 `lade_agent_state_run_drone_step`、`lade_agent_state_run_region_step`、`lade_agent_state_run_step` 三组 RANGE 索引；
  - 使用 `db.awaitIndexes()` 等待索引就绪；
  - 调用 `scripts/copy_neo4j_runs.py --verify-only` 做双库数据校验。

### 后续建议

- 先在具有 sudo 凭据的终端运行预检：

  ```bash
  cd /data/zc/datasets/LaDe/viewer
  bash scripts/resume_lade_index.sh
  ```

- 预检通过后执行正式续跑：

  ```bash
  bash scripts/resume_lade_index.sh --apply
  ```

- 两次运行都会交互式询问之前为 `lade` 设置的 Neo4j 密码，输入时终端不会回显。
- 成功后可用 `sudo docker ps -a | grep lade` 查看两个容器；索引库浏览器地址为 `http://127.0.0.1:61474`，Bolt 地址为 `bolt://127.0.0.1:61687`。
