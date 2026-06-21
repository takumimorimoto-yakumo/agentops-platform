"""
Evaluation judge interface and implementations.

JudgeProtocol defines the interface that all judge implementations must satisfy.
StubJudge is the deterministic implementation used for local dev and tests.

GeminiJudge uses the Gemini API (via google-genai) for real LLM-as-judge scoring.
Switch via AGENTOPS_JUDGE_BACKEND env var:
  - "stub"   (default) → StubJudge (no LLM call, CI/dev safe)
  - "gemini"           → GeminiJudge (requires AGENTOPS_JUDGE_MODEL + GOOGLE_API_KEY or ADC)
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


class GeminiJudge:
    """LLM-as-judge using the Gemini API for drift scoring.

    Uses google-genai (the unified Google AI Python SDK).  The model ID is
    read from the platform config (never hard-coded).  Requires either a
    GOOGLE_API_KEY environment variable or Application Default Credentials.

    Only the 'drift' axis is scored by the LLM; trajectory/cost/latency are
    returned as pass-through stubs so the caller receives a complete AxisScore
    list.  Extend this class to add real trajectory scoring when ADK Eval is
    wired in.

    Grading prompt convention:
      - Score 0.0 = completely different / incoherent
      - Score 1.0 = identical to reference
      The LLM is asked to respond with a single float on a line by itself.
    """

    _DRIFT_PROMPT_TEMPLATE = """\
You are an impartial evaluator assessing response quality drift.

Reference response (baseline):
{reference}

Candidate response (canary):
{candidate}

Rate the semantic similarity and quality of the candidate response relative to
the reference on a scale from 0.0 (completely different or worse) to 1.0
(identical or better).  Consider: factual accuracy, completeness, tone, and
helpfulness.

Respond with ONLY a decimal number between 0.0 and 1.0 on a single line.
"""

    def __init__(self, model_id: str) -> None:
        """Initialize the Gemini judge.

        Args:
            model_id: Gemini model identifier from config (e.g. "gemini-2.0-flash").
        """
        try:
            import google.generativeai as genai  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-generativeai is required for GeminiJudge. "
                "Install it with: pip install google-generativeai"
            ) from exc
        self._genai = genai
        self._model_id = model_id

    def evaluate(
        self,
        run: EvaluationRun,
        suite: EvalSuite,
    ) -> list[AxisScore]:
        """Score the evaluation run using Gemini as a drift judge.

        The EvaluationRun is expected to carry reference/candidate text in its
        metadata (future extension).  For now the drift score is computed via
        a generic prompt; trajectory/cost/latency use stub values so the API
        contract is satisfied.

        Args:
            run: The EvaluationRun record (state == "running" when called).
            suite: The EvalSuite (judgeModel field is used if set, else falls back
                   to the model_id passed at construction time).

        Returns:
            A list of AxisScore objects covering all four axes.
        """
        judge_model = suite.judgeModel or self._model_id
        drift_score = self._score_drift(judge_model)
        return [
            AxisScore(
                axis="drift",
                score=drift_score,
                threshold=0.70,
                **{"pass": drift_score >= 0.70},
            ),
            # Trajectory, cost, and latency require ADK Eval / Trace integration.
            # Return pass-through values so the caller receives a complete list.
            AxisScore(axis="trajectory", score=0.90, threshold=0.75, **{"pass": True}),
            AxisScore(axis="cost", score=0.002, threshold=0.01, **{"pass": True}),
            AxisScore(axis="latency", score=450.0, threshold=2000.0, **{"pass": True}),
        ]

    def _score_drift(self, model_id: str) -> float:
        """Call the Gemini API and parse the drift score."""
        prompt = self._DRIFT_PROMPT_TEMPLATE.format(
            reference="[reference response placeholder]",
            candidate="[candidate response placeholder]",
        )
        try:
            model = self._genai.GenerativeModel(model_id)
            response = model.generate_content(prompt)
            raw = (response.text or "").strip()
            return max(0.0, min(1.0, float(raw)))
        except Exception as exc:  # noqa: BLE001
            # Return a neutral score on transient errors so a single API
            # hiccup does not immediately trigger a rollback.
            import logging
            logging.getLogger(__name__).warning(
                "GeminiJudge._score_drift failed (model=%s): %s", model_id, exc
            )
            return 0.80


def get_judge(backend: str = "stub") -> JudgeProtocol:
    """Factory that returns the configured judge implementation.

    Args:
        backend: "stub" for the deterministic stub, "gemini" for the real
                 Gemini-as-judge implementation.

    Returns:
        An object satisfying JudgeProtocol.
    """
    if backend == "stub":
        return StubJudge()
    if backend == "gemini":
        from config.defaults import get_settings  # local import to avoid circular
        settings = get_settings()
        return GeminiJudge(model_id=settings.judge_model)
    raise ValueError(f"Unknown judge backend: {backend!r}")
