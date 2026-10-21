import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { formatQueryMs, formatQueryValue, initGraphQueryPanel, parseParameters } from "../src/dynamic_query.mjs";

class Element {
  constructor() {
    this.value = "";
    this.disabled = false;
    this.textContent = "";
    this.children = [];
    this.handlers = {};
  }
  set innerHTML(_) { throw new Error("Query values must not be rendered as HTML"); }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
}

function fixture() {
  const elements = new Map();
  const document = {
    querySelector(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    createElement() { return new Element(); },
    createDocumentFragment() { return new Element(); },
  };
  const el = (id) => document.querySelector(`#${id}`);
  el("graphQueryCypher").value = "RETURN $text AS value";
  el("graphQueryParams").value = '{"text":"<img src=x onerror=alert(1)>"}';
  el("graphQueryMaxRows").value = "100";
  el("graphQueryTimeout").value = "5";
  const calls = [];
  const config = { enabled: true, target: "bolt://localhost:61687", database: "neo4j", max_rows: 200, max_timeout_seconds: 15 };
  let queryResponse = {
    ok: true, target: config.target, database: config.database, executed_at: "2026-10-21T00:00:00Z",
    columns: ["value"], rows: [["<img src=x onerror=alert(1)>"]], row_count: 1, truncated: false, notifications: [],
    timing: { server_reported_ms: 0, query_client_ms: 1, validation_ms: 2, backend_total_ms: 3 },
  };
  const fetcher = async (url, options = {}) => {
    calls.push([url, options]);
    const result = url.endsWith("/config") ? config : queryResponse;
    return { ok: result.ok !== false, status: result.ok === false ? 400 : 200, json: async () => structuredClone(result) };
  };
  return { document, el, fetcher, calls, config, setResponse(value) { queryResponse = value; } };
}

describe("manual graph query panel", () => {
  it("accepts only object-shaped JSON parameters", () => {
    assert.deepEqual(parseParameters(""), {});
    assert.deepEqual(parseParameters('{"limit":20}'), { limit: 20 });
    for (const value of ["null", "[]", "1", '"text"', "{bad}"]) assert.throws(() => parseParameters(value));
  });

  it("formats zero timings and nested values without confusing null with zero", () => {
    assert.equal(formatQueryMs(0), "0.000 ms");
    assert.equal(formatQueryMs(null), "—");
    assert.equal(formatQueryValue(null), "null");
    assert.equal(formatQueryValue({ n: 1 }), '{"n":1}');
  });

  it("initialization fetches configuration only, without executing a query", async () => {
    const f = fixture();
    await initGraphQueryPanel(f.document, f.fetcher);
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0][0], "./api/graph-query/config");
    assert.equal(f.el("graphQuerySubmit").disabled, false);
  });

  it("submits custom Cypher, renders data as text and records separate timings", async () => {
    const f = fixture();
    await initGraphQueryPanel(f.document, f.fetcher);
    await f.el("graphQueryForm").handlers.submit({ preventDefault() {} });
    assert.equal(f.calls[1][1].method, "POST");
    assert.equal(JSON.parse(f.calls[1][1].body).query, "RETURN $text AS value");
    const cell = f.el("graphQueryRows").children[0].children[0].children[0];
    assert.equal(cell.textContent, "<img src=x onerror=alert(1)>");
    assert.equal(f.el("graphQueryDbMs").textContent, "0.000 ms");
    assert.equal(f.el("graphQueryClientMs").textContent, "1.000 ms");
    assert.match(f.el("graphQueryBrowserMs").textContent, / ms$/);
    assert.equal(f.el("graphQueryExport").disabled, false);
    assert.equal(f.el("graphQueryHistoryRows").children[0].children.length, 1);
  });

  it("invalid parameters do not send a query request", async () => {
    const f = fixture();
    await initGraphQueryPanel(f.document, f.fetcher);
    f.el("graphQueryParams").value = "[]";
    await f.el("graphQueryForm").handlers.submit({ preventDefault() {} });
    assert.equal(f.calls.length, 1);
    assert.match(f.el("graphQueryError").textContent, /JSON 对象/);
  });

  it("clears stale results on failed requests and does not call simulation endpoints", async () => {
    const f = fixture();
    await initGraphQueryPanel(f.document, f.fetcher);
    await f.el("graphQueryForm").handlers.submit({ preventDefault() {} });
    f.setResponse({ ok: false, error: "只允许读取" });
    await f.el("graphQueryForm").handlers.submit({ preventDefault() {} });
    assert.equal(f.el("graphQueryRows").children.length, 0);
    assert.equal(f.el("graphQueryClientMs").textContent, "—");
    assert.match(f.el("graphQueryError").textContent, /只允许读取/);
    assert.equal(f.el("graphQueryExport").disabled, true);
    assert.ok(f.calls.every(([url]) => url.startsWith("./api/graph-query")));
  });

  it("keeps only twenty successful history entries", async () => {
    const f = fixture();
    await initGraphQueryPanel(f.document, f.fetcher);
    for (let index = 0; index < 21; index++) {
      await f.el("graphQueryForm").handlers.submit({ preventDefault() {} });
    }
    assert.equal(f.el("graphQueryHistoryRows").children[0].children.length, 20);
  });

  it("disables execution when no database is configured", async () => {
    const f = fixture();
    f.config.enabled = false;
    await initGraphQueryPanel(f.document, f.fetcher);
    await f.el("graphQueryForm").handlers.submit({ preventDefault() {} });
    assert.equal(f.calls.length, 1);
    assert.equal(f.el("graphQuerySubmit").disabled, true);
  });

  it("handles older backends without breaking the simulation page", async () => {
    const f = fixture();
    await initGraphQueryPanel(f.document, async () => { throw new Error("404"); });
    assert.match(f.el("graphQueryError").textContent, /仿真结束后重启/);
    assert.match(f.el("graphQueryStatus").textContent, /其他仿真功能不受影响/);
  });
});
