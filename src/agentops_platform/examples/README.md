# Autonomy Demo

Self-contained end-to-end demonstration of the AgentOps Platform's autonomous
canary management loop.

No network calls. No GitHub access. No LLM calls. No human decisions required.

---

## Run the demo

```bash
# from the agentops-platform/ directory
PYTHONPATH=src:. python -m agentops_platform.examples.autonomy_demo
```

### With live Dashboard

Start the API server first, then run the demo in a second terminal:

```bash
# Terminal 1 — API server
PYTHONPATH=src:. uvicorn agentops_platform.main:app --port 8080

# Terminal 2 — demo (writes to the same in-memory store via the server process)
PYTHONPATH=src:. python -m agentops_platform.examples.autonomy_demo --with-server
```

Open http://localhost:8080/dashboard in a browser to watch the panels update.

---

## What happens (3-minute script)

| Time | What the system does | Human action |
|------|----------------------|--------------|
| 0:00 | Register a sample managed agent; create and evaluate the **stable (baseline)** version; deploy it to 100% traffic (promoted). | none |
| 0:30 | Register a **degraded candidate** version — drift score drops from 0.88 → 0.79, trajectory 0.92 → 0.83, retention_rate 0.81 → 0.63. Start a canary deployment at 10% traffic. | none |
| 1:00 | Inject evaluation scores and outcome metrics for the canary window. The degradation is now observable across all axes. | none |
| 1:30 | **Meta-agent decision cycle runs.** Layer 1 (safety floor) is not breached (drops < 0.10 hard limit). Layer 2 (gray-zone) detects that both drift and trajectory exceed the aggressive warning threshold (warn × 1.5 = 0.075). Deterministic fallback decides: **ROLLBACK**. | none |
| 2:00 | Canary deployment transitions to `rolled_back` (0% traffic). Meta-agent files a **PR draft** (dryrun) with root-cause diagnosis, signal table, and improvement checklist. | none |
| 2:30 | All state changes are visible in the audit event log and the Dashboard panels. | none |

---

## Dashboard panels explained

Open http://localhost:8080/dashboard after running `--with-server`.

| Panel | What it shows after the demo |
|-------|------------------------------|
| **Evaluation Score Timeline** | Two rows: baseline (drift=0.88, trajectory=0.92) and canary (drift=0.79, trajectory=0.83). The canary row is visually inferior. |
| **Canary Status** | The canary deployment shows `rolled_back`, 0% traffic. |
| **Rollback History & Meta-agent Decisions** | One row: action=rollback, rationale explaining the multi-axis threshold breach, link to the PR draft. |

The page auto-refreshes every 10 seconds (`/dashboard/data` polling).

---

## Key platform concepts demonstrated

- **Two-layer decision architecture**
  - Layer 1 (safety floor): deterministic hard thresholds evaluated first — if
    breached, rollback is immediate without consulting Layer 2.
  - Layer 2 (gray-zone judgment): when the floor is NOT breached but soft
    warning thresholds ARE exceeded, the meta-agent reasons about the trend
    and decides advance / hold / rollback.

- **Outcome metrics integration**
  External signals (e.g. `retention_rate`, `task_success_rate`) are ingested
  alongside evaluation scores. The meta-agent considers both in its judgment.

- **PR draft on rollback**
  Every autonomous rollback produces a structured improvement proposal:
  diagnosis, signal table, and a checklist for the next iteration.

- **Audit trail**
  Every evaluation, rollback, and meta-agent decision is recorded as an Event
  in the platform's event log. Nothing is ephemeral.

---

## Scenario tests

```bash
# from agentops-platform/
python -m pytest tests/test_autonomy_demo.py -v
```

20 tests covering:
- Canary → rollback contract
- PR draft content (sections, deployment reference, signal table)
- PR draft ↔ DecisionRecord linkage
- Audit event types and counts
- Dashboard data endpoint post-rollback
- Demo repeatability and subprocess isolation
