# 实时页面顶部 Step 显示

日期：2026-10-21

### 任务背景

用户希望在页面上方看到当前 Step，以便填写图谱查询中的 `step_index`。本轮在实时仿真页顶部指标区新增“当前 Step”，同时核实前端帧编号与图谱步数的关系，避免查询错位。

### 关键结论

- 已在实时页顶部增加“当前 Step”指标，辅助说明为“查询用 step_index”，数字可以选中复制。
- 当前服务的 SSE `frame_index` 从 0 开始；Local 和 Ray 环境均在追加帧后使用 `len(self.frames)` 记录图谱 Step，因此对应关系为 `step_index = frame_index + 1`。
- 第一帧显示 Step 1。没有收到帧时显示“—”，不把未开始或未知状态伪装成 Step 0。
- 步数取自所选帧的原始编号，不使用时间轴位置计数；例如 `frame_index` 为 0、5、10 时，显示的 Step 为 1、6、11。
- 回看历史时跟随所选帧更新，后台继续收帧不会覆盖历史 Step；同时提示“最新接收”的 Step。点击实时后恢复最新已接收帧的 Step。
- 运行结束时仍显示当前画面对应的 Step，不用结束消息中的总步数覆盖它，避免采样后末帧与实际结束步数不同造成误导。
- 时间轴测试通过，动态服务测试 16 项通过，隔离浏览器 12 组验收通过；没有连接现有实时流或数据库，无需重启服务。

#### 编号依据与使用边界

[scripts/run_dynamic_sim.py](/data/zc/datasets/LaDe/viewer/scripts/run_dynamic_sim.py:105) 使用 `for step in range(max_steps)` 推进仿真，完成该步后发送 `frame_index: step`。

[SimulationEnv.step_once](/data/zc/datasets/LaDe/viewer/simulation/delivery_sim.py:624) 和 [RaySimulationEnv.step_once](/data/zc/datasets/LaDe/viewer/simulation/ray_delivery_sim.py:486) 先追加帧，再调用 `record_step(len(self.frames), ...)`。因此页面不能直接把原有帧编号当作图谱 Step。

新增解析函数优先接受明确的 `step_index` 字段；当前后端没有该字段时，根据现有编号关系换算。没有修改服务端协议，已经启动的服务也可使用。

“当前 Step”表示当前画面对应的仿真步数。“最新接收”表示浏览器已收到的最新帧，不是额外查询数据库得到的全库最新 Step。查询时仍应选择对应数据库及 `run_id`。

### 执行过的操作

- 阅读实时 HTML、页面渲染和时间轴逻辑，核对 SSE 推送代码以及 Local、Ray 的图谱写入编号。
- 更新顶部指标、时间轴渲染与响应式布局。
- 新增采样、历史回看和显式编号的解析测试；新增服务端帧编号与已记录 Step 的对应关系测试。
- 运行 `node --test tests/dynamic_timeline.test.mjs`。
- 运行 `./run_in_lade.sh python -m unittest tests.dynamic_server_test`：16 项通过。
- 运行 `node --check src/dynamic_app.mjs`、`git diff --check`。
- 使用临时 Playwright/Chromium 和随机端口的隔离服务验证初始未知值、实时、历史回看、后台收帧、采样、回到实时及运行结束后的显示；同时检查桌面与手机布局。

验收文件：

- [桌面预览](./21-当前Step-桌面预览.png)
- [手机预览](./21-当前Step-手机预览.png)
- [浏览器验收结果](./21-当前Step-浏览器验收.json)

截图使用隔离示例帧，不代表当前真实实验运行状态。浏览器测试没有请求真实 `/events` 或数据库。

### 修改内容

- `dynamic.html`：顶部新增当前 Step 指标；更新资源版本；恢复区域边界菜单原有 `regionBoundaryMenu` 标识，保持既有页面契约。
- `src/dynamic_app.mjs`：随时间轴同步渲染当前 Step 和最新接收 Step；使用带版本的时间轴模块入口。
- `src/dynamic_timeline.mjs`：新增 `frameStepIndex()`，统一处理显式步数、原始帧编号与未知值。
- `src/styles.css`：实时页顶部六项指标布局及窄屏适配，突出 Step 数字并支持整体选择。
- `tests/dynamic_timeline.test.mjs`：验证从 1 开始、采样帧、回看和缺失数据的处理。
- `tests/dynamic_server_test.py`：验证采样帧与同时间图谱 Step 的对应关系。
- 新增本说明及相关预览、验收结果。

未修改仿真后端、图谱写入、查询执行或历史帧保存策略；没有启动、重启或替换现有仿真。

### 后续建议

- 本轮已完成，无需额外确认；现有服务可继续运行。
- 查询 `step_index` 可使用顶部显示值；回看时注意它对应历史画面。
- 如需查看真实页面，可刷新前端资源；实时页刷新仍保留原有重新连接并启动仿真的行为，正在运行重要实验时可先查看预览。
