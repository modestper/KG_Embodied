export function initSimulationControl(onChange) {
  const button = document.getElementById("simulationPause");
  const feedback = document.getElementById("simulationControlFeedback");
  let current = null;
  let busy = false;
  let generation = 0;

  function render() {
    const paused = current && ["paused", "pausing"].includes(current.state);
    button.disabled = !current || busy;
    button.textContent = busy ? "正在请求…" : paused ? "继续仿真" : "暂停仿真";
    button.setAttribute("aria-pressed", String(Boolean(paused)));
    button.title = "控制后端仿真；暂停在当前 Step 完成后生效";
    feedback.hidden = !paused;
    feedback.textContent =
      current?.state === "pausing"
        ? "正在暂停仿真：等待当前 Step 完成，随后停止推进。"
        : paused
          ? `仿真已暂停于 Step ${current.step_index}，点击“继续仿真”接着运行。`
          : "";
  }

  function update(snapshot) {
    if (
      !current ||
      snapshot?.run_id !== current.run_id ||
      snapshot.revision < current.revision
    )
      return;
    if (["finished", "stopped", "superseded"].includes(snapshot.state)) {
      reset();
      return;
    }
    current = snapshot;
    render();
    onChange(current);
  }

  function reset() {
    generation += 1;
    current = null;
    busy = false;
    render();
    onChange(null);
  }

  button.addEventListener("click", async () => {
    if (!current || busy) return;
    const requestGeneration = generation;
    const action = ["paused", "pausing"].includes(current.state)
      ? "resume"
      : "pause";
    busy = true;
    render();
    try {
      const response = await fetch("./api/simulation/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: current.run_id, action }),
      });
      const payload = await response.json();
      if (requestGeneration !== generation) return;
      if (!response.ok || !payload.ok)
        throw new Error(payload.error || "仿真控制失败，请重试。");
      update(payload.simulation);
    } catch (error) {
      if (requestGeneration !== generation) return;
      feedback.hidden = false;
      feedback.textContent = `操作未确认：${error.message}`;
    } finally {
      if (requestGeneration === generation) {
        busy = false;
        button.disabled = !current;
        button.textContent =
          current && ["paused", "pausing"].includes(current.state)
            ? "继续仿真"
            : "暂停仿真";
      }
    }
  });

  return {
    reset,
    update,
    start(snapshot) {
      reset();
      if (snapshot?.run_id) {
        current = snapshot;
        update(snapshot);
      }
    },
  };
}
