"""
Tests for the Managed Agents section of the dashboard data endpoint.

Verifies that:
  - GET /dashboard/data includes an 'agents' key
  - A fresh store with no agents returns an empty agents list
  - An externally registered agent appears in the agents list with correct fields
  - Agent versions (count, latestVersion, gitCommit) are reflected correctly
  - Multiple agents with different version counts are all included
  - Metric sample counts are aggregated correctly
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ── Helpers ───────────────────────────────────────────────────────────────────


def _register_agent(client: TestClient, name: str, description: str | None = None) -> dict:
    body: dict = {"name": name, "runtime": "adk-cloud-run"}
    if description:
        body["description"] = description
    resp = client.post("/v1/agents", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_version(
    client: TestClient,
    agent_id: str,
    git_commit: str | None = None,
) -> dict:
    body = {
        "image": "gcr.io/project/agent@sha256:abc123",
        "model": "gemini-2.0-flash",
        "promptDigest": "sha256:deadbeef",
    }
    if git_commit is not None:
        body["gitCommit"] = git_commit
    resp = client.post(f"/v1/agents/{agent_id}/versions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _ingest_metrics(client: TestClient, agent_id: str, version_id: str) -> None:
    resp = client.post(
        f"/v1/agents/{agent_id}/metrics",
        json={
            "versionId": version_id,
            "source": "test",
            "samples": [
                {"name": "retention_rate", "value": 0.85, "observedAt": "2026-06-26T10:00:00Z"},
                {"name": "error_rate", "value": 0.02, "observedAt": "2026-06-26T10:01:00Z"},
            ],
        },
    )
    assert resp.status_code == 202, resp.text


def _dashboard_data(client: TestClient) -> dict:
    resp = client.get("/dashboard/data")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestDashboardAgentsKey:
    def test_agents_key_present_in_dashboard_data(self, client: TestClient) -> None:
        """The 'agents' key must always be present in the dashboard payload."""
        data = _dashboard_data(client)
        assert "agents" in data

    def test_agents_empty_when_no_agents_registered(self, client: TestClient) -> None:
        """An empty store returns an empty agents list (not missing, not null)."""
        data = _dashboard_data(client)
        assert isinstance(data["agents"], list)
        assert len(data["agents"]) == 0


class TestDashboardAgentRegistration:
    def test_registered_agent_appears_in_agents_section(self, client: TestClient) -> None:
        """An agent registered via POST /v1/agents must appear in dashboard/data agents."""
        agent = _register_agent(client, "marketing-shorts-agent")
        data = _dashboard_data(client)

        agent_ids = [a["agentId"] for a in data["agents"]]
        assert agent["agentId"] in agent_ids

    def test_agent_fields_correct(self, client: TestClient) -> None:
        """Agent summary contains all expected fields with correct values."""
        agent = _register_agent(client, "marketing-shorts-agent", description="Test agent")
        data = _dashboard_data(client)

        entry = next(a for a in data["agents"] if a["agentId"] == agent["agentId"])
        assert entry["name"] == "marketing-shorts-agent"
        assert entry["runtime"] == "adk-cloud-run"
        assert "createdAt" in entry
        assert "lastActivityAt" in entry
        assert "versionCount" in entry
        assert "metricSampleCount" in entry

    def test_agent_no_versions_has_zero_version_count(self, client: TestClient) -> None:
        """An agent with no versions shows versionCount=0 and latestVersion=None."""
        _register_agent(client, "bare-agent")
        data = _dashboard_data(client)

        entry = next(a for a in data["agents"] if a["name"] == "bare-agent")
        assert entry["versionCount"] == 0
        assert entry["latestVersion"] is None


class TestDashboardAgentVersions:
    def test_single_version_reflected_in_latest_version(self, client: TestClient) -> None:
        """After registering one version, latestVersion appears with correct versionId."""
        agent = _register_agent(client, "agent-with-version")
        version = _create_version(client, agent["agentId"], git_commit="abc1234567")

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent["agentId"])

        assert entry["versionCount"] == 1
        assert entry["latestVersion"] is not None
        assert entry["latestVersion"]["versionId"] == version["versionId"]
        assert entry["latestVersion"]["gitCommit"] == "abc1234567"

    def test_latest_version_reflects_most_recent(self, client: TestClient) -> None:
        """With two versions, latestVersion is the second (most recently registered)."""
        agent = _register_agent(client, "multi-version-agent")
        _create_version(client, agent["agentId"], git_commit="first-commit")
        v2 = _create_version(client, agent["agentId"], git_commit="second-commit")

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent["agentId"])

        assert entry["versionCount"] == 2
        assert entry["latestVersion"]["versionId"] == v2["versionId"]
        assert entry["latestVersion"]["gitCommit"] == "second-commit"

    def test_version_without_git_commit(self, client: TestClient) -> None:
        """A version with no gitCommit has gitCommit=None in latestVersion."""
        agent = _register_agent(client, "no-commit-agent")
        _create_version(client, agent["agentId"])  # no gitCommit kwarg

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent["agentId"])

        assert entry["latestVersion"] is not None
        assert entry["latestVersion"]["gitCommit"] is None


class TestDashboardMultipleAgents:
    def test_multiple_agents_all_appear(self, client: TestClient) -> None:
        """Both a seed-style demo agent and an external agent appear in the same list."""
        a1 = _register_agent(client, "demo-managed-agent")
        a2 = _register_agent(client, "marketing-shorts-agent")
        _create_version(client, a2["agentId"], git_commit="deadbeef1234")
        _create_version(client, a2["agentId"], git_commit="cafebabe5678")

        data = _dashboard_data(client)
        agent_names = {a["name"] for a in data["agents"]}
        assert "demo-managed-agent" in agent_names
        assert "marketing-shorts-agent" in agent_names

        mktg = next(a for a in data["agents"] if a["name"] == "marketing-shorts-agent")
        assert mktg["versionCount"] == 2
        assert mktg["latestVersion"]["gitCommit"] == "cafebabe5678"

    def test_agents_count_matches_registered(self, client: TestClient) -> None:
        """The number of entries in agents equals the number of registered agents."""
        _register_agent(client, "agent-alpha")
        _register_agent(client, "agent-beta")
        _register_agent(client, "agent-gamma")

        data = _dashboard_data(client)
        assert len(data["agents"]) == 3


class TestDashboardAgentMetrics:
    def test_metric_sample_count_aggregated(self, client: TestClient) -> None:
        """metricSampleCount reflects the total number of ingested metric samples."""
        agent = _register_agent(client, "metrics-agent")
        version = _create_version(client, agent["agentId"])
        _ingest_metrics(client, agent["agentId"], version["versionId"])

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent["agentId"])

        # _ingest_metrics pushes 2 samples
        assert entry["metricSampleCount"] == 2

    def test_no_metrics_shows_zero_count(self, client: TestClient) -> None:
        """An agent with no metrics ingested shows metricSampleCount=0."""
        agent = _register_agent(client, "no-metrics-agent")
        data = _dashboard_data(client)

        entry = next(a for a in data["agents"] if a["agentId"] == agent["agentId"])
        assert entry["metricSampleCount"] == 0


def _ingest_metrics_with_qa(
    client: TestClient,
    agent_id: str,
    version_id: str,
    qa_pass: float,
    reason: str | None = None,
) -> None:
    """Helper: POST metrics with video_qa_pass in extra."""
    extra: dict = {"video_qa_pass": qa_pass}
    if reason is not None:
        extra["video_qa_visual_reason"] = reason
    resp = client.post(
        f"/v1/agents/{agent_id}/metrics",
        json={
            "versionId": version_id,
            "source": "video-qa",
            "samples": [
                {"name": "qa_sentinel", "value": qa_pass, "observedAt": "2026-07-11T10:00:00Z"},
            ],
            "extra": extra,
        },
    )
    assert resp.status_code == 202, resp.text


class TestDashboardVideoQaSummary:
    """Video QA サマリ算出のテスト群。"""

    def _setup(self, client: TestClient, name: str) -> tuple[str, str]:
        agent = _register_agent(client, name)
        version = _create_version(client, agent["agentId"])
        return agent["agentId"], version["versionId"]

    def test_no_qa_metrics_returns_zero_counts(self, client: TestClient) -> None:
        """extra に video_qa_pass が含まれないメトリクスだけの場合、pass/fail ともに 0。"""
        agent = _register_agent(client, "qa-agent-no-qa")
        version = _create_version(client, agent["agentId"])
        # QA なしの通常メトリクス
        _ingest_metrics(client, agent["agentId"], version["versionId"])

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent["agentId"])
        qa = entry["videoQaSummary"]
        assert qa["pass_count"] == 0
        assert qa["fail_count"] == 0
        assert qa["latest_pass"] is None
        assert qa["latest_at"] is None

    def test_single_pass_verdict(self, client: TestClient) -> None:
        """video_qa_pass=1.0 の1件でpass_count=1, fail_count=0, latest_pass=True。"""
        agent_id, version_id = self._setup(client, "qa-agent-single-pass")
        _ingest_metrics_with_qa(client, agent_id, version_id, qa_pass=1.0)

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent_id)
        qa = entry["videoQaSummary"]
        assert qa["pass_count"] == 1
        assert qa["fail_count"] == 0
        assert qa["latest_pass"] is True

    def test_single_fail_verdict(self, client: TestClient) -> None:
        """video_qa_pass=0.0 の1件でpass_count=0, fail_count=1, latest_pass=False。"""
        agent_id, version_id = self._setup(client, "qa-agent-single-fail")
        _ingest_metrics_with_qa(client, agent_id, version_id, qa_pass=0.0)

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent_id)
        qa = entry["videoQaSummary"]
        assert qa["pass_count"] == 0
        assert qa["fail_count"] == 1
        assert qa["latest_pass"] is False

    def test_mixed_verdicts_count_correctly(self, client: TestClient) -> None:
        """pass 2件 + fail 1件 → pass_count=2, fail_count=1, latest_pass は最終バッチ。"""
        agent_id, version_id = self._setup(client, "qa-agent-mixed")
        _ingest_metrics_with_qa(client, agent_id, version_id, qa_pass=1.0)
        _ingest_metrics_with_qa(client, agent_id, version_id, qa_pass=0.0)
        _ingest_metrics_with_qa(client, agent_id, version_id, qa_pass=1.0)

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent_id)
        qa = entry["videoQaSummary"]
        assert qa["pass_count"] == 2
        assert qa["fail_count"] == 1
        # 最後に投入した qa_pass=1.0 が latest
        assert qa["latest_pass"] is True

    def test_fail_reason_is_included(self, client: TestClient) -> None:
        """video_qa_visual_reason が extra にある場合、latest_reason に反映される。"""
        agent_id, version_id = self._setup(client, "qa-agent-reason")
        reason = "映像が暗すぎてテロップが判読できない"
        _ingest_metrics_with_qa(client, agent_id, version_id, qa_pass=0.0, reason=reason)

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent_id)
        qa = entry["videoQaSummary"]
        assert qa["latest_reason"] == reason

    def test_video_qa_summary_key_always_present(self, client: TestClient) -> None:
        """videoQaSummary キーはメトリクスがなくても常に存在する。"""
        agent = _register_agent(client, "qa-key-presence-agent")
        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent["agentId"])
        assert "videoQaSummary" in entry


def _ingest_metrics_msa_wire(
    client: TestClient,
    agent_id: str,
    version_id: str,
    qa_pass: float,
    observed_at: str = "2026-07-11T10:00:00Z",
) -> None:
    """Helper: POST metrics in MSA real wire format.

    marketing-shorts-agent sends video_qa_pass as a flat MetricSample
    (value 1.0 / 0.0) with NO extra field.
    """
    resp = client.post(
        f"/v1/agents/{agent_id}/metrics",
        json={
            "versionId": version_id,
            "source": "video-qa",
            "samples": [
                {
                    "name": "video_qa_pass",
                    "value": qa_pass,
                    "observedAt": observed_at,
                },
            ],
            # extra は意図的に省略（MSA の実 wire 形式）
        },
    )
    assert resp.status_code == 202, resp.text


class TestDashboardVideoQaSummaryMsaWire:
    """MSA 実 wire 形式（extra なし・samples に video_qa_pass）のテスト群。"""

    def _setup(self, client: TestClient, name: str) -> tuple[str, str]:
        agent = _register_agent(client, name)
        version = _create_version(client, agent["agentId"])
        return agent["agentId"], version["versionId"]

    def test_msa_single_pass_via_sample(self, client: TestClient) -> None:
        """extra なし・samples[video_qa_pass=1.0] → pass_count=1, latest_pass=True."""
        agent_id, version_id = self._setup(client, "msa-qa-single-pass")
        _ingest_metrics_msa_wire(client, agent_id, version_id, qa_pass=1.0)

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent_id)
        qa = entry["videoQaSummary"]
        assert qa["pass_count"] == 1
        assert qa["fail_count"] == 0
        assert qa["latest_pass"] is True

    def test_msa_single_fail_via_sample(self, client: TestClient) -> None:
        """extra なし・samples[video_qa_pass=0.0] → fail_count=1, latest_pass=False."""
        agent_id, version_id = self._setup(client, "msa-qa-single-fail")
        _ingest_metrics_msa_wire(client, agent_id, version_id, qa_pass=0.0)

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent_id)
        qa = entry["videoQaSummary"]
        assert qa["pass_count"] == 0
        assert qa["fail_count"] == 1
        assert qa["latest_pass"] is False

    def test_msa_mixed_verdicts_via_sample(self, client: TestClient) -> None:
        """extra なし・pass 2件 + fail 1件 → counts と latest が正しい。"""
        agent_id, version_id = self._setup(client, "msa-qa-mixed")
        _ingest_metrics_msa_wire(
            client, agent_id, version_id, qa_pass=1.0, observed_at="2026-07-11T10:00:00Z"
        )
        _ingest_metrics_msa_wire(
            client, agent_id, version_id, qa_pass=0.0, observed_at="2026-07-11T11:00:00Z"
        )
        _ingest_metrics_msa_wire(
            client, agent_id, version_id, qa_pass=1.0, observed_at="2026-07-11T12:00:00Z"
        )

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent_id)
        qa = entry["videoQaSummary"]
        assert qa["pass_count"] == 2
        assert qa["fail_count"] == 1
        # 最後に投入した qa_pass=1.0 が latest
        assert qa["latest_pass"] is True

    def test_msa_latest_at_reflects_observed_at(self, client: TestClient) -> None:
        """extra なし wire で latest_at が samples[video_qa_pass].observedAt を反映する。"""
        agent_id, version_id = self._setup(client, "msa-qa-latest-at")
        _ingest_metrics_msa_wire(
            client, agent_id, version_id, qa_pass=1.0, observed_at="2026-07-11T09:30:00Z"
        )

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent_id)
        qa = entry["videoQaSummary"]
        assert qa["latest_at"] is not None
        assert "2026-07-11" in qa["latest_at"]

    def test_msa_latest_reason_is_none_when_no_extra(self, client: TestClient) -> None:
        """extra がない MSA wire では latest_reason が None になる（フロントは None を安全に扱う想定）。"""
        agent_id, version_id = self._setup(client, "msa-qa-no-reason")
        _ingest_metrics_msa_wire(client, agent_id, version_id, qa_pass=1.0)

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent_id)
        qa = entry["videoQaSummary"]
        assert qa["latest_reason"] is None

    def test_msa_wire_does_not_affect_extra_path(self, client: TestClient) -> None:
        """extra 経路と samples 経路を混在させても合計 counts が正しい。"""
        agent_id, version_id = self._setup(client, "msa-qa-mixed-paths")
        # extra 経路 (pass)
        _ingest_metrics_with_qa(client, agent_id, version_id, qa_pass=1.0)
        # MSA wire 経路 (fail)
        _ingest_metrics_msa_wire(client, agent_id, version_id, qa_pass=0.0)

        data = _dashboard_data(client)
        entry = next(a for a in data["agents"] if a["agentId"] == agent_id)
        qa = entry["videoQaSummary"]
        assert qa["pass_count"] == 1
        assert qa["fail_count"] == 1
