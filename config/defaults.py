"""
AgentOps Platform — centralized defaults and configuration.

All magic numbers, model IDs, thresholds and URL templates live here.
Application code must import from this module rather than hard-coding values.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Platform-wide configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Server ────────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080)

    # ── Persistence ───────────────────────────────────────────────────────
    repository_backend: Literal["memory", "sqlite"] = Field(default="memory")
    db_url: str = Field(default="sqlite:///./agentops.db")

    # ── Evaluation / Judge ────────────────────────────────────────────────
    judge_model: str = Field(default="gemini-2.0-flash")
    judge_backend: Literal["stub", "gemini"] = Field(default="stub")

    # ── Canary defaults ───────────────────────────────────────────────────
    canary_default_steps: str = Field(default="10,25,50,100")
    rollback_window_minutes: int = Field(default=15)

    # ── Auth ──────────────────────────────────────────────────────────────
    auth_mode: Literal["none", "google-id-token"] = Field(default="none")

    # ── Google Cloud ──────────────────────────────────────────────────────
    google_cloud_project: str = Field(default="")
    google_cloud_region: str = Field(default="asia-northeast1")

    # ── Meta-agent (Wave 2) ───────────────────────────────────────────────
    # PR mode: "dryrun" = generate patch/body locally only; "gh" = create real PR.
    pr_mode: Literal["dryrun", "gh"] = Field(default="dryrun")
    # Gray-zone thresholds: the meta-agent watches these softer limits.
    # When they are breached but the hard safety-floor is NOT, the meta-agent
    # decides whether to advance, hold, or rollback (with LLM reasoning).
    # Safety-floor thresholds (DEFAULT_MAX_*) are always evaluated first.
    meta_agent_drift_warn: float = Field(default=0.05)        # 5% drop triggers concern
    meta_agent_trajectory_warn: float = Field(default=0.05)   # 5% drop triggers concern
    meta_agent_cost_warn: float = Field(default=0.20)          # 20% increase triggers concern
    meta_agent_latency_warn_ms: float = Field(default=1500.0)  # 1500 ms soft ceiling
    # Base URL for the control-plane API (used by the meta-agent to call back)
    control_plane_url: str = Field(default="http://localhost:8080")

    @property
    def canary_steps(self) -> list[int]:
        """Parse the comma-separated canary steps string into a list of ints."""
        return [int(s.strip()) for s in self.canary_default_steps.split(",") if s.strip()]


# ── Rollback policy defaults ──────────────────────────────────────────────────
# These are the platform-wide fallback values when a deployment does not supply
# an explicit RollbackPolicy.  All threshold semantics match the openapi.yaml
# RollbackPolicy schema.

DEFAULT_ROLLBACK_WINDOW_MINUTES: int = 15
DEFAULT_MAX_DRIFT_DROP: float = 0.10          # 10 % drop in drift score triggers rollback
DEFAULT_MAX_TRAJECTORY_DROP: float = 0.10     # 10 % drop in trajectory score
DEFAULT_MAX_COST_INCREASE_RATIO: float = 0.50  # 50 % cost increase
DEFAULT_MAX_LATENCY_P95_MS: float = 3000.0    # 3 000 ms absolute p95 ceiling

# ── Evaluation axis enum ──────────────────────────────────────────────────────
# Single source of truth for axis names used across the codebase.

AXIS_DRIFT = "drift"
AXIS_TRAJECTORY = "trajectory"
AXIS_COST = "cost"
AXIS_LATENCY = "latency"

ALL_AXES: tuple[str, ...] = (AXIS_DRIFT, AXIS_TRAJECTORY, AXIS_COST, AXIS_LATENCY)

# ── Deployment state enum ─────────────────────────────────────────────────────

DEPLOYMENT_STATE_PENDING = "pending"
DEPLOYMENT_STATE_CANARY = "canary"
DEPLOYMENT_STATE_PROMOTED = "promoted"
DEPLOYMENT_STATE_ROLLED_BACK = "rolled_back"
DEPLOYMENT_STATE_FAILED = "failed"

# ── Evaluation run state enum ─────────────────────────────────────────────────

EVAL_STATE_QUEUED = "queued"
EVAL_STATE_RUNNING = "running"
EVAL_STATE_SUCCEEDED = "succeeded"
EVAL_STATE_FAILED = "failed"

# ── Event type enum ───────────────────────────────────────────────────────────

EVENT_EVALUATION_FINISHED = "evaluation.finished"
EVENT_REGRESSION_FINISHED = "regression.finished"
EVENT_DEPLOYMENT_CREATED = "deployment.created"
EVENT_DEPLOYMENT_STEP_ADVANCED = "deployment.step_advanced"
EVENT_DEPLOYMENT_PROMOTED = "deployment.promoted"
EVENT_DEPLOYMENT_ROLLED_BACK = "deployment.rolled_back"
EVENT_META_AGENT_DECISION = "meta_agent.decision"

# ── Meta-agent decision enum ──────────────────────────────────────────────────

META_DECISION_ADVANCE = "advance"
META_DECISION_HOLD = "hold"
META_DECISION_ROLLBACK = "rollback"

# ── PR mode ───────────────────────────────────────────────────────────────────

PR_MODE_DRYRUN = "dryrun"
PR_MODE_GH = "gh"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance (reads .env once)."""
    return Settings()
