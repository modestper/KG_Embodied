"""Extended read-only Cypher workload for the LaDe temporal knowledge graph."""


EXTENDED_TEMPLATE_SPECS = (
    {
        "id": "q13",
        "name": "latest_step_summary",
        "category": "snapshot",
        "required_params": ("run_id",),
        "description": "读取最新 Step 汇总，作为全局态势点查基线。",
        "cypher": """
        MATCH (s:Step {run_id: $run_id})
        RETURN s.step_index AS step_index,
               s.time_minute AS time_minute,
               s.completed_tasks AS completed_tasks,
               s.unfinished_tasks AS unfinished_tasks,
               s.idle_drones AS idle_drones,
               s.delivering_drones AS delivering_drones,
               s.returning_drones AS returning_drones,
               s.charging_drones AS charging_drones,
               s.assisting_drones AS assisting_drones,
               s.low_battery_drones AS low_battery_drones,
               s.total_distance_m AS total_distance_m
        ORDER BY s.step_index DESC
        LIMIT 1
        """,
    },
    {
        "id": "q14",
        "name": "step_summary_window",
        "category": "temporal",
        "required_params": ("run_id", "start_step", "end_step", "limit"),
        "description": "读取 Step 汇总时间窗，观察全局指标随时间变化。",
        "cypher": """
        MATCH (s:Step {run_id: $run_id})
        WHERE s.step_index >= $start_step AND s.step_index <= $end_step
        RETURN s.step_index AS step_index,
               s.time_minute AS time_minute,
               s.completed_tasks AS completed_tasks,
               s.unfinished_tasks AS unfinished_tasks,
               s.idle_drones AS idle_drones,
               s.delivering_drones AS delivering_drones,
               s.charging_drones AS charging_drones,
               s.assisting_drones AS assisting_drones,
               s.low_battery_drones AS low_battery_drones,
               s.total_distance_m AS total_distance_m
        ORDER BY s.step_index
        LIMIT $limit
        """,
    },
    {
        "id": "q15",
        "name": "latest_agent_states",
        "category": "snapshot",
        "required_params": ("run_id", "limit"),
        "description": "读取最新 Step 下的多智能体状态，模拟全局协同快照。",
        "cypher": """
        MATCH (s:Step {run_id: $run_id})
        WITH max(s.step_index) AS latest_step
        MATCH (a:AgentState {run_id: $run_id})
        WHERE a.step_index = latest_step
        RETURN latest_step AS step_index,
               a.drone_id AS drone_id,
               a.region_id AS region_id,
               a.status AS status,
               a.x AS x,
               a.y AS y,
               a.queue_len AS queue_len,
               a.onboard_count AS onboard_count,
               a.battery_remaining_m AS battery_remaining_m
        ORDER BY a.drone_id
        LIMIT $limit
        """,
    },
    {
        "id": "q16",
        "name": "step_status_distribution",
        "category": "snapshot",
        "required_params": ("run_id", "step_index"),
        "description": "统计指定 Step 的智能体状态分布。",
        "cypher": """
        MATCH (a:AgentState {run_id: $run_id, step_index: $step_index})
        RETURN a.status AS status,
               count(*) AS agent_count,
               avg(a.queue_len) AS avg_queue_len,
               avg(a.onboard_count) AS avg_onboard_count,
               avg(a.battery_remaining_m) AS avg_battery_remaining_m
        ORDER BY agent_count DESC, status
        """,
    },
    {
        "id": "q17",
        "name": "low_battery_agents_at_step",
        "category": "energy",
        "required_params": ("run_id", "step_index", "limit"),
        "description": "定位指定 Step 低于各自阈值的智能体。",
        "cypher": """
        MATCH (a:AgentState {run_id: $run_id, step_index: $step_index})-[:OF_DRONE]->
              (d:Drone {run_id: $run_id})
        WHERE a.battery_remaining_m <= coalesce(d.low_battery_threshold_m, 0)
        RETURN a.drone_id AS drone_id,
               a.region_id AS region_id,
               a.status AS status,
               a.battery_remaining_m AS battery_remaining_m,
               d.low_battery_threshold_m AS threshold_m,
               a.queue_len AS queue_len,
               a.onboard_count AS onboard_count
        ORDER BY battery_remaining_m ASC
        LIMIT $limit
        """,
    },
    {
        "id": "q18",
        "name": "agent_battery_history",
        "category": "energy",
        "required_params": ("run_id", "drone_id", "start_step", "end_step"),
        "description": "读取单智能体电量历史，分析消耗与充电恢复。",
        "cypher": """
        MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
        WHERE a.step_index >= $start_step AND a.step_index <= $end_step
        RETURN a.step_index AS step_index,
               a.time_minute AS time_minute,
               a.status AS status,
               a.battery_remaining_m AS battery_remaining_m,
               a.total_distance_m AS total_distance_m
        ORDER BY a.step_index
        """,
    },
    {
        "id": "q19",
        "name": "agent_distance_history",
        "category": "temporal",
        "required_params": ("run_id", "drone_id", "start_step", "end_step"),
        "description": "读取单智能体累计里程和位置轨迹。",
        "cypher": """
        MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
        WHERE a.step_index >= $start_step AND a.step_index <= $end_step
        RETURN a.step_index AS step_index,
               a.time_minute AS time_minute,
               a.x AS x,
               a.y AS y,
               a.region_id AS region_id,
               a.total_distance_m AS total_distance_m
        ORDER BY a.step_index
        """,
    },
    {
        "id": "q20",
        "name": "high_queue_agents_at_step",
        "category": "workload",
        "required_params": ("run_id", "step_index", "limit"),
        "description": "查找指定 Step 队列压力最大的智能体。",
        "cypher": """
        MATCH (a:AgentState {run_id: $run_id, step_index: $step_index})
        RETURN a.drone_id AS drone_id,
               a.region_id AS region_id,
               a.status AS status,
               a.queue_len AS queue_len,
               a.onboard_count AS onboard_count,
               a.battery_remaining_m AS battery_remaining_m
        ORDER BY a.queue_len DESC, a.onboard_count DESC, a.battery_remaining_m ASC
        LIMIT $limit
        """,
    },
    {
        "id": "q21",
        "name": "status_transition_summary",
        "category": "temporal",
        "required_params": ("run_id", "start_step", "end_step", "limit"),
        "description": "聚合相邻 Step 的状态迁移，测试自连接和时序推理。",
        "cypher": """
        MATCH (previous:AgentState {run_id: $run_id})
        WHERE previous.step_index >= $start_step AND previous.step_index < $end_step
        MATCH (next:AgentState {run_id: $run_id})
        WHERE next.drone_id = previous.drone_id
          AND next.step_index = previous.step_index + 1
          AND coalesce(next.status, "") <> coalesce(previous.status, "")
        RETURN previous.status AS from_status,
               next.status AS to_status,
               count(*) AS transitions,
               count(DISTINCT previous.drone_id) AS agents
        ORDER BY transitions DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q22",
        "name": "agent_status_dwell",
        "category": "temporal",
        "required_params": ("run_id", "drone_id", "start_step", "end_step"),
        "description": "统计单智能体在窗口内各状态的停留样本数。",
        "cypher": """
        MATCH (a:AgentState {run_id: $run_id, drone_id: $drone_id})
        WHERE a.step_index >= $start_step AND a.step_index <= $end_step
        RETURN a.status AS status,
               count(*) AS step_samples,
               min(a.step_index) AS first_step,
               max(a.step_index) AS last_step,
               avg(a.queue_len) AS avg_queue_len,
               avg(a.battery_remaining_m) AS avg_battery_remaining_m
        ORDER BY step_samples DESC, status
        """,
    },
    {
        "id": "q23",
        "name": "region_agent_snapshot",
        "category": "region",
        "required_params": ("run_id", "step_index", "limit"),
        "description": "按区域聚合指定 Step 的智能体数量与负载。",
        "cypher": """
        MATCH (a:AgentState {run_id: $run_id, step_index: $step_index})
        RETURN a.region_id AS region_id,
               count(*) AS agent_count,
               count(DISTINCT a.status) AS status_kinds,
               sum(a.queue_len) AS queued_tasks,
               sum(a.onboard_count) AS onboard_tasks,
               avg(a.battery_remaining_m) AS avg_battery_remaining_m
        ORDER BY queued_tasks DESC, agent_count DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q24",
        "name": "region_battery_risk",
        "category": "energy",
        "required_params": ("run_id", "step_index", "limit"),
        "description": "按区域聚合低电量智能体比例和最低电量。",
        "cypher": """
        MATCH (a:AgentState {run_id: $run_id, step_index: $step_index})-[:OF_DRONE]->
              (d:Drone {run_id: $run_id})
        RETURN a.region_id AS region_id,
               count(*) AS agent_count,
               sum(CASE WHEN a.battery_remaining_m <= coalesce(d.low_battery_threshold_m, 0)
                        THEN 1 ELSE 0 END) AS low_battery_agents,
               min(a.battery_remaining_m) AS min_battery_remaining_m,
               avg(a.battery_remaining_m) AS avg_battery_remaining_m
        ORDER BY low_battery_agents DESC, min_battery_remaining_m ASC
        LIMIT $limit
        """,
    },
    {
        "id": "q25",
        "name": "region_movement_distance",
        "category": "region",
        "required_params": ("run_id", "start_step", "end_step", "limit"),
        "description": "由相邻状态坐标估算各区域智能体移动距离。",
        "cypher": """
        MATCH (previous:AgentState {run_id: $run_id})
        WHERE previous.step_index >= $start_step AND previous.step_index < $end_step
        MATCH (next:AgentState {run_id: $run_id})
        WHERE next.drone_id = previous.drone_id
          AND next.step_index = previous.step_index + 1
        WITH previous.region_id AS region_id,
             sqrt((next.x - previous.x) * (next.x - previous.x) +
                  (next.y - previous.y) * (next.y - previous.y)) AS distance_m,
             previous.drone_id AS drone_id
        RETURN region_id,
               count(*) AS movement_samples,
               count(DISTINCT drone_id) AS agents,
               sum(distance_m) AS estimated_distance_m,
               avg(distance_m) AS avg_step_distance_m
        ORDER BY estimated_distance_m DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q26",
        "name": "open_task_backlog_by_region",
        "category": "task",
        "required_params": ("run_id", "limit"),
        "description": "统计各区域尚未完成的任务积压。",
        "cypher": """
        MATCH (t:Task {run_id: $run_id})
        WHERE coalesce(t.status, "") <> "completed"
        RETURN t.region_id AS region_id,
               t.status AS task_status,
               count(*) AS task_count,
               min(t.deadline_minute) AS earliest_deadline_minute,
               avg(t.deadline_minute - t.release_minute) AS avg_deadline_span_minute
        ORDER BY task_count DESC, earliest_deadline_minute ASC
        LIMIT $limit
        """,
    },
    {
        "id": "q27",
        "name": "tasks_due_in_window",
        "category": "task",
        "required_params": ("run_id", "start_time", "end_time", "limit"),
        "description": "查找时间窗内到期且已经释放的任务。",
        "cypher": """
        MATCH (t:Task {run_id: $run_id})
        WHERE t.release_minute <= $end_time
          AND t.deadline_minute >= $start_time
          AND t.deadline_minute < $end_time
        RETURN t.id AS task_id,
               t.region_id AS region_id,
               t.status AS task_status,
               t.release_minute AS release_minute,
               t.deadline_minute AS deadline_minute,
               t.assigned_to AS assigned_to,
               t.onboard_drone_id AS onboard_drone_id,
               t.takeover_count AS takeover_count
        ORDER BY t.deadline_minute, t.release_minute
        LIMIT $limit
        """,
    },
    {
        "id": "q28",
        "name": "task_status_distribution",
        "category": "task",
        "required_params": ("run_id",),
        "description": "统计任务状态、迟到与接管情况分布。",
        "cypher": """
        MATCH (t:Task {run_id: $run_id})
        RETURN t.status AS task_status,
               coalesce(t.late, false) AS late,
               CASE WHEN coalesce(t.takeover_count, 0) > 0 THEN true ELSE false END AS taken_over,
               count(*) AS task_count
        ORDER BY task_count DESC, task_status
        """,
    },
    {
        "id": "q29",
        "name": "task_deadline_span_buckets",
        "category": "task",
        "required_params": ("run_id",),
        "description": "按释放至截止时长分桶，刻画任务时间约束分布。",
        "cypher": """
        MATCH (t:Task {run_id: $run_id})
        WITH t,
             CASE
               WHEN t.deadline_minute - t.release_minute < 30 THEN "under_30m"
               WHEN t.deadline_minute - t.release_minute < 60 THEN "30_to_60m"
               WHEN t.deadline_minute - t.release_minute < 120 THEN "60_to_120m"
               ELSE "120m_or_more"
             END AS deadline_span_bucket
        RETURN deadline_span_bucket,
               count(*) AS task_count,
               avg(t.deadline_minute - t.release_minute) AS avg_span_minute
        ORDER BY avg_span_minute
        """,
    },
    {
        "id": "q30",
        "name": "task_completion_latency",
        "category": "task",
        "required_params": ("run_id", "limit"),
        "description": "按区域统计任务从释放到完成的时长和迟到率。",
        "cypher": """
        MATCH (t:Task {run_id: $run_id})
        WHERE t.completed_at IS NOT NULL
        RETURN t.region_id AS region_id,
               count(*) AS completed_tasks,
               avg(t.completed_at - t.release_minute) AS avg_completion_minute,
               max(t.completed_at - t.release_minute) AS max_completion_minute,
               sum(CASE WHEN coalesce(t.late, false) THEN 1 ELSE 0 END) AS late_tasks
        ORDER BY avg_completion_minute DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q31",
        "name": "overdue_unfinished_tasks",
        "category": "task",
        "required_params": ("run_id", "end_time", "limit"),
        "description": "查找在给定时刻已经逾期但仍未完成的任务。",
        "cypher": """
        MATCH (t:Task {run_id: $run_id})
        WHERE coalesce(t.status, "") <> "completed"
          AND t.deadline_minute < $end_time
        RETURN t.id AS task_id,
               t.region_id AS region_id,
               t.status AS task_status,
               t.deadline_minute AS deadline_minute,
               $end_time - t.deadline_minute AS overdue_minutes,
               t.assigned_to AS assigned_to,
               t.onboard_drone_id AS onboard_drone_id
        ORDER BY overdue_minutes DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q32",
        "name": "task_takeover_hotspots",
        "category": "collaboration",
        "required_params": ("run_id", "limit"),
        "description": "查找接管次数最多的任务及其协助原因。",
        "cypher": """
        MATCH (t:Task {run_id: $run_id})
        WHERE coalesce(t.takeover_count, 0) > 0
        RETURN t.id AS task_id,
               t.region_id AS region_id,
               t.status AS task_status,
               t.takeover_count AS takeover_count,
               t.assistance_reason AS assistance_reason,
               t.handover_from_drone_id AS handover_from_drone_id,
               t.handover_to_drone_id AS handover_to_drone_id,
               t.handover_completed_at AS handover_completed_at
        ORDER BY takeover_count DESC, handover_completed_at DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q33",
        "name": "active_task_load_by_agent",
        "category": "workload",
        "required_params": ("run_id", "limit"),
        "description": "统计各智能体当前有效任务关系形成的工作负载。",
        "cypher": """
        MATCH (d:Drone {run_id: $run_id})-[rel:ASSIGNED|CARRYING|TAKES_OVER]->
              (t:Task {run_id: $run_id})
        WHERE coalesce(rel.active, true) = true
          AND coalesce(t.status, "") <> "completed"
        RETURN d.id AS drone_id,
               d.region_id AS region_id,
               count(DISTINCT t) AS active_tasks,
               min(t.deadline_minute) AS earliest_deadline_minute,
               collect(DISTINCT type(rel)) AS active_relation_types
        ORDER BY active_tasks DESC, earliest_deadline_minute ASC
        LIMIT $limit
        """,
    },
    {
        "id": "q34",
        "name": "agent_task_funnel",
        "category": "workload",
        "required_params": ("run_id", "limit"),
        "description": "统计智能体任务分配、携带、接管和完成关系漏斗。",
        "cypher": """
        MATCH (d:Drone {run_id: $run_id})
        OPTIONAL MATCH (d)-[rel:ASSIGNED|CARRYING|TAKES_OVER|COMPLETED]->
                       (t:Task {run_id: $run_id})
        RETURN d.id AS drone_id,
               count(DISTINCT CASE WHEN type(rel) = "ASSIGNED" THEN t END) AS assigned_tasks,
               count(DISTINCT CASE WHEN type(rel) = "CARRYING" THEN t END) AS carried_tasks,
               count(DISTINCT CASE WHEN type(rel) = "TAKES_OVER" THEN t END) AS takeover_tasks,
               count(DISTINCT CASE WHEN type(rel) = "COMPLETED" THEN t END) AS completed_tasks
        ORDER BY completed_tasks DESC, assigned_tasks DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q35",
        "name": "event_type_distribution",
        "category": "event",
        "required_params": ("run_id", "start_time", "end_time"),
        "description": "统计时间窗内事件类型和涉及实体规模。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})
        WHERE e.time_minute >= $start_time AND e.time_minute < $end_time
        RETURN e.type AS event_type,
               count(*) AS event_count,
               count(DISTINCT e.drone_id) AS agents,
               count(DISTINCT e.task_id) AS tasks,
               count(DISTINCT e.region_id) AS regions
        ORDER BY event_count DESC, event_type
        """,
    },
    {
        "id": "q36",
        "name": "event_rate_time_buckets",
        "category": "event",
        "required_params": ("run_id", "start_time", "end_time", "limit"),
        "description": "按 10 分钟分桶统计事件速率，覆盖计算表达式和聚合。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})
        WHERE e.time_minute >= $start_time AND e.time_minute < $end_time
        WITH toInteger(floor(e.time_minute / 10.0)) * 10 AS bucket_start_minute,
             e
        RETURN bucket_start_minute,
               e.type AS event_type,
               count(*) AS event_count,
               count(DISTINCT e.drone_id) AS agents
        ORDER BY bucket_start_minute, event_count DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q37",
        "name": "agent_event_timeline",
        "category": "event",
        "required_params": ("run_id", "drone_id", "start_time", "end_time", "limit"),
        "description": "读取智能体作为执行者、请求方或协助方参与的事件时间线。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})
        WHERE (e.drone_id = $drone_id
               OR e.source_drone_id = $drone_id
               OR e.helper_drone_id = $drone_id)
          AND e.time_minute >= $start_time
          AND e.time_minute < $end_time
        RETURN e.time_minute AS time_minute,
               e.type AS event_type,
               e.drone_id AS actor_drone_id,
               e.source_drone_id AS source_drone_id,
               e.helper_drone_id AS helper_drone_id,
               e.task_id AS task_id,
               e.region_id AS region_id,
               e.reason AS reason
        ORDER BY e.time_minute DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q38",
        "name": "region_event_distribution",
        "category": "region",
        "required_params": ("run_id", "start_time", "end_time", "limit"),
        "description": "统计各区域在时间窗内的事件构成和参与智能体数。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})
        WHERE e.time_minute >= $start_time AND e.time_minute < $end_time
        RETURN e.region_id AS region_id,
               e.type AS event_type,
               count(*) AS event_count,
               count(DISTINCT e.drone_id) AS agents,
               count(DISTINCT e.task_id) AS tasks
        ORDER BY event_count DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q39",
        "name": "late_completion_events",
        "category": "event",
        "required_params": ("run_id", "start_time", "end_time", "limit"),
        "description": "读取时间窗内的迟到完成事件及任务截止信息。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id, type: "task_completed"})-[:ABOUT_TASK]->
              (t:Task {run_id: $run_id})
        WHERE coalesce(e.late, false) = true
          AND e.time_minute >= $start_time
          AND e.time_minute < $end_time
        RETURN e.time_minute AS completion_minute,
               t.id AS task_id,
               e.drone_id AS drone_id,
               e.region_id AS region_id,
               t.deadline_minute AS deadline_minute,
               e.time_minute - t.deadline_minute AS late_minutes
        ORDER BY late_minutes DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q40",
        "name": "task_event_lifecycle",
        "category": "path",
        "required_params": ("run_id", "start_time", "end_time", "limit"),
        "description": "沿 Event-Task-Region 路径读取任务生命周期事件。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})-[:ABOUT_TASK]->
              (t:Task {run_id: $run_id})-[:BELONGS_TO]->(r:Region {run_id: $run_id})
        WHERE e.time_minute >= $start_time AND e.time_minute < $end_time
        RETURN t.id AS task_id,
               r.id AS region_id,
               e.time_minute AS time_minute,
               e.type AS event_type,
               e.drone_id AS drone_id,
               t.status AS final_task_status,
               t.takeover_count AS takeover_count
        ORDER BY t.id, e.time_minute
        LIMIT $limit
        """,
    },
    {
        "id": "q41",
        "name": "battery_risk_events",
        "category": "energy",
        "required_params": ("run_id", "start_time", "end_time", "limit"),
        "description": "关联事件与智能体阈值，查找低电量状态下发生的动作。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})-[:ABOUT_DRONE]->
              (d:Drone {run_id: $run_id})
        WHERE e.time_minute >= $start_time
          AND e.time_minute < $end_time
          AND e.battery_remaining_m IS NOT NULL
          AND e.battery_remaining_m <= coalesce(d.low_battery_threshold_m, 0)
        RETURN e.time_minute AS time_minute,
               e.type AS event_type,
               d.id AS drone_id,
               e.region_id AS region_id,
               e.battery_remaining_m AS battery_remaining_m,
               d.low_battery_threshold_m AS threshold_m,
               e.task_id AS task_id
        ORDER BY battery_remaining_m ASC, time_minute DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q42",
        "name": "collaboration_event_timeline",
        "category": "collaboration",
        "required_params": ("run_id", "start_time", "end_time", "limit"),
        "description": "读取协助请求、派遣、接管和交接事件时间线。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})
        WHERE e.type IN ["assist_requested", "assist_dispatched", "assist_unavailable",
                         "task_handover_completed", "task_taken_over"]
          AND e.time_minute >= $start_time
          AND e.time_minute < $end_time
        RETURN e.time_minute AS time_minute,
               e.type AS event_type,
               e.task_id AS task_id,
               e.source_drone_id AS source_drone_id,
               e.helper_drone_id AS helper_drone_id,
               e.region_id AS region_id,
               e.reason AS reason,
               e.takeover_count AS takeover_count
        ORDER BY e.time_minute DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q43",
        "name": "assistance_reason_distribution",
        "category": "collaboration",
        "required_params": ("run_id", "start_time", "end_time"),
        "description": "统计多智能体协助触发原因及参与规模。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})
        WHERE e.type IN ["assist_requested", "assist_dispatched", "assist_unavailable"]
          AND e.time_minute >= $start_time
          AND e.time_minute < $end_time
        RETURN coalesce(e.reason, "unknown") AS reason,
               e.type AS event_type,
               count(*) AS event_count,
               count(DISTINCT e.source_drone_id) AS source_agents,
               count(DISTINCT e.helper_drone_id) AS helper_agents,
               count(DISTINCT e.task_id) AS tasks
        ORDER BY event_count DESC, reason
        """,
    },
    {
        "id": "q44",
        "name": "active_assistance_pairs",
        "category": "collaboration",
        "required_params": ("run_id", "limit"),
        "description": "读取当前活跃的 helper-source 协助关系。",
        "cypher": """
        MATCH (helper:Drone {run_id: $run_id})-[assist:ASSISTS]->
              (source:Drone {run_id: $run_id})
        WHERE coalesce(assist.active, false) = true
        RETURN helper.id AS helper_drone_id,
               source.id AS source_drone_id,
               assist.task_id AS task_id,
               assist.reason AS reason,
               assist.updated_at AS updated_at,
               helper.region_id AS helper_region_id,
               source.region_id AS source_region_id
        ORDER BY updated_at DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q45",
        "name": "handover_network",
        "category": "collaboration",
        "required_params": ("run_id", "limit"),
        "description": "聚合智能体之间的任务交接网络。",
        "cypher": """
        MATCH (source:Drone {run_id: $run_id})-[handover:HANDOVER_TO]->
              (helper:Drone {run_id: $run_id})
        RETURN source.id AS source_drone_id,
               helper.id AS helper_drone_id,
               count(*) AS handover_relations,
               sum(CASE WHEN coalesce(handover.active, false) THEN 1 ELSE 0 END) AS active_handovers,
               max(handover.updated_at) AS latest_update_minute,
               collect(DISTINCT handover.reason) AS reasons
        ORDER BY handover_relations DESC, latest_update_minute DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q46",
        "name": "takeover_task_details",
        "category": "collaboration",
        "required_params": ("run_id", "limit"),
        "description": "沿 Drone-TAKES_OVER-Task 路径读取接管任务详情。",
        "cypher": """
        MATCH (helper:Drone {run_id: $run_id})-[takeover:TAKES_OVER]->
              (t:Task {run_id: $run_id})
        RETURN helper.id AS helper_drone_id,
               t.id AS task_id,
               t.region_id AS region_id,
               t.handover_from_drone_id AS source_drone_id,
               takeover.reason AS reason,
               takeover.time_minute AS takeover_time_minute,
               takeover.active AS active,
               t.status AS task_status,
               t.takeover_count AS takeover_count
        ORDER BY takeover_time_minute DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q47",
        "name": "multi_agent_shared_tasks",
        "category": "collaboration",
        "required_params": ("run_id", "limit"),
        "description": "查找被多个智能体经任务关系共同关联的任务。",
        "cypher": """
        MATCH (d:Drone {run_id: $run_id})-[:ASSIGNED|CARRYING|TAKES_OVER|COMPLETED]->
              (t:Task {run_id: $run_id})
        WITH t,
             count(DISTINCT d) AS agent_count,
             collect(DISTINCT d.id) AS drone_ids
        WHERE agent_count > 1
        RETURN t.id AS task_id,
               t.region_id AS region_id,
               t.status AS task_status,
               t.takeover_count AS takeover_count,
               agent_count,
               drone_ids
        ORDER BY agent_count DESC, takeover_count DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q48",
        "name": "collaboration_outcome_summary",
        "category": "collaboration",
        "required_params": ("run_id", "start_time", "end_time"),
        "description": "汇总协助请求、派遣、不可用、交接和接管事件结果。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})
        WHERE e.type IN ["assist_requested", "assist_dispatched", "assist_unavailable",
                         "task_handover_completed", "task_taken_over"]
          AND e.time_minute >= $start_time
          AND e.time_minute < $end_time
        RETURN sum(CASE WHEN e.type = "assist_requested" THEN 1 ELSE 0 END) AS requested,
               sum(CASE WHEN e.type = "assist_dispatched" THEN 1 ELSE 0 END) AS dispatched,
               sum(CASE WHEN e.type = "assist_unavailable" THEN 1 ELSE 0 END) AS unavailable,
               sum(CASE WHEN e.type = "task_handover_completed" THEN 1 ELSE 0 END) AS handovers_completed,
               sum(CASE WHEN e.type = "task_taken_over" THEN 1 ELSE 0 END) AS tasks_taken_over,
               count(DISTINCT e.source_drone_id) AS source_agents,
               count(DISTINCT e.helper_drone_id) AS helper_agents,
               count(DISTINCT e.task_id) AS affected_tasks
        """,
    },
    {
        "id": "q49",
        "name": "failed_drone_snapshot",
        "category": "resilience",
        "required_params": ("run_id", "limit"),
        "description": "读取当前失败或带故障模式的智能体快照。",
        "cypher": """
        MATCH (d:Drone {run_id: $run_id})
        WHERE d.status = "failed" OR d.failure_mode IS NOT NULL
        RETURN d.id AS drone_id,
               d.region_id AS region_id,
               d.status AS status,
               d.failure_mode AS failure_mode,
               d.takeover_mode AS takeover_mode,
               d.queue_len AS queue_len,
               d.onboard_count AS onboard_count,
               d.battery_remaining_m AS battery_remaining_m,
               d.updated_at AS updated_at
        ORDER BY updated_at DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q50",
        "name": "resilience_event_timeline",
        "category": "resilience",
        "required_params": ("run_id", "start_time", "end_time", "limit"),
        "description": "读取故障、低电量和协助不可用等韧性事件。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})
        WHERE (e.type CONTAINS "fail"
               OR e.type = "drone_low_battery"
               OR e.type = "assist_unavailable")
          AND e.time_minute >= $start_time
          AND e.time_minute < $end_time
        RETURN e.time_minute AS time_minute,
               e.type AS event_type,
               e.drone_id AS drone_id,
               e.source_drone_id AS source_drone_id,
               e.helper_drone_id AS helper_drone_id,
               e.task_id AS task_id,
               e.region_id AS region_id,
               e.reason AS reason,
               e.battery_remaining_m AS battery_remaining_m
        ORDER BY e.time_minute DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q51",
        "name": "failure_mode_timeseries",
        "category": "resilience",
        "required_params": ("run_id", "start_step", "end_step", "limit"),
        "description": "按 Step 和故障模式统计受影响智能体数量。",
        "cypher": """
        MATCH (a:AgentState {run_id: $run_id})
        WHERE a.step_index >= $start_step AND a.step_index <= $end_step
          AND (a.failure_mode IS NOT NULL OR a.status = "failed")
        RETURN a.step_index AS step_index,
               coalesce(a.failure_mode, "unknown") AS failure_mode,
               count(*) AS affected_agents,
               collect(DISTINCT a.drone_id)[..10] AS sample_drone_ids
        ORDER BY a.step_index, affected_agents DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q52",
        "name": "orphan_agent_states",
        "category": "quality",
        "required_params": ("run_id", "limit"),
        "description": "查找缺少 Step、Drone 或 Run 关系的 AgentState。",
        "cypher": """
        MATCH (a:AgentState {run_id: $run_id})
        WHERE NOT EXISTS {
                MATCH (a)-[:IN_STEP]->(:Step {run_id: $run_id})
              }
           OR NOT EXISTS {
                MATCH (a)-[:OF_DRONE]->(:Drone {run_id: $run_id})
              }
           OR NOT EXISTS {
                MATCH (a)-[:IN_RUN]->(:Run {id: $run_id})
              }
        RETURN a.id AS agent_state_id,
               a.drone_id AS drone_id,
               a.step_index AS step_index,
               NOT EXISTS { MATCH (a)-[:IN_STEP]->(:Step {run_id: $run_id}) } AS missing_step,
               NOT EXISTS { MATCH (a)-[:OF_DRONE]->(:Drone {run_id: $run_id}) } AS missing_drone,
               NOT EXISTS { MATCH (a)-[:IN_RUN]->(:Run {id: $run_id}) } AS missing_run
        ORDER BY step_index, drone_id
        LIMIT $limit
        """,
    },
    {
        "id": "q53",
        "name": "orphan_events",
        "category": "quality",
        "required_params": ("run_id", "limit"),
        "description": "查找缺少 Run、Drone 或应有 Task 关系的 Event。",
        "cypher": """
        MATCH (e:Event {run_id: $run_id})
        WHERE NOT EXISTS {
                MATCH (e)-[:IN_RUN]->(:Run {id: $run_id})
              }
           OR NOT EXISTS {
                MATCH (e)-[:ABOUT_DRONE]->(:Drone {run_id: $run_id})
              }
           OR (e.task_id IS NOT NULL AND NOT EXISTS {
                MATCH (e)-[:ABOUT_TASK]->(:Task {run_id: $run_id})
              })
        RETURN e.id AS event_id,
               e.type AS event_type,
               e.time_minute AS time_minute,
               e.drone_id AS drone_id,
               e.task_id AS task_id,
               NOT EXISTS { MATCH (e)-[:IN_RUN]->(:Run {id: $run_id}) } AS missing_run,
               NOT EXISTS { MATCH (e)-[:ABOUT_DRONE]->(:Drone {run_id: $run_id}) } AS missing_drone,
               (e.task_id IS NOT NULL AND NOT EXISTS {
                 MATCH (e)-[:ABOUT_TASK]->(:Task {run_id: $run_id})
               }) AS missing_task
        ORDER BY time_minute DESC
        LIMIT $limit
        """,
    },
    {
        "id": "q54",
        "name": "task_relation_gaps",
        "category": "quality",
        "required_params": ("run_id", "limit"),
        "description": "查找缺少区域、Run 或生命周期关联的 Task。",
        "cypher": """
        MATCH (t:Task {run_id: $run_id})
        WHERE NOT EXISTS {
                MATCH (t)-[:BELONGS_TO]->(:Region {run_id: $run_id})
              }
           OR NOT EXISTS {
                MATCH (t)-[:IN_RUN]->(:Run {id: $run_id})
              }
           OR NOT EXISTS {
                MATCH (:Drone {run_id: $run_id})-[:ASSIGNED|CARRYING|TAKES_OVER|COMPLETED]->(t)
              }
        RETURN t.id AS task_id,
               t.region_id AS region_id,
               t.status AS task_status,
               NOT EXISTS { MATCH (t)-[:BELONGS_TO]->(:Region {run_id: $run_id}) } AS missing_region,
               NOT EXISTS { MATCH (t)-[:IN_RUN]->(:Run {id: $run_id}) } AS missing_run,
               NOT EXISTS {
                 MATCH (:Drone {run_id: $run_id})-[:ASSIGNED|CARRYING|TAKES_OVER|COMPLETED]->(t)
               } AS missing_agent_relation
        ORDER BY task_status, task_id
        LIMIT $limit
        """,
    },
    {
        "id": "q55",
        "name": "run_integrity_summary",
        "category": "quality",
        "required_params": ("run_id",),
        "description": "一次返回 Run 下主要节点数量，作为数据完整性总览。",
        "cypher": """
        MATCH (run:Run {id: $run_id})
        CALL (run) {
          MATCH (s:Step)-[:IN_RUN]->(run)
          RETURN count(s) AS step_count
        }
        CALL (run) {
          MATCH (a:AgentState)-[:IN_RUN]->(run)
          RETURN count(a) AS agent_state_count,
                 count(DISTINCT a.drone_id) AS state_agent_count
        }
        CALL (run) {
          MATCH (d:Drone)-[:IN_RUN]->(run)
          RETURN count(d) AS drone_count
        }
        CALL (run) {
          MATCH (t:Task)-[:IN_RUN]->(run)
          RETURN count(t) AS task_count
        }
        CALL (run) {
          MATCH (e:Event)-[:IN_RUN]->(run)
          RETURN count(e) AS event_count
        }
        CALL (run) {
          MATCH (r:Region)-[:IN_RUN]->(run)
          RETURN count(r) AS region_count
        }
        RETURN run.id AS run_id,
               step_count,
               agent_state_count,
               state_agent_count,
               drone_count,
               task_count,
               event_count,
               region_count
        """,
    },
)


def build_extended_templates(template_type):
    """Build q13-q55 using the benchmark module's QueryTemplate type."""
    return {
        spec["id"]: template_type(**spec)
        for spec in EXTENDED_TEMPLATE_SPECS
    }
