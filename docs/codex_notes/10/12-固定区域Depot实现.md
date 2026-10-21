### 任务背景

将五个城市的区域 depot 从“按当前仿真抽取的任务子集临时计算”改为固定坐标。每个 depot 使用该城市、该区域全部订单主地址 `lng/lat` 转换到仿真坐标系后的算术平均值，确保不同日期、任务规模、随机种子和 local/Ray 后端得到相同结果。

### 关键结论

- 已为五城 172 个区域生成固定 depot：重庆 48、杭州 51、吉林 4、上海 52、烟台 17。
- 固定坐标保存在 `data/fixed_region_depots.json`，包含城市、区域、坐标和参与均值计算的订单数。
- `build_region_depots()` 不再根据传入任务计算均值，只读取固定表并为当前任务涉及的区域赋值。
- 固定表缺少城市或区域时会明确报错，不会静默退回样本均值，从而保证实验可复现。
- local、Ray、动态服务、离线 trace 和 checkpoint 恢复入口均复用 `load_delivery_tasks()` / `build_region_depots()`，行为保持一致。
- 重庆 `0615 / 10 tasks / seed 13` 复算后，完全重合任务由 8 个降为 0，平均 depot 到目的地距离由约 210.6 米变为约 2,373.1 米。
- 任务 `1100131` 的固定 R160 depot 为 `(11857874.210, 3451700.079)`，与目的地相距约 470.9 米。

### 执行过的操作

- 搜索并核对 `load_delivery_tasks()`、`build_region_depots()` 在动态、离线、Ray 和 checkpoint 流程中的调用位置。
- 执行 `./run_in_lade.sh python scripts/build_fixed_region_depots.py`，流式扫描五城 4,514,661 条订单并生成固定表。
- 执行 `./run_in_lade.sh python -m unittest tests.fixed_region_depots_test tests.delivery_sim_test`，25 项测试通过。
- 执行 `./run_in_lade.sh python -m unittest tests.dynamic_server_test tests.ray_delivery_sim_test tests.bootstrap_headless_checkpoint_test`，24 项测试通过。
- 重新计算重庆 0615 小样本，确认固定 depot 已生效。
- 再次生成 `/tmp/fixed_region_depots.verify.json` 并与项目固定表执行 `cmp`，内容完全一致，证明生成过程确定可复现。
- 对本次修改文件执行定向 `git diff --check`，通过。

### 修改内容

- `scripts/build_fixed_region_depots.py`：新增固定 depot 全量统计和确定性 JSON 生成脚本。
- `data/fixed_region_depots.json`：新增五城 172 个区域的固定坐标表。
- `simulation/delivery_sim.py`：新增固定表缓存、校验和按城市/区域读取逻辑；移除按当前任务子集计算 depot 的行为。
- `tests/delivery_sim_test.py`：新增任务子集变化不影响 depot、缺失区域时报错的测试。
- `tests/fixed_region_depots_test.py`：新增全量区域算术平均生成算法测试。
- `README.md`、`KNOWLEDGE_GRAPH_AND_LLM.md`：更新 depot 数据来源、运行语义和重新生成命令。

### 后续建议

- 停止并重新启动当前 `s8010` 进程，使 Python 服务加载新的固定 depot 逻辑；已运行中的仿真不会热更新任务坐标。
- 原始 delivery CSV 更新后，重新执行 `./run_in_lade.sh python scripts/build_fixed_region_depots.py` 并提交新的固定表。
- 后续实验报告应注明 depot 使用“五城全量订单区域均值”，不要再描述为“当前任务样本区域均值”。
