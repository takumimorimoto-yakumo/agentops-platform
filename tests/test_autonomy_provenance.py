"""
Regression tests for autonomy-integrity guarantees added 2026-06-25:

  - DecisionRecord.judgedBy records the provenance of every decision so a
    silent degradation from real LLM judgment to a deterministic fallback is
    always visible in the audit trail.
  - Gemini call failure is recorded as 'gemini_failed' (NOT a real AI decision).
  - A canary with no stable baseline is held (never silently auto-advanced).
  - A single axis degrading severely (near the hard floor) rolls back on its
    own, even when no second axis is concerning.

These lock in behaviour that the autonomy story depends on; do not weaken
without updating docs/JUDGE_QA.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from agentops_platform.meta_agent import MetaAgentCycle, _clear_store
from agentops_platform.models import (
    AgentCreate,
    AgentVersionCreate,
    AxisScore,
    CanaryStrategy,
    Deployment,
    EvaluationRun,
    RollbackPolicy,
)
from agentops_platform.repository import MemoryStore
from config.defaults import EVAL_STATE_QUEUED, Settings


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "judge_backend": "stub",
        "judge_model": "gemini-2.0-flash",
        "pr_mode": "dryrun",
        "meta_agent_drift_warn": 0.05,
        "meta_agent_trajectory_warn": 0.05,
        "meta_agent_cost_warn": 0.20,
        "meta_agent_latency_warn_ms": 1500.0,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def _bootstrap(store: MemoryStore) -> tuple[str, str, str]:
    """Create agent + version + canary deployment (hard floor at 0.10 drops)."""
    agent = store.create_agent(AgentCreate(name="t", runtime="adk-cloud-run"))
    version = store.create_version(
        agent.agentId,
        AgentVersionCreate(image="gcr.io/p/i@sha256:a", model="gemini-2.0-flash", promptDigest="d"),
    )
    assert version is not None
    dep = Deployment(
        deploymentId="dep-prov-001",
        versionId=version.versionId,
        strategy=CanaryStrategy(type="canary", steps=[10, 25, 50, 100]),
        rollbackPolicy=RollbackPolicy(
            windowMinutes=15,
            maxDriftDrop=0.10,
            maxTrajectoryDrop=0.10,
            maxCostIncreaseRatio=0.50,
            maxLatencyP95Ms=3000.0,
        ),
        state="canary",
        currentTrafficPercent=10,
    )
    store.create_deployment(agent.agentId, dep)
    return agent.agentId, version.versionId, dep.deploymentId


def _add_eval(
    store: MemoryStore,
    agent_id: str,
    version_id: str,
    eval_id: str,
    *,
    drift: float,
    trajectory: float,
    cost: float = 0.002,
    latency: float = 400.0,
) -> EvaluationRun:
    run = EvaluationRun(
        evaluationId=eval_id,
        versionId=version_id,
        suiteId="suite-1",
        state="succeeded",
        scores=[
            AxisScore(axis="drift", score=drift),
            AxisScore(axis="trajectory", score=trajectory),
            AxisScore(axis="cost", score=cost),
            AxisScore(axis="latency", score=latency),
        ],
        startedAt=_now(),
        finishedAt=_now(),
    )
    store.create_evaluation(run)
    store.register_evaluation_for_agent(agent_id, eval_id)
    return run


@pytest.fixture(autouse=True)
def _clear() -> None:
    _clear_store()


def test_auto_advance_provenance() -> None:
    """Healthy canary advances with judgedBy='auto_advance'."""
    store = MemoryStore()
    agent_id, vid, dep_id = _bootstrap(store)
    _add_eval(store, agent_id, vid, "c", drift=0.90, trajectory=0.92)
    _add_eval(store, agent_id, vid, "s", drift=0.90, trajectory=0.92)
    rec = MetaAgentCycle(store, _settings(), use_gemini=False).run("dep-prov-001", "c", "s")
    assert rec.action == "advance"
    assert rec.judgedBy == "auto_advance"


def test_safety_floor_provenance() -> None:
    """A hard-floor breach rolls back with judgedBy='safety_floor'."""
    store = MemoryStore()
    agent_id, vid, dep_id = _bootstrap(store)
    _add_eval(store, agent_id, vid, "c", drift=0.78, trajectory=0.92)  # drop 0.12 > 0.10 floor
    _add_eval(store, agent_id, vid, "s", drift=0.90, trajectory=0.92)
    rec = MetaAgentCycle(store, _settings(), use_gemini=False).run("dep-prov-001", "c", "s")
    assert rec.action == "rollback"
    assert rec.judgedBy == "safety_floor"


def test_heuristic_rollback_provenance() -> None:
    """Two concerning axes (below floor) roll back via the deterministic heuristic."""
    store = MemoryStore()
    agent_id, vid, dep_id = _bootstrap(store)
    # drift drop 0.085 and trajectory drop 0.085: concerning (>0.075) but < floor 0.10
    # and < severe 0.09 — so this exercises the concerning_count>=2 path, not severe.
    _add_eval(store, agent_id, vid, "c", drift=0.815, trajectory=0.835)
    _add_eval(store, agent_id, vid, "s", drift=0.90, trajectory=0.92)
    rec = MetaAgentCycle(store, _settings(), use_gemini=False).run("dep-prov-001", "c", "s")
    assert rec.action == "rollback"
    assert rec.judgedBy == "heuristic"


def test_single_axis_severe_rolls_back() -> None:
    """R6: one axis severely degraded (near floor) rolls back even if alone."""
    store = MemoryStore()
    agent_id, vid, dep_id = _bootstrap(store)
    # drift drop 0.095: > severe (0.05*1.8=0.09), < floor 0.10. Other axes healthy.
    _add_eval(store, agent_id, vid, "c", drift=0.805, trajectory=0.92)
    _add_eval(store, agent_id, vid, "s", drift=0.90, trajectory=0.92)
    rec = MetaAgentCycle(store, _settings(), use_gemini=False).run("dep-prov-001", "c", "s")
    assert rec.action == "rollback"
    assert rec.judgedBy == "heuristic"
    assert "single-axis severe" in rec.rationale


def test_missing_baseline_holds_not_advances() -> None:
    """A canary with no stable baseline is held, never silently advanced."""
    store = MemoryStore()
    agent_id, vid, dep_id = _bootstrap(store)
    _add_eval(store, agent_id, vid, "c", drift=0.84, trajectory=0.86)
    rec = MetaAgentCycle(store, _settings(), use_gemini=False).run(
        "dep-prov-001", evaluation_id="c", stable_evaluation_id=None
    )
    assert rec.action == "hold"
    assert rec.judgedBy == "missing_baseline"
    dep = store.get_deployment("dep-prov-001")
    assert dep is not None
    assert dep.state == "canary"
    assert dep.currentTrafficPercent == 10  # unchanged


def test_gemini_decision_provenance() -> None:
    """When the LLM actually decides, judgedBy='gemini'."""
    store = MemoryStore()
    agent_id, vid, dep_id = _bootstrap(store)
    _add_eval(store, agent_id, vid, "c", drift=0.84, trajectory=0.86)  # gray zone, < floor
    _add_eval(store, agent_id, vid, "s", drift=0.90, trajectory=0.92)
    resp = '{"action": "rollback", "rationale": "LLM judged degradation"}'
    with patch("agentops_platform.gemini_client.generate_text", return_value=resp):
        rec = MetaAgentCycle(store, _settings(judge_backend="gemini"), use_gemini=True).run(
            "dep-prov-001", "c", "s"
        )
    assert rec.action == "rollback"
    assert rec.judgedBy == "gemini"


def test_gemini_failure_is_recorded_as_degraded() -> None:
    """When the LLM call fails, the decision is marked 'gemini_failed', not 'gemini'."""
    store = MemoryStore()
    agent_id, vid, dep_id = _bootstrap(store)
    _add_eval(store, agent_id, vid, "c", drift=0.84, trajectory=0.86)
    _add_eval(store, agent_id, vid, "s", drift=0.90, trajectory=0.92)
    with patch("agentops_platform.gemini_client.generate_text", return_value=""):
        rec = MetaAgentCycle(store, _settings(judge_backend="gemini"), use_gemini=True).run(
            "dep-prov-001", "c", "s"
        )
    assert rec.judgedBy == "gemini_failed"
    assert rec.action == "hold"  # safe default on failure
