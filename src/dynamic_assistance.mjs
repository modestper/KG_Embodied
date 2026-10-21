export const assistanceVisuals = Object.freeze({
  requester: Object.freeze({
    borderColor: "#cf222e",
    rowClass: "agent-row--requesting-help",
    label: "求助无人机",
  }),
  helper: Object.freeze({
    borderColor: "#1a7f37",
    rowClass: "agent-row--assisting",
    label: "援助无人机",
  }),
});

export function assistanceRoleForDrone(drone, taskStates = {}) {
  if (!drone) return null;

  const activeTasks = Object.values(taskStates).filter((task) => task.status !== "completed");
  const isActiveRequester = activeTasks.some(
    (task) => task.assistance_source_drone_id === drone.id,
  );
  if (drone.failure_mode || drone.takeover_mode === "requesting_help" || isActiveRequester) {
    return "requester";
  }

  const isActiveHelper = activeTasks.some(
    (task) =>
      task.assistance_source_drone_id &&
      task.assistance_source_drone_id !== drone.id &&
      task.assigned_to === drone.id,
  );
  if (drone.takeover_mode === "assisting" || isActiveHelper) return "helper";

  return null;
}

export function frameHasActiveAssistance(frame) {
  return (frame?.drones ?? []).some(
    (drone) => assistanceRoleForDrone(drone, frame?.task_states) !== null,
  );
}
