# Neo4j Run Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-run isolation to the Neo4j knowledge graph backend so simulation data from different runs does not mix in KG queries.

**Architecture:** `Neo4jKnowledgeStore` owns a `run_id`. It creates or merges a `Run` node, links all graph entities and events to that run, and filters Neo4j query methods by `run_id`. `TemporalKnowledgeStore` remains unchanged.

**Tech Stack:** Python `unittest`, Neo4j Python driver API, Cypher, environment variables.

---

### Task 1: Add failing tests for run isolation

**Files:**
- Modify: `tests/knowledge_test.py`

- [ ] **Step 1: Add tests**

Add tests that assert:
- `Neo4jKnowledgeStore(driver=driver, run_id="run-a")` writes a `Run` node.
- written entities/events are linked to `Run`.
- `recent_agent_performance()` and `recent_events()` pass `run_id` into Cypher params.
- `build_knowledge_store_from_env()` reads `KG_RUN_ID`.

- [ ] **Step 2: Run targeted tests**

Run: `python3 -m unittest tests.knowledge_test`

Expected before implementation: failure because `Neo4jKnowledgeStore` does not accept or use `run_id`.

### Task 2: Implement run isolation

**Files:**
- Modify: `simulation/knowledge.py`

- [ ] **Step 1: Store run identity**

Update `Neo4jKnowledgeStore.__init__()` to accept `run_id=None`, default to a generated ID when not provided, and expose it as `self.run_id`.

- [ ] **Step 2: Link graph data to run**

Update `_write_event()` Cypher to merge `(:Run {id: $run_id})`, set metadata, and create `IN_RUN` relationships from `Event`, `Task`, `Drone`, and `Region` to the run.

- [ ] **Step 3: Filter Neo4j queries by run**

Update `recent_agent_performance()` and `recent_events()` to match events through `(:Run {id: $run_id})`.

- [ ] **Step 4: Read environment variable**

Update `build_knowledge_store_from_env()` so `KG_RUN_ID` can pin a run ID for repeatable experiments.

### Task 3: Document and verify

**Files:**
- Modify: `README.md`
- Create: `docs/codex_notes/2026-06-18-neo4j-run-isolation.md`

- [ ] **Step 1: Document `Run` schema and `KG_RUN_ID`**

Add `Run` and `IN_RUN` to the README Neo4j schema section and document `KG_RUN_ID`.

- [ ] **Step 2: Run verification**

Run:

```bash
python3 -m unittest tests.knowledge_test
python3 -m unittest discover -s tests -p '*_test.py'
bash -n run_local_sim.sh run_ray_sim.sh
```

Expected: all commands pass.
