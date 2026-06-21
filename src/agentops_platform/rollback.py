"""
Deployment state machine and deterministic rollback policy evaluation.

This module is the "safety floor": when a RollbackPolicy threshold is
breached the deployment transitions to 'rolled_back' without consulting
the meta-agent.  The meta-agent (Wave 2) operates in the *gray zone* —
ambiguous deltas that do not breach the hard floor.

State transitions (allowed paths):
  pending  → canary        (first canary step applied)
  canary   → canary        (next canary step advanced)
  canary   → promoted      (100 % traffic confirmed)
  canary   → rolled_back   (policy breach OR manual rollback)
  pending  → promoted      (strategy == all-at-once, immediate 100 %)
  pending  → failed        (unexpected error)
  canary   → failed        (unexpected error)

Immutable terminal states: promoted, rolled_back, failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from config.defaults import (
    DEFAULT_MAX_COST_INCREASE_RATIO,
    DEFAULT_MAX_DRIFT_DROP,
    DEFAULT_MAX_LATENCY_P95_MS,
    DEFAULT_MAX_TRAJECTORY_DROP,
    DEFAULT_ROLLBACK_WINDOW_MINUTES,
    EVENT_DEPLOYMENT_PROMOTED,
    EVENT_DEPLOYMENT_ROLLED_BACK,
    EVENT_DEPLOYMENT_STEP_ADVANCED,
)
from .models import AxisScore, Deployment, Event, RollbackPolicy


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PolicyCheckResult:
    """Outcome of a single rollback-policy evaluation."""

    should_rollback: bool
    reason: str


DeploymentStateType = Literal["pending", "canary", "promoted", "rolled_back", "failed"]

TERMINAL_STATES: frozenset[str] = frozenset({"promoted", "rolled_back", "failed"})


# ── Policy evaluation ─────────────────────────────────────────────────────────


def evaluate_rollback_policy(
    scores: list[AxisScore],
    stable_scores: list[AxisScore],
    policy: RollbackPolicy | None,
) -> PolicyCheckResult:
    """Evaluate the rollback policy against live canary scores.

    Args:
        scores: Per-axis scores from the canary version's latest evaluation.
        stable_scores: Per-axis scores from the current stable version.
        policy: The RollbackPolicy attached to the deployment.  Defaults are
                used for any field left as None.

    Returns:
        PolicyCheckResult indicating whether to roll back and why.

    Notes:
        - drift / trajectory use *normalized* 0..1 scores (higher is better).
          The policy fires when the drop exceeds maxDriftDrop / maxTrajectoryDrop.
        - cost uses USD/task (lower is better).
          The policy fires when the increase ratio exceeds maxCostIncreaseRatio.
        - latency uses raw ms p95 (lower is better).
          The policy fires when the canary p95 exceeds maxLatencyP95Ms (absolute).
    """
    # Resolve effective thresholds
    max_drift_drop = _effective(
        policy.maxDriftDrop if policy else None,
        DEFAULT_MAX_DRIFT_DROP,
    )
    max_traj_drop = _effective(
        policy.maxTrajectoryDrop if policy else None,
        DEFAULT_MAX_TRAJECTORY_DROP,
    )
    max_cost_ratio = _effective(
        policy.maxCostIncreaseRatio if policy else None,
        DEFAULT_MAX_COST_INCREASE_RATIO,
    )
    max_latency = _effective(
        policy.maxLatencyP95Ms if policy else None,
        DEFAULT_MAX_LATENCY_P95_MS,
    )

    # Index scores by axis
    canary_by_axis = {s.axis: s.score for s in scores}
    stable_by_axis = {s.axis: s.score for s in stable_scores}

    # ── drift check ───────────────────────────────────────────────────────
    if "drift" in canary_by_axis and "drift" in stable_by_axis:
        stable_drift = stable_by_axis["drift"]
        canary_drift = canary_by_axis["drift"]
        drop = stable_drift - canary_drift
        if drop > max_drift_drop:
            return PolicyCheckResult(
                should_rollback=True,
                reason=(
                    f"drift score dropped by {drop:.3f} "
                    f"(stable={stable_drift:.3f}, canary={canary_drift:.3f}); "
                    f"threshold={max_drift_drop:.3f}"
                ),
            )

    # ── trajectory check ──────────────────────────────────────────────────
    if "trajectory" in canary_by_axis and "trajectory" in stable_by_axis:
        stable_traj = stable_by_axis["trajectory"]
        canary_traj = canary_by_axis["trajectory"]
        drop = stable_traj - canary_traj
        if drop > max_traj_drop:
            return PolicyCheckResult(
                should_rollback=True,
                reason=(
                    f"trajectory score dropped by {drop:.3f} "
                    f"(stable={stable_traj:.3f}, canary={canary_traj:.3f}); "
                    f"threshold={max_traj_drop:.3f}"
                ),
            )

    # ── cost check ────────────────────────────────────────────────────────
    if "cost" in canary_by_axis and "cost" in stable_by_axis:
        stable_cost = stable_by_axis["cost"]
        canary_cost = canary_by_axis["cost"]
        if stable_cost > 0:
            increase_ratio = (canary_cost - stable_cost) / stable_cost
            if increase_ratio > max_cost_ratio:
                return PolicyCheckResult(
                    should_rollback=True,
                    reason=(
                        f"cost increased by {increase_ratio:.1%} "
                        f"(stable={stable_cost:.4f}, canary={canary_cost:.4f} USD/task); "
                        f"threshold={max_cost_ratio:.1%}"
                    ),
                )

    # ── latency p95 check ─────────────────────────────────────────────────
    if "latency" in canary_by_axis:
        canary_latency = canary_by_axis["latency"]
        if canary_latency > max_latency:
            return PolicyCheckResult(
                should_rollback=True,
                reason=(
                    f"canary p95 latency {canary_latency:.0f}ms "
                    f"exceeds ceiling {max_latency:.0f}ms"
                ),
            )

    return PolicyCheckResult(should_rollback=False, reason="all checks passed")


# ── State machine helpers ─────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _effective(value: float | None, default: float) -> float:
    return value if value is not None else default


def _make_event(
    event_type: str,
    deployment_id: str,
    detail: str | None = None,
) -> Event:
    return Event(
        eventId=f"evt-{_now().strftime('%Y%m%dT%H%M%S%f')}",
        type=event_type,  # type: ignore[arg-type]
        occurredAt=_now(),
        deploymentId=deployment_id,
        detail=detail,
    )


def advance_canary_step(
    deployment: Deployment,
    next_traffic_percent: int,
) -> tuple[Deployment, Event]:
    """Advance the canary deployment to the next traffic step.

    Args:
        deployment: Current deployment record.
        next_traffic_percent: Target traffic percentage for this step.

    Returns:
        Updated Deployment and an Event record to persist.

    Raises:
        ValueError: If the deployment is in a terminal or non-canary state.
    """
    if deployment.state in TERMINAL_STATES:
        raise ValueError(
            f"Cannot advance deployment {deployment.deploymentId!r} "
            f"in terminal state {deployment.state!r}"
        )
    now = _now()
    updated = deployment.model_copy(
        update={
            "state": "canary",
            "currentTrafficPercent": next_traffic_percent,
            "updatedAt": now,
        }
    )
    event = _make_event(
        EVENT_DEPLOYMENT_STEP_ADVANCED,
        deployment.deploymentId,
        detail=f"traffic advanced to {next_traffic_percent}%",
    )
    return updated, event


def promote_deployment(deployment: Deployment) -> tuple[Deployment, Event]:
    """Promote the canary deployment to 100 % stable.

    Args:
        deployment: Current deployment record.

    Returns:
        Updated Deployment and an Event record.

    Raises:
        ValueError: If the deployment is already in a terminal state.
    """
    if deployment.state in TERMINAL_STATES:
        raise ValueError(
            f"Cannot promote deployment {deployment.deploymentId!r} "
            f"in terminal state {deployment.state!r}"
        )
    now = _now()
    updated = deployment.model_copy(
        update={
            "state": "promoted",
            "currentTrafficPercent": 100,
            "updatedAt": now,
        }
    )
    event = _make_event(
        EVENT_DEPLOYMENT_PROMOTED,
        deployment.deploymentId,
        detail="promoted to 100% traffic",
    )
    return updated, event


def rollback_deployment(
    deployment: Deployment,
    reason: str | None = None,
) -> tuple[Deployment, Event]:
    """Roll back the deployment to the previous stable version.

    Args:
        deployment: Current deployment record.
        reason: Human-readable reason (logged in the Event detail).

    Returns:
        Updated Deployment and an Event record.

    Raises:
        ValueError: If the deployment is already in a terminal state.
    """
    if deployment.state in TERMINAL_STATES:
        raise ValueError(
            f"Cannot roll back deployment {deployment.deploymentId!r} "
            f"in terminal state {deployment.state!r}"
        )
    now = _now()
    updated = deployment.model_copy(
        update={
            "state": "rolled_back",
            "currentTrafficPercent": 0,
            "updatedAt": now,
        }
    )
    detail = reason or "rollback triggered"
    event = _make_event(
        EVENT_DEPLOYMENT_ROLLED_BACK,
        deployment.deploymentId,
        detail=detail,
    )
    return updated, event


def apply_policy_check(
    deployment: Deployment,
    canary_scores: list[AxisScore],
    stable_scores: list[AxisScore],
) -> tuple[Deployment, Event] | None:
    """Evaluate the rollback policy and apply the rollback if triggered.

    Args:
        deployment: Current canary deployment.
        canary_scores: Scores from the latest evaluation of the canary version.
        stable_scores: Scores from the latest evaluation of the stable version.

    Returns:
        A (Deployment, Event) tuple if rollback was triggered, otherwise None.
    """
    result = evaluate_rollback_policy(
        scores=canary_scores,
        stable_scores=stable_scores,
        policy=deployment.rollbackPolicy,
    )
    if result.should_rollback:
        return rollback_deployment(deployment, reason=f"auto-rollback: {result.reason}")
    return None
