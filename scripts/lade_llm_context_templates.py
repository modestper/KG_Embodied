"""Drone-scoped knowledge lookups used to prepare a decision context.

The built-in catalog targets the observed delivery graph, not every structure
supported by newer simulation writers. Current Task/Drone properties do not
reconstruct historical task ownership. No assistance events/edges are assumed.
"""

import math
import re


CATALOG_VERSION = "i56-observed-graph-v2"

# Observed on 57687 / neo4j / hangzhou-0621-headless-002. This is a
# conservative contract, not a claim that every node has every property.
GRAPH_NODE_PROPERTIES = {
    "Run": frozenset("id".split()),
    "Region": frozenset("id run_id city depot_x depot_y".split()),
    "Drone": frozenset("id run_id region_id x y status capacity queue_capacity cargo_capacity "
                       "queue_len onboard_count completed_count speed_m_per_minute battery_range_m "
                       "battery_capacity_m battery_remaining_m low_battery_threshold_m safety_buffer_m "
                       "total_distance_m updated_at".split()),
    "Task": frozenset("id run_id city region_id aoi_id release_minute deadline_minute pickup_x "
                      "pickup_y dropoff_x dropoff_y status assigned_to picked_up pickup_time "
                      "onboard_drone_id completed_at late".split()),
    "Event": frozenset("id run_id type time_minute time region_id task_id drone_id queue_len "
                       "onboard_count late status battery_remaining_m".split()),
    "Step": frozenset("id run_id step_index time_minute time completed_tasks unfinished_tasks "
                      "idle_drones delivering_drones returning_drones charging_drones "
                      "low_battery_drones total_distance_m".split()),
    "AgentState": frozenset("id run_id step_index time_minute drone_id region_id x y status "
                            "battery_remaining_m queue_len onboard_count total_distance_m".split()),
}
GRAPH_RELATIONSHIP_PROPERTIES = {
    name: frozenset() for name in (
        "IN_RUN", "IN_STEP", "OF_DRONE", "HOME_REGION", "CURRENTLY_IN",
        "BELONGS_TO", "ABOUT_DRONE", "ABOUT_TASK", "IN_REGION",
    )
}
GRAPH_RELATIONSHIP_PROPERTIES.update({
    "ASSIGNED": frozenset("time_minute time".split()),
    "CARRYING": frozenset("active pickup_time_minute pickup_time completed_time_minute completed_time".split()),
    "COMPLETED": frozenset("time_minute time late".split()),
})
GRAPH_EVENT_TYPES = frozenset((
    "task_assigned", "task_picked_up", "task_completed",
    "drone_low_battery", "drone_returning_to_charge", "drone_charged",
))


CONTEXT_PARAM_DEFAULTS = {
    "radius_m": 5000.0,
    "min_battery_m": 0.0,
    "max_state_age_steps": 10,
    "movement_epsilon_m": 1.0,
}


BASE_TEMPORAL_SCOPES = {
    "i0": "current",
    "i1": "snapshot",
    "i2": "snapshot",
    "i3": "current",
    "i4": "event_window",
    "i5": "event_window",
    "i6": "snapshot",
    "i7": "snapshot",
    "i8": "current_with_snapshot",
    "i9": "snapshot",
    "i10": "snapshot_region_events",
    "i11": "snapshot",
}


def distance(x1, y1, x2, y2):
    return f"sqrt((({x1})-({x2}))*(({x1})-({x2}))+(({y1})-({y2}))*(({y1})-({y2})))"


SELF_CURRENT = """
MATCH (self:Drone {run_id: $run_id, id: $drone_id})
"""

SELF_STATE = """
MATCH (self:AgentState {run_id: $run_id, drone_id: $drone_id})
WHERE self.step_index <= $step_index
WITH self ORDER BY self.step_index DESC LIMIT 1
"""

ACTIVE_TASKS = SELF_CURRENT + """
MATCH (self)-[rel:ASSIGNED|CARRYING]->(t:Task {run_id: $run_id})
WHERE coalesce(rel.active, true) = true
  AND coalesce(t.status, "") <> "completed"
  AND t.release_minute <= $time_minute
  AND (t.assigned_to = $drone_id OR t.onboard_drone_id = $drone_id
       OR (t.assigned_to IS NULL AND t.onboard_drone_id IS NULL))
WITH DISTINCT self, t
"""

TASK_GEOMETRY = """
WITH self, t,
     CASE WHEN t.picked_up = true AND t.onboard_drone_id = $drone_id
          THEN self.x ELSE t.pickup_x END AS pickup_x,
     CASE WHEN t.picked_up = true AND t.onboard_drone_id = $drone_id
          THEN self.y ELSE t.pickup_y END AS pickup_y
"""

SELF_EVENTS = """
MATCH (e:Event {run_id: $run_id, drone_id: $drone_id})
WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
"""

SNAPSHOT_PEERS = SELF_STATE + """
MATCH (a:AgentState {run_id: $run_id, step_index: self.step_index})
      -[:OF_DRONE]->(d:Drone {run_id: $run_id})
WHERE a.drone_id <> $drone_id
  AND a.status IN ["idle", "queued", "delivering"]
  AND d.queue_capacity IS NOT NULL AND a.queue_len < d.queue_capacity
  AND a.battery_remaining_m >= $min_battery_m
WITH self, a, d, """ + distance("self.x", "self.y", "a.x", "a.y") + " AS distance_m\n"


def spec(query_id, name, category, temporal_scope, description, cypher):
    return {
        "id": query_id,
        "name": name,
        "category": category,
        "temporal_scope": temporal_scope,
        "description": description,
        "required_params": tuple(sorted(set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", cypher)))),
        "cypher": cypher,
    }


EXTENDED_QUERY_SPECS = (
    spec("i12", "self_capability_limits", "capability", "current",
         "读取自身队列/载货容量、速度和电量配置，给 LLM 提供行动约束。",
         SELF_CURRENT + """
         RETURN self.id AS drone_id, self.queue_capacity AS queue_capacity,
                self.cargo_capacity AS cargo_capacity,
                self.speed_m_per_minute AS speed_m_per_minute,
                self.battery_capacity_m AS battery_capacity_m,
                self.low_battery_threshold_m AS low_battery_threshold_m,
                self.safety_buffer_m AS safety_buffer_m,
                self.queue_capacity - self.queue_len AS free_queue_slots,
                self.cargo_capacity - self.onboard_count AS free_cargo_slots
         LIMIT 1
         """),
    spec("i13", "self_snapshot_freshness", "readiness", "snapshot",
         "返回自身实际快照 step 和年龄，避免 LLM 把旧状态当成当前状态。",
         SELF_STATE + """
         RETURN self.drone_id AS drone_id, $step_index AS requested_step,
                self.step_index AS resolved_step, self.time_minute AS state_time_minute,
                $step_index - self.step_index AS state_age_steps,
                $step_index - self.step_index <= $max_state_age_steps AS fresh_enough
         LIMIT 1
         """),
    spec("i14", "self_recent_state_changes", "memory", "snapshot",
         "对比近期窗口首末状态，压缩自身位置、队列、载货和电量变化。",
         """
         MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
         WHERE a.step_index >= $start_step AND a.step_index <= $step_index
         WITH a ORDER BY a.step_index
         WITH collect(a) AS states
         WITH head(states) AS first, last(states) AS latest, size(states) AS samples
         RETURN samples, first.step_index AS first_step, latest.step_index AS last_step,
                first.status AS previous_status, latest.status AS latest_status,
                latest.x - first.x AS delta_x, latest.y - first.y AS delta_y,
                latest.queue_len - first.queue_len AS queue_delta,
                latest.onboard_count - first.onboard_count AS onboard_delta,
                latest.battery_remaining_m - first.battery_remaining_m AS battery_delta_m,
                latest.total_distance_m - first.total_distance_m AS distance_delta_m
         """),
    spec("i15", "self_recent_status_samples", "memory", "snapshot",
         "按状态汇总自身历史样本，帮助 LLM 判断持续空闲或持续忙碌；不是连续时长。",
         """
         MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
         WHERE a.step_index >= $start_step AND a.step_index <= $step_index
         RETURN a.status AS status, count(*) AS samples,
                min(a.step_index) AS first_step, max(a.step_index) AS last_step,
                avg(a.queue_len) AS avg_queue_len
         ORDER BY samples DESC, status LIMIT $limit
         """),
    spec("i16", "self_stationary_evidence", "memory", "snapshot",
         "返回自身窗口内坐标包围范围和任务负载，辅助判断是否需要排查停滞。",
         """
         MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
         WHERE a.step_index >= $start_step AND a.step_index <= $step_index
         WITH count(a) AS samples, min(a.x) AS min_x, max(a.x) AS max_x,
              min(a.y) AS min_y, max(a.y) AS max_y,
              max(a.queue_len) AS max_queue_len, max(a.onboard_count) AS max_onboard_count
         WITH *, sqrt((max_x-min_x)*(max_x-min_x)+(max_y-min_y)*(max_y-min_y)) AS position_span_m
         RETURN samples, position_span_m, max_queue_len, max_onboard_count,
                CASE WHEN samples < 2 THEN null
                     ELSE position_span_m <= $movement_epsilon_m END AS nearly_stationary
         """),
    spec("i17", "self_operational_status", "recovery", "current",
         "读取自身现有运行状态和负载，识别返航/充电；不推断未记录的故障或互助模式。",
         SELF_CURRENT + """
         RETURN self.id AS drone_id, self.status AS status,
                self.queue_len AS queue_len, self.onboard_count AS onboard_count,
                self.status IN ["returning", "charging"] AS returning_or_charging,
                self.updated_at AS last_event_update_minute
         LIMIT 1
         """),
    spec("i18", "self_active_task_summary", "task_detail", "current",
         "把自身当前有效任务汇总为待取件、载货和最早截止信息。",
         SELF_CURRENT + """
         OPTIONAL MATCH (self)-[rel:ASSIGNED|CARRYING]->(t:Task {run_id: $run_id})
         WHERE coalesce(rel.active, true) = true AND coalesce(t.status, "") <> "completed"
           AND t.release_minute <= $time_minute
           AND (t.assigned_to = $drone_id OR t.onboard_drone_id = $drone_id
                OR (t.assigned_to IS NULL AND t.onboard_drone_id IS NULL))
         RETURN self.id AS drone_id, count(DISTINCT t) AS active_tasks,
                count(DISTINCT CASE WHEN coalesce(t.picked_up, false) = false THEN t END) AS pending_pickups,
                count(DISTINCT CASE WHEN t.onboard_drone_id = $drone_id THEN t END) AS onboard_tasks,
                min(t.deadline_minute) AS earliest_deadline_minute
         """),
    spec("i19", "self_pending_pickup_locations", "task_detail", "current",
         "查询自身待取任务在现有 Task 上记录的取件点。",
         ACTIVE_TASKS + """
         WHERE coalesce(t.picked_up, false) = false
         RETURN t.id AS task_id, t.deadline_minute AS deadline_minute,
                t.pickup_x AS pickup_x, t.pickup_y AS pickup_y,
                t.release_minute AS release_minute
         ORDER BY t.deadline_minute, t.id LIMIT $limit
         """),
    spec("i20", "self_onboard_delivery_targets", "task_detail", "current",
         "读取自身实际携带的未完成任务和投递目标，避免把历史 CARRYING 当作当前载货。",
         ACTIVE_TASKS + """
         WHERE t.picked_up = true AND t.onboard_drone_id = $drone_id
         RETURN t.id AS task_id, t.dropoff_x AS dropoff_x, t.dropoff_y AS dropoff_y,
                t.deadline_minute AS deadline_minute, t.pickup_time AS pickup_time
         ORDER BY t.deadline_minute, t.id LIMIT $limit
         """),
    spec("i21", "self_urgent_task_slack", "task_risk", "current",
         "查询自身即将到期及已逾期任务的剩余时间，辅助 LLM 确定优先级。",
         ACTIVE_TASKS + """
         WHERE t.deadline_minute <= $time_minute + $deadline_horizon_minutes
         RETURN t.id AS task_id, t.status AS task_status,
                t.deadline_minute - $time_minute AS slack_minutes,
                t.onboard_drone_id = $drone_id AS onboard,
                t.release_minute AS release_minute
         ORDER BY slack_minutes, t.id LIMIT $limit
         """),
    spec("i22", "self_overdue_active_tasks", "task_risk", "current",
         "定位自身当前仍需处理的逾期任务，用于补救和求助判断。",
         ACTIVE_TASKS + """
         WHERE t.deadline_minute < $time_minute
         RETURN t.id AS task_id, t.status AS task_status,
                $time_minute - t.deadline_minute AS overdue_minutes,
                t.onboard_drone_id AS onboard_drone_id,
                t.release_minute AS release_minute
         ORDER BY overdue_minutes DESC, t.id LIMIT $limit
         """),
    spec("i23", "self_task_action_candidates", "task_detail", "current",
         "给出自身任务对应的取件/投递候选动作及直线距离，不假定图谱记录了完整队列顺序。",
         ACTIVE_TASKS + TASK_GEOMETRY + """
         WITH self, t, pickup_x, pickup_y,
              CASE WHEN t.picked_up = true AND t.onboard_drone_id = $drone_id
                   THEN t.dropoff_x ELSE pickup_x END AS target_x,
              CASE WHEN t.picked_up = true AND t.onboard_drone_id = $drone_id
                   THEN t.dropoff_y ELSE pickup_y END AS target_y
         RETURN t.id AS task_id,
                CASE WHEN t.picked_up = true AND t.onboard_drone_id = $drone_id THEN "deliver"
                     ELSE "pickup" END AS action,
                target_x, target_y,
                """ + distance("self.x", "self.y", "target_x", "target_y") + """ AS distance_m,
                t.deadline_minute - $time_minute AS slack_minutes
         ORDER BY t.picked_up DESC, t.deadline_minute, t.id LIMIT $limit
         """),
    spec("i24", "self_direct_completion_estimates", "task_risk", "current",
         "估算自身每个任务的直达完成时间下界；不包含队列绕行和等待，供 LLM 识别明显风险。",
         ACTIVE_TASKS + TASK_GEOMETRY + """
         WITH self, t, """ + distance("self.x", "self.y", "pickup_x", "pickup_y") + " + " +
         distance("pickup_x", "pickup_y", "t.dropoff_x", "t.dropoff_y") + """ AS direct_distance_m
         WITH self, t, direct_distance_m,
              CASE WHEN self.speed_m_per_minute > 0
                   THEN $time_minute + direct_distance_m / self.speed_m_per_minute END AS earliest_completion_minute
         RETURN t.id AS task_id, direct_distance_m, earliest_completion_minute,
                t.deadline_minute AS deadline_minute,
                earliest_completion_minute > t.deadline_minute AS direct_route_already_late,
                "direct_route_lower_bound" AS estimate_scope
         ORDER BY t.deadline_minute, t.id LIMIT $limit
         """),
    spec("i25", "self_active_task_event_context", "task_detail", "current_task_events",
         "读取自身当前任务的近期分配、取件和完成事件及真实执行者。",
         ACTIVE_TASKS + """
         WITH t ORDER BY t.deadline_minute, t.id LIMIT $limit
         MATCH (e:Event {run_id: $run_id})-[:ABOUT_TASK]->(t)
         WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
         RETURN t.id AS task_id, e.type AS event_type, e.time_minute AS time_minute,
                e.drone_id AS actor_drone_id, e.late AS late
         ORDER BY e.time_minute DESC, t.id LIMIT $limit
         """),
    spec("i26", "self_recent_assignments", "task_history", "event_window",
         "读取自身近期真实派单事件及当时队列长度。",
         """
         MATCH (e:Event {run_id: $run_id, drone_id: $drone_id, type: "task_assigned"})
         WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
         RETURN e.task_id AS task_id, e.time_minute AS assigned_at,
                e.queue_len AS queue_len_at_assignment, e.region_id AS region_id
         ORDER BY assigned_at DESC, task_id LIMIT $limit
         """),
    spec("i27", "self_recent_pickups", "task_history", "event_window",
         "读取自身近期真实取件事件及当时载货数量。",
         """
         MATCH (e:Event {run_id: $run_id, drone_id: $drone_id, type: "task_picked_up"})
         WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
         RETURN e.task_id AS task_id, e.time_minute AS picked_up_at,
                e.onboard_count AS onboard_count_at_pickup, e.region_id AS region_id
         ORDER BY picked_up_at DESC, task_id LIMIT $limit
         """),
    spec("i28", "self_pending_pickup_ages", "task_risk", "current_task_events",
         "为自身当前待取任务查找此前最近派单时间，识别等待取件较久的任务。",
         ACTIVE_TASKS + """
         WHERE coalesce(t.picked_up, false) = false
         WITH t ORDER BY t.deadline_minute, t.id LIMIT $limit
         CALL (t) {
           OPTIONAL MATCH (assigned:Event {run_id:$run_id, drone_id:$drone_id,
                                          task_id:t.id, type:"task_assigned"})
           WHERE assigned.time_minute <= $time_minute
           RETURN max(assigned.time_minute) AS assigned_at
         }
         RETURN t.id AS task_id, assigned_at,
                $time_minute - assigned_at AS observed_wait_minutes,
                t.deadline_minute - $time_minute AS slack_minutes
         ORDER BY observed_wait_minutes DESC, task_id
         """),
    spec("i29", "self_onboard_delivery_ages", "task_risk", "current",
         "读取自身当前载货任务的已观察配送时间和剩余截止时间。",
         ACTIVE_TASKS + """
         WHERE t.picked_up = true AND t.onboard_drone_id = $drone_id
           AND t.pickup_time <= $time_minute
         RETURN t.id AS task_id, t.pickup_time AS picked_up_at,
                $time_minute - t.pickup_time AS onboard_minutes,
                t.deadline_minute - $time_minute AS slack_minutes,
                t.dropoff_x AS dropoff_x, t.dropoff_y AS dropoff_y
         ORDER BY slack_minutes, task_id LIMIT $limit
         """),
    spec("i30", "self_recent_completed_tasks", "task_history", "event_window",
         "以真实完成事件列出自身近期送达任务与 deadline，不以 Task 最新完成状态替代历史。",
         """
         MATCH (done:Event {run_id:$run_id, drone_id:$drone_id, type:"task_completed"})
               -[:ABOUT_TASK]->(t:Task {run_id:$run_id})
         WHERE done.time_minute >= $start_time AND done.time_minute <= $time_minute
         RETURN t.id AS task_id, done.time_minute AS completed_at, done.late AS late,
                t.deadline_minute AS deadline_minute, t.region_id AS region_id
         ORDER BY completed_at DESC, task_id LIMIT $limit
         """),
    spec("i31", "self_recent_event_type_summary", "task_history", "event_window",
         "按实际事件类型汇总自身近期活动，不假定存在协助事件。",
         SELF_EVENTS + """
         RETURN e.type AS event_type, count(*) AS events,
                count(DISTINCT e.task_id) AS tasks, max(e.time_minute) AS latest_time_minute
         ORDER BY events DESC, event_type LIMIT $limit
         """),
    spec("i32", "self_nearby_available_peers", "peer", "snapshot_with_config",
         "按距离、已知队列容量和电量筛选附近无人机；仅是候选信息，不声称已派遣协助。",
         SNAPSHOT_PEERS + """
         WHERE distance_m <= $radius_m
         RETURN a.drone_id AS peer_drone_id, self.step_index AS resolved_step,
                a.region_id AS region_id, a.status AS status, distance_m,
                a.queue_len AS queue_len, d.queue_capacity - a.queue_len AS free_queue_slots,
                a.battery_remaining_m AS battery_remaining_m
         ORDER BY distance_m, a.queue_len, peer_drone_id LIMIT $limit
         """),
    spec("i33", "self_task_peer_route_estimates", "peer", "current",
         "用已有任务和无人机位置/电量估算其他无人机的单任务直达路线；不是实际协助关系或完整队列可行性。",
         ACTIVE_TASKS + TASK_GEOMETRY + """
         WITH self, t, pickup_x, pickup_y ORDER BY t.deadline_minute, t.id LIMIT $limit
         CALL (t) {
           MATCH (r:Region {run_id: $run_id})
           WHERE r.depot_x IS NOT NULL AND r.depot_y IS NOT NULL
           RETURN min(""" + distance("t.dropoff_x", "t.dropoff_y", "r.depot_x", "r.depot_y") + """
                     ) AS return_distance_m
         }
         MATCH (peer:Drone {run_id: $run_id})
         WHERE peer.id <> $drone_id AND peer.status IN ["idle", "queued", "delivering"]
           AND peer.queue_len < peer.queue_capacity AND peer.speed_m_per_minute > 0
           AND peer.battery_remaining_m >= $min_battery_m
         WITH self, t, peer, return_distance_m,
              """ + distance("peer.x", "peer.y", "pickup_x", "pickup_y") + """ AS to_pickup_m,
              """ + distance("pickup_x", "pickup_y", "t.dropoff_x", "t.dropoff_y") + """ AS delivery_m
         WHERE to_pickup_m <= $radius_m
         WITH self, t, peer, to_pickup_m, delivery_m,
              to_pickup_m + delivery_m + return_distance_m + peer.safety_buffer_m AS individual_required_m,
              $time_minute + (to_pickup_m + delivery_m) / peer.speed_m_per_minute AS earliest_completion_minute
         WHERE individual_required_m <= peer.battery_remaining_m
         RETURN t.id AS task_id, peer.id AS candidate_drone_id,
                peer.region_id = t.region_id AS same_region, to_pickup_m,
                individual_required_m, peer.battery_remaining_m - individual_required_m AS individual_margin_m,
                earliest_completion_minute, t.deadline_minute AS deadline_minute,
                "individual_direct_route_not_full_queue_feasibility" AS estimate_scope
         ORDER BY t.deadline_minute, earliest_completion_minute, peer.queue_len, candidate_drone_id LIMIT $limit
         """),
    spec("i34", "self_cross_region_peer_candidates", "peer", "snapshot_with_config",
         "按同一步位置、队列和电量返回半径内其他区域的无人机候选信息。",
         SNAPSHOT_PEERS + """
         WHERE a.region_id <> self.region_id AND distance_m <= $radius_m
         RETURN a.drone_id AS peer_drone_id, self.step_index AS resolved_step,
                a.region_id AS peer_region_id, distance_m, a.queue_len AS queue_len,
                a.battery_remaining_m AS battery_remaining_m,
                d.queue_capacity - a.queue_len AS free_queue_slots
         ORDER BY distance_m, a.queue_len, peer_drone_id LIMIT $limit
         """),
    spec("i35", "self_nearby_returning_peers", "peer", "snapshot",
         "读取同一步附近正在返航/充电的无人机，避免把它们当成可立即接单资源。",
         SELF_STATE + """
         MATCH (a:AgentState {run_id:$run_id, step_index:self.step_index})
         WHERE a.drone_id <> $drone_id AND a.status IN ["returning", "charging"]
         WITH self, a, """ + distance("self.x", "self.y", "a.x", "a.y") + """ AS distance_m
         WHERE distance_m <= $radius_m
         RETURN a.drone_id AS peer_drone_id, self.step_index AS resolved_step,
                a.status AS status, distance_m, a.battery_remaining_m AS battery_remaining_m
         ORDER BY distance_m, peer_drone_id LIMIT $limit
         """),
    spec("i36", "self_region_queue_comparison", "peer", "snapshot",
         "比较自身与同一步同区域其他无人机的队列负载，不假定存在协助承诺。",
         SELF_STATE + """
         OPTIONAL MATCH (a:AgentState {run_id:$run_id, region_id:self.region_id, step_index:self.step_index})
         WHERE a.drone_id <> $drone_id
         RETURN self.drone_id AS drone_id, self.step_index AS resolved_step,
                self.queue_len AS self_queue_len, count(a) AS peers,
                avg(a.queue_len) AS peer_avg_queue_len,
                self.queue_len - avg(a.queue_len) AS queue_delta_from_peers,
                sum(CASE WHEN a.queue_len < self.queue_len THEN 1 ELSE 0 END) AS less_loaded_peers
         """),
    spec("i37", "self_recent_served_regions", "task_history", "event_window",
         "按真实完成事件汇总自身近期服务过的任务区域，而不是把区域边当实时位置。",
         SELF_EVENTS + """
         WITH e WHERE e.type = "task_completed"
         RETURN e.region_id AS region_id, count(DISTINCT e.task_id) AS completed_tasks,
                sum(CASE WHEN coalesce(e.late,false) THEN 1 ELSE 0 END) AS late,
                max(e.time_minute) AS latest_completion_minute
         ORDER BY latest_completion_minute DESC, region_id LIMIT $limit
         """),
    spec("i38", "self_known_depot_return_margin", "energy", "current",
         "结合已知 depot、当前位置和安全余量估算返航阈值；depot 不完整时仅是已知范围内结果。",
         SELF_CURRENT + """
         OPTIONAL MATCH (r:Region {run_id: $run_id})
         WHERE r.depot_x IS NOT NULL AND r.depot_y IS NOT NULL
         WITH self, r, """ + distance("self.x", "self.y", "r.depot_x", "r.depot_y") + """ AS return_distance_m
         ORDER BY return_distance_m, r.id LIMIT 1
         WITH self, r, return_distance_m,
              CASE WHEN r IS NULL OR self.safety_buffer_m IS NULL OR self.low_battery_threshold_m IS NULL THEN null
                   WHEN return_distance_m + self.safety_buffer_m > self.low_battery_threshold_m
                   THEN return_distance_m + self.safety_buffer_m
                   ELSE self.low_battery_threshold_m END AS known_return_threshold_m
         RETURN r.id AS nearest_known_depot_region_id, return_distance_m,
                self.battery_remaining_m AS battery_remaining_m, known_return_threshold_m,
                self.battery_remaining_m - known_return_threshold_m AS return_margin_m,
                self.battery_remaining_m <= known_return_threshold_m AS return_advised_by_known_depots,
                "known_graph_depots_only" AS depot_scope
         """),
    spec("i39", "self_reachable_known_depots", "energy", "current",
         "列出按自身电量和安全余量可到达的已知 depot，支持返航目的地选择。",
         SELF_CURRENT + """
         MATCH (r:Region {run_id: $run_id})
         WHERE r.depot_x IS NOT NULL AND r.depot_y IS NOT NULL
         WITH self, r, """ + distance("self.x", "self.y", "r.depot_x", "r.depot_y") + """ AS distance_m
         WHERE distance_m + self.safety_buffer_m <= self.battery_remaining_m
         RETURN r.id AS depot_region_id, r.depot_x AS depot_x, r.depot_y AS depot_y,
                distance_m, self.battery_remaining_m - distance_m - self.safety_buffer_m AS reserve_margin_m
         ORDER BY distance_m, depot_region_id LIMIT $limit
         """),
    spec("i40", "self_individual_task_energy_estimates", "energy", "current",
         "估算单项任务直达取送及最近已知 depot 的最低航程需求，不包含其他任务队列绕行。",
         ACTIVE_TASKS + TASK_GEOMETRY + """
         WITH self, t, """ + distance("self.x", "self.y", "pickup_x", "pickup_y") + " + " +
         distance("pickup_x", "pickup_y", "t.dropoff_x", "t.dropoff_y") + """ AS direct_task_distance_m
         CALL (t) {
           MATCH (r:Region {run_id: $run_id})
           WHERE r.depot_x IS NOT NULL AND r.depot_y IS NOT NULL
           RETURN min(""" + distance("t.dropoff_x", "t.dropoff_y", "r.depot_x", "r.depot_y") + """
                     ) AS known_return_distance_m
         }
         WITH self, t, direct_task_distance_m, known_return_distance_m,
              direct_task_distance_m + known_return_distance_m + self.safety_buffer_m AS individual_required_m
         RETURN t.id AS task_id, direct_task_distance_m, known_return_distance_m,
                individual_required_m, self.battery_remaining_m - individual_required_m AS individual_margin_m,
                "individual_direct_route_known_depots" AS estimate_scope
         ORDER BY individual_margin_m, t.id LIMIT $limit
         """),
    spec("i41", "self_recent_charge_cycle_events", "energy", "event_window",
         "读取自己的低电量、返航和充电完成事件，解释电量变化原因。",
         """
         MATCH (e:Event {run_id: $run_id, drone_id: $drone_id})
         WHERE e.type IN ["drone_low_battery", "drone_returning_to_charge", "drone_charged"]
           AND e.time_minute >= $start_time AND e.time_minute <= $time_minute
         RETURN e.type AS event_type, e.time_minute AS time_minute,
                e.battery_remaining_m AS battery_remaining_m, e.region_id AS region_id
         ORDER BY time_minute DESC, event_type LIMIT $limit
         """),
    spec("i42", "self_observed_battery_changes", "energy", "snapshot",
         "聚合自身相邻快照的电量升降，提供观察到的放电和恢复证据，不推断缺失步。",
         """
         MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
         WHERE a.step_index >= $start_step AND a.step_index < $step_index
         MATCH (b:AgentState {run_id: $run_id, drone_id: $drone_id})
         WHERE b.step_index = a.step_index + 1
         RETURN count(*) AS adjacent_pairs,
                sum(CASE WHEN a.battery_remaining_m > b.battery_remaining_m
                         THEN a.battery_remaining_m - b.battery_remaining_m ELSE 0 END) AS observed_discharge_m,
                sum(CASE WHEN b.battery_remaining_m > a.battery_remaining_m
                         THEN b.battery_remaining_m - a.battery_remaining_m ELSE 0 END) AS observed_recovery_m,
                sum(CASE WHEN b.battery_remaining_m > a.battery_remaining_m THEN 1 ELSE 0 END) AS recovery_steps
         """),
    spec("i43", "self_last_known_charge", "energy", "event_window",
         "读取截至决策时间的最近充电完成记录，判断距上次充电多久；无记录表示未知。",
         """
         MATCH (e:Event {run_id: $run_id, drone_id: $drone_id, type: "drone_charged"})
         WHERE e.time_minute <= $time_minute
         RETURN e.time_minute AS last_charge_minute,
                $time_minute - e.time_minute AS minutes_since_charge,
                e.battery_remaining_m AS charged_battery_m, e.region_id AS region_id
         ORDER BY last_charge_minute DESC LIMIT 1
         """),
    spec("i44", "self_region_known_task_pressure", "local_context", "current",
         "统计自己当前区域内图谱已知的未完成、逾期与待取件任务，不声称覆盖尚未入图任务。",
         SELF_CURRENT + """
         OPTIONAL MATCH (t:Task {run_id: $run_id, region_id: self.region_id})
         WHERE coalesce(t.status, "") <> "completed" AND t.release_minute <= $time_minute
         RETURN self.region_id AS region_id, count(t) AS known_open_tasks,
                sum(CASE WHEN t.deadline_minute < $time_minute THEN 1 ELSE 0 END) AS overdue_tasks,
                sum(CASE WHEN t.deadline_minute >= $time_minute
                              AND t.deadline_minute <= $time_minute + $deadline_horizon_minutes THEN 1 ELSE 0 END) AS due_soon_tasks,
                sum(CASE WHEN t IS NOT NULL AND coalesce(t.picked_up,false) = false THEN 1 ELSE 0 END) AS pending_pickups,
                min(t.deadline_minute) AS earliest_deadline_minute
         """),
    spec("i45", "self_region_available_queue_capacity", "local_context", "snapshot_with_config",
         "汇总自身区域其他无人机的已知可用队列容量，提供区域资源信息。",
         SELF_STATE + """
         OPTIONAL MATCH (a:AgentState {run_id: $run_id, step_index: self.step_index, region_id: self.region_id})
                        -[:OF_DRONE]->(d:Drone {run_id: $run_id})
         WHERE a.drone_id <> $drone_id
         RETURN self.region_id AS region_id, self.step_index AS resolved_step,
                count(a) AS peer_agents, count(d.queue_capacity) AS peers_with_capacity_config,
                sum(CASE WHEN a.status IN ["idle", "queued", "delivering"]
                              AND a.queue_len < d.queue_capacity
                         THEN 1 ELSE 0 END) AS available_peers,
                sum(CASE WHEN a.status IN ["idle", "queued", "delivering"]
                              AND a.queue_len < d.queue_capacity
                         THEN d.queue_capacity - a.queue_len ELSE 0 END) AS known_free_queue_slots
         """),
    spec("i46", "self_region_recent_completion_quality", "local_context", "current_region_events",
         "汇总自身当前区域近期完成与迟到情况，给 LLM 一个局部服务质量视角。",
         SELF_CURRENT + """
         OPTIONAL MATCH (e:Event {run_id: $run_id, region_id: self.region_id, type: "task_completed"})
         WHERE e.time_minute >= $start_time AND e.time_minute <= $time_minute
         WITH self, count(e) AS completed,
              sum(CASE WHEN coalesce(e.late, false) THEN 1 ELSE 0 END) AS late
         RETURN self.region_id AS region_id, completed, late,
                CASE WHEN completed = 0 THEN null ELSE toFloat(late) / completed END AS late_fraction
         """),
    spec("i47", "self_region_recent_task_flow", "local_context", "current_region_events",
         "统计自己区域近期真实派单、取件和送达事件及参与无人机数量。",
         SELF_CURRENT + """
         OPTIONAL MATCH (e:Event {run_id: $run_id, region_id: self.region_id})
         WHERE e.type IN ["task_assigned", "task_picked_up", "task_completed"]
           AND e.time_minute >= $start_time AND e.time_minute <= $time_minute
         RETURN self.region_id AS region_id,
                sum(CASE WHEN e.type = "task_assigned" THEN 1 ELSE 0 END) AS assigned,
                sum(CASE WHEN e.type = "task_picked_up" THEN 1 ELSE 0 END) AS picked_up,
                sum(CASE WHEN e.type = "task_completed" THEN 1 ELSE 0 END) AS completed,
                count(DISTINCT e.drone_id) AS participating_drones
         """),
    spec("i48", "self_nearby_known_open_tasks", "local_context", "current",
         "读取自身附近图谱已知的未完成紧急任务及承担者，不假定等待任务已入图或可以重复派单。",
         SELF_CURRENT + """
         MATCH (t:Task {run_id: $run_id})
         WHERE coalesce(t.status, "") <> "completed" AND t.release_minute <= $time_minute
           AND t.deadline_minute <= $time_minute + $deadline_horizon_minutes
         WITH self, t, """ + distance("self.x", "self.y", "t.pickup_x", "t.pickup_y") + """ AS pickup_distance_m
         WHERE pickup_distance_m <= $radius_m
         RETURN t.id AS task_id, t.region_id AS region_id, pickup_distance_m,
                t.deadline_minute - $time_minute AS slack_minutes,
                t.pickup_x AS pickup_x, t.pickup_y AS pickup_y,
                t.assigned_to AS assigned_to, t.onboard_drone_id AS onboard_drone_id
         ORDER BY slack_minutes, pickup_distance_m, task_id LIMIT $limit
         """),
    spec("i49", "self_nearby_low_battery_peers", "recovery", "snapshot_with_config",
         "按同一步电量和已知低电基准阈值识别附近低电无人机，不替代动态返航判据。",
         SELF_STATE + """
         MATCH (a:AgentState {run_id: $run_id, step_index: self.step_index})
               -[:OF_DRONE]->(d:Drone {run_id:$run_id})
         WHERE a.drone_id <> $drone_id AND a.battery_remaining_m <= d.low_battery_threshold_m
         WITH self, a, d, """ + distance("self.x", "self.y", "a.x", "a.y") + """ AS distance_m
         WHERE distance_m <= $radius_m
         RETURN a.drone_id AS peer_drone_id, self.step_index AS resolved_step,
                a.region_id AS region_id, distance_m, a.status AS status,
                a.battery_remaining_m AS battery_remaining_m,
                d.low_battery_threshold_m AS baseline_threshold_m
         ORDER BY distance_m, peer_drone_id LIMIT $limit
         """),
    spec("i50", "self_assignment_to_pickup_feedback", "feedback", "event_window",
         "为近期派单匹配下次同任务派单前的首次取件，观察实际等待时间；未取件不等于失败。",
         """
         MATCH (assigned:Event {run_id:$run_id, drone_id:$drone_id, type:"task_assigned"})
         WHERE assigned.time_minute >= $start_time AND assigned.time_minute <= $time_minute
         WITH assigned ORDER BY assigned.time_minute DESC, assigned.id LIMIT $limit
         CALL (assigned) {
           OPTIONAL MATCH (later:Event {run_id:$run_id, task_id:assigned.task_id, type:"task_assigned"})
           WHERE later.time_minute > assigned.time_minute AND later.time_minute <= $time_minute
           RETURN min(later.time_minute) AS next_assignment_minute
         }
         CALL (assigned, next_assignment_minute) {
           OPTIONAL MATCH (pickup:Event {run_id:$run_id, drone_id:$drone_id,
                                        task_id:assigned.task_id, type:"task_picked_up"})
           WHERE pickup.time_minute >= assigned.time_minute AND pickup.time_minute <= $time_minute
             AND (next_assignment_minute IS NULL OR pickup.time_minute < next_assignment_minute)
           RETURN min(pickup.time_minute) AS picked_up_at
         }
         RETURN assigned.task_id AS task_id, assigned.time_minute AS assigned_at, picked_up_at,
                picked_up_at - assigned.time_minute AS pickup_wait_minutes,
                CASE WHEN picked_up_at IS NOT NULL THEN "picked_up"
                     WHEN next_assignment_minute IS NOT NULL THEN "reassigned"
                     ELSE "awaiting_pickup" END AS observation_status,
                CASE WHEN picked_up_at IS NULL THEN coalesce(next_assignment_minute,$time_minute)
                     - assigned.time_minute END AS no_pickup_observed_for_minutes
         ORDER BY assigned_at DESC, task_id
         """),
    spec("i51", "self_release_to_completion_feedback", "feedback", "event_window",
         "根据真实完成事件与 Task 释放/deadline 时间回顾端到端耗时和逾期分钟。",
         """
         MATCH (done:Event {run_id:$run_id, drone_id:$drone_id, type:"task_completed"})
               -[:ABOUT_TASK]->(t:Task {run_id:$run_id})
         WHERE done.time_minute >= $start_time AND done.time_minute <= $time_minute
         RETURN t.id AS task_id, t.release_minute AS release_minute,
                done.time_minute AS completed_at, done.late AS late,
                done.time_minute - t.release_minute AS release_to_completion_minutes,
                CASE WHEN done.time_minute > t.deadline_minute
                     THEN done.time_minute - t.deadline_minute ELSE 0 END AS overdue_minutes
         ORDER BY completed_at DESC, task_id LIMIT $limit
         """),
    spec("i52", "self_current_snapshot_comparison", "readiness", "current_with_snapshot",
         "并列返回自身当前属性与目标 step 快照的差异，提示 LLM 时间混用，不直接判为数据错误。",
         SELF_CURRENT + """
         OPTIONAL MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
         WHERE a.step_index <= $step_index
         WITH self, a ORDER BY a.step_index DESC LIMIT 1
         RETURN self.id AS drone_id, a.step_index AS snapshot_step,
                self.status AS current_status, a.status AS snapshot_status,
                self.queue_len AS current_queue_len, a.queue_len AS snapshot_queue_len,
                self.onboard_count AS current_onboard_count, a.onboard_count AS snapshot_onboard_count,
                self.battery_remaining_m AS current_battery_m, a.battery_remaining_m AS snapshot_battery_m,
                "current_and_snapshot_may_differ_legitimately" AS interpretation
         """),
    spec("i53", "self_recent_snapshot_coverage", "readiness", "snapshot",
         "检查自己的短期记忆窗口中有多少不同 step 样本，避免把缺样本当成没有行动。",
         """
         MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
         WHERE a.step_index >= $start_step AND a.step_index <= $step_index
         RETURN $drone_id AS drone_id, count(a) AS samples,
                count(DISTINCT a.step_index) AS distinct_steps,
                min(a.step_index) AS first_step, max(a.step_index) AS last_step,
                $step_index - $start_step + 1 - count(DISTINCT a.step_index) AS missing_step_samples
         """),
    spec("i54", "self_recent_delivery_feedback", "feedback", "event_window",
         "把自己的完成事件与此前最近取件事件关联，回顾实际投递耗时及迟到情况。",
         """
         MATCH (done:Event {run_id: $run_id, drone_id: $drone_id, type: "task_completed"})
         WHERE done.time_minute >= $start_time AND done.time_minute <= $time_minute
         WITH done ORDER BY done.time_minute DESC, done.id LIMIT $limit
         CALL (done) {
           OPTIONAL MATCH (pickup:Event {run_id: $run_id, drone_id: $drone_id,
                                        task_id: done.task_id, type: "task_picked_up"})
           WHERE pickup.time_minute <= done.time_minute
           RETURN max(pickup.time_minute) AS picked_up_at
         }
         RETURN done.task_id AS task_id, picked_up_at, done.time_minute AS completed_at,
                done.time_minute - picked_up_at AS observed_delivery_minutes,
                done.late AS late, done.region_id AS region_id
         ORDER BY completed_at DESC, task_id
         """),
    spec("i55", "self_decision_data_readiness", "readiness", "snapshot_with_config",
         "检查自身实体、快照新鲜度及容量/速度配置是否齐备，返回可解释的决策数据缺口。",
         """
         OPTIONAL MATCH (d:Drone {run_id: $run_id, id: $drone_id})
         OPTIONAL MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
         WHERE a.step_index <= $step_index
         WITH d, a ORDER BY a.step_index DESC LIMIT 1
         RETURN $drone_id AS drone_id, d IS NOT NULL AS drone_exists,
                a IS NOT NULL AS snapshot_exists, a.step_index AS resolved_step,
                $step_index - a.step_index AS state_age_steps,
                CASE WHEN a IS NULL THEN false
                     ELSE $step_index - a.step_index <= $max_state_age_steps END AS snapshot_fresh,
                d.queue_capacity IS NOT NULL AS queue_capacity_known,
                d.cargo_capacity IS NOT NULL AS cargo_capacity_known,
                coalesce(d.speed_m_per_minute > 0, false) AS positive_speed_known,
                a.battery_remaining_m IS NOT NULL AS battery_known,
                "readiness_is_not_action_authorization" AS interpretation
         """),
)


EXTENDED_QUERY_PACKS = {
    "task_decision": ("i1", "i12", "i18", "i19", "i20", "i21", "i23", "i24", "i40"),
    "energy_safety": ("i1", "i12", "i38", "i39", "i40", "i41", "i42", "i43"),
    # Decision inputs only: neither pack claims that an assistance edge exists.
    "request_help": ("i1", "i17", "i21", "i22", "i28", "i32", "i33", "i34", "i36", "i50"),
    "provide_help": ("i1", "i12", "i17", "i29", "i36", "i45", "i48", "i49"),
    "task_progress": ("i1", "i19", "i25", "i26", "i27", "i28", "i29", "i30", "i50", "i51"),
    "recovery": ("i1", "i13", "i16", "i17", "i22", "i31", "i38", "i49", "i55"),
    "memory_review": ("i2", "i5", "i14", "i15", "i25", "i31", "i37", "i51", "i54"),
    "local_awareness": ("i1", "i6", "i10", "i32", "i35", "i44", "i45", "i46", "i47"),
    "data_readiness": ("i0", "i1", "i13", "i52", "i53", "i55"),
}


def build_extended_queries(query_type):
    return {item["id"]: query_type(**item) for item in EXTENDED_QUERY_SPECS}


def builtin_schema_violations(cypher):
    """Check our simple built-in patterns; this is not a general Cypher parser.

    Also checked against the live graph by the opt-in read-only integration suite.
    Custom --query-file templates are deliberately outside this contract.
    """
    errors = []
    # Strings must not be mistaken for node patterns or property accesses.
    code = re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', '""', cypher)
    aliases = {"first": GRAPH_NODE_PROPERTIES["AgentState"],
               "latest": GRAPH_NODE_PROPERTIES["AgentState"]}
    for match in re.finditer(r"\((\w*):(\w+)\s*(?:\{([^{}]*)\})?\)", code):
        alias, label, properties = match.groups()
        allowed = GRAPH_NODE_PROPERTIES.get(label)
        if allowed is None:
            errors.append(f"unknown label: {label}")
            continue
        if alias:
            aliases[alias] = allowed
        for key in re.findall(r"(?:^|,)\s*(\w+)\s*:", properties or ""):
            if key not in allowed:
                errors.append(f"unknown node property: {label}.{key}")
    for match in re.finditer(r"\[(\w*):([A-Z_]+(?:\|[A-Z_]+)*)\s*(?:\{([^{}]*)\})?\]", code):
        alias, names, properties = match.groups()
        allowed = set()
        for name in names.split("|"):
            if name not in GRAPH_RELATIONSHIP_PROPERTIES:
                errors.append(f"unknown relationship: {name}")
            allowed.update(GRAPH_RELATIONSHIP_PROPERTIES.get(name, ()))
        if alias:
            aliases[alias] = allowed
        for key in re.findall(r"(?:^|,)\s*(\w+)\s*:", properties or ""):
            if key not in allowed:
                errors.append(f"unknown relationship property: {names}.{key}")
    for alias, key in re.findall(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b", code):
        if alias not in aliases:
            errors.append(f"unresolved property owner: {alias}.{key}")
        elif key not in aliases[alias]:
            errors.append(f"unknown property: {alias}.{key}")
    for literal in re.findall(r'"([^"\n]*)"', cypher):
        if literal.startswith(("task_", "drone_", "assist_")) and literal not in GRAPH_EVENT_TYPES:
            errors.append(f"unknown event type: {literal}")
    return sorted(set(errors))


def validate_builtin_queries(queries):
    problems = {query.id: errors for query in queries
                if (errors := builtin_schema_violations(query.cypher))}
    if problems:
        raise ValueError(f"内置查询超出现有图谱结构，请先核对真实 schema: {problems}")


def add_context_arguments(parser):
    parser.add_argument("--radius-m", type=float, default=CONTEXT_PARAM_DEFAULTS["radius_m"],
                        help="邻近无人机/任务的查询半径，单位米")
    parser.add_argument("--min-battery-m", type=float, default=CONTEXT_PARAM_DEFAULTS["min_battery_m"],
                        help="附近无人机候选最低剩余航程；不代表已派遣协助")
    parser.add_argument("--max-state-age-steps", type=int, default=CONTEXT_PARAM_DEFAULTS["max_state_age_steps"],
                        help="判断自身快照新鲜度的最大 step 年龄")
    parser.add_argument("--movement-epsilon-m", type=float, default=CONTEXT_PARAM_DEFAULTS["movement_epsilon_m"],
                        help="自身近似静止证据的坐标范围阈值，单位米")


def add_context_params(params, args):
    for name, default in CONTEXT_PARAM_DEFAULTS.items():
        params.setdefault(name, getattr(args, name, default))
        value = params[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise SystemExit(f"上下文参数 {name} 必须是非负数")
        if name == "max_state_age_steps" and int(value) != value:
            raise SystemExit("上下文参数 max_state_age_steps 必须是非负整数")
    return params
