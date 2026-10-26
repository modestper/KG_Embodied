export function escapeHtml(value) {
  return String(value ?? "—").replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character],
  );
}

export function statusBadge(label, tone = "neutral") {
  return `<span class="status-badge" data-tone="${escapeHtml(tone)}">${escapeHtml(label)}</span>`;
}

export function droneStatusBadge(status) {
  const states = {
    idle: ["空闲", "success"],
    queued: ["待执行", "warning"],
    delivering: ["执行配送", "active"],
    returning: ["返航", "warning"],
    charging: ["充电", "warning"],
    failed: ["故障", "danger"],
  };
  return statusBadge(...(states[status] ?? [status || "等待数据", "neutral"]));
}

export function initDisclosures() {
  const settings = document.querySelector("#sceneSettings");
  if (settings && window.matchMedia("(max-width: 760px)").matches)
    settings.open = false;
  for (const link of document.querySelectorAll("[data-open-panel]")) {
    link.addEventListener("click", () => {
      const target = document.getElementById(link.dataset.openPanel);
      if (target) target.open = true;
    });
  }
  const target = document.getElementById(window.location.hash.slice(1));
  if (target?.tagName === "DETAILS") target.open = true;
}

export function initTabs(container) {
  if (!container) return;
  const tabs = [...container.querySelectorAll('[role="tab"]')];
  function select(tab) {
    for (const item of tabs) {
      const active = item === tab;
      item.setAttribute("aria-selected", String(active));
      item.tabIndex = active ? 0 : -1;
      document.getElementById(item.getAttribute("aria-controls")).hidden =
        !active;
    }
  }
  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      const next =
        event.key === "ArrowRight"
          ? (index + 1) % tabs.length
          : event.key === "ArrowLeft"
            ? (index - 1 + tabs.length) % tabs.length
            : event.key === "Home"
              ? 0
              : event.key === "End"
                ? tabs.length - 1
                : null;
      if (next === null) return;
      event.preventDefault();
      select(tabs[next]);
      tabs[next].focus();
    });
  }
}

export function showPageError(error) {
  document.querySelector(".page-notice")?.remove();
  const notice = document.createElement("div");
  notice.className = "page-notice";
  notice.setAttribute("role", "alert");
  notice.textContent = `数据加载失败：${error.message || error}`;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = "重新加载页面";
  retry.addEventListener("click", () => window.location.reload());
  notice.append(retry);
  document.querySelector(".topbar").after(notice);
}
