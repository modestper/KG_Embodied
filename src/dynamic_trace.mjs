// The browser sends only a run identifier; complete frames stay on the server.
export function initTraceSave() {
  const find = (id) => document.getElementById(id);
  const button = find("saveTrace");
  const dialog = find("traceSaveDialog");
  const form = find("traceSaveForm");
  const folders = find("traceDirectories");
  const up = find("traceDirectoryUp");
  const current = find("traceDirectoryCurrent");
  const subfolder = find("traceNewFolder");
  const filename = find("traceFilename");
  const confirm = find("traceSaveConfirm");
  const cancel = find("traceSaveCancel");
  const feedback = find("traceSaveFeedback");
  const result = find("traceSaveResult");
  const resultText = find("traceSaveResultText");
  const replay = find("traceReplayLink");
  let trace = null;
  let directory = "";
  let parent = "";
  let root = "data/traces";
  let saving = false;
  let browsing = false;
  let directoryReady = false;
  let generation = 0;
  let browseSequence = 0;

  function controls() {
    button.disabled = !trace || saving;
    confirm.disabled = !trace || saving || browsing || !directoryReady;
    cancel.disabled = saving;
    for (const control of [folders, subfolder, filename]) {
      control.disabled = saving || browsing;
    }
    up.disabled = saving || browsing || !directory;
    confirm.textContent = saving ? "正在保存…" : "保存到服务器";
  }

  async function request(url, options) {
    const response = await fetch(url, { cache: "no-store", ...options });
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error("服务器未提供轨迹保存接口，请确认运行的是更新后的服务。");
    }
    if (!response.ok || !payload.ok) {
      const error = new Error(payload.error || "轨迹保存请求失败，请重试。");
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function browse(path) {
    const sequence = ++browseSequence;
    browsing = true;
    directoryReady = false;
    feedback.textContent = "正在读取服务器文件夹…";
    controls();
    try {
      const payload = await request(
        `./api/traces/directories?${new URLSearchParams({ path })}`,
      );
      if (sequence !== browseSequence) return;
      directory = payload.path;
      directoryReady = true;
      parent = payload.parent;
      root = payload.root;
      current.textContent = `${root}${directory ? `/${directory}` : ""}`;
      folders.replaceChildren(new Option("选择子文件夹进入…", ""));
      for (const folder of payload.directories) {
        folders.add(new Option(folder.name, folder.path));
      }
      feedback.textContent = payload.directories.length
        ? ""
        : "当前没有子文件夹，可直接保存或填写新建文件夹。";
    } catch (error) {
      if (sequence !== browseSequence) return;
      feedback.textContent = error.message;
      // Keep saving unavailable when the server directory cannot be read.
      return;
    } finally {
      if (sequence === browseSequence) {
        browsing = false;
        controls();
      }
    }
  }

  button.addEventListener("click", () => {
    if (!trace || saving) return;
    filename.value = trace.filename;
    subfolder.value = "";
    feedback.textContent = "";
    dialog.showModal();
    void browse("");
  });
  folders.addEventListener("change", () => {
    if (folders.value) void browse(folders.value);
  });
  up.addEventListener("click", () => void browse(parent));
  cancel.addEventListener("click", () => dialog.close());
  dialog.addEventListener("cancel", (event) => {
    if (saving) event.preventDefault();
  });
  dialog.addEventListener("close", () => {
    browseSequence += 1;
    browsing = false;
    controls();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!trace || saving || browsing || !directoryReady) return;
    const runGeneration = generation;
    const targetDirectory = [directory, subfolder.value.trim()]
      .filter(Boolean)
      .join("/");
    saving = true;
    feedback.textContent = "正在写入轨迹，请等待保存完成。";
    controls();
    try {
      const payload = await request("./api/traces/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_id: trace.run_id,
          directory: targetDirectory,
          filename: filename.value.trim(),
        }),
      });
      if (runGeneration !== generation) return;
      resultText.textContent = `已保存至 ${payload.path} · ${payload.frame_count.toLocaleString()} 帧`;
      replay.href = payload.replay_url;
      result.hidden = false;
      dialog.close();
    } catch (error) {
      if (runGeneration !== generation) return;
      feedback.textContent = error.message;
      if (error.status === 410) trace = null;
    } finally {
      if (runGeneration === generation) {
        saving = false;
        controls();
      }
    }
  });

  return {
    reset() {
      generation += 1;
      trace = null;
      saving = false;
      result.hidden = true;
      if (dialog.open) dialog.close();
      browseSequence += 1;
      browsing = false;
      directoryReady = false;
      button.title = "模拟结束后可保存完整轨迹";
      controls();
    },
    complete(descriptor) {
      trace =
        descriptor?.run_id && descriptor.frame_count > 0 ? descriptor : null;
      button.title = trace
        ? `保存本次完整轨迹，共 ${trace.frame_count} 帧`
        : "当前服务没有提供可保存的轨迹";
      controls();
    },
  };
}
