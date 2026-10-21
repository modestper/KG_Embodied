# Neo4j Cypher 语言学习总结

### 任务背景

用户希望系统学习 Neo4j 的 Cypher 查询语言，掌握从图模型、基本增删改查到路径查询、聚合、索引和性能分析的常用方法。

### 关键结论

- Cypher 的核心是用“节点—关系—节点”的图模式描述想要匹配或创建的数据。
- `MATCH ... WHERE ... RETURN` 是最基本的查询骨架，变量用于在同一条语句中引用节点、关系和路径。
- `CREATE` 总是新建数据；`MERGE` 保证整个指定模式至少存在一次，两者不能混用理解。
- `WITH` 是查询管道的分段符，负责传递变量、聚合、排序、分页和控制后续查询范围。
- `OPTIONAL MATCH` 类似关系数据库的左外连接，匹配失败时保留已有行并返回 `null`。
- 索引和约束应建立在常用入口字段上，但索引不能弥补笛卡尔积、无限制路径和返回数据过多等查询设计问题。
- 优化时先用 `EXPLAIN` 查看计划，再用 `PROFILE` 查看实际执行数据量和数据库访问量。

## 1. 先理解 Cypher 的图模型

Neo4j 中最重要的三个概念是：

- 节点（Node）：实体，例如用户、订单、商品。
- 标签（Label）：节点的类别，例如 `:Person`、`:Movie`。
- 关系（Relationship）：节点之间有方向、有类型的连接，例如 `[:ACTED_IN]`。
- 属性（Property）：节点或关系上的键值数据，例如 `name`、`released`。

Cypher 使用括号表示节点，方括号表示关系：

```cypher
(p:Person {name: 'Tom Hanks'})-[:ACTED_IN]->(m:Movie)
```

可以把它读成：“名为 Tom Hanks 的 `Person` 节点，通过 `ACTED_IN` 关系指向某个 `Movie` 节点。”

常见语法：

```cypher
(n)                         // 任意节点
(n:Person)                  // 带 Person 标签的节点
(n:Person {name: 'Alice'})  // 带属性条件的节点
-[r]->                      // 任意有向关系
-[r:KNOWS]->                // KNOWS 类型的关系
-[:KNOWS]-                  // 查询时不限制方向
```

变量 `n`、`r` 只在当前查询中使用，不是数据库中的字段。

## 2. 最基本的查询：MATCH、WHERE、RETURN

查询所有电影：

```cypher
MATCH (m:Movie)
RETURN m;
```

按属性过滤：

```cypher
MATCH (m:Movie)
WHERE m.released >= 2000 AND m.title CONTAINS 'Matrix'
RETURN m.title, m.released;
```

查询有关联的数据：

```cypher
MATCH (p:Person)-[r:ACTED_IN]->(m:Movie)
WHERE p.name = $person_name
RETURN p.name, r.roles, m.title;
```

推荐使用参数，而不是把外部输入直接拼接进查询：

```text
Cypher: MATCH (p:Person {name: $name}) RETURN p
参数:   {"name": "Tom Hanks"}
```

这样更安全，也更有利于执行计划复用。

常用比较和判断：

```cypher
=  <>  <  <=  >  >=
AND  OR  NOT
IN
STARTS WITH
ENDS WITH
CONTAINS
IS NULL
IS NOT NULL
```

## 3. 控制返回结果

```cypher
MATCH (m:Movie)
RETURN DISTINCT m.title AS title, m.released AS year
ORDER BY year DESC, title ASC
SKIP 10
LIMIT 10;
```

- `AS`：给返回列起别名。
- `DISTINCT`：去重。
- `ORDER BY`：排序。
- `SKIP`、`LIMIT`：分页。

不要默认返回完整节点。只需要几个字段时，优先返回字段：

```cypher
RETURN m.title, m.released
```

这通常比 `RETURN m` 传输的数据更少。

## 4. 创建数据：CREATE

创建节点：

```cypher
CREATE (p:Person {name: 'Alice', age: 25})
RETURN p;
```

创建两个节点及其关系：

```cypher
CREATE (p:Person {name: 'Alice'})
CREATE (m:Movie {title: 'Graph Story', released: 2026})
CREATE (p)-[:ACTED_IN {role: 'Engineer'}]->(m)
RETURN p, m;
```

如果节点已经存在，只创建关系，应该先 `MATCH`：

```cypher
MATCH (p:Person {name: $person_name})
MATCH (m:Movie {title: $movie_title})
CREATE (p)-[:ACTED_IN]->(m);
```

`CREATE` 每执行一次都会创建新数据，不会自动检查重复。

## 5. 匹配或创建：MERGE

`MERGE` 表示“匹配整个模式；如果不存在则创建”：

```cypher
MERGE (p:Person {name: $name})
ON CREATE SET p.created_at = datetime()
ON MATCH SET p.last_seen_at = datetime()
RETURN p;
```

创建关系时，通常先分别定位两端节点：

```cypher
MATCH (p:Person {name: $person_name})
MATCH (m:Movie {title: $movie_title})
MERGE (p)-[r:LIKES]->(m)
ON CREATE SET r.created_at = datetime();
```

需要特别注意：

```cypher
MERGE (p:Person {name: $name, age: $age})
```

这里匹配的是 `name` 和 `age` 的组合，并不是“按 name 找到后顺便更新 age”。如果 `age` 改变，可能创建另一个节点。更稳妥的方式是：

```cypher
MERGE (p:Person {name: $name})
SET p.age = $age;
```

关键业务标识最好配合唯一约束，避免并发写入时产生重复节点。

## 6. 修改属性和标签

设置或更新属性：

```cypher
MATCH (p:Person {name: $name})
SET p.age = 26,
    p.updated_at = datetime();
```

用 Map 批量合并属性：

```cypher
MATCH (p:Person {name: $name})
SET p += $properties
RETURN p;
```

`SET p += map` 保留未提及的原属性；`SET p = map` 会整体替换属性，应谨慎使用。

添加标签：

```cypher
MATCH (p:Person {name: $name})
SET p:Employee;
```

删除属性或标签：

```cypher
MATCH (p:Person {name: $name})
REMOVE p.temporary_field
REMOVE p:Employee;
```

## 7. 删除数据

删除关系：

```cypher
MATCH (:Person {name: $name})-[r:LIKES]->(:Movie)
DELETE r;
```

删除没有关系的节点：

```cypher
MATCH (p:Person {name: $name})
DELETE p;
```

删除节点及其所有关系：

```cypher
MATCH (p:Person {name: $name})
DETACH DELETE p;
```

`DELETE p` 在节点仍有关联关系时会失败；`DETACH DELETE p` 会同时删除关系，使用前一定要确保匹配范围准确。

## 8. OPTIONAL MATCH：关系不存在也保留结果

```cypher
MATCH (p:Person)
OPTIONAL MATCH (p)-[:DIRECTED]->(m:Movie)
RETURN p.name, collect(m.title) AS directed_movies;
```

即使某个人没有导演任何电影，这个人仍然会出现在结果中。

过滤可选模式时，`WHERE` 应紧跟对应的 `OPTIONAL MATCH`：

```cypher
MATCH (p:Person)
OPTIONAL MATCH (p)-[:ACTED_IN]->(m:Movie)
WHERE m.released >= 2020
RETURN p.name, collect(m.title);
```

`WHERE` 不是一个独立的后处理步骤，它会约束紧邻的 `MATCH` 或 `OPTIONAL MATCH`。

## 9. WITH：把长查询分成多个阶段

`WITH` 类似管道：它把当前结果传给下一段查询。

```cypher
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)
WITH p, count(m) AS movie_count
WHERE movie_count >= 3
RETURN p.name, movie_count
ORDER BY movie_count DESC;
```

`WITH` 的常见用途：

- 聚合后继续过滤。
- 控制传入下一阶段的变量。
- 在写入前去重。
- 分阶段排序、分页。
- 避免后续查询意外形成大规模组合。

没有出现在 `WITH` 后面的变量，通常不能在后续语句中继续使用：

```cypher
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)
WITH p
RETURN p.name;  // 此处不能再使用 m
```

## 10. 聚合函数

常用聚合函数：

```cypher
count(*)
count(n)
count(DISTINCT n)
sum(n.value)
avg(n.value)
min(n.value)
max(n.value)
collect(n)
```

例子：统计每部电影的演员数：

```cypher
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)
RETURN m.title, count(DISTINCT p) AS actor_count
ORDER BY actor_count DESC;
```

Cypher 没有显式的 `GROUP BY`。当 `RETURN` 或 `WITH` 中同时出现普通表达式和聚合函数时，普通表达式就是分组键。上例按 `m.title` 分组。

## 11. 列表、Map 和 UNWIND

列表：

```cypher
RETURN [1, 2, 3] AS numbers;
```

Map：

```cypher
RETURN {name: 'Alice', age: 25} AS person;
```

列表推导：

```cypher
RETURN [x IN range(1, 10) WHERE x % 2 = 0 | x * x] AS squares;
```

`UNWIND` 把列表展开为多行，常用于批量写入：

```cypher
UNWIND $people AS item
MERGE (p:Person {name: item.name})
SET p.age = item.age;
```

参数示例：

```json
{
  "people": [
    {"name": "Alice", "age": 25},
    {"name": "Bob", "age": 30}
  ]
}
```

## 12. 路径查询

固定长度路径：

```cypher
MATCH path = (a:Person {name: $name})-[:KNOWS]->(friend:Person)
RETURN path;
```

一到三跳的变长路径：

```cypher
MATCH path = (a:Person {name: $name})-[:KNOWS*1..3]->(other:Person)
RETURN other.name, length(path);
```

最短路径：

```cypher
MATCH (a:Person {name: $from}), (b:Person {name: $to})
MATCH path = shortestPath((a)-[:KNOWS*..10]-(b))
RETURN path, length(path);
```

路径查询的危险点是搜索空间膨胀。生产查询应尽量：

- 限定起点标签和索引属性。
- 限定关系类型。
- 限定方向。
- 设置合理的最大跳数。
- 只返回需要的节点或路径。

## 13. 子查询和存在性判断

判断某种模式是否存在：

```cypher
MATCH (p:Person)
WHERE EXISTS {
  MATCH (p)-[:ACTED_IN]->(:Movie {title: $title})
}
RETURN p.name;
```

使用 `CALL` 子查询隔离一段逻辑：

```cypher
MATCH (p:Person)
CALL (p) {
  MATCH (p)-[:ACTED_IN]->(m:Movie)
  RETURN count(m) AS movie_count
}
RETURN p.name, movie_count;
```

子查询适合封装局部聚合、限制中间结果或组合复杂读写逻辑。不同 Neo4j 小版本对子查询变量导入语法的推荐形式可能不同，使用时应结合服务器版本核对手册。

## 14. UNION：合并查询结果

```cypher
MATCH (p:Person)
RETURN p.name AS name
UNION
MATCH (m:Movie)
RETURN m.title AS name;
```

- `UNION`：合并并去重。
- `UNION ALL`：合并但保留重复行，通常开销更小。
- 两边返回列数和列名需要兼容。

## 15. 索引和约束

为常用查询入口创建范围索引：

```cypher
CREATE INDEX person_name_index IF NOT EXISTS
FOR (p:Person)
ON (p.name);
```

创建唯一约束：

```cypher
CREATE CONSTRAINT person_name_unique IF NOT EXISTS
FOR (p:Person)
REQUIRE p.name IS UNIQUE;
```

查看索引和约束：

```cypher
SHOW INDEXES;
SHOW CONSTRAINTS;
```

适合建立索引的属性通常是：

- 经常作为查询起点的字段。
- `MERGE` 使用的业务唯一标识。
- 经常做等值或范围过滤的字段。

不要为每个属性都建立索引。索引会占用空间，并增加写入和维护成本。

对于这个项目中类似 `Run`、`Step`、`AgentState` 的数据，常见思路是先根据查询入口评估组合字段，例如：

```cypher
CREATE INDEX step_run_index IF NOT EXISTS
FOR (s:Step)
ON (s.run_id);

CREATE INDEX agent_state_lookup IF NOT EXISTS
FOR (a:AgentState)
ON (a.run_id, a.step_index, a.agent_id);
```

以上只是索引设计示例；应以真实标签、属性、选择性和查询计划为准。

## 16. EXPLAIN 和 PROFILE

只生成执行计划，不真正执行查询：

```cypher
EXPLAIN
MATCH (p:Person {name: $name})
RETURN p;
```

执行查询并记录实际运行统计：

```cypher
PROFILE
MATCH (p:Person {name: $name})
RETURN p;
```

重点观察：

- 是否使用了合适的索引查找，而不是扫描大量节点。
- 中间结果行数是否突然成倍增长。
- 是否出现无意的笛卡尔积。
- 数据库访问量是否明显高于最终结果量。
- 排序、去重和聚合是否处理了过多数据。

`PROFILE` 会真正执行查询。对写语句使用 `PROFILE` 时尤其要谨慎，因为数据确实会被修改。

## 17. 常见错误与性能陷阱

### 17.1 无意产生笛卡尔积

```cypher
MATCH (a:Person), (b:Movie)
RETURN a, b;
```

如果有 1000 个人和 1000 部电影，中间结果可能达到 100 万行。两个模式没有关系时，应确认这种组合是否真的是目标。

### 17.2 MERGE 范围过大

```cypher
MERGE (p:Person {name: $name})-[:LIKES]->(m:Movie {title: $title})
```

这会把整条模式作为匹配单位。实际项目通常更适合分别 `MERGE` 或 `MATCH` 两端节点，再 `MERGE` 关系。

### 17.3 OPTIONAL MATCH 后过滤位置错误

如果把可选部分返回的 `null` 在后续 `WHERE` 中过滤掉，可能无意中把外连接效果变回内连接。应明确过滤属于哪个模式。

### 17.4 无限制的变长路径

```cypher
MATCH p = (a)-[*]-(b)
RETURN p;
```

这类查询可能枚举巨大数量的路径。应限制关系类型、方向和最大跳数。

### 17.5 先展开大量数据，再聚合或 LIMIT

`LIMIT` 放在查询末尾只能减少最终返回量，不一定能减少前面已经发生的遍历。可以使用 `WITH ... LIMIT ...` 提前缩小中间结果。

### 17.6 把内部 ID 当业务 ID

业务逻辑应使用自己的唯一属性和约束。需要引用 Neo4j 元素标识时，现代查询优先使用 `elementId()`；不要把内部存储标识当作长期稳定的业务键。

## 18. 一条完整查询的阅读顺序

```cypher
MATCH (p:Person)-[:ACTED_IN]->(m:Movie)
WHERE m.released >= $start_year
WITH p, count(DISTINCT m) AS movie_count
WHERE movie_count >= $minimum
RETURN p.name AS actor, movie_count
ORDER BY movie_count DESC
LIMIT 10;
```

可以逐行理解为：

1. 找出演员和电影之间的出演关系。
2. 只保留指定年份之后的电影。
3. 按演员分组，统计其不同电影数量。
4. 过滤出演数量不足的演员。
5. 返回演员名和数量。
6. 按数量降序排列，只取前 10 名。

学习 Cypher 最有效的方法，就是把每条查询都翻译成这种逐阶段的数据流。

## 19. 常用模板速查

```cypher
// 按属性查节点
MATCH (n:Label {key: $value})
RETURN n;

// 查一跳关系
MATCH (a:LabelA)-[r:REL_TYPE]->(b:LabelB)
WHERE a.key = $value
RETURN a, r, b;

// 创建节点
CREATE (n:Label {key: $value})
RETURN n;

// 匹配或创建，并更新属性
MERGE (n:Label {unique_key: $value})
SET n += $properties
RETURN n;

// 更新
MATCH (n:Label {key: $value})
SET n.property = $new_value
RETURN n;

// 删除节点及关系
MATCH (n:Label {key: $value})
DETACH DELETE n;

// 分组统计
MATCH (a:LabelA)-[:REL_TYPE]->(b:LabelB)
RETURN a.key, count(DISTINCT b) AS count
ORDER BY count DESC;

// 批量写入
UNWIND $rows AS row
MERGE (n:Label {key: row.key})
SET n += row.properties;
```

## 20. 推荐学习顺序

1. 掌握节点、标签、关系、属性和图模式写法。
2. 熟练使用 `MATCH`、`WHERE`、`RETURN`。
3. 学习 `CREATE`、`SET`、`DELETE` 和 `MERGE`。
4. 掌握 `WITH`、聚合、排序、分页和 `UNWIND`。
5. 学习 `OPTIONAL MATCH`、路径和子查询。
6. 最后学习约束、索引、`EXPLAIN` 和 `PROFILE`。

建议自己建立一个包含 5 到 10 个节点的小图，每学一个子句就立即写一条查询验证。与其背语法，更重要的是养成三个习惯：先明确起点、再画匹配模式、最后检查中间结果规模。

### 执行过的操作

- 按 Cypher 的图模型、读写子句、数据管道、路径、索引和查询优化组织学习内容。
- 编写可独立运行的查询示例和常用模板。
- 针对 `MERGE`、`OPTIONAL MATCH`、笛卡尔积和变长路径等高频误区增加说明。
- 尝试访问 Neo4j 官方在线手册；当前检索接口未返回正文，因此本文避免依赖未经核对的边缘版本特性。

### 修改内容

- 新增 `docs/codex_notes/09/18-Cypher语言学习总结.md`。
- 未修改项目代码或数据库数据。

### 后续建议

- 下一步可选取项目中的一条真实查询，逐段标注其匹配模式、索引入口和中间结果。
- 执行练习前先使用测试数据库，涉及 `DELETE`、`DETACH DELETE`、`CREATE`、`MERGE` 的语句不要直接在重要数据上试验。
- 根据 Neo4j 服务器实际版本，通过官方手册确认子查询、路径模式和索引类型的具体语法。

## 官方参考入口

- [Cypher Manual：Clauses](https://neo4j.com/docs/cypher-manual/current/clauses/)
- [Cypher Manual：Patterns](https://neo4j.com/docs/cypher-manual/current/patterns/)
- [Cypher Manual：Functions](https://neo4j.com/docs/cypher-manual/current/functions/)
- [Cypher Manual：Indexes](https://neo4j.com/docs/cypher-manual/current/indexes/)
- [Cypher Manual：Query tuning](https://neo4j.com/docs/cypher-manual/current/planning-and-tuning/)
