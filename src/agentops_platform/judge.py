"""
Evaluation judge interface and implementations.

JudgeProtocol defines the interface that all judge implementations must satisfy.
StubJudge is the deterministic implementation used for local dev and tests.

TODO (Wave 2 — Gemini judge):
  - Implement GeminiJudge(JudgeProtocol) using google-genai SDK
  - Input: prompt text + candidate response + reference response
  - Output: AxisScore for the 'drift' axis (score = cosine / LLM-as-judge)
  - Wire via AGENTOPS_JUDGE_BACKEND=gemini in config/defaults.py
  - Required: AGENTOPS_JUDGE_MODEL env var (e.g. gemini-2.0-flash)
  - Do NOT import google.generativeai or anthropic here; rely on google-adk
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import AxisScore, EvalSuite, EvaluationRun


@runtime_checkable
class JudgeProtocol(Protocol):
    """Contract for evaluation judges.

    A judge receives a finalized EvaluationRun (with versionId and suiteId
    resolved) and returns per-axis scores.  The platform stores the scores
    back into the EvaluationRun and transitions its state.
    """

    def evaluate(
        self,
        run: EvaluationRun,
        suite: EvalSuite,
    ) -> list[AxisScore]:
        """Run the evaluation and return per-axis scores.

        Args:
            run: The EvaluationRun record (state == "running" when called).
            suite: The EvalSuite that specifies the dataset and judge model.

        Returns:
            A list of AxisScore objects covering at minimum the 'drift' axis.
            The caller (evaluator service) stores these back into the run.
        """
        ...


# ── Stub judge (deterministic, no LLM call) ──────────────────────────────────


_STUB_DRIFT_SCORE = 0.85
_STUB_TRAJECTORY_SCORE = 0.90
_STUB_COST_USD = 0.002   # USD per task
_STUB_LATENCY_MS = 450.0  # milliseconds

# Default pass thresholds used by the stub
_STUB_DRIFT_THRESHOLD = 0.70
_STUB_TRAJECTORY_THRESHOLD = 0.75
_STUB_COST_THRESHOLD = 0.01    # max USD/task
_STUB_LATENCY_THRESHOLD = 2000.0  # ms


class StubJudge:
    """Deterministic judge that always returns fixed scores.

    Suitable for unit tests, local demos and CI runs that should not make
    real LLM calls.  Scores are configured at construction time so tests
    can inject specific values.
    """

    def __init__(
        self,
        drift_score: float = _STUB_DRIFT_SCORE,
        trajectory_score: float = _STUB_TRAJECTORY_SCORE,
        cost_usd: float = _STUB_COST_USD,
        latency_ms: float = _STUB_LATENCY_MS,
    ) -> None:
        self._drift_score = drift_score
        self._trajectory_score = trajectory_score
        self._cost_usd = cost_usd
        self._latency_ms = latency_ms

    def evaluate(
        self,
        run: EvaluationRun,
        suite: EvalSuite,  # noqa: ARG002
    ) -> list[AxisScore]:
        """Return fixed axis scores regardless of the run's input data."""
        return [
            AxisScore(
                axis="drift",
                score=self._drift_score,
                threshold=_STUB_DRIFT_THRESHOLD,
                **{"pass": self._drift_score >= _STUB_DRIFT_THRESHOLD},
            ),
            AxisScore(
                axis="trajectory",
                score=self._trajectory_score,
                threshold=_STUB_TRAJECTORY_THRESHOLD,
                **{"pass": self._trajectory_score >= _STUB_TRAJECTORY_THRESHOLD},
            ),
            AxisScore(
                axis="cost",
                score=self._cost_usd,
                threshold=_STUB_COST_THRESHOLD,
                **{"pass": self._cost_usd <= _STUB_COST_THRESHOLD},
            ),
            AxisScore(
                axis="latency",
                score=self._latency_ms,
                threshold=_STUB_LATENCY_THRESHOLD,
                **{"pass": self._latency_ms <= _STUB_LATENCY_THRESHOLD},
            ),
        ]


def get_judge(backend: str = "stub") -> JudgeProtocol:
    """Factory that returns the configured judge implementation.

    Args:
        backend: "stub" for the deterministic stub, "gemini" for the real
                 Gemini-as-judge (not yet implemented; raises NotImplementedError).

    Returns:
        An object satisfying JudgeProtocol.
    """
    if backend == "stub":
        return StubJudge()
    if backend == "gemini":
        # TODO (Wave 2): return GeminiJudge()
        raise NotImplementedError(
            "GeminiJudge is not implemented yet. "
            "Set AGENTOPS_JUDGE_BACKEND=stub for local development."
        )
    raise ValueError(f"Unknown judge backend: {backend!r}")
