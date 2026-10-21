# Drone Battery Cargo KG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend local and Ray drone delivery simulation with battery, charging, multi-cargo, Neo4j event/schema expansion, and frontend agent status fields.

**Architecture:** Put shared behavior in `DeliveryTask` and `DroneAgent` so local and Ray actors use the same movement, pickup, charging, and battery logic. Keep Ray in sync by extending payload/snapshot proxy fields. Extend `TemporalKnowledgeStore` and `Neo4jKnowledgeStore` with generic event writers and per-step summary writers.

**Tech Stack:** Python dataclasses/unittest, Neo4j Cypher driver API, browser JavaScript table rendering.

---

### Task 1: DroneAgent data structure

**Files:**
- Modify: `simulation/delivery_sim.py`
- Modify: `simulation/ray_delivery_sim.py`
- Test: `tests/delivery_sim_test.py`
- Test: `tests/ray_delivery_sim_test.py`

- [ ] Add tests for default battery/capacity fields and Ray payload roundtrip.
- [ ] Implement `queue_capacity`, `cargo_capacity`, `onboard_tasks`, battery fields, `pickup_time`, `onboard_drone_id`, reserved failure/takeover fields.
- [ ] Keep `capacity` as queue compatibility alias.

### Task 2: Battery acceptance and consumption

**Files:**
- Modify: `simulation/delivery_sim.py`
- Modify: `simulation/planner.py`
- Test: `tests/delivery_sim_test.py`
- Test: `tests/planner_test.py`

- [ ] Add tests that a drone cannot accept a task when it lacks task + return + buffer battery.
- [ ] Consume battery on every movement segment.
- [ ] Filter or heavily penalize infeasible drone-task assignments.

### Task 3: Depot charging

**Files:**
- Modify: `simulation/delivery_sim.py`
- Modify: `simulation/ray_delivery_sim.py`
- Test: `tests/delivery_sim_test.py`
- Test: `tests/ray_delivery_sim_test.py`

- [ ] Add tests for low battery return and immediate full charge at depot.
- [ ] Add region depot / nearest depot selection.
- [ ] Emit low-battery, returning, and charged events through environment step processing.

### Task 4: Cargo capacity and onboard tasks

**Files:**
- Modify: `simulation/delivery_sim.py`
- Modify: `simulation/ray_delivery_sim.py`
- Test: `tests/delivery_sim_test.py`

- [ ] Add tests for max three onboard tasks and picked-up/completed transitions.
- [ ] Implement `onboard_tasks` target selection by earliest deadline.
- [ ] Remove completed tasks from both queue and onboard list.

### Task 5: Task picked-up event

**Files:**
- Modify: `simulation/delivery_sim.py`
- Modify: `simulation/ray_delivery_sim.py`
- Modify: `simulation/knowledge.py`
- Test: `tests/knowledge_test.py`

- [ ] Add `task_picked_up` to memory and Neo4j stores.
- [ ] Return picked-up events from drone step and record them in local/Ray environments.

### Task 6: Neo4j field expansion

**Files:**
- Modify: `simulation/knowledge.py`
- Test: `tests/knowledge_test.py`

- [ ] Add Drone battery/cargo fields and Task pickup/onboard fields.
- [ ] Add `CARRYING` relation active lifecycle.
- [ ] Verify event types `task_picked_up`, `drone_low_battery`, `drone_returning_to_charge`, `drone_charged`.

### Task 7: Step nodes and frontend

**Files:**
- Modify: `simulation/delivery_sim.py`
- Modify: `simulation/ray_delivery_sim.py`
- Modify: `simulation/knowledge.py`
- Modify: `dynamic.html`
- Modify: `src/dynamic_app.mjs`
- Test: `tests/knowledge_test.py`
- Test: `tests/dynamic_server_test.py`

- [ ] Add `record_step()` to memory and Neo4j stores.
- [ ] Record Step after each simulation step in local and Ray.
- [ ] Include battery and onboard fields in snapshots.
- [ ] Add frontend Agent table columns for battery and onboard load.

### Verification

Run:

```bash
./run_in_lade.sh python -m unittest tests.delivery_sim_test tests.knowledge_test tests.ray_delivery_sim_test tests.planner_test tests.dynamic_server_test
./run_in_lade.sh python -m unittest discover -s tests -p '*_test.py'
node --check src/dynamic_app.mjs
bash -n run_local_sim.sh run_ray_sim.sh run_in_lade.sh
```
