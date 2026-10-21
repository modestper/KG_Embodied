import json
import os
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from simulation.delivery_sim import euclidean, format_minute


class RuleBasedPlanner:
    def estimate_cost(self, drone, task, now_minute):
        if hasattr(drone, "can_accept_task") and not drone.can_accept_task(task):
            return 1_000_000_000
        queue_penalty = len(drone.queue) * 1500
        pickup_distance = euclidean(drone.position, task.pickup_position)
        delivery_distance = euclidean(task.pickup_position, task.dropoff_position)
        distance = pickup_distance + delivery_distance
        lateness_risk = max(0, now_minute + distance / drone.speed_m_per_minute - task.deadline_minute) * 250
        region_penalty = 0 if drone.region_id in (None, task.region_id) else 25_000
        battery_risk = 0
        if hasattr(drone, "required_battery_for"):
            battery_risk = max(0, drone.required_battery_for(task) - drone.battery_remaining_m) * 10
        elif drone.total_distance_m + distance > drone.battery_range_m:
            battery_risk = 10_000
        return distance + queue_penalty + lateness_risk + region_penalty + battery_risk


class LLMPlanner:
    def __init__(
        self,
        api_key,
        model,
        api_base="https://api.openai.com/v1",
        request_func=None,
        fallback=None,
        max_tokens=256,
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.request_func = request_func or self._request
        self.fallback = fallback or RuleBasedPlanner()
        self.max_tokens = max_tokens

    def estimate_cost(self, drone, task, now_minute):
        request = self.build_request(drone, task, now_minute)
        try:
            response = self.request_func(request)
            decision = self.parse_response(response)
        except Exception:
            return self.fallback.estimate_cost(drone, task, now_minute)
        if not decision.get("accept", True):
            return 1_000_000 + float(decision.get("cost", 0) or 0)
        return float(decision["cost"])

    def build_request(self, drone, task, now_minute):
        system = (
            "You are a drone delivery planner. Return only compact JSON with "
            "accept:boolean, cost:number, reason:string. Lower cost means better assignment."
        )
        user = {
            "now": format_minute(now_minute),
            "drone": {
                "id": drone.drone_id,
                "region_id": drone.region_id,
                "queue_len": len(drone.queue),
                "position": {"x": round(drone.x, 3), "y": round(drone.y, 3)},
                "speed_m_per_minute": drone.speed_m_per_minute,
                "total_distance_m": round(drone.total_distance_m, 1),
            },
            "task": {
                "id": task.task_id,
                "region_id": task.region_id,
                "pickup": {"x": task.pickup_x, "y": task.pickup_y},
                "dropoff": {"x": task.x, "y": task.y},
                "release": format_minute(task.release_minute),
                "deadline": format_minute(task.deadline_minute),
            },
        }
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))},
            ],
        }

    def parse_response(self, response):
        content = response["choices"][0]["message"]["content"].strip()
        return json.loads(content)

    def _request(self, payload):
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.api_base}/chat/completions",
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))


class RegionLLMCoordinator:
    def __init__(
        self,
        api_key,
        model,
        api_base="https://api.openai.com/v1",
        request_func=None,
        fallback=None,
        max_tasks_per_region=20,
        max_drones_per_region=10,
        knowledge_store=None,
        max_tokens=512,
        log_path=None,
    ):
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.request_func = request_func or self._request
        self.fallback = fallback or _auction_fallback()
        self.max_tasks_per_region = max_tasks_per_region
        self.max_drones_per_region = max_drones_per_region
        self.knowledge_store = knowledge_store
        self.max_tokens = max_tokens
        self.llm_events = []
        self.log_path = Path(log_path) if log_path else None

    def assign(self, tasks, drones, now_minute):
        assignments = {}
        for region_id, region_tasks in self.group_tasks(tasks).items():
            region_drones = [
                drone for drone in drones if drone.can_accept() and drone.region_id == region_id
            ]
            if not region_drones:
                continue
            context = self.region_context(region_id, region_tasks, region_drones, now_minute)
            batch_tasks = self.tasks_from_context(context, region_tasks)
            batch_drones = self.drones_from_context(context, region_drones)
            request = None
            try:
                request = self.build_request(region_id, batch_tasks, batch_drones, now_minute, context=context)
                started = time.time()
                response = self.request_func(request)
                elapsed_ms = round((time.time() - started) * 1000)
                decisions = self.parse_response(response)
                self.record_llm_event(now_minute, region_id, request, response, decisions, elapsed_ms)
                assignments.update(self.apply_assignments(decisions, batch_tasks, batch_drones))
            except Exception as exc:
                self.record_llm_event(
                    now_minute,
                    region_id,
                    request,
                    None,
                    [],
                    None,
                    error=str(exc),
                )
                assignments.update(self.fallback.assign(region_tasks, drones, now_minute))
        return assignments

    def group_tasks(self, tasks):
        grouped = defaultdict(list)
        for task in tasks:
            grouped[task.region_id].append(task)
        return grouped

    def region_context(self, region_id, tasks, drones, now_minute):
        if self.knowledge_store is None:
            return None
        return self.knowledge_store.query_region_context(
            region_id=region_id,
            tasks=tasks,
            drones=drones,
            now_minute=now_minute,
            max_tasks=self.max_tasks_per_region,
            max_drones=self.max_drones_per_region,
        )

    def tasks_from_context(self, context, tasks):
        if context is None:
            return tasks[: self.max_tasks_per_region]
        ids = {item["task_id"] for item in context["urgent_tasks"]}
        return [task for task in tasks if task.task_id in ids]

    def drones_from_context(self, context, drones):
        if context is None:
            return drones[: self.max_drones_per_region]
        ids = {item["drone_id"] for item in context["candidate_drones"]}
        return [drone for drone in drones if drone.drone_id in ids]

    def build_request(self, region_id, tasks, drones, now_minute, context=None):
        depot = {"x": tasks[0].pickup_x, "y": tasks[0].pickup_y} if tasks else None
        payload = {
            "time": format_minute(now_minute),
            "objective": "Assign waiting delivery tasks to drones. Minimize late delivery and balance queues.",
            "region_id": region_id,
            "depot": depot,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "deadline": format_minute(task.deadline_minute),
                    "dropoff": {"x": task.x, "y": task.y},
                    "distance_from_depot_m": round(euclidean(task.pickup_position, task.dropoff_position), 1),
                }
                for task in tasks
            ],
            "drones": [
                {
                    "drone_id": drone.drone_id,
                    "position": {"x": round(drone.x, 3), "y": round(drone.y, 3)},
                    "queue_len": len(drone.queue),
                    "distance_to_depot_m": round(euclidean(drone.position, (depot["x"], depot["y"])), 1)
                    if depot
                    else 0,
                    "completed": len(drone.completed),
                }
                for drone in drones
            ],
        }
        if context is not None:
            payload["kg_context"] = {
                "recent_agent_performance": context["recent_agent_performance"],
                "recent_events": context["recent_events"],
            }
        system = (
            "You are a regional drone delivery coordinator. Return only JSON with "
            "assignments:[{task_id:string,drone_id:string,reason:string}]. "
            "Use only provided task_id and drone_id values. Do not include markdown."
        )
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
            ],
        }

    def parse_response(self, response):
        content = response["choices"][0]["message"]["content"].strip()
        return json.loads(content).get("assignments", [])

    def record_llm_event(self, now_minute, region_id, request, response, decisions, elapsed_ms, error=None):
        event = {
            "time": format_minute(now_minute),
            "region_id": region_id,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "elapsed_ms": elapsed_ms,
            "request": request,
            "response": response,
            "decisions": decisions,
            "error": error,
            "used_fallback": error is not None,
        }
        self.llm_events.append(event)
        self.llm_events = self.llm_events[-20:]
        self.write_llm_log(event)

    def write_llm_log(self, event):
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"event": "llm_call", **event}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def apply_assignments(self, decisions, tasks, drones):
        task_by_id = {task.task_id: task for task in tasks if task.status == "waiting"}
        drone_by_id = {drone.drone_id: drone for drone in drones if drone.can_accept()}
        assignments = {}
        for decision in decisions:
            task = task_by_id.get(str(decision.get("task_id")))
            drone = drone_by_id.get(str(decision.get("drone_id")))
            if not task or not drone:
                continue
            if hasattr(drone, "can_accept_task") and not drone.can_accept_task(task):
                continue
            if drone.assign(task):
                assignments[task.task_id] = drone.drone_id
        return assignments

    def _request(self, payload):
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.api_base}/chat/completions",
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))


def build_planner_from_env():
    if os.environ.get("DRONE_PLANNER", "rule").lower() != "llm":
        return RuleBasedPlanner()
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("VLLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL") or os.environ.get("VLLM_MODEL") or "gpt-4o-mini"
    api_base = os.environ.get("LLM_API_BASE") or os.environ.get("VLLM_API_BASE") or "https://api.openai.com/v1"
    if api_base.startswith("https://api.openai.com") and not api_key:
        return RuleBasedPlanner()
    return LLMPlanner(
        api_key=api_key,
        model=model,
        api_base=api_base,
        max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "256")),
    )


def build_coordinator_from_env():
    from simulation.delivery_sim import AuctionCoordinator

    if os.environ.get("DRONE_COORDINATOR", "auction").lower() not in ("llm-region", "region-llm"):
        return AuctionCoordinator()
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("VLLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL") or os.environ.get("VLLM_MODEL") or "gpt-4o-mini"
    api_base = os.environ.get("LLM_API_BASE") or os.environ.get("VLLM_API_BASE") or "https://api.openai.com/v1"
    if api_base.startswith("https://api.openai.com") and not api_key:
        return AuctionCoordinator()
    return RegionLLMCoordinator(
        api_key=api_key,
        model=model,
        api_base=api_base,
        fallback=AuctionCoordinator(),
        max_tasks_per_region=int(os.environ.get("LLM_MAX_TASKS_PER_REGION", "20")),
        max_drones_per_region=int(os.environ.get("LLM_MAX_DRONES_PER_REGION", "10")),
        max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "512")),
        log_path=os.environ.get("LLM_LOG_PATH"),
    )


def _auction_fallback():
    from simulation.delivery_sim import AuctionCoordinator

    return AuctionCoordinator()
