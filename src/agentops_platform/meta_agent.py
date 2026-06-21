"""
Autonomous meta-agent: two-layer decision engine for canary deployments.

Architecture (two layers):
  Layer 1 — Safety floor (deterministic, Wave 1):
    Evaluated first.  A catastrophic threshold breach always triggers an
    immediate rollback without consulting the meta-agent.
    Implemented in agentops_platform.rollback.apply_policy_check().

  Layer 2 — Gray-zone judgment (this module):
    When the safety floor is NOT breached but soft warning thresholds ARE
    exceeded, the meta-agent calls Gemini to reason about whether to:
      - advance  → move to the next canary step
      - hold     → stay at the current step and wait
      - rollback → revert to the previous stable version

    On rollback, an improvement PR draft is generated (diagnosis + suggestions).

Decision record:
  Every cycle produces a DecisionRecord stored in the repository (audit trail).

PR draft:
  Generated on rollback.  AGENTOPS_PR_MODE controls the output:
    "dryrun" (default) — write to pr_drafts in-memory, no external call.
    "gh"               — call `gh pr create` to open a real GitHub PR.

Usage:
  cycle = MetaAgentCycle(store, settings)
  record = cycle.run(deployment_id="dep-xxx", evaluation_id="eval-yyy")
"""

from __future__ import annotations

import json
import logging
import subprocess
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from config.defaults import (
    META_DECISION_ADVANCE,
    META_DECISION_HOLD,
    META_DECISION_ROLLBACK,
    PR_MODE_DRYRUN,
    PR_MODE_GH,
    Settings,
    get_settings,
)
from .models import (
    AxisScore,
    DecisionRecord,
    Deployment,
    EvaluationRun,
    GrayZoneSignal,
    MetricIngest,
    PRDraft,
)
from .repository import MemoryStore
from .rollback import (
    TERMINAL_STATES,
    advance_canary_step,
    apply_policy_check,
    rollback_deployment,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── In-memory storage for decision records and PR drafts ──────────────────────
# These are intentionally separate from the main MemoryStore so Wave 2
# artefacts can be collected without changing the Wave 1 store interface.
_decision_records: dict[str, DecisionRecord] = {}
_pr_drafts: dict[str, PRDraft] = {}


def list_decisions(deployment_id: str | None = None) -> list[DecisionRecord]:
    """Return decision records, optionally filtered by deployment."""
    records = list(_decision_records.values())
    if deployment_id:
        records = [r for r in records if r.deploymentId == deployment_id]
    return sorted(records, key=lambda r: r.decidedAt)


def list_pr_drafts(deployment_id: str | None = None) -> list[PRDraft]:
    """Return PR drafts, optionally filtered by deployment."""
    drafts = list(_pr_drafts.values())
    if deployment_id:
        drafts = [d for d in drafts if d.deploymentId == deployment_id]
    return sorted(drafts, key=lambda d: d.createdAt)


def _clear_store() -> None:
    """Reset in-memory stores (used in tests)."""
    _decision_records.clear()
    _pr_drafts.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _scores_by_axis(scores: list[AxisScore]) -> dict[str, float]:
    return {s.axis: s.score for s in scores}


def _compute_signal(
    canary_scores: list[AxisScore],
    stable_scores: list[AxisScore],
    outcome_metrics: list[MetricIngest],
) -> GrayZoneSignal:
    """Compute the gray-zone signal from evaluation scores and outcome metrics."""
    canary = _scores_by_axis(canary_scores)
    stable = _scores_by_axis(stable_scores)

    drift_drop = max(0.0, stable.get("drift", 0.0) - canary.get("drift", 0.0))
    traj_drop = max(0.0, stable.get("trajectory", 0.0) - canary.get("trajectory", 0.0))

    stable_cost = stable.get("cost", 0.0)
    canary_cost = canary.get("cost", 0.0)
    cost_ratio = (
        (canary_cost - stable_cost) / stable_cost if stable_cost > 0 else 0.0
    )
    cost_ratio = max(0.0, cost_ratio)

    canary_latency = canary.get("latency", 0.0)

    # Average outcome metric values per metric name
    agg: dict[str, list[float]] = {}
    for ingest in outcome_metrics:
        for sample in ingest.samples:
            agg.setdefault(sample.name, []).append(sample.value)
    avg_outcomes = {name: sum(vals) / len(vals) for name, vals in agg.items()}

    return GrayZoneSignal(
        drift_drop=drift_drop,
        trajectory_drop=traj_drop,
        cost_increase_ratio=cost_ratio,
        canary_latency_ms=canary_latency,
        outcome_metrics=avg_outcomes,
    )


def _is_in_gray_zone(signal: GrayZoneSignal, settings: Settings) -> bool:
    """Return True if any soft warning threshold is exceeded."""
    if signal.drift_drop > settings.meta_agent_drift_warn:
        return True
    if signal.trajectory_drop > settings.meta_agent_trajectory_warn:
        return True
    if signal.cost_increase_ratio > settings.meta_agent_cost_warn:
        return True
    if signal.canary_latency_ms > settings.meta_agent_latency_warn_ms:
        return True
    return False


# ── Gemini-based gray-zone judgment ───────────────────────────────────────────

_JUDGMENT_PROMPT_TEMPLATE = """\
You are the meta-agent of an AI deployment platform.
A canary deployment is running and you must decide what to do next.

## Current signal
- Drift score drop (stable − canary): {drift_drop:.3f}  (warn threshold: {warn_drift:.3f})
- Trajectory score drop:              {traj_drop:.3f}  (warn threshold: {warn_traj:.3f})
- Cost increase ratio:                {cost_ratio:.1%}  (warn threshold: {warn_cost:.1%})
- Canary p95 latency:                 {latency_ms:.0f} ms  (warn threshold: {warn_latency:.0f} ms)
- Outcome metrics: {outcome_metrics}

## Context
- The safety-floor hard thresholds have NOT been breached (no immediate rollback forced).
- The soft warning thresholds above HAVE been exceeded, which is why you are being consulted.
- Current canary traffic step: {traffic_pct}%
- Canary steps remaining: {steps_remaining}

## Your task
Based on the signal above, choose ONE action:
  - advance   : metrics look acceptable; promote to the next traffic step
  - hold      : concerning trend but not conclusive; wait and re-evaluate
  - rollback  : degradation is clear enough; revert to the stable version

Respond with a JSON object and nothing else:
{{
  "action": "<advance|hold|rollback>",
  "rationale": "<one or two sentences explaining your reasoning>"
}}
"""


def _call_gemini_judge(
    prompt: str,
    model_id: str,
) -> tuple[Literal["advance", "hold", "rollback"], str]:
    """Call the Gemini API and parse the action/rationale JSON.

    Returns a safe default of ("hold", <reason>) on any error.
    """
    try:
        import google.generativeai as genai  # type: ignore[import-untyped]
        model = genai.GenerativeModel(model_id)
        response = model.generate_content(prompt)
        raw = (response.text or "").strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.splitlines()
                if not line.startswith("```")
            ).strip()
        parsed = json.loads(raw)
        action = parsed.get("action", "hold")
        if action not in ("advance", "hold", "rollback"):
            action = "hold"
        rationale = parsed.get("rationale", "No rationale provided.")
        return action, rationale  # type: ignore[return-value]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini gray-zone judgment failed (%s): %s", model_id, exc)
        return "hold", f"Gemini call failed; defaulting to hold. Error: {exc}"


def _decide_gray_zone(
    signal: GrayZoneSignal,
    deployment: Deployment,
    settings: Settings,
    use_gemini: bool,
) -> tuple[Literal["advance", "hold", "rollback"], str]:
    """Determine the action for a gray-zone situation.

    When use_gemini is True and the judge_backend is configured for Gemini,
    delegates to the LLM.  Otherwise applies deterministic heuristics:
      - If drift_drop or trajectory_drop > warn * 1.5 → rollback
      - Else → hold

    Args:
        signal: Computed gray-zone signal.
        deployment: Current deployment state.
        settings: Platform settings.
        use_gemini: Whether to call the Gemini API.

    Returns:
        (action, rationale) tuple.
    """
    steps_remaining = 0
    if deployment.strategy.steps:
        current = deployment.currentTrafficPercent or 0
        remaining = [s for s in deployment.strategy.steps if s > current]
        steps_remaining = len(remaining)

    if use_gemini and settings.judge_backend == "gemini":
        prompt = _JUDGMENT_PROMPT_TEMPLATE.format(
            drift_drop=signal.drift_drop,
            warn_drift=settings.meta_agent_drift_warn,
            traj_drop=signal.trajectory_drop,
            warn_traj=settings.meta_agent_trajectory_warn,
            cost_ratio=signal.cost_increase_ratio,
            warn_cost=settings.meta_agent_cost_warn,
            latency_ms=signal.canary_latency_ms,
            warn_latency=settings.meta_agent_latency_warn_ms,
            outcome_metrics=signal.outcome_metrics,
            traffic_pct=deployment.currentTrafficPercent or 0,
            steps_remaining=steps_remaining,
        )
        return _call_gemini_judge(prompt, model_id=settings.judge_model)

    # Deterministic fallback heuristics (used in tests and when judge_backend=stub)
    aggressive_warn_drift = settings.meta_agent_drift_warn * 1.5
    aggressive_warn_traj = settings.meta_agent_trajectory_warn * 1.5
    aggressive_warn_cost = settings.meta_agent_cost_warn * 1.5
    aggressive_warn_latency = settings.meta_agent_latency_warn_ms * 1.5

    concerning_count = 0
    reasons: list[str] = []

    if signal.drift_drop > aggressive_warn_drift:
        concerning_count += 1
        reasons.append(f"drift drop {signal.drift_drop:.3f} exceeds {aggressive_warn_drift:.3f}")
    if signal.trajectory_drop > aggressive_warn_traj:
        concerning_count += 1
        reasons.append(
            f"trajectory drop {signal.trajectory_drop:.3f} exceeds {aggressive_warn_traj:.3f}"
        )
    if signal.cost_increase_ratio > aggressive_warn_cost:
        concerning_count += 1
        reasons.append(
            f"cost increase {signal.cost_increase_ratio:.1%} exceeds {aggressive_warn_cost:.1%}"
        )
    if signal.canary_latency_ms > aggressive_warn_latency:
        concerning_count += 1
        reasons.append(
            f"latency {signal.canary_latency_ms:.0f}ms exceeds {aggressive_warn_latency:.0f}ms"
        )

    if concerning_count >= 2:
        rationale = (
            "Deterministic fallback: multiple axes exceeded aggressive warning thresholds: "
            + "; ".join(reasons)
        )
        return META_DECISION_ROLLBACK, rationale  # type: ignore[return-value]

    if reasons:
        rationale = (
            "Deterministic fallback: soft warning exceeded but within acceptable range: "
            + "; ".join(reasons)
            + ". Holding for next evaluation window."
        )
        return META_DECISION_HOLD, rationale  # type: ignore[return-value]

    # All in gray zone but still acceptable — advance
    rationale = (
        "Soft warning thresholds exceeded but trend appears acceptable. Advancing canary."
    )
    return META_DECISION_ADVANCE, rationale  # type: ignore[return-value]


# ── PR draft generation ───────────────────────────────────────────────────────


def _build_pr_body(
    deployment: Deployment,
    signal: GrayZoneSignal,
    rationale: str,
    decision_id: str,
) -> tuple[str, str]:
    """Build a PR title and body for the improvement proposal.

    Returns:
        (title, body) tuple.
    """
    title = (
        f"fix(agent): address canary regression in deployment {deployment.deploymentId[:8]}"
    )
    body = f"""\
## Canary Regression Report

**Deployment**: `{deployment.deploymentId}`
**Decision**: `{decision_id}`
**Action taken**: rollback (meta-agent judgment)

## Diagnosis

Meta-agent detected degradation in the canary deployment and triggered a rollback.

**Gray-zone signal observed:**
| Metric | Value |
|---|---|
| Drift score drop | {signal.drift_drop:.4f} |
| Trajectory score drop | {signal.trajectory_drop:.4f} |
| Cost increase ratio | {signal.cost_increase_ratio:.2%} |
| Canary p95 latency | {signal.canary_latency_ms:.0f} ms |

**Outcome metrics:**
{_format_outcome_metrics(signal.outcome_metrics)}

**Rationale from meta-agent:**
> {rationale}

## Suggested improvements

1. **Review prompt changes** between the stable version and the canary version.
   Compare `promptDigest` values to identify what changed.
2. **Analyze trajectory failures** in the ADK Eval run associated with this canary.
   Look for tool-call sequence deviations.
3. **Check cost drivers** — if cost increased, profile which tool calls or
   LLM invocations are responsible.
4. **Latency investigation** — review Cloud Trace spans for the canary version
   to identify which step added latency.

## Next steps

- [ ] Fix the identified issues in a new agent version
- [ ] Run regression evaluation: compare new version against stable baseline
- [ ] Re-deploy with the same canary strategy once regression passes

---
*Generated automatically by the AgentOps Platform meta-agent.*
"""
    return title, body


def _format_outcome_metrics(metrics: dict[str, float]) -> str:
    if not metrics:
        return "_No outcome metrics ingested during this canary window._"
    lines = ["| Metric | Average value |", "|---|---|"]
    for name, value in metrics.items():
        lines.append(f"| {name} | {value:.4f} |")
    return "\n".join(lines)


def _create_pr_draft(
    deployment: Deployment,
    signal: GrayZoneSignal,
    rationale: str,
    decision_id: str,
    settings: Settings,
) -> PRDraft:
    """Generate a PR draft and optionally create a real GitHub PR."""
    title, body = _build_pr_body(deployment, signal, rationale, decision_id)
    draft_id = _new_id()
    pr_url: str | None = None
    mode = settings.pr_mode

    if mode == PR_MODE_GH:
        pr_url = _create_github_pr(title, body)

    draft = PRDraft(
        prDraftId=draft_id,
        deploymentId=deployment.deploymentId,
        decisionId=decision_id,
        title=title,
        body=body,
        mode=mode,  # type: ignore[arg-type]
        prUrl=pr_url,
        createdAt=_now(),
    )
    _pr_drafts[draft_id] = draft
    return draft


def _create_github_pr(title: str, body: str) -> str | None:
    """Create a real GitHub PR using the gh CLI.

    Returns the PR URL on success, None on failure.
    """
    try:
        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--title", title,
                "--body", body,
                "--draft",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            url = result.stdout.strip().split("\n")[-1]
            logger.info("Created GitHub PR: %s", url)
            return url
        logger.warning("gh pr create failed: %s", result.stderr)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("gh pr create error: %s", exc)
        return None


# ── Main decision cycle ───────────────────────────────────────────────────────


class MetaAgentCycle:
    """Runs one complete meta-agent decision cycle for a canary deployment.

    A cycle:
      1. Load the deployment and its latest evaluation.
      2. Evaluate the safety-floor rollback policy (Layer 1).
         If the floor is breached → rollback immediately, no LLM call.
      3. Compute the gray-zone signal.
      4. If no soft warnings → advance the canary (or hold if final step).
      5. If soft warnings → call Gemini (or deterministic fallback) for judgment.
      6. Execute the chosen action via the deployment API.
      7. Persist a DecisionRecord and (on rollback) a PRDraft.

    Args:
        store: The platform repository.
        settings: Platform settings (can be overridden in tests).
        use_gemini: Set False to always use deterministic fallback (for tests).
    """

    def __init__(
        self,
        store: MemoryStore,
        settings: Settings | None = None,
        use_gemini: bool = True,
    ) -> None:
        self._store = store
        self._settings = settings or get_settings()
        self._use_gemini = use_gemini

    def run(
        self,
        deployment_id: str,
        evaluation_id: str | None = None,
        stable_evaluation_id: str | None = None,
    ) -> DecisionRecord:
        """Execute one decision cycle.

        Args:
            deployment_id: The canary deployment to evaluate.
            evaluation_id: The latest evaluation run for the canary version.
                           If None, the most recent evaluation for the version is used.
            stable_evaluation_id: Evaluation run for the stable version (used by
                                  the safety floor).  If None, fallback to empty scores.

        Returns:
            A DecisionRecord describing what the meta-agent decided and why.

        Raises:
            ValueError: If the deployment does not exist or is in a terminal state.
        """
        deployment = self._store.get_deployment(deployment_id)
        if deployment is None:
            raise ValueError(f"Deployment {deployment_id!r} not found")
        if deployment.state in TERMINAL_STATES:
            raise ValueError(
                f"Deployment {deployment_id!r} is in terminal state {deployment.state!r}"
            )

        # Resolve evaluations
        canary_eval = self._resolve_evaluation(evaluation_id, deployment.versionId)
        stable_eval = self._resolve_evaluation(stable_evaluation_id, version_id=None)

        canary_scores = canary_eval.scores if canary_eval else []
        stable_scores = stable_eval.scores if stable_eval else []

        # ── Layer 1: safety-floor check ───────────────────────────────────
        floor_result = apply_policy_check(deployment, canary_scores, stable_scores)
        if floor_result is not None:
            updated_dep, rollback_event = floor_result
            self._store.update_deployment(updated_dep)
            agent_id = self._find_agent_id(deployment_id)
            if agent_id:
                self._store.append_event(agent_id, rollback_event)

            rationale = (
                f"Safety-floor breach: {rollback_event.detail}. "
                "Immediate rollback without consulting gray-zone judgment."
            )
            record = self._make_record(
                deployment_id=deployment_id,
                evaluation_id=evaluation_id,
                action=META_DECISION_ROLLBACK,
                rationale=rationale,
                signal=GrayZoneSignal(),
            )
            # Also produce a PR draft for safety-floor rollbacks and link it
            pr = self._maybe_create_pr(deployment, GrayZoneSignal(), rationale, record.decisionId)
            if pr:
                linked = DecisionRecord(
                    decisionId=record.decisionId,
                    deploymentId=record.deploymentId,
                    evaluationId=record.evaluationId,
                    action=record.action,
                    rationale=record.rationale,
                    signal=record.signal,
                    prDraftId=pr.prDraftId,
                    decidedAt=record.decidedAt,
                )
                _decision_records[record.decisionId] = linked
                return linked
            return record

        # ── Layer 2: gray-zone judgment ───────────────────────────────────
        outcome_metrics = self._collect_outcome_metrics(deployment.versionId)
        signal = _compute_signal(canary_scores, stable_scores, outcome_metrics)

        if not _is_in_gray_zone(signal, self._settings):
            # All clear — advance to next canary step if available
            action, rationale = self._compute_advance_or_complete(deployment)
        else:
            # Gray zone — consult Gemini (or deterministic fallback)
            action, rationale = _decide_gray_zone(
                signal, deployment, self._settings, self._use_gemini
            )

        # ── Execute the chosen action ─────────────────────────────────────
        self._execute_action(action, deployment)

        # ── Persist decision record ───────────────────────────────────────
        record = self._make_record(
            deployment_id=deployment_id,
            evaluation_id=evaluation_id,
            action=action,  # type: ignore[arg-type]
            rationale=rationale,
            signal=signal,
        )

        if action == META_DECISION_ROLLBACK:
            pr = self._maybe_create_pr(deployment, signal, rationale, record.decisionId)
            if pr:
                updated = DecisionRecord(
                    decisionId=record.decisionId,
                    deploymentId=record.deploymentId,
                    evaluationId=record.evaluationId,
                    action=record.action,
                    rationale=record.rationale,
                    signal=record.signal,
                    prDraftId=pr.prDraftId,
                    decidedAt=record.decidedAt,
                )
                _decision_records[record.decisionId] = updated
                return updated

        return record

    # ── Private helpers ───────────────────────────────────────────────────

    def _resolve_evaluation(
        self, evaluation_id: str | None, version_id: str | None
    ) -> EvaluationRun | None:
        if evaluation_id:
            return self._store.get_evaluation(evaluation_id)
        if version_id is None:
            return None
        # Find the most recent succeeded evaluation for this version
        all_evals: list[EvaluationRun] = []
        for agent in self._store.list_agents():
            evals = self._store.list_evaluations(agent.agentId, version_id=version_id)
            all_evals.extend(evals)
        succeeded = [e for e in all_evals if e.state == "succeeded"]
        if not succeeded:
            return None
        return max(succeeded, key=lambda e: e.finishedAt or datetime.min.replace(tzinfo=timezone.utc))

    def _collect_outcome_metrics(self, version_id: str) -> list[MetricIngest]:
        """Collect all ingested outcome metrics for the canary version."""
        result: list[MetricIngest] = []
        for agent in self._store.list_agents():
            ingests = self._store.list_metrics(agent.agentId)
            result.extend(
                ingest for ingest in ingests if ingest.versionId == version_id
            )
        return result

    def _find_agent_id(self, deployment_id: str) -> str | None:
        for agent in self._store.list_agents():
            for dep in self._store.list_deployments(agent.agentId):
                if dep.deploymentId == deployment_id:
                    return agent.agentId
        return None

    def _compute_advance_or_complete(
        self, deployment: Deployment
    ) -> tuple[Literal["advance", "hold", "rollback"], str]:
        """Determine if we can advance to the next canary step."""
        steps = deployment.strategy.steps or []
        current = deployment.currentTrafficPercent or 0
        next_steps = [s for s in steps if s > current]
        if not next_steps:
            return META_DECISION_ADVANCE, (  # type: ignore[return-value]
                "No warning thresholds exceeded and no more canary steps remain. "
                "Deployment is ready for promotion."
            )
        return META_DECISION_ADVANCE, (  # type: ignore[return-value]
            f"No warning thresholds exceeded. Advancing from {current}% "
            f"to {next_steps[0]}%."
        )

    def _execute_action(
        self,
        action: str,
        deployment: Deployment,
    ) -> None:
        """Execute the meta-agent's chosen action on the deployment."""
        agent_id = self._find_agent_id(deployment.deploymentId)
        if action == META_DECISION_ADVANCE:
            steps = deployment.strategy.steps or []
            current = deployment.currentTrafficPercent or 0
            next_steps = [s for s in steps if s > current]
            if next_steps:
                updated, event = advance_canary_step(deployment, next_steps[0])
            else:
                # No more steps — promote
                from .rollback import promote_deployment
                updated, event = promote_deployment(deployment)
            self._store.update_deployment(updated)
            if agent_id:
                self._store.append_event(agent_id, event)

        elif action == META_DECISION_HOLD:
            # No state change — just log
            logger.info(
                "Meta-agent: holding deployment %s at %s%%",
                deployment.deploymentId,
                deployment.currentTrafficPercent,
            )

        elif action == META_DECISION_ROLLBACK:
            updated, event = rollback_deployment(
                deployment,
                reason="meta-agent gray-zone rollback",
            )
            self._store.update_deployment(updated)
            if agent_id:
                self._store.append_event(agent_id, event)

    def _make_record(
        self,
        deployment_id: str,
        evaluation_id: str | None,
        action: Literal["advance", "hold", "rollback"],
        rationale: str,
        signal: GrayZoneSignal,
    ) -> DecisionRecord:
        record = DecisionRecord(
            decisionId=_new_id(),
            deploymentId=deployment_id,
            evaluationId=evaluation_id,
            action=action,
            rationale=rationale,
            signal=signal,
            decidedAt=_now(),
        )
        _decision_records[record.decisionId] = record
        return record

    def _maybe_create_pr(
        self,
        deployment: Deployment,
        signal: GrayZoneSignal,
        rationale: str,
        decision_id: str,
    ) -> PRDraft | None:
        try:
            return _create_pr_draft(deployment, signal, rationale, decision_id, self._settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PR draft creation failed: %s", exc)
            return None
