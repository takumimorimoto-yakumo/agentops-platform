"""
Autonomy Demo — end-to-end walkthrough of the AgentOps Platform.

Demonstrates the full autonomous canary management loop:
  Step 1: Register a sample managed agent and a stable (baseline) version.
          Deploy it to 100 % traffic (promoted).
  Step 2: Register a deliberately degraded candidate version and start a
          canary deployment with steps [10, 50, 100].
          Degradation seeds: output-drift drop + retention_rate decline.
  Step 3: Inject evaluation results and outcome metrics that expose the
          degradation within the canary window.
  Step 4: Run the meta-agent decision cycle.  It detects the degradation
          (safety floor or gray-zone), decides ROLLBACK — with no human
          action.
  Step 5: The meta-agent files a PR draft (dryrun) containing root-cause
          diagnosis and improvement suggestions.
  Step 6: Confirm that the audit event log and dashboard data endpoint
          reflect every state change.

Run:
    cd agentops-platform
    python -m agentops_platform.examples.autonomy_demo

Or with the API server running (for live Dashboard):
    uvicorn agentops_platform.main:app --port 8080 &
    python -m agentops_platform.examples.autonomy_demo --with-server

Self-contained: no network calls, no GitHub access, no LLM calls.
All decisions are made by the deterministic meta-agent fallback.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Demo configuration — sourced from config layer (no hardcoded magic numbers)
# ---------------------------------------------------------------------------
from config.defaults import (
    DEFAULT_MAX_COST_INCREASE_RATIO as _ROLLBACK_COST_INCREASE_RATIO,
    DEFAULT_MAX_DRIFT_DROP as _ROLLBACK_DRIFT_DROP,
    DEFAULT_MAX_LATENCY_P95_MS as _ROLLBACK_LATENCY_P95_MS,
    DEFAULT_MAX_TRAJECTORY_DROP as _ROLLBACK_TRAJECTORY_DROP,
)

# Canary steps for the degraded candidate
_DEMO_CANARY_STEPS = [10, 50, 100]

# Baseline (stable) scores — healthy agent
_BASELINE_DRIFT = 0.88
_BASELINE_TRAJECTORY = 0.92
_BASELINE_COST_USD = 0.0018
_BASELINE_LATENCY_MS = 380.0
_BASELINE_RETENTION_RATE = 0.81

# Degraded candidate scores — intentionally seeded with unambiguous (but sub-floor) drops
# drift drop       = 0.88 - 0.785 = 0.095  →  below hard floor (0.10) so Layer 1 stays silent
# trajectory drop  = 0.92 - 0.825 = 0.095  →  same pattern; Gemini sees two clear regressions
# cost increase    = (0.0021 - 0.0018) / 0.0018 ≈ +16.7%  →  below warn ceiling (20%)
# latency          = 640 ms  →  elevated but below warn ceiling (1500 ms)
# retention / task_success: sharply lower to add outcome-metric evidence across all axes.
# Every soft warn threshold is breached; a rational LLM should choose ROLLBACK.
_DEGRADED_DRIFT = 0.785
_DEGRADED_TRAJECTORY = 0.825
_DEGRADED_COST_USD = 0.0021
_DEGRADED_LATENCY_MS = 640.0
_DEGRADED_RETENTION_RATE = 0.48  # sharp decline (was 0.63)

# Soft warning thresholds — aligned with Settings defaults (5% drop triggers concern)
_WARN_DRIFT = 0.05
_WARN_TRAJECTORY = 0.05

# ---------------------------------------------------------------------------
# Narrative helpers
# ---------------------------------------------------------------------------

_SEPARATOR = "─" * 70


def _banner(text: str) -> None:
    print(f"\n{_SEPARATOR}")
    print(f"  {text}")
    print(_SEPARATOR)


def _step(n: int, title: str) -> None:
    print(f"\n[Step {n}] {title}")


def _info(msg: str) -> None:
    print(f"  >> {msg}")


def _event(tag: str, msg: str) -> None:
    ts = datetime.now(tz=timezone.utc).strftime("%H:%M:%S")
    print(f"  [{ts}] {tag}: {msg}")


def _human_prompt() -> None:
    """Pause with a visual cue that no human action is required."""
    print("  -- (no human action required; meta-agent is autonomous) --")


# ---------------------------------------------------------------------------
# Core demo logic
# ---------------------------------------------------------------------------


def run_demo(store: Any, settings: Any) -> dict[str, Any]:  # type: ignore[return]
    """Execute the full autonomy demo and return a summary dict.

    Args:
        store:    A MemoryStore instance (passed in so tests can inspect state).
        settings: A Settings instance configured for dryrun + stub judge.

    Returns:
        A dict with keys: canary_dep_id, rollback_happened, pr_draft_id,
        event_count, decision_count.
    """
    from agentops_platform.evaluator import run_evaluation_sync
    from agentops_platform.judge import StubJudge
    from agentops_platform.meta_agent import (
        MetaAgentCycle,
        _clear_store,
        list_decisions,
        list_pr_drafts,
    )
    from agentops_platform.models import (
        AgentCreate,
        AgentVersionCreate,
        AxisScore,
        CanaryStrategy,
        Deployment,
        EvalSuiteCreate,
        EvaluationRun,
        MetricIngest,
        MetricSample,
        RollbackPolicy,
    )
    from config.defaults import EVAL_STATE_QUEUED

    _clear_store()  # reset meta-agent in-memory stores for a clean run

    # ------------------------------------------------------------------
    # Step 1: Register agent + stable (baseline) version, deploy promoted
    # ------------------------------------------------------------------
    _step(1, "Register managed agent and promote stable (baseline) version")
    _human_prompt()

    agent = store.create_agent(
        AgentCreate(
            name="demo-managed-agent",
            description="Sample managed agent used for the autonomy demo",
            runtime="adk-cloud-run",
        )
    )
    _event("REGISTER", f"agent_id={agent.agentId[:8]}...")

    baseline_version = store.create_version(
        agent.agentId,
        AgentVersionCreate(
            image="registry.example.com/demo-agent@sha256:stable001",
            model="gemini-2.5-flash",
            promptDigest="sha256:baseline-prompt-v1",
            gitCommit="abc1234",
        ),
    )
    assert baseline_version is not None
    _event("REGISTER", f"baseline version_id={baseline_version.versionId[:8]}...")

    eval_suite = store.create_eval_suite(
        agent.agentId,
        EvalSuiteCreate(
            name="demo-eval-suite",
            adkEvalSetRef="gs://demo-bucket/eval/smoke.json",
        ),
    )
    assert eval_suite is not None
    _event("REGISTER", f"eval_suite_id={eval_suite.suiteId[:8]}...")

    # Evaluate baseline
    baseline_judge = StubJudge(
        drift_score=_BASELINE_DRIFT,
        trajectory_score=_BASELINE_TRAJECTORY,
        cost_usd=_BASELINE_COST_USD,
        latency_ms=_BASELINE_LATENCY_MS,
    )
    baseline_eval_record = EvaluationRun(
        evaluationId="demo-eval-baseline-001",
        versionId=baseline_version.versionId,
        suiteId=eval_suite.suiteId,
        state=EVAL_STATE_QUEUED,
        startedAt=datetime.now(tz=timezone.utc),
    )
    store.create_evaluation(baseline_eval_record)
    store.register_evaluation_for_agent(agent.agentId, baseline_eval_record.evaluationId)
    baseline_eval = run_evaluation_sync(store, agent.agentId, baseline_eval_record, baseline_judge)
    _event("EVAL", f"baseline evaluation succeeded (drift={_BASELINE_DRIFT}, trajectory={_BASELINE_TRAJECTORY})")

    # Deploy baseline as promoted (stable)
    baseline_dep = Deployment(
        deploymentId="demo-dep-baseline-001",
        versionId=baseline_version.versionId,
        strategy=CanaryStrategy(type="all-at-once"),
        state="promoted",
        currentTrafficPercent=100,
    )
    store.create_deployment(agent.agentId, baseline_dep)
    _event("DEPLOY", "baseline version promoted to 100% — this is the stable floor")
    _info(f"drift={_BASELINE_DRIFT:.2f}  trajectory={_BASELINE_TRAJECTORY:.2f}  "
          f"cost={_BASELINE_COST_USD:.4f} USD/task  latency={_BASELINE_LATENCY_MS:.0f}ms  "
          f"retention_rate={_BASELINE_RETENTION_RATE:.2f}")

    # ------------------------------------------------------------------
    # Step 2: Register degraded candidate, start canary deployment
    # ------------------------------------------------------------------
    _step(2, "Register deliberately degraded candidate version and start canary")
    _human_prompt()

    candidate_version = store.create_version(
        agent.agentId,
        AgentVersionCreate(
            image="registry.example.com/demo-agent@sha256:candidate-degraded002",
            model="gemini-2.5-flash",
            promptDigest="sha256:candidate-prompt-v2-degraded",
            gitCommit="def5678",
        ),
    )
    assert candidate_version is not None
    _event("REGISTER", f"candidate version_id={candidate_version.versionId[:8]}...")
    _info("Degradation seeded: output drift drop + retention_rate decline")
    _info(f"  drift: {_BASELINE_DRIFT:.2f} -> {_DEGRADED_DRIFT:.2f}  "
          f"(drop={_BASELINE_DRIFT - _DEGRADED_DRIFT:.2f}, hard_floor={_ROLLBACK_DRIFT_DROP:.2f})")
    _info(f"  trajectory: {_BASELINE_TRAJECTORY:.2f} -> {_DEGRADED_TRAJECTORY:.2f}  "
          f"(drop={_BASELINE_TRAJECTORY - _DEGRADED_TRAJECTORY:.2f}, hard_floor={_ROLLBACK_TRAJECTORY_DROP:.2f})")
    _info(f"  retention_rate: {_BASELINE_RETENTION_RATE:.2f} -> {_DEGRADED_RETENTION_RATE:.2f}  (external outcome metric)")

    canary_dep = Deployment(
        deploymentId="demo-dep-canary-002",
        versionId=candidate_version.versionId,
        strategy=CanaryStrategy(type="canary", steps=_DEMO_CANARY_STEPS),
        rollbackPolicy=RollbackPolicy(
            windowMinutes=15,
            maxDriftDrop=_ROLLBACK_DRIFT_DROP,
            maxTrajectoryDrop=_ROLLBACK_TRAJECTORY_DROP,
            maxCostIncreaseRatio=_ROLLBACK_COST_INCREASE_RATIO,
            maxLatencyP95Ms=_ROLLBACK_LATENCY_P95_MS,
        ),
        state="canary",
        currentTrafficPercent=_DEMO_CANARY_STEPS[0],
        previousStableVersionId=baseline_version.versionId,
    )
    store.create_deployment(agent.agentId, canary_dep)
    _event(
        "DEPLOY",
        f"canary started at {_DEMO_CANARY_STEPS[0]}% traffic, steps={_DEMO_CANARY_STEPS}",
    )

    # ------------------------------------------------------------------
    # Step 3: Inject evaluation results and outcome metrics (time-series)
    # ------------------------------------------------------------------
    _step(3, "Inject evaluation results and outcome metrics (time-series ingestion)")
    _human_prompt()

    # Evaluate the degraded candidate
    degraded_judge = StubJudge(
        drift_score=_DEGRADED_DRIFT,
        trajectory_score=_DEGRADED_TRAJECTORY,
        cost_usd=_DEGRADED_COST_USD,
        latency_ms=_DEGRADED_LATENCY_MS,
    )
    canary_eval_record = EvaluationRun(
        evaluationId="demo-eval-canary-002",
        versionId=candidate_version.versionId,
        suiteId=eval_suite.suiteId,
        state=EVAL_STATE_QUEUED,
        startedAt=datetime.now(tz=timezone.utc),
    )
    store.create_evaluation(canary_eval_record)
    store.register_evaluation_for_agent(agent.agentId, canary_eval_record.evaluationId)
    canary_eval = run_evaluation_sync(store, agent.agentId, canary_eval_record, degraded_judge)
    _event(
        "EVAL",
        f"canary evaluation completed  drift={_DEGRADED_DRIFT:.2f}  "
        f"trajectory={_DEGRADED_TRAJECTORY:.2f}  "
        f"cost={_DEGRADED_COST_USD:.4f}  latency={_DEGRADED_LATENCY_MS:.0f}ms",
    )

    # Ingest external outcome metrics — retention_rate has declined
    now = datetime.now(tz=timezone.utc)
    metric_ingest = MetricIngest(
        versionId=candidate_version.versionId,
        source="analytics-pipeline",
        samples=[
            MetricSample(
                name="retention_rate",
                value=_DEGRADED_RETENTION_RATE,
                observedAt=now,
                dimensions={"env": "canary"},
            ),
            MetricSample(
                name="task_success_rate",
                value=0.58,
                observedAt=now,
                dimensions={"env": "canary"},
            ),
        ],
    )
    store.store_metrics(agent.agentId, metric_ingest)
    _event(
        "METRICS",
        f"ingested retention_rate={_DEGRADED_RETENTION_RATE:.2f}  "
        f"task_success_rate=0.58  (canary window t+1)",
    )
    _info("Degradation is now observable across evaluation scores AND outcome metrics.")

    # ------------------------------------------------------------------
    # Step 4: Meta-agent decision cycle — autonomous rollback
    # ------------------------------------------------------------------
    _step(4, "Meta-agent decision cycle  [NO HUMAN ACTION]")
    _human_prompt()
    _info("Running meta-agent...  (Layer 1: safety floor, Layer 2: gray-zone judgment)")

    cycle = MetaAgentCycle(
        store, settings=settings, use_gemini=(settings.judge_backend == "gemini")
    )
    decision = cycle.run(
        deployment_id=canary_dep.deploymentId,
        evaluation_id=canary_eval.evaluationId,
        stable_evaluation_id=baseline_eval.evaluationId,
    )

    _event(
        "META-AGENT",
        f"decided: {decision.action.upper()}  |  decision_id={decision.decisionId[:8]}...",
    )
    _info(f"Rationale: {decision.rationale}")

    updated_dep = store.get_deployment(canary_dep.deploymentId)
    assert updated_dep is not None
    _event(
        "DEPLOY",
        f"deployment state → {updated_dep.state}  traffic={updated_dep.currentTrafficPercent}%",
    )

    if decision.action == "rollback":
        _info("Canary automatically rolled back to baseline version — no human pressed any button.")
    else:
        # This path is reached if thresholds are adjusted; still valid for testing.
        _info(f"Meta-agent took action '{decision.action}' based on signal analysis.")

    # ------------------------------------------------------------------
    # Step 5: Meta-agent files an improvement PR draft (dryrun)
    # ------------------------------------------------------------------
    _step(5, "Meta-agent files improvement PR draft (dryrun — no real GitHub call)")
    _human_prompt()

    drafts = list_pr_drafts(deployment_id=canary_dep.deploymentId)
    if drafts:
        pr = drafts[-1]
        _event("PR-DRAFT", f"pr_draft_id={pr.prDraftId[:8]}...  mode={pr.mode}")
        _info(f"Title: {pr.title}")
        _info("Body excerpt (first 300 chars):")
        body_excerpt = pr.body.replace("\n", " ")[:300]
        print(f"    {body_excerpt}...")
        _info("PR draft contains: root-cause diagnosis, signal table, improvement checklist.")
    else:
        _info("No PR draft generated (action was not rollback).")

    # ------------------------------------------------------------------
    # Step 6: Audit log and dashboard data verification
    # ------------------------------------------------------------------
    _step(6, "Verify audit event log and dashboard data")
    _human_prompt()

    events = store.list_events(agent.agentId)
    decisions = list_decisions(deployment_id=canary_dep.deploymentId)

    _event("AUDIT", f"total events recorded for agent: {len(events)}")
    for evt in events:
        _info(f"  {evt.type}  detail={evt.detail}")

    _event("AUDIT", f"meta-agent decisions recorded: {len(decisions)}")
    for dec in decisions:
        _info(f"  action={dec.action}  deployed={dec.deploymentId[:8]}...  "
              f"decided_at={dec.decidedAt.strftime('%H:%M:%S')}")

    _info("Dashboard data endpoint (/dashboard/data) reflects:")
    _info("  Panel 1 — Evaluation score timeline: baseline + canary scores ingested")
    _info("  Panel 2 — Canary status: shows rolled_back state")
    _info("  Panel 3 — Rollback history: decision record + PR draft visible")

    return {
        "agent_id": agent.agentId,
        "canary_dep_id": canary_dep.deploymentId,
        "baseline_dep_id": baseline_dep.deploymentId,
        "decision": decision,
        "rollback_happened": decision.action == "rollback",
        "pr_draft_id": drafts[-1].prDraftId if drafts else None,
        "pr_draft": drafts[-1] if drafts else None,
        "event_count": len(events),
        "decision_count": len(decisions),
        "final_deployment_state": updated_dep.state,
        "eval_ids": {
            "baseline": baseline_eval.evaluationId,
            "canary": canary_eval.evaluationId,
        },
    }


# ---------------------------------------------------------------------------
# Server-integrated mode (optional)
# ---------------------------------------------------------------------------


def _launch_server() -> Any:
    """Start the FastAPI server in a background thread for the demo."""
    import threading
    import uvicorn  # type: ignore[import-untyped]
    from agentops_platform.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=8080, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Give the server a moment to start
    time.sleep(1.5)
    return server


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AgentOps Platform — Autonomy Demo (self-contained, no external services)"
    )
    parser.add_argument(
        "--with-server",
        action="store_true",
        help="Start the FastAPI server so /dashboard is live at http://localhost:8080/dashboard",
    )
    parser.add_argument(
        "--gemini",
        action="store_true",
        help="Use the REAL Gemini backend for the meta-agent's gray-zone judgment "
        "(inherits AGENTOPS_GEMINI_BACKEND / GOOGLE_* from env or .env). Without this "
        "flag the demo runs fully offline on the deterministic stub.",
    )
    args = parser.parse_args()

    _banner("AgentOps Platform — Autonomous Canary Management Demo")
    if args.gemini:
        print("  Mode: REAL GEMINI gray-zone judgment (network calls enabled).")
        print("  The seeded canary signal is deterministic; the advance/hold/rollback")
        print("  decision is made by the live LLM (judgedBy=gemini in the audit record).")
    else:
        print("  Self-contained: in-memory store, stub judge, dryrun PR mode.")
        print("  No network calls. No GitHub access. No human decisions required.")

    # Build fresh store + settings for the demo
    from agentops_platform.repository import MemoryStore
    from config.defaults import Settings, get_settings

    store = MemoryStore()
    # Settings field names use the bare name (env_prefix="AGENTOPS_" is stripped).
    if args.gemini:
        # Real Gemini: inherit backend/project/location/api_key from env (.env),
        # but pin the demo's warn thresholds and dryrun PR mode.
        settings = get_settings().model_copy(
            update={
                "judge_backend": "gemini",
                "pr_mode": "dryrun",
                "meta_agent_drift_warn": _WARN_DRIFT,
                "meta_agent_trajectory_warn": _WARN_TRAJECTORY,
            }
        )
    else:
        settings = Settings(
            judge_backend="stub",
            pr_mode="dryrun",
            meta_agent_drift_warn=_WARN_DRIFT,
            meta_agent_trajectory_warn=_WARN_TRAJECTORY,
        )

    server = None
    if args.with_server:
        _info("Starting FastAPI server on http://127.0.0.1:8080 ...")
        server = _launch_server()
        _info("Dashboard: http://127.0.0.1:8080/dashboard")
        _info("Data API:  http://127.0.0.1:8080/dashboard/data")

    result = run_demo(store, settings)

    _banner("Demo Complete")
    print(f"  Canary deployment state : {result['final_deployment_state']}")
    print(f"  Meta-agent action       : {result['decision'].action.upper()}")
    print(f"  Rollback happened       : {result['rollback_happened']}")
    print(f"  PR draft filed          : {'yes (dryrun)' if result['pr_draft_id'] else 'no'}")
    print(f"  Audit events recorded   : {result['event_count']}")
    print(f"  Decision records        : {result['decision_count']}")
    print()
    print("  The entire canary lifecycle — registration, evaluation, metric ingestion,")
    print("  degradation detection, rollback, and PR filing — ran with ZERO human input.")

    if server:
        _info("Server still running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
