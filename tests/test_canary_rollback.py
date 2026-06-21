"""
End-to-end canary → auto-rollback state machine tests.

This test file exercises the core killer-demo scenario:
  1. Register agent + version
  2. Create a canary deployment
  3. Run evaluation with a degraded judge (scores breach the rollback policy)
  4. Call apply_policy_check — deployment transitions to rolled_back
  5. A deployment.rolled_back event is emitted and persisted

These tests use the domain layer directly (no HTTP) to validate the
deterministic safety floor in isolation.
"""

from __future__ import annotations

import pytest

from agentops_platform.evaluator import run_evaluation_sync
from agentops_platform.judge import StubJudge
from agentops_platform.models import (
    AgentCreate,
    AgentVersionCreate,
    CanaryStrategy,
    Deployment,
    DeploymentCreate,
    EvalSuiteCreate,
    EvaluationCreate,
    EvaluationRun,
    RollbackPolicy,
)
from agentops_platform.repository import MemoryStore
from agentops_platform.rollback import apply_policy_check
from config.defaults import EVAL_STATE_QUEUED
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class TestCanaryAutoRollback:
    def _bootstrap(
        self, store: MemoryStore, judge: StubJudge
    ) -> tuple[str, str, str, str]:
        """Create agent, version, suite, deployment. Return (agent_id, version_id, suite_id, dep_id)."""
        agent = store.create_agent(AgentCreate(name="my-agent", runtime="adk-cloud-run"))
        version = store.create_version(
            agent.agentId,
            AgentVersionCreate(
                image="gcr.io/p/img@sha256:abc",
                model="gemini-2.0-flash",
                promptDigest="sha256:111",
            ),
        )
        suite = store.create_eval_suite(
            agent.agentId,
            EvalSuiteCreate(name="smoke", adkEvalSetRef="gs://b/eval.json"),
        )
        dep = Deployment(
            deploymentId="dep-canary-001",
            versionId=version.versionId,  # type: ignore[union-attr]
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
        return (
            agent.agentId,
            version.versionId,  # type: ignore[union-attr]
            suite.suiteId,  # type: ignore[union-attr]
            dep.deploymentId,
        )

    def test_canary_stable_judge_no_rollback(
        self, store: MemoryStore, stub_judge: StubJudge
    ) -> None:
        """Healthy canary scores should not trigger rollback."""
        agent_id, version_id, suite_id, dep_id = self._bootstrap(store, stub_judge)
        deployment = store.get_deployment(dep_id)
        assert deployment is not None

        # Run eval for canary and stable (both use the same stub → same good scores)
        run = EvaluationRun(
            evaluationId="eval-canary-001",
            versionId=version_id,
            suiteId=suite_id,
            state=EVAL_STATE_QUEUED,
            startedAt=_now(),
        )
        store.create_evaluation(run)
        store._register_evaluation_for_agent(agent_id, run.evaluationId)
        canary_eval = run_evaluation_sync(store, agent_id, run, stub_judge)

        # stable baseline (same version, same judge)
        stable_run = EvaluationRun(
            evaluationId="eval-stable-001",
            versionId=version_id,
            suiteId=suite_id,
            state=EVAL_STATE_QUEUED,
            startedAt=_now(),
        )
        store.create_evaluation(stable_run)
        store._register_evaluation_for_agent(agent_id, stable_run.evaluationId)
        stable_eval = run_evaluation_sync(store, agent_id, stable_run, stub_judge)

        result = apply_policy_check(deployment, canary_eval.scores, stable_eval.scores)
        assert result is None, "Healthy canary should not trigger rollback"
        # Deployment state unchanged
        dep = store.get_deployment(dep_id)
        assert dep is not None
        assert dep.state == "canary"

    def test_canary_degraded_judge_triggers_rollback(
        self, store: MemoryStore, stub_judge: StubJudge
    ) -> None:
        """A degraded canary (bad scores) must trigger auto-rollback."""
        agent_id, version_id, suite_id, dep_id = self._bootstrap(store, stub_judge)
        deployment = store.get_deployment(dep_id)
        assert deployment is not None

        # Stable version evaluated with good judge
        stable_run = EvaluationRun(
            evaluationId="eval-stable-002",
            versionId=version_id,
            suiteId=suite_id,
            state=EVAL_STATE_QUEUED,
            startedAt=_now(),
        )
        store.create_evaluation(stable_run)
        store._register_evaluation_for_agent(agent_id, stable_run.evaluationId)
        stable_eval = run_evaluation_sync(store, agent_id, stable_run, stub_judge)

        # Canary version evaluated with degraded judge
        degraded_judge = StubJudge(
            drift_score=0.40,       # stable=0.85, drop=0.45 >> threshold 0.10
            trajectory_score=0.35,
            cost_usd=0.05,
            latency_ms=5000.0,
        )
        canary_run = EvaluationRun(
            evaluationId="eval-canary-002",
            versionId=version_id,
            suiteId=suite_id,
            state=EVAL_STATE_QUEUED,
            startedAt=_now(),
        )
        store.create_evaluation(canary_run)
        store._register_evaluation_for_agent(agent_id, canary_run.evaluationId)
        canary_eval = run_evaluation_sync(store, agent_id, canary_run, degraded_judge)

        result = apply_policy_check(deployment, canary_eval.scores, stable_eval.scores)
        assert result is not None, "Degraded canary MUST trigger rollback"

        updated_dep, event = result
        assert updated_dep.state == "rolled_back"
        assert updated_dep.currentTrafficPercent == 0
        assert "auto-rollback" in (event.detail or "")
        assert event.type == "deployment.rolled_back"

        # Persist and verify
        store.update_deployment(updated_dep)
        store.append_event(agent_id, event)

        dep = store.get_deployment(dep_id)
        assert dep is not None
        assert dep.state == "rolled_back"

        events = store.list_events(agent_id)
        rollback_events = [e for e in events if e.type == "deployment.rolled_back"]
        assert len(rollback_events) >= 1

    def test_rollback_is_terminal(self, store: MemoryStore, stub_judge: StubJudge) -> None:
        """Once rolled back, a second rollback attempt must raise ValueError."""
        from agentops_platform.rollback import rollback_deployment, TERMINAL_STATES

        dep = Deployment(
            deploymentId="dep-terminal-test",
            versionId="ver-001",
            strategy=CanaryStrategy(type="canary", steps=[10, 50]),
            rollbackPolicy=None,
            state="rolled_back",
            currentTrafficPercent=0,
        )
        assert dep.state in TERMINAL_STATES
        with pytest.raises(ValueError, match="terminal state"):
            rollback_deployment(dep)

    def test_full_canary_advance_then_rollback(
        self, store: MemoryStore, stub_judge: StubJudge
    ) -> None:
        """Simulate: 10% → 25% → rollback. All state transitions must be correct."""
        from agentops_platform.rollback import advance_canary_step, rollback_deployment

        dep = Deployment(
            deploymentId="dep-full-flow",
            versionId="ver-001",
            strategy=CanaryStrategy(type="canary", steps=[10, 25, 50, 100]),
            rollbackPolicy=None,
            state="pending",
            currentTrafficPercent=0,
        )
        store.create_deployment("dummy-agent", dep)

        # Step 1: pending → canary 10%
        dep_10, event_10 = advance_canary_step(dep, 10)
        assert dep_10.state == "canary"
        assert dep_10.currentTrafficPercent == 10
        assert event_10.type == "deployment.step_advanced"

        # Step 2: canary 10% → canary 25%
        dep_25, event_25 = advance_canary_step(dep_10, 25)
        assert dep_25.state == "canary"
        assert dep_25.currentTrafficPercent == 25

        # Step 3: rollback
        dep_rb, event_rb = rollback_deployment(dep_25, reason="threshold breach")
        assert dep_rb.state == "rolled_back"
        assert dep_rb.currentTrafficPercent == 0
        assert "threshold breach" in (event_rb.detail or "")
