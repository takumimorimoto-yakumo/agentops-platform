"""
Scenario tests for the autonomous canary management demo.

Asserts the full end-to-end contract:
  - A canary deployment with degraded scores transitions to rolled_back.
  - The meta-agent produces a DecisionRecord with action == "rollback".
  - A PR draft (dryrun) is generated and contains expected sections.
  - The corresponding audit events are recorded in the store.
  - The dashboard data endpoint reflects the post-rollback state.

These tests are self-contained: in-memory store, stub judge, dryrun PR mode.
No network calls, no GitHub access.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentops_platform.examples.autonomy_demo import run_demo
from agentops_platform.main import app
from agentops_platform.meta_agent import _clear_store, list_decisions, list_pr_drafts
from agentops_platform.repository import MemoryStore
from agentops_platform.routers.deps import get_store, get_judge_dep
from agentops_platform.judge import StubJudge
from config.defaults import Settings


# ── Shared helpers ────────────────────────────────────────────────────────────


def _demo_settings(**overrides: str) -> Settings:
    """Return a Settings instance configured for the demo (stub + dryrun)."""
    kwargs: dict[str, str] = {
        "AGENTOPS_JUDGE_BACKEND": "stub",
        "AGENTOPS_PR_MODE": "dryrun",
        "AGENTOPS_META_AGENT_DRIFT_WARN": "0.05",
        "AGENTOPS_META_AGENT_TRAJECTORY_WARN": "0.05",
        "AGENTOPS_META_AGENT_COST_WARN": "0.20",
        "AGENTOPS_META_AGENT_LATENCY_WARN_MS": "1500.0",
    }
    kwargs.update(overrides)
    return Settings(**{k.replace("AGENTOPS_", "").lower(): v for k, v in kwargs.items()
                       if k.startswith("AGENTOPS_")})


@pytest.fixture(autouse=True)
def _reset_meta_store() -> None:
    """Ensure a clean meta-agent in-memory state for every test."""
    _clear_store()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def settings() -> Settings:
    return _demo_settings()


# ── Core scenario contract ────────────────────────────────────────────────────


class TestAutonomyDemoContract:
    """The core scenario: canary with degraded scores → automatic rollback."""

    def test_rollback_happens_automatically(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """The meta-agent rolls back the canary without any manual intervention."""
        result = run_demo(store, settings)

        assert result["rollback_happened"] is True, (
            "Expected meta-agent to roll back the degraded canary, "
            f"but action was '{result['decision'].action}'"
        )

    def test_canary_deployment_state_is_rolled_back(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """After the demo, the canary deployment must be in 'rolled_back' state."""
        result = run_demo(store, settings)

        dep = store.get_deployment(result["canary_dep_id"])
        assert dep is not None
        assert dep.state == "rolled_back", (
            f"Expected deployment state 'rolled_back', got '{dep.state}'"
        )
        assert dep.currentTrafficPercent == 0

    def test_meta_agent_decision_record_exists(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """A DecisionRecord with action='rollback' must be persisted."""
        result = run_demo(store, settings)

        decisions = list_decisions(deployment_id=result["canary_dep_id"])
        assert len(decisions) >= 1

        rollback_decisions = [d for d in decisions if d.action == "rollback"]
        assert len(rollback_decisions) >= 1, (
            f"Expected at least one rollback decision, got: {[d.action for d in decisions]}"
        )

    def test_decision_record_has_rationale(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """Each DecisionRecord must include a non-empty rationale."""
        result = run_demo(store, settings)

        decisions = list_decisions(deployment_id=result["canary_dep_id"])
        for dec in decisions:
            assert dec.rationale, f"Decision {dec.decisionId} has empty rationale"
            assert len(dec.rationale) > 10


class TestPRDraftGeneration:
    """The meta-agent must file a PR draft on rollback."""

    def test_pr_draft_generated_on_rollback(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """A PR draft must be created when the meta-agent rolls back."""
        result = run_demo(store, settings)

        assert result["pr_draft_id"] is not None, (
            "Expected a PR draft to be generated, but pr_draft_id is None"
        )

    def test_pr_draft_is_dryrun_mode(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """In dryrun mode the PR draft must have no real GitHub URL."""
        result = run_demo(store, settings)

        drafts = list_pr_drafts(deployment_id=result["canary_dep_id"])
        assert len(drafts) >= 1

        draft = drafts[-1]
        assert draft.mode == "dryrun"
        assert draft.prUrl is None

    def test_pr_draft_body_contains_diagnosis_section(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """The PR body must contain the diagnosis and canary regression report."""
        result = run_demo(store, settings)

        pr = result["pr_draft"]
        assert pr is not None

        # Required sections in the PR body
        assert "Canary Regression Report" in pr.body
        assert "Diagnosis" in pr.body
        assert "Suggested improvements" in pr.body
        assert "Next steps" in pr.body

    def test_pr_draft_body_references_deployment(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """The PR body must reference the deployment ID."""
        result = run_demo(store, settings)

        pr = result["pr_draft"]
        assert pr is not None
        assert result["canary_dep_id"] in pr.body

    def test_pr_draft_body_contains_eval_signal_table(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """The PR body must include the gray-zone signal metrics table."""
        result = run_demo(store, settings)

        pr = result["pr_draft"]
        assert pr is not None

        # Signal table headers
        assert "Drift score drop" in pr.body
        assert "Trajectory score drop" in pr.body

    def test_pr_draft_linked_to_decision_record(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """The DecisionRecord for a rollback must reference the PR draft ID."""
        result = run_demo(store, settings)

        decisions = list_decisions(deployment_id=result["canary_dep_id"])
        rollback_decisions = [d for d in decisions if d.action == "rollback"]
        assert len(rollback_decisions) >= 1

        dec = rollback_decisions[-1]
        assert dec.prDraftId is not None
        assert dec.prDraftId == result["pr_draft_id"]


class TestAuditEventLog:
    """Rollback and evaluation events must appear in the audit log."""

    def test_events_are_recorded(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """At least one event must be recorded for the demo agent."""
        result = run_demo(store, settings)

        assert result["event_count"] > 0

    def test_rollback_event_is_recorded(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """A deployment.rolled_back event must appear in the audit log."""
        result = run_demo(store, settings)

        events = store.list_events(result["agent_id"])
        event_types = [e.type for e in events]

        assert "deployment.rolled_back" in event_types, (
            f"Expected 'deployment.rolled_back' in events, got: {event_types}"
        )

    def test_evaluation_events_are_recorded(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """Evaluation finished events must appear for both baseline and canary."""
        result = run_demo(store, settings)

        events = store.list_events(result["agent_id"])
        eval_events = [e for e in events if e.type == "evaluation.finished"]

        # At least two evaluations: baseline + canary
        assert len(eval_events) >= 2, (
            f"Expected >= 2 evaluation.finished events, got {len(eval_events)}"
        )

    def test_rollback_event_detail_is_informative(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """The rollback event detail must contain a non-trivial description."""
        result = run_demo(store, settings)

        events = store.list_events(result["agent_id"])
        rollback_events = [e for e in events if e.type == "deployment.rolled_back"]
        assert len(rollback_events) >= 1

        evt = rollback_events[-1]
        assert evt.detail is not None
        assert len(evt.detail) > 5


class TestDashboardDataReflectsRollback:
    """The /dashboard/data endpoint must reflect the post-rollback state."""

    @pytest.fixture
    def demo_client(self) -> TestClient:
        """TestClient backed by a fresh store and stub judge."""
        store = MemoryStore()
        stub_judge = StubJudge()
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_judge_dep] = lambda: stub_judge
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_dashboard_data_includes_decision_after_demo(self) -> None:
        """After running the demo, /dashboard/data must include decision records."""
        store = MemoryStore()
        settings = _demo_settings()
        stub_judge = StubJudge()

        # Wire the demo store into the API
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_judge_dep] = lambda: stub_judge
        client = TestClient(app)

        try:
            result = run_demo(store, settings)
            assert result["rollback_happened"]

            resp = client.get("/dashboard/data")
            assert resp.status_code == 200

            data = resp.json()
            assert "decisions" in data
            assert "pr_drafts" in data

            rollback_decisions = [
                d for d in data["decisions"] if d["action"] == "rollback"
            ]
            assert len(rollback_decisions) >= 1, (
                f"Expected rollback decisions in /dashboard/data, "
                f"got decisions: {[d['action'] for d in data['decisions']]}"
            )
        finally:
            app.dependency_overrides.clear()

    def test_dashboard_data_includes_rolled_back_deployment(self) -> None:
        """The deployments list in /dashboard/data must show rolled_back state."""
        store = MemoryStore()
        settings = _demo_settings()
        stub_judge = StubJudge()

        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_judge_dep] = lambda: stub_judge
        client = TestClient(app)

        try:
            result = run_demo(store, settings)
            assert result["rollback_happened"]

            resp = client.get("/dashboard/data")
            assert resp.status_code == 200

            data = resp.json()
            rolled_back_deps = [
                d for d in data["deployments"] if d["state"] == "rolled_back"
            ]
            assert len(rolled_back_deps) >= 1, (
                "Expected at least one rolled_back deployment in /dashboard/data"
            )
        finally:
            app.dependency_overrides.clear()

    def test_dashboard_data_includes_pr_drafts(self) -> None:
        """PR drafts from the demo must appear in /dashboard/data."""
        store = MemoryStore()
        settings = _demo_settings()
        stub_judge = StubJudge()

        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_judge_dep] = lambda: stub_judge
        client = TestClient(app)

        try:
            result = run_demo(store, settings)

            resp = client.get("/dashboard/data")
            assert resp.status_code == 200

            data = resp.json()
            assert len(data["pr_drafts"]) >= 1, (
                "Expected at least one PR draft in /dashboard/data"
            )
        finally:
            app.dependency_overrides.clear()

    def test_dashboard_data_evaluations_cover_both_versions(self) -> None:
        """Both baseline and canary evaluations must appear in the score timeline."""
        store = MemoryStore()
        settings = _demo_settings()
        stub_judge = StubJudge()

        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[get_judge_dep] = lambda: stub_judge
        client = TestClient(app)

        try:
            result = run_demo(store, settings)

            resp = client.get("/dashboard/data")
            assert resp.status_code == 200

            data = resp.json()
            eval_version_ids = {e["versionId"] for e in data["evaluations"]}

            # Both the baseline and canary version IDs must appear
            baseline_dep = store.get_deployment(result["baseline_dep_id"])
            canary_dep = store.get_deployment(result["canary_dep_id"])
            assert baseline_dep is not None
            assert canary_dep is not None

            assert baseline_dep.versionId in eval_version_ids, (
                "Baseline version evaluation not found in /dashboard/data"
            )
            assert canary_dep.versionId in eval_version_ids, (
                "Canary version evaluation not found in /dashboard/data"
            )
        finally:
            app.dependency_overrides.clear()


class TestDemoIsolation:
    """The demo must be fully self-contained and repeatable."""

    def test_demo_is_repeatable(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """Running the demo twice in fresh stores must both yield rollback."""
        result1 = run_demo(store, settings)

        store2 = MemoryStore()
        _clear_store()
        result2 = run_demo(store2, settings)

        assert result1["rollback_happened"] is True
        assert result2["rollback_happened"] is True

    def test_demo_produces_no_external_calls(
        self, store: MemoryStore, settings: Settings
    ) -> None:
        """In dryrun mode no subprocess should be spawned."""
        from unittest.mock import patch

        with patch("agentops_platform.meta_agent.subprocess.run") as mock_sub:
            run_demo(store, settings)
            mock_sub.assert_not_called()
