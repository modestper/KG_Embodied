### 任务背景

用户希望理解创建独立 Neo4j 查询库并复制两个 run 的全部命令，尤其是 Shell 变量赋值、命令替换、Docker image ID、volume、端口映射、环境变量及 Python 复制工具参数的含义。

需要复制的 run 为：

- `million-nodes-hangzhou-0621-100agents`
- `million-hangzhou-0831-agents03`

### 关键结论

- `LADE_SOURCE_NEO4J_IMAGE_ID=$(...)` 只是读取源容器使用的镜像 ID并保存到当前终端变量，不复制数据，也不修改或停止源容器。
- Docker image 是创建容器的程序模板；container 是正在运行的 Neo4j 进程环境；volume 是持久保存数据库文件的位置；Bolt 端口是 Python 与 Neo4j 通信的入口。
- 创建目标容器只会得到一个使用相同 Neo4j 版本的空数据库，真正的数据复制由 `scripts/copy_neo4j_runs.py` 通过 Bolt 协议完成。
- 不带 `--apply` 时脚本只预览；带 `--apply` 才会写目标库；`--verify-only` 只比较源、目标统计。
- 源 Neo4j 服务不需要停止，但复制期间不应继续写这两个目标 run，否则不同复制阶段可能看到不同数据状态。

### 一、先理解四个不同概念

#### Docker image

image 是只读程序模板，里面包含 Neo4j 程序、Java 环境和启动脚本。例如：

```text
neo4j:5.26-community
sha256:abc123...
```

镜像本身不是正在运行的数据库，也不包含挂载在外部 volume 中的业务数据。

#### Docker container

container 是根据 image 启动出来的运行实例。当前源容器叫：

```text
neo4j
```

准备创建的目标容器叫：

```text
lade-query-neo4j
```

两个容器可以使用相同 image，但拥有不同端口、不同进程和不同数据 volume。

#### Docker volume

volume 用于持久保存 Neo4j 的数据库文件。容器删除或重建后，只要 volume 仍然存在，数据仍可能保留。

目标库使用：

```text
lade-query-neo4j-data
```

它会挂载到目标容器内部的 `/data`。

#### Neo4j database

Neo4j DBMS 内部的实际数据库名称通常是：

```text
neo4j
```

注意这里同时出现了三个 `neo4j`，含义不同：

```text
neo4j 容器名
neo4j 用户名
neo4j database 名
```

恰好同名，但并不是同一个概念。

### 二、进入项目目录

命令：

```bash
cd /data/zc/datasets/LaDe/viewer
```

解释：

- `cd` 是 change directory，切换当前工作目录。
- 后面的路径是项目根目录。
- 后续 `./run_in_lade.sh`、`scripts/...` 等相对路径都以这个目录为起点。

查看当前目录可以执行：

```bash
pwd
```

预期输出：

```text
/data/zc/datasets/LaDe/viewer
```

### 三、查询源容器的 Neo4j 版本

命令：

```bash
sudo docker exec neo4j neo4j --version
```

逐段解释：

- `sudo`：以管理员权限执行后续命令，因为当前用户可能没有直接访问 Docker daemon 的权限。
- `docker exec`：在一个已经运行的容器里执行命令。
- 第一个 `neo4j`：容器名称。
- 第二个 `neo4j`：容器内部的 Neo4j 命令行程序。
- `--version`：只打印版本。

该命令只读取版本，不会停止数据库或修改数据。

### 四、取得源容器的 image ID

命令：

```bash
LADE_SOURCE_NEO4J_IMAGE_ID=$(sudo docker inspect --format '{{.Image}}' neo4j)
```

这是一个 Shell 变量赋值，可以拆成两部分理解。

#### 右边的命令

```bash
sudo docker inspect --format '{{.Image}}' neo4j
```

逐段解释：

- `docker inspect`：读取 Docker 对象的详细元数据。
- `neo4j`：要检查的容器名称。
- `--format`：不要输出完整的大段 JSON，只输出模板指定的字段。
- `{{.Image}}`：Docker 使用的 Go template 表达式，表示读取容器元数据中的 `Image` 字段。

输出通常类似：

```text
sha256:1234567890abcdef...
```

这是创建源容器时实际使用的镜像内容 ID。它比 `neo4j:latest` 更明确，因为 `latest` 标签以后可能指向另一个版本，而 image ID 指向当前本机已有的确定镜像。

#### 外层的 `$()`

```bash
$(命令)
```

表示 command substitution，即：

1. 先执行括号中的命令；
2. 获取它输出的文本；
3. 把文本放到当前位置。

所以整条命令大致等价于：

```bash
LADE_SOURCE_NEO4J_IMAGE_ID=sha256:1234567890abcdef...
```

#### 左边的变量名

```bash
LADE_SOURCE_NEO4J_IMAGE_ID=...
```

表示在当前 Shell 中创建变量。查看变量内容：

```bash
echo "$LADE_SOURCE_NEO4J_IMAGE_ID"
```

双引号可以避免变量内容被 Shell 进行不必要的分词或通配符展开。

这个变量默认只在当前终端 Shell 中存在。关闭终端后它会消失；这没有问题，因为它只是创建目标容器时临时使用。

#### 为什么不直接写 `neo4j:latest`

因为源容器虽然显示由 `neo4j:latest` 创建，但这个标签随时间可能变化。如果目标容器拉取到更新版本，源、目标版本可能不一致。使用源容器当前的 image ID，可保证目标容器使用相同 Neo4j 程序版本。

#### 这条命令不会做什么

它不会：

- 复制 Neo4j 数据；
- 创建新容器；
- 停止源容器；
- 修改源库；
- 复制源容器的 volume。

它只是读取一个标识符并保存到 Shell 变量。

### 五、交互读取源密码

命令：

```bash
read -rsp '源 Neo4j 密码: ' SOURCE_NEO4J_PASSWORD
echo
export SOURCE_NEO4J_PASSWORD
```

第一行逐段解释：

- `read`：从键盘读取输入。
- `-r`：把反斜杠按普通字符读取，不进行特殊转义。
- `-s`：silent，输入时不把密码显示在屏幕上。
- `-p '...'`：显示提示文字。
- `SOURCE_NEO4J_PASSWORD`：把输入保存到这个 Shell 变量。

第二行 `echo` 只是输出换行。因为 `read -s` 输入完成后屏幕通常仍停留在提示文字后面，补一个换行更易读。

第三行：

```bash
export SOURCE_NEO4J_PASSWORD
```

表示把普通 Shell 变量导出为环境变量，使随后启动的子进程也能读取它。Python 脚本就是当前 Shell 启动的子进程。

如果不 `export`，当前 Shell 可以读取变量，但 `./run_in_lade.sh python ...` 通常读不到它。

### 六、交互读取目标密码

命令：

```bash
read -rsp '目标 Neo4j 新密码: ' TARGET_NEO4J_PASSWORD
echo
export TARGET_NEO4J_PASSWORD
```

含义与源密码相同，只是这个密码用于新建的目标 Neo4j。

Neo4j Docker 容器第一次创建数据库时，会通过 `NEO4J_AUTH` 接收初始用户名和密码。目标密码应至少 8 个字符。

### 七、创建目标 Docker volume

命令：

```bash
sudo docker volume create lade-query-neo4j-data
```

解释：

- `docker volume create`：创建一个由 Docker 管理的持久化存储区域。
- `lade-query-neo4j-data`：volume 名称。

输出通常就是：

```text
lade-query-neo4j-data
```

这时只是创建了空存储空间，还没有启动 Neo4j，也没有复制数据。

查看 volume：

```bash
sudo docker volume inspect lade-query-neo4j-data
```

### 八、创建并启动目标容器

完整命令：

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

行尾的反斜杠 `\` 表示命令还没有结束，下一行仍属于同一条命令。它只是为了排版清晰，也可以写成一整行。

逐项解释如下。

#### `sudo docker run`

根据指定 image 创建并启动一个新容器。

#### `-d`

detached mode，后台运行。命令执行后终端会返回容器 ID，而 Neo4j 继续在后台运行。

如果没有 `-d`，日志会一直占据当前终端。

#### `--name lade-query-neo4j`

给新容器设置固定名称。以后可以通过名称操作：

```bash
sudo docker logs lade-query-neo4j
sudo docker stop lade-query-neo4j
sudo docker start lade-query-neo4j
```

#### `--restart unless-stopped`

如果 Docker daemon 或机器重启，容器会自动恢复；但如果是你主动执行 `docker stop`，它不会立刻自行重启。

#### `-p 37474:7474`

端口映射格式为：

```text
宿主机端口:容器内部端口
```

因此：

```text
浏览器访问宿主机 37474
  -> 转发到目标容器内部 Neo4j HTTP 7474
```

目标 Browser 地址为：

```text
http://127.0.0.1:37474
```

#### `-p 37687:7687`

把宿主机的 `37687` 转发到容器内部的 Neo4j Bolt 端口 `7687`。

Python 连接地址因此是：

```text
bolt://127.0.0.1:37687
```

源库使用宿主机 `17687`，目标库使用 `37687`，两者不会冲突。

#### `-v lade-query-neo4j-data:/data`

格式为：

```text
Docker volume 名:容器内目录
```

它把目标 volume 挂载到容器内部 `/data`。Neo4j 会把数据库 store、事务日志等写进 `/data`。

因此：

- 删除并重建容器不一定删除数据；
- 删除 volume 才会删除这份目标数据库的持久数据；
- 源容器和目标容器没有共享 volume，所以不会互相覆盖数据。

#### `-e NEO4J_AUTH="neo4j/${TARGET_NEO4J_PASSWORD}"`

`-e` 表示向容器传入环境变量。

Shell 会先把：

```bash
${TARGET_NEO4J_PASSWORD}
```

替换成刚才输入的目标密码。最终传给容器的值形式为：

```text
NEO4J_AUTH=neo4j/目标密码
```

斜杠前的 `neo4j` 是初始用户名，斜杠后是密码。

这不会修改源库密码，只设置新目标库的初始认证。

#### `"$LADE_SOURCE_NEO4J_IMAGE_ID"`

这是 `docker run` 最后的 image 参数，表示使用源容器相同的镜像程序创建目标容器。

它只复用 Neo4j 程序版本，不会自动复制源容器的数据。目标数据来自刚创建的空 volume。

### 九、查看目标启动日志

命令：

```bash
sudo docker logs --tail 100 lade-query-neo4j
```

解释：

- `docker logs`：查看容器标准输出和错误日志。
- `--tail 100`：只显示最后 100 行，避免输出过多。
- `lade-query-neo4j`：目标容器名称。

这条命令只读日志，不修改容器。

如果想持续跟踪日志，可以执行：

```bash
sudo docker logs -f lade-query-neo4j
```

`-f` 表示 follow。看到启动完成后按 `Ctrl+C` 只是退出日志查看，不会停止容器。

### 十、测试目标连接

命令：

```bash
sudo docker exec -it lade-query-neo4j cypher-shell \
  -u neo4j \
  -p "$TARGET_NEO4J_PASSWORD" \
  -d neo4j \
  'RETURN 1 AS ok;'
```

解释：

- `docker exec`：在目标容器中运行程序。
- `-i`：保持标准输入连接。
- `-t`：分配终端。
- `cypher-shell`：Neo4j 自带的 Cypher 命令行客户端。
- `-u neo4j`：登录用户名。
- `-p ...`：登录密码。
- `-d neo4j`：连接名为 `neo4j` 的 database。
- `'RETURN 1 AS ok;'`：执行最简单的 Cypher，用于验证服务和认证是否正常。

成功时会返回值 `1`。这条查询不会创建节点。

### 十一、默认预览命令

命令：

```bash
./run_in_lade.sh python scripts/copy_neo4j_runs.py \
  --run-id million-nodes-hangzhou-0621-100agents \
  --run-id million-hangzhou-0831-agents03
```

逐段解释如下。

#### `./run_in_lade.sh`

执行项目脚本 `run_in_lade.sh`。它会：

1. 加载 Conda；
2. 激活 `lade` 环境；
3. 执行后面的命令。

这样可以确保使用项目所需的 Python 和 Neo4j driver。

#### `python scripts/copy_neo4j_runs.py`

用 Python 运行新增加的迁移工具。

#### 两个 `--run-id`

`--run-id` 可以重复传入。这里构造的目标集合包含两个 run，而不是运行两次互不相关的复制。

脚本选择：

```text
run_id 属于这两个值的普通节点
加上 id 属于这两个值的 Run 节点
```

关系只有在起点和终点都属于这个目标集合时才复制。

#### 为什么不用显式写连接地址

工具默认值已经是：

```text
源：bolt://127.0.0.1:17687
目标：bolt://127.0.0.1:37687
```

用户名默认都是 `neo4j`，database 默认都是 `neo4j`。密码从刚才 export 的两个环境变量读取。

#### 为什么预览不会写入

脚本把 `--apply` 设计成显式开关。没有这个参数时只会：

- 测试连接；
- 检查版本；
- 确认两个 Run 存在；
- 统计节点和关系；
- 检查边界关系；
- 读取 schema；
- 查看目标节点数。

最后打印：

```text
预览完成：未写入目标库。追加 --apply 才会执行复制。
```

### 十二、什么是边界关系

边界关系指一端属于目标 run，另一端不属于目标 run 的关系：

```text
目标节点 --关系--> 非目标节点
非目标节点 --关系--> 目标节点
```

如果直接只复制目标节点，这些关系无法完整复制。工具默认中止是为了避免悄悄丢失关系。

如果预览显示边界关系不为 0，应该先判断另一端是：

- 应一起复制的共享节点；
- 错误关联到其他 run 的节点；
- 确实可以忽略的外部关系。

不要在没看清数据前直接使用 `--allow-boundary-relationships`。

### 十三、正式复制命令

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

新增参数的含义如下。

#### `--batch-size 2000`

每次向目标库提交最多约 2000 个节点或关系。

分批的原因是：

- 避免把百万节点全部放入一次事务；
- 降低 Python 和 Neo4j 内存压力；
- 单批失败时范围更小。

值越大不一定越快。2000 是偏保守的初始值。

#### `--fetch-size 1000`

Neo4j Python driver 从源库拉取结果时，每批向客户端传送的记录数量提示值。

它控制网络结果流的抓取窗口，不等同于目标写事务大小。

可以这样区分：

```text
fetch-size：源 Neo4j -> Python
batch-size：Python -> 目标 Neo4j
```

#### `--progress-every 50000`

每复制约 50000 个节点或关系打印一次进度。它只影响日志频率，不影响选择哪些数据。

#### `--index-timeout-seconds 900`

复制数据后，工具会创建源库中支持自动复制的约束和索引。这个参数表示最多等待 900 秒，让目标索引进入 `ONLINE` 状态。

超时不会删除已经复制的数据，但工具会报错，提示需要检查索引。

#### `--apply`

这是写入确认开关。出现它以后，脚本才真正创建目标节点、关系和 schema。

执行 `--apply` 前必须确认：

- 目标库节点数是 0；
- 两个 run 已停止写入；
- 预览中的边界关系符合预期；
- 源、目标版本一致。

### 十四、正式复制内部做了什么

#### 1. 检查目标为空

脚本执行类似：

```cypher
MATCH (n) RETURN count(n);
```

只要目标库已有节点就拒绝写入，避免数据混合或重复复制。

#### 2. 建立临时节点映射

源 Neo4j 和目标 Neo4j 的内部 node ID 不相同，因此不能直接说“把源节点 123 的关系连到目标节点 123”。

工具会在目标节点上临时保存源节点的 `elementId()`，并添加临时标签：

```text
临时标签：__LaDeRunCopy
临时属性：_lade_source_element_id_v1
```

这只用于迁移期间匹配关系端点。

#### 3. 分批复制节点

每个节点复制：

- 全部 labels；
- 全部 properties；
- 临时源节点映射。

源节点不会被修改。

#### 4. 分批复制关系

根据临时映射找到目标起点和终点，然后复制：

- 关系类型；
- 方向；
- 全部关系属性。

#### 5. 删除临时信息

关系复制完后，删除临时标签、临时属性和临时唯一性约束。最终业务数据中不保留源 `elementId()`。

#### 6. 复制 schema

脚本从源库读取 `SHOW CONSTRAINTS` 和 `SHOW INDEXES`，自动重建常用 schema。

当前自动支持：

- 节点唯一性约束；
- RANGE 索引；
- TEXT 索引；
- POINT 索引。

约束自动产生的 backing index 不会重复创建；目标默认的 LOOKUP 索引也不会重复创建。

如果遇到 FULLTEXT、VECTOR、存在性约束等未自动处理的 schema，会打印警告，需要人工核对。

#### 7. 等待索引 ONLINE

新索引需要扫描已复制数据并建立索引结构。在所有索引达到 `ONLINE` 前，不应开始正式查询压测。

#### 8. 自动校验

工具重新统计源和目标：

- 每种 labels 组合的节点数；
- 每种 relationship type 的关系数；
- 目标总节点数；
- 目标是否仍有跨边界关系。

数量不一致时会报错。

需要注意：这是结构与数量校验，不是每个属性逐字节哈希校验。因此复制期间必须停止这两个 run 的写入，避免数量不变但属性更新。

### 十五、为什么不用停止整个源 Neo4j

Python 迁移工具通过 Bolt 执行普通只读 Cypher，不读取底层 store 文件，所以源 Neo4j 可以保持在线。

但工具的这些阶段属于不同事务：

```text
预统计
节点读取
关系读取
最终统计
```

如果这两个 run 在中途继续变化，各阶段可能看到不同状态。因此需要停止的是“对这两个 run 的写入进程”，不是整个 Neo4j 服务。

其他 run 可以继续写入，但会竞争源库 CPU、磁盘和缓存，使复制变慢。若希望复制和后续性能实验更稳定，最好同时减少其他高负载任务。

### 十六、校验命令

```bash
./run_in_lade.sh python scripts/copy_neo4j_runs.py \
  --run-id million-nodes-hangzhou-0621-100agents \
  --run-id million-hangzhou-0831-agents03 \
  --verify-only
```

`--verify-only` 表示：

- 不创建节点；
- 不创建关系；
- 不创建索引；
- 只重新读取并比较源、目标统计。

预期成功信息：

```text
校验通过：源和目标的节点/关系分组计数一致。
```

### 十七、清理 Shell 变量

操作结束后：

```bash
unset SOURCE_NEO4J_PASSWORD
unset TARGET_NEO4J_PASSWORD
unset LADE_SOURCE_NEO4J_IMAGE_ID
```

`unset` 只删除当前 Shell 中的变量，不会：

- 删除容器；
- 删除 volume；
- 修改 Neo4j 密码；
- 删除已复制数据。

它的作用是避免密码继续留在当前终端进程的环境变量中。

### 十八、哪些命令会修改状态

只读命令：

```text
docker inspect
docker logs
neo4j --version
复制脚本默认预览
复制脚本 --verify-only
```

会创建资源：

```text
docker volume create
docker run
复制脚本 --apply
```

会停止服务但不删除数据：

```text
docker stop
```

具有破坏性、当前流程中不应随意执行：

```text
docker rm
docker volume rm
Cypher DETACH DELETE
```

尤其是 `docker volume rm` 会删除对应目标库的持久化数据。源容器和源 volume 不应执行这些删除操作。

### 执行过的操作

- 阅读并解释此前整理的 Docker 创建、密码设置、迁移预览、正式复制和校验命令。
- 核对 `scripts/copy_neo4j_runs.py` 的默认连接、参数和内部复制顺序。
- 未执行 Docker 创建、停止、删除操作。
- 未连接或修改源、目标 Neo4j。

### 修改内容

- 未修改迁移脚本或业务代码。
- 新增解释文档：`docs/codex_notes/2026-09-17-Neo4j复制命令逐句解释.md`。

### 后续建议

- 先执行到默认预览命令，不要一开始使用 `--apply`。
- 每执行一步都观察输出；如果与文档中的预期不一致，停在当前步骤处理，不要继续堆叠命令。
- 如果预览输出中出现边界关系或目标库非空，把完整输出保存下来再判断。
- 正式复制前确认这两个 run 已停止写入，且目标 volume 是本次新建的空 volume。
