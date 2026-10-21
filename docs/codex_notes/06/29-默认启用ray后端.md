### 任务背景

用户使用 `--backend ray` 启动动态仿真后，执行 `ray status` 提示找不到正在运行的 Ray instance，因此希望调整项目启动逻辑，让动态服务默认使用 Ray 后端。

### 关键结论

- `scripts/run_dynamic_sim.py` 原来的默认 backend 是 `local`。
- 已将 `scripts/run_dynamic_sim.py` 的 `--backend` 默认值改为 `ray`。
- 直接运行 `python3 scripts/run_dynamic_sim.py ...` 时，现在默认会初始化 Ray。
- `run_ray_sim.sh` 原本已经显式传 `--backend ray`，行为不变。
- `run_local_sim.sh` 仍然显式传 `--backend local`，因此仍可用于本地非 Ray 模式。
- `ray status` 找不到 Ray 不一定说明仿真没有用 Ray；如果 Ray 是由 Python 服务进程内部启动的，查询时需要服务进程仍在运行，并且有时需要使用 `ray status --address=auto`。

### 执行过的操作

- 阅读 `scripts/run_dynamic_sim.py` 中的 `parse_args()`、`initialize_ray_if_needed()` 和 `build_runtime()`。
- 阅读 `run_local_sim.sh`、`run_ray_sim.sh` 和 README 中的 Ray 说明。
- 新增测试 `test_parse_args_defaults_to_ray_backend`。
- 先运行该测试，确认当前默认值是 `local`，测试失败。
- 修改 `scripts/run_dynamic_sim.py`，将 `--backend` 默认值改为 `ray`。
- 修改 `README.md`，同步“服务默认”后端说明。
- 运行相关测试和脚本语法检查。

### 修改内容

- `scripts/run_dynamic_sim.py`
  - `parser.add_argument("--backend", choices=["local", "ray"], default="ray")`

- `tests/dynamic_server_test.py`
  - 新增测试，确认不传 `--backend` 时默认是 `ray`。

- `README.md`
  - 更新动态页面 Backend 默认行为说明。

- `docs/codex_notes/2026-06-29-默认启用ray后端.md`
  - 新增本文档。

### 后续建议

- 直接启动动态服务时可以不再传 `--backend ray`：

```bash
python3 scripts/run_dynamic_sim.py --host 127.0.0.1 --port 8010
```

- 如果要强制 local 模式，仍可显式传：

```bash
python3 scripts/run_dynamic_sim.py --backend local --host 127.0.0.1 --port 8010
```

- 查询 Ray 状态时，建议在服务进程仍运行时尝试：

```bash
ray status --address=auto
```

验证命令：

```bash
./run_in_lade.sh python -m unittest tests.dynamic_server_test
bash -n run_local_sim.sh run_ray_sim.sh
./run_in_lade.sh python scripts/run_dynamic_sim.py --help
```
