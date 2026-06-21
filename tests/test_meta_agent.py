"""
Tests for the autonomous meta-agent (Wave 2).

Covers:
  - Gray-zone signal computation
  - Deterministic advance/hold/rollback judgment (no LLM)
  - Safety-floor bypass: meta-agent respects Layer 1 (RollbackPolicy)
  - PR draft generation in dryrun mode
  - Gemini backend switching (stub confirms GeminiJudge factory)
  - Dashboard data endpoint
  - DecisionRecord audit trail
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from agentops_platform.evaluator import run_evaluation_sync
from agentops_platform.judge import StubJudge, GeminiJudge, get_judge
from agentops_platform.meta_agent import (
    MetaAgentCycle,
    _compute_signal,
    _is_in_gray_zone,
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
    GrayZoneSignal,
    MetricIngest,
    MetricSample,
    RollbackPolicy,
)
from agentops_platform.repository import MemoryStore
from config.defaults import Settings, get_settings, EVAL_STATE_QUEUED
from datetime import datetime, timezone


# ── Shared fixtures ───────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_scores(
    drift: float = 0.85,
    trajectory: float = 0.90,
    cost: float = 0.002,
    latency: float = 400.0,
) -> list[AxisScore]:
    return [
        AxisScore(axis="drift", score=drift),
        AxisScore(axis="trajectory", score=trajectory),
        AxisScore(axis="cost", score=cost),
        AxisScore(axis="latency", score=latency),
    ]


def _make_test_settings(**overrides: object) -> Settings:
    """Return Settings with all AGENTOPS_ env requirements satisfied."""
    kwargs = {
        "AGENTOPS_JUDGE_BACKEND": "stub",
        "AGENTOPS_JUDGE_MODEL": "gemini-2.0-flash",
        "AGENTOPS_PR_MODE": "dryrun",
        "AGENTOPS_META_AGENT_DRIFT_WARN": "0.05",
        "AGENTOPS_META_AGENT_TRAJECTORY_WARN": "0.05",
        "AGENTOPS_META_AGENT_COST_WARN": "0.20",
        "AGENTOPS_META_AGENT_LATENCY_WARN_MS": "1500.0",
    }
    kwargs.update({k.upper() if not k.startswith("AGENTOPS_") else k: str(v)
                   for k, v in overrides.items()})
    return Settings(**{k.replace("AGENTOPS_", "").lower(): v
                       for k, v in kwargs.items()
                       if k.startswith("AGENTOPS_")})


@pytest.fixture(autouse=True)
def clear_meta_store() -> None:
    """Clear meta-agent in-memory stores before each test."""
    _clear_store()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def _bootstrap_store(store: MemoryStore) -> tuple[str, str, str, str]:
    """Create agent, version, suite, canary deployment. Return IDs."""
    agent = store.create_agent(AgentCreate(name="test-agent", runtime="adk-cloud-run"))
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
        EvalSuiteCreate(name="smoke", adkEvalSetRef="gs://bucket/eval.json"),
    )
    dep = Deployment(
        deploymentId="dep-meta-001",
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


def _run_eval(
    store: MemoryStore,
    agent_id: str,
    version_id: str,
    suite_id: str,
    judge: StubJudge,
    eval_id: str | None = None,
) -> EvaluationRun:
    run = EvaluationRun(
        evaluationId=eval_id or f"eval-{id(judge)}",
        versionId=version_id,
        suiteId=suite_id,
        state=EVAL_STATE_QUEUED,
        startedAt=_now(),
    )
    store.create_evaluation(run)
    store._register_evaluation_for_agent(agent_id, run.evaluationId)
    return run_evaluation_sync(store, agent_id, run, judge)


# ── Signal computation tests ──────────────────────────────────────────────────


class TestGrayZoneSignal:
    def test_compute_signal_healthy(self) -> None:
        stable = _make_scores(drift=0.85, trajectory=0.90, cost=0.002, latency=400.0)
        canary = _make_scores(drift=0.85, trajectory=0.90, cost=0.002, latency=400.0)
        signal = _compute_signal(canary, stable, [])
        assert signal.drift_drop == pytest.approx(0.0)
        assert signal.trajectory_drop == pytest.approx(0.0)
        assert signal.cost_increase_ratio == pytest.approx(0.0)
        assert signal.canary_latency_ms == pytest.approx(400.0)

    def test_compute_signal_drift_drop(self) -> None:
        stable = _make_scores(drift=0.85)
        canary = _make_scores(drift=0.78)
        signal = _compute_signal(canary, stable, [])
        assert signal.drift_drop == pytest.approx(0.07, abs=1e-9)

    def test_compute_signal_cost_increase(self) -> None:
        stable = _make_scores(cost=0.002)
        canary = _make_scores(cost=0.003)
        signal = _compute_signal(canary, stable, [])
        assert signal.cost_increase_ratio == pytest.approx(0.5, abs=1e-6)

    def test_compute_signal_outcome_metrics(self) -> None:
        stable = _make_scores()
        canary = _make_scores()
        sample = MetricSample(name="retention_rate", value=0.72, observedAt=_now())
        ingest = MetricIngest(versionId="v1", source="analytics", samples=[sample])
        signal = _compute_signal(canary, stable, [ingest])
        assert "retention_rate" in signal.outcome_metrics
        assert signal.outcome_metrics["retention_rate"] == pytest.approx(0.72)

    def test_is_in_gray_zone_drift(self) -> None:
        # Drift drop of 0.07 > warn threshold of 0.05
        signal = GrayZoneSignal(drift_drop=0.07)
        settings = _make_test_settings()
        assert _is_in_gray_zone(signal, settings) is True

    def test_is_not_in_gray_zone(self) -> None:
        signal = GrayZoneSignal(drift_drop=0.01, trajectory_drop=0.01)
        settings = _make_test_settings()
        assert _is_in_gray_zone(signal, settings) is False

    def test_is_in_gray_zone_latency(self) -> None:
        signal = GrayZoneSignal(canary_latency_ms=2000.0)  # > warn 1500
        settings = _make_test_settings()
        assert _is_in_gray_zone(signal, settings) is True


# ── MetaAgentCycle: advance path ──────────────────────────────────────────────


class TestMetaAgentAdvance:
    def test_healthy_scores_advance_canary(self, store: MemoryStore) -> None:
        """When all scores are fine, the meta-agent advances the canary step."""
        agent_id, version_id, suite_id, dep_id = _bootstrap_store(store)

        good_judge = StubJudge(drift_score=0.85, trajectory_score=0.90)
        canary_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-c1")
        stable_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-s1")

        settings = _make_test_settings()
        cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
        record = cycle.run(dep_id, canary_eval.evaluationId, stable_eval.evaluationId)

        assert record.action == "advance"
        updated = store.get_deployment(dep_id)
        assert updated is not None
        # Should have advanced to 25% (next step after 10%)
        assert updated.currentTrafficPercent == 25

    def test_decision_record_persisted(self, store: MemoryStore) -> None:
        agent_id, version_id, suite_id, dep_id = _bootstrap_store(store)
        good_judge = StubJudge()
        canary_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-c2")
        stable_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-s2")

        settings = _make_test_settings()
        cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
        record = cycle.run(dep_id, canary_eval.evaluationId, stable_eval.evaluationId)

        decisions = list_decisions(deployment_id=dep_id)
        assert len(decisions) >= 1
        assert decisions[-1].decisionId == record.decisionId
        assert decisions[-1].action == "advance"


# ── MetaAgentCycle: hold path ─────────────────────────────────────────────────


class TestMetaAgentHold:
    def test_mild_gray_zone_single_axis_advances(self, store: MemoryStore) -> None:
        """Single-axis mild gray-zone breach: below aggressive threshold → advance.

        The deterministic fallback requires 2+ axes to exceed the aggressive
        threshold (warn * 1.5) to trigger rollback.  A single-axis breach below
        that level results in advance (acceptable gray zone).
        """
        agent_id, version_id, suite_id, dep_id = _bootstrap_store(store)

        good_judge = StubJudge(drift_score=0.85, trajectory_score=0.90)
        # Slightly degraded canary: drift 0.85 → 0.79, drop=0.06
        # > warn (0.05) but < aggressive (0.075) → single-axis warn only
        mild_judge = StubJudge(drift_score=0.79, trajectory_score=0.90)

        stable_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-s3")
        canary_eval = _run_eval(store, agent_id, version_id, suite_id, mild_judge, "eval-c3")

        settings = _make_test_settings()
        cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
        record = cycle.run(dep_id, canary_eval.evaluationId, stable_eval.evaluationId)

        # Single-axis below aggressive threshold → advance (acceptable gray zone)
        assert record.action == "advance"
        dep = store.get_deployment(dep_id)
        assert dep is not None
        assert dep.currentTrafficPercent == 25  # advanced from 10% to 25%

    def test_single_axis_exceeds_aggressive_triggers_hold(self, store: MemoryStore) -> None:
        """Single-axis exceeding the aggressive threshold → hold."""
        agent_id, version_id, suite_id, dep_id = _bootstrap_store(store)

        good_judge = StubJudge(drift_score=0.85, trajectory_score=0.90)
        # drift drop = 0.09 > aggressive warn threshold (0.075)
        # trajectory drop = 0.0 < aggressive threshold
        # → only 1 axis flagged → hold
        hold_judge = StubJudge(drift_score=0.76, trajectory_score=0.90)

        stable_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-s3b")
        canary_eval = _run_eval(store, agent_id, version_id, suite_id, hold_judge, "eval-c3b")

        settings = _make_test_settings()
        cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
        record = cycle.run(dep_id, canary_eval.evaluationId, stable_eval.evaluationId)

        assert record.action == "hold"
        dep = store.get_deployment(dep_id)
        assert dep is not None
        assert dep.currentTrafficPercent == 10  # unchanged


# ── MetaAgentCycle: rollback path ─────────────────────────────────────────────


class TestMetaAgentRollback:
    def test_safety_floor_rollback_bypasses_meta_agent(self, store: MemoryStore) -> None:
        """A safety-floor breach triggers immediate rollback, not gray-zone judgment."""
        agent_id, version_id, suite_id, dep_id = _bootstrap_store(store)

        good_judge = StubJudge(drift_score=0.85, trajectory_score=0.90)
        # Catastrophically degraded: drift drops by 0.45 >> hard threshold 0.10
        bad_judge = StubJudge(drift_score=0.40, trajectory_score=0.35)

        stable_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-s4")
        canary_eval = _run_eval(store, agent_id, version_id, suite_id, bad_judge, "eval-c4")

        settings = _make_test_settings()
        cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
        record = cycle.run(dep_id, canary_eval.evaluationId, stable_eval.evaluationId)

        assert record.action == "rollback"
        dep = store.get_deployment(dep_id)
        assert dep is not None
        assert dep.state == "rolled_back"
        assert dep.currentTrafficPercent == 0

    def test_gray_zone_severe_rollback(self, store: MemoryStore) -> None:
        """Severe multi-axis gray-zone breach triggers meta-agent rollback."""
        agent_id, version_id, suite_id, dep_id = _bootstrap_store(store)

        # Stable: good scores
        good_judge = StubJudge(drift_score=0.85, trajectory_score=0.90)
        stable_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-s5")

        # Canary: degraded but NOT breaching the hard floor (drop=0.09 < 0.10 threshold)
        # But both drift and trajectory are in aggressive gray zone
        gray_judge = StubJudge(
            drift_score=0.77,       # drop = 0.08, > warn*1.5=0.075 => concerning
            trajectory_score=0.82,  # drop = 0.08, > warn*1.5=0.075 => concerning
            cost_usd=0.002,
            latency_ms=400.0,
        )
        canary_eval = _run_eval(store, agent_id, version_id, suite_id, gray_judge, "eval-c5")

        settings = _make_test_settings()
        cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
        record = cycle.run(dep_id, canary_eval.evaluationId, stable_eval.evaluationId)

        assert record.action == "rollback"
        dep = store.get_deployment(dep_id)
        assert dep is not None
        assert dep.state == "rolled_back"

    def test_rollback_pr_draft_generated(self, store: MemoryStore) -> None:
        """A rollback must produce a PR draft (dryrun mode)."""
        agent_id, version_id, suite_id, dep_id = _bootstrap_store(store)

        good_judge = StubJudge(drift_score=0.85)
        bad_judge = StubJudge(drift_score=0.40, trajectory_score=0.35)

        stable_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-s6")
        canary_eval = _run_eval(store, agent_id, version_id, suite_id, bad_judge, "eval-c6")

        settings = _make_test_settings(AGENTOPS_PR_MODE="dryrun")
        cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
        record = cycle.run(dep_id, canary_eval.evaluationId, stable_eval.evaluationId)

        assert record.action == "rollback"
        assert record.prDraftId is not None

        drafts = list_pr_drafts(deployment_id=dep_id)
        assert len(drafts) == 1
        draft = drafts[0]
        assert draft.mode == "dryrun"
        assert draft.prUrl is None  # dryrun — no real PR
        assert "Canary Regression Report" in draft.body
        assert dep_id in draft.body

    def test_terminal_deployment_raises(self, store: MemoryStore) -> None:
        """Running a cycle on a terminal deployment must raise ValueError."""
        store.create_agent(AgentCreate(name="a", runtime="adk-cloud-run"))
        dep = Deployment(
            deploymentId="dep-terminal",
            versionId="v1",
            strategy=CanaryStrategy(type="canary", steps=[10]),
            state="rolled_back",
            currentTrafficPercent=0,
        )
        store.create_deployment("a", dep)

        settings = _make_test_settings()
        cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
        with pytest.raises(ValueError, match="terminal state"):
            cycle.run("dep-terminal")

    def test_nonexistent_deployment_raises(self, store: MemoryStore) -> None:
        settings = _make_test_settings()
        cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
        with pytest.raises(ValueError, match="not found"):
            cycle.run("nonexistent-dep")


# ── PR dryrun vs gh mode ──────────────────────────────────────────────────────


class TestPRDraftMode:
    def test_dryrun_no_subprocess(self, store: MemoryStore) -> None:
        """In dryrun mode no subprocess should be called."""
        agent_id, version_id, suite_id, dep_id = _bootstrap_store(store)

        good_judge = StubJudge(drift_score=0.85)
        bad_judge = StubJudge(drift_score=0.40, trajectory_score=0.35)
        stable_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-s7")
        canary_eval = _run_eval(store, agent_id, version_id, suite_id, bad_judge, "eval-c7")

        settings = _make_test_settings(AGENTOPS_PR_MODE="dryrun")
        with patch("agentops_platform.meta_agent.subprocess.run") as mock_sub:
            cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
            cycle.run(dep_id, canary_eval.evaluationId, stable_eval.evaluationId)
            mock_sub.assert_not_called()

    def test_gh_mode_calls_subprocess(self, store: MemoryStore) -> None:
        """In gh mode, subprocess.run should be called with 'gh pr create'."""
        agent_id, version_id, suite_id, dep_id = _bootstrap_store(store)

        good_judge = StubJudge(drift_score=0.85)
        bad_judge = StubJudge(drift_score=0.40, trajectory_score=0.35)
        stable_eval = _run_eval(store, agent_id, version_id, suite_id, good_judge, "eval-s8")
        canary_eval = _run_eval(store, agent_id, version_id, suite_id, bad_judge, "eval-c8")

        settings = _make_test_settings(AGENTOPS_PR_MODE="gh")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/owner/repo/pull/99\n"
        with patch("agentops_platform.meta_agent.subprocess.run", return_value=mock_result) as mock_sub:
            cycle = MetaAgentCycle(store, settings=settings, use_gemini=False)
            record = cycle.run(dep_id, canary_eval.evaluationId, stable_eval.evaluationId)
            mock_sub.assert_called_once()
            args = mock_sub.call_args[0][0]
            assert "gh" in args
            assert "pr" in args
            assert "create" in args

        drafts = list_pr_drafts(deployment_id=dep_id)
        assert len(drafts) == 1
        assert drafts[0].prUrl == "https://github.com/owner/repo/pull/99"


# ── GeminiJudge backend switching ─────────────────────────────────────────────


class TestGeminiJudgeSwitch:
    def test_get_judge_stub(self) -> None:
        judge = get_judge("stub")
        from agentops_platform.judge import StubJudge
        assert isinstance(judge, StubJudge)

    def test_get_judge_gemini_returns_gemini_judge(self) -> None:
        """get_judge('gemini') should return a GeminiJudge instance."""
        # GeminiJudge.__init__ tries to import google.generativeai.
        # If unavailable, we confirm the factory raises ImportError, not ValueError.
        with patch("config.defaults.get_settings") as mock_settings:
            mock_settings.return_value = _make_test_settings(
                AGENTOPS_JUDGE_BACKEND="gemini",
                AGENTOPS_JUDGE_MODEL="gemini-2.0-flash",
            )
            try:
                judge = get_judge("gemini")
                assert isinstance(judge, GeminiJudge)
            except ImportError:
                pass  # google-generativeai not installed: expected in CI

    def test_get_judge_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown judge backend"):
            get_judge("unknown-backend")


# ── Dashboard endpoint ────────────────────────────────────────────────────────


class TestDashboard:
    def test_dashboard_html_returns_200(self, client: TestClient) -> None:
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "AgentOps Platform" in resp.text

    def test_dashboard_data_returns_json(self, client: TestClient) -> None:
        resp = client.get("/dashboard/data")
        assert resp.status_code == 200
        data = resp.json()
        assert "evaluations" in data
        assert "deployments" in data
        assert "decisions" in data
        assert "pr_drafts" in data

    def test_dashboard_data_includes_deployments(self, client: TestClient) -> None:
        # Register agent + deploy
        agent = client.post(
            "/v1/agents", json={"name": "dash-agent", "runtime": "adk-cloud-run"}
        ).json()
        version = client.post(
            f"/v1/agents/{agent['agentId']}/versions",
            json={
                "image": "gcr.io/p/img@sha256:abc",
                "model": "gemini-2.0-flash",
                "promptDigest": "sha256:aaa",
            },
        ).json()
        client.post(
            f"/v1/agents/{agent['agentId']}/deployments",
            json={
                "versionId": version["versionId"],
                "strategy": {"type": "canary", "steps": [10, 50, 100]},
            },
        )

        resp = client.get("/dashboard/data")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["deployments"]) >= 1
