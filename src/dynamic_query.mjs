// Manual graph queries have their own lifecycle; never subscribe to SSE frames.
export function parseParameters(text) {
  let value;
  try {
    value = JSON.parse(text.trim() || "{}");
  } catch {
    throw new Error("参数不是合法 JSON，请检查引号、逗号和括号。");
  }
  if (value === null || Array.isArray(value) || typeof value !== "object") {
    throw new Error('参数必须是 JSON 对象，例如 {"limit": 20}。');
  }
  return value;
}

export function formatQueryValue(value) {
  if (value === null) return "null";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function formatQueryMs(value) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(3)} ms` : "—";
}

function fillTable(document, head, body, columns, rows) {
  const header = document.createElement("tr");
  for (const column of columns) {
    const cell = document.createElement("th");
    cell.textContent = column;
    header.append(cell);
  }
  head.replaceChildren(header);
  const fragment = document.createDocumentFragment();
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of row) {
      const cell = document.createElement("td");
      const text = formatQueryValue(value);
      cell.textContent = text.length > 2000 ? `${text.slice(0, 2000)}…（完整值见导出）` : text;
      tr.append(cell);
    }
    fragment.append(tr);
  }
  body.replaceChildren(fragment);
}

export async function initGraphQueryPanel(document = globalThis.document, fetcher = globalThis.fetch) {
  const panel = document.querySelector("#graphQueryPanel");
  if (!panel) return;
  const el = (id) => document.querySelector(`#${id}`);
  const form = el("graphQueryForm");
  const query = el("graphQueryCypher");
  const parameters = el("graphQueryParams");
  const submit = el("graphQuerySubmit");
  const exportButton = el("graphQueryExport");
  const status = el("graphQueryStatus");
  const error = el("graphQueryError");
  const warnings = el("graphQueryWarnings");
  const history = [];
  let lastResult = null;
  let busy = false;
  let enabled = false;

  function setTiming(timing = {}) {
    for (const [key, id] of Object.entries({
      server_reported_ms: "graphQueryDbMs",
      query_client_ms: "graphQueryClientMs",
      validation_ms: "graphQueryValidationMs",
      backend_total_ms: "graphQueryBackendMs",
      browser_request_ms: "graphQueryBrowserMs",
    })) {
      el(id).textContent = formatQueryMs(timing[key]);
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !enabled) return;
    let request;
    error.textContent = "";
    warnings.textContent = "";
    try {
      request = {
        query: query.value.trim(),
        parameters: parseParameters(parameters.value),
        max_rows: Number(el("graphQueryMaxRows").value),
        timeout_seconds: Number(el("graphQueryTimeout").value),
      };
      if (!request.query) throw new Error("请先输入只读 Cypher 查询。");
    } catch (cause) {
      error.textContent = cause.message;
      return;
    }
    busy = true;
    submit.disabled = true;
    exportButton.disabled = true;
    lastResult = null;
    setTiming();
    el("graphQueryHead").replaceChildren();
    el("graphQueryRows").replaceChildren();
    status.textContent = "正在校验并执行只读查询；不会暂停或重启仿真。";
    const started = performance.now();
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetcher("./api/graph-query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify(request),
        signal: controller.signal,
      });
      const result = await response.json();
      const browserMs = performance.now() - started;
      if (!response.ok || !result.ok) throw new Error(result.error || `查询失败（HTTP ${response.status}）。`);
      result.timing.browser_request_ms = browserMs;
      setTiming(result.timing);
      fillTable(document, el("graphQueryHead"), el("graphQueryRows"), result.columns, result.rows);
      status.textContent = `${result.target} / ${result.database || "默认库"} · 已展示 ${result.row_count} 行${result.truncated ? "（结果已截断，请缩小查询范围或调整展示上限）" : ""} · ${new Date(result.executed_at).toLocaleTimeString()}`;
      warnings.textContent = result.notifications.map((item) => `[${item.severity || "提示"} ${item.code}] ${item.description}`).join("\n");
      const entry = {
        executed_at: result.executed_at,
        target: result.target,
        database: result.database,
        ...request,
        row_count: result.row_count,
        truncated: result.truncated,
        timing: result.timing,
      };
      history.unshift(entry);
      if (history.length > 20) history.pop();
      fillTable(document, el("graphQueryHistoryHead"), el("graphQueryHistoryRows"),
        ["时间", "查询", "数据库报告", "后端查询", "网页请求", "展示行数"],
        history.map((item) => [new Date(item.executed_at).toLocaleTimeString(),
          item.query.slice(0, 90), formatQueryMs(item.timing.server_reported_ms),
          formatQueryMs(item.timing.query_client_ms), formatQueryMs(item.timing.browser_request_ms), item.row_count]));
      lastResult = { ...result, request, history: [...history] };
      exportButton.disabled = false;
    } catch (cause) {
      error.textContent = cause.name === "AbortError"
        ? "网页请求超过 30 秒，已停止等待；后端事务仍受其超时限制，停止等待不等于数据库立即取消。"
        : cause.message || "查询请求失败，请检查页面服务连接。";
      status.textContent = "本次查询失败，未展示上一次的结果。";
      el("graphQueryBrowserMs").textContent = formatQueryMs(performance.now() - started);
    } finally {
      clearTimeout(deadline);
      busy = false;
      submit.disabled = !enabled;
    }
  });

  exportButton.addEventListener("click", () => {
    if (!lastResult) return;
    const blob = new Blob([JSON.stringify(lastResult, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `graph-query-${Date.now()}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  try {
    const response = await fetcher("./api/graph-query/config", { cache: "no-store" });
    const config = await response.json();
    if (!response.ok) throw new Error(config.error || "无法读取查询服务配置。");
    enabled = Boolean(config.enabled);
    el("graphQueryTarget").textContent = `查询目标：${config.target} / ${config.database}${config.run_id ? ` · 环境 Run ID：${config.run_id}` : ""}`;
    el("graphQueryMaxRows").max = config.max_rows;
    el("graphQueryTimeout").max = config.max_timeout_seconds;
    status.textContent = enabled ? "等待手动执行。此面板不调用 LLM，也不自动执行 i/q 查询集。" : "未配置查询数据库，请在服务端设置 GRAPH_QUERY_URI 或 NEO4J_URI 后启动服务。";
    submit.disabled = !enabled;
  } catch (cause) {
    error.textContent = `查询接口尚不可用：${cause.message} 已运行的旧版后端需在仿真结束后重启。`;
    status.textContent = "查询面板不可用；其他仿真功能不受影响。";
  }
}
