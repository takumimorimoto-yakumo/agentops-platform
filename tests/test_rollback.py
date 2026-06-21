"""
Unit tests for the deterministic rollback policy (state machine).

Tests verify:
- PolicyCheckResult is correct for each axis when threshold is breached
- PolicyCheckResult is "no rollback" when all axes are within limits
- State transitions: canary → rolled_back, canary → promoted, canary → canary
- Terminal state protection: rolled_back / promoted cannot be transitioned again
- apply_policy_check orchestrates the full rollback when triggered
"""

from __future__ import annotations

import pytest

from agentops_platform.models import AxisScore, Deployment, CanaryStrategy, RollbackPolicy
from agentops_platform.rollback import (
    PolicyCheckResult,
    TERMINAL_STATES,
    advance_canary_step,
    apply_policy_check,
    evaluate_rollback_policy,
    promote_deployment,
    rollback_deployment,
)
from config.defaults import (
    DEFAULT_MAX_DRIFT_DROP,
    DEFAULT_MAX_TRAJECTORY_DROP,
    DEFAULT_MAX_COST_INCREASE_RATIO,
    DEFAULT_MAX_LATENCY_P95_MS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_scores(
    drift: float = 0.90,
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


def _make_canary_deployment(
    policy: RollbackPolicy | None = None,
    traffic: int = 25,
) -> Deployment:
    return Deployment(
        deploymentId="dep-001",
        versionId="ver-001",
        strategy=CanaryStrategy(type="canary", steps=[10, 25, 50, 100]),
        rollbackPolicy=policy,
        state="canary",
        currentTrafficPercent=traffic,
    )


# ── evaluate_rollback_policy tests ────────────────────────────────────────────


class TestEvaluateRollbackPolicy:
    def test_no_breach_returns_no_rollback(self) -> None:
        stable = _make_scores(drift=0.90, trajectory=0.90, cost=0.002, latency=400.0)
        canary = _make_scores(drift=0.89, trajectory=0.89, cost=0.003, latency=450.0)
        result = evaluate_rollback_policy(canary, stable, policy=None)
        assert result.should_rollback is False

    def test_drift_breach_triggers_rollback(self) -> None:
        stable = _make_scores(drift=0.90)
        # Drop of 0.15 > DEFAULT_MAX_DRIFT_DROP (0.10)
        canary = _make_scores(drift=0.75)
        result = evaluate_rollback_policy(canary, stable, policy=None)
        assert result.should_rollback is True
        assert "drift" in result.reason

    def test_trajectory_breach_triggers_rollback(self) -> None:
        stable = _make_scores(trajectory=0.90)
        canary = _make_scores(trajectory=0.70)  # drop = 0.20 > 0.10
        result = evaluate_rollback_policy(canary, stable, policy=None)
        assert result.should_rollback is True
        assert "trajectory" in result.reason

    def test_cost_breach_triggers_rollback(self) -> None:
        stable = _make_scores(cost=0.004)
        canary = _make_scores(cost=0.010)  # ratio = (0.010-0.004)/0.004 = 1.5 > 0.50
        result = evaluate_rollback_policy(canary, stable, policy=None)
        assert result.should_rollback is True
        assert "cost" in result.reason

    def test_latency_absolute_ceiling_triggers_rollback(self) -> None:
        stable = _make_scores(latency=1000.0)
        # 4000 ms exceeds DEFAULT_MAX_LATENCY_P95_MS (3000 ms)
        canary = _make_scores(latency=4000.0)
        result = evaluate_rollback_policy(canary, stable, policy=None)
        assert result.should_rollback is True
        assert "latency" in result.reason

    def test_custom_policy_overrides_defaults(self) -> None:
        """A strict custom policy with maxDriftDrop=0.01 fires at a tiny drop."""
        policy = RollbackPolicy(maxDriftDrop=0.01)
        stable = _make_scores(drift=0.90)
        canary = _make_scores(drift=0.88)  # drop = 0.02 > 0.01
        result = evaluate_rollback_policy(canary, stable, policy=policy)
        assert result.should_rollback is True

    def test_custom_policy_loose_threshold_no_rollback(self) -> None:
        """A loose policy with maxDriftDrop=0.50 does not fire on a small drop."""
        policy = RollbackPolicy(maxDriftDrop=0.50)
        stable = _make_scores(drift=0.90)
        canary = _make_scores(drift=0.70)  # drop = 0.20 < 0.50
        result = evaluate_rollback_policy(canary, stable, policy=policy)
        assert result.should_rollback is False

    def test_zero_stable_cost_skips_cost_check(self) -> None:
        """If stable cost is 0, the ratio check is skipped to avoid division by zero."""
        stable = _make_scores(cost=0.0)
        canary = _make_scores(cost=0.999)
        # Should not raise; cost check is skipped
        result = evaluate_rollback_policy(canary, stable, policy=None)
        # Only latency / drift / trajectory determine the result here
        assert isinstance(result.should_rollback, bool)

    def test_missing_axis_in_canary_no_error(self) -> None:
        """If canary scores are missing an axis, that check is skipped gracefully."""
        stable = _make_scores()
        # Canary only has latency
        canary = [AxisScore(axis="latency", score=400.0)]
        result = evaluate_rollback_policy(canary, stable, policy=None)
        assert isinstance(result.should_rollback, bool)


# ── State machine tests ────────────────────────────────────────────────────────


class TestStateMachine:
    def test_advance_canary_step(self) -> None:
        dep = _make_canary_deployment(traffic=10)
        updated, event = advance_canary_step(dep, next_traffic_percent=25)
        assert updated.state == "canary"
        assert updated.currentTrafficPercent == 25
        assert event.type == "deployment.step_advanced"
        assert "25%" in (event.detail or "")

    def test_promote_deployment(self) -> None:
        dep = _make_canary_deployment(traffic=50)
        updated, event = promote_deployment(dep)
        assert updated.state == "promoted"
        assert updated.currentTrafficPercent == 100
        assert event.type == "deployment.promoted"

    def test_rollback_deployment(self) -> None:
        dep = _make_canary_deployment(traffic=25)
        updated, event = rollback_deployment(dep, reason="test rollback")
        assert updated.state == "rolled_back"
        assert updated.currentTrafficPercent == 0
        assert event.type == "deployment.rolled_back"
        assert "test rollback" in (event.detail or "")

    def test_cannot_advance_terminal_promoted(self) -> None:
        dep = _make_canary_deployment()
        dep = dep.model_copy(update={"state": "promoted"})
        with pytest.raises(ValueError, match="terminal state"):
            advance_canary_step(dep, 100)

    def test_cannot_advance_terminal_rolled_back(self) -> None:
        dep = _make_canary_deployment()
        dep = dep.model_copy(update={"state": "rolled_back"})
        with pytest.raises(ValueError, match="terminal state"):
            advance_canary_step(dep, 50)

    def test_cannot_promote_terminal(self) -> None:
        dep = _make_canary_deployment()
        dep = dep.model_copy(update={"state": "rolled_back"})
        with pytest.raises(ValueError, match="terminal state"):
            promote_deployment(dep)

    def test_cannot_rollback_terminal(self) -> None:
        dep = _make_canary_deployment()
        dep = dep.model_copy(update={"state": "promoted"})
        with pytest.raises(ValueError, match="terminal state"):
            rollback_deployment(dep)

    def test_terminal_states_set(self) -> None:
        assert "promoted" in TERMINAL_STATES
        assert "rolled_back" in TERMINAL_STATES
        assert "failed" in TERMINAL_STATES
        assert "canary" not in TERMINAL_STATES
        assert "pending" not in TERMINAL_STATES


# ── apply_policy_check integration ────────────────────────────────────────────


class TestApplyPolicyCheck:
    def test_breach_returns_rollback_tuple(self) -> None:
        dep = _make_canary_deployment()
        stable_scores = _make_scores(drift=0.90)
        canary_scores = _make_scores(drift=0.70)  # 0.20 drop > 0.10 threshold
        result = apply_policy_check(dep, canary_scores, stable_scores)
        assert result is not None
        updated_dep, event = result
        assert updated_dep.state == "rolled_back"
        assert "auto-rollback" in (event.detail or "")

    def test_no_breach_returns_none(self) -> None:
        dep = _make_canary_deployment()
        stable_scores = _make_scores(drift=0.90)
        canary_scores = _make_scores(drift=0.89)
        result = apply_policy_check(dep, canary_scores, stable_scores)
        assert result is None
