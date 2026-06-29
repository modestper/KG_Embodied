### 任务背景

用户说明本项目通常使用 `lade` conda 环境，希望将 Neo4j Python driver 安装到该环境，并要求后续项目操作也基于该环境执行。

### 关键结论

- `lade` 环境存在，路径为 `/data/zc/apps/miniconda3/envs/lade`。
- `lade` 环境 Python 为 `Python 3.13.13`。
- 已在 `lade` 环境安装 `neo4j==6.2.0` 和依赖 `pytz==2026.2`。
- 已通过 `bolt://127.0.0.1:17687`、用户 `neo4j`、密码 `neo4j123456` 验证 Neo4j 连接成功。
- 后续项目 Python 命令应优先使用 `conda run -n lade ...` 执行。

### 执行过的操作

- `conda env list`
- `conda run -n lade python --version`
- `conda run -n lade python -c "import sys, importlib.util; ..."`
- `conda run -n lade python -m pip --version`
- `conda run -n lade python -m pip install neo4j`
- `conda run -n lade python -c "import neo4j; ..."`
- `conda run -n lade python -m unittest tests.knowledge_test`
- `conda run -n lade python -c "from neo4j import GraphDatabase; ... verify_connectivity() ..."`

### 修改内容

- 新增本文档：`docs/codex_notes/2026-06-18-neo4j-lade环境.md`。
- 未修改项目运行代码。
- 在 `lade` conda 环境中安装了 Neo4j Python driver。

### 后续建议

- 运行仿真并写入 Neo4j 时使用：

```bash
KG_BACKEND=neo4j \
KG_RUN_ID=jilin-0925-local001 \
NEO4J_URI=bolt://127.0.0.1:17687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD=neo4j123456 \
conda run -n lade ./run_local_sim.sh
```

- 如果使用 Ray 后端，将最后一行改为：

```bash
conda run -n lade ./run_ray_sim.sh
```
