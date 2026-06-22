"""
Unit tests for Gemini backend switching (unified google-genai SDK).

Covers:
  - gemini_client.generate_text routes to the correct backend based on the
    `backend` argument.
  - AI Studio path: calls genai.Client(api_key=...) then
    client.models.generate_content(model=..., contents=...)
  - Vertex / Gemini Enterprise Agent Platform path: calls
    genai.Client(vertexai=True, project=..., location=...) then
    client.models.generate_content(model=..., contents=...)
  - Both paths return the model's text on success.
  - Both paths return "" on import error (SDK not installed).
  - Both paths return "" on API error (graceful degradation).
  - Settings.gemini_backend defaults to "aistudio".
  - MetaAgentCycle._call_gemini_judge uses gemini_client.generate_text
    (via the settings-driven backend), not google.genai directly.
  - GeminiJudge._score_drift uses gemini_client.generate_text.
  - Stub judge default: existing tests stay green without any real LLM.
"""

from __future__ import annotations

import sys
import os

# Ensure packages are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest

from agentops_platform.gemini_client import generate_text, _generate_aistudio, _generate_vertex
from agentops_platform.judge import GeminiJudge, StubJudge, get_judge
from agentops_platform.models import EvalSuite, EvaluationRun
from config.defaults import Settings


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(**overrides: object) -> Settings:
    """Build a Settings object with sensible test defaults."""
    kwargs = {
        "AGENTOPS_JUDGE_BACKEND": "stub",
        "AGENTOPS_JUDGE_MODEL": "gemini-2.0-flash",
        "AGENTOPS_GEMINI_BACKEND": "aistudio",
        "AGENTOPS_GOOGLE_API_KEY": "",
        "AGENTOPS_GOOGLE_CLOUD_PROJECT": "test-project",
        "AGENTOPS_GOOGLE_CLOUD_LOCATION": "us-central1",
    }
    kwargs.update({k: str(v) for k, v in overrides.items()})
    return Settings(**{k.replace("AGENTOPS_", "").lower(): v
                       for k, v in kwargs.items()
                       if k.startswith("AGENTOPS_")})


def _make_genai_client_mock(text: str = "0.85") -> MagicMock:
    """Build a mock that mimics the unified google-genai Client interface.

    Simulates: client.models.generate_content(model=..., contents=...) -> response
    where response.text == text.
    """
    mock_response = MagicMock()
    mock_response.text = text
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    mock_genai = MagicMock()
    mock_genai.Client.return_value = mock_client
    return mock_genai


# ── Settings defaults ─────────────────────────────────────────────────────────


class TestSettingsDefaults:
    def test_gemini_backend_default_is_aistudio(self) -> None:
        settings = _make_settings()
        assert settings.gemini_backend == "aistudio"

    def test_gemini_backend_vertex(self) -> None:
        settings = _make_settings(AGENTOPS_GEMINI_BACKEND="vertex")
        assert settings.gemini_backend == "vertex"

    def test_google_cloud_location_default(self) -> None:
        settings = Settings(gemini_backend="aistudio", judge_model="gemini-2.0-flash")
        assert settings.google_cloud_location == "asia-northeast1"

    def test_google_api_key_default_empty(self) -> None:
        settings = _make_settings()
        assert settings.google_api_key == ""

    def test_judge_backend_default_is_stub(self) -> None:
        settings = _make_settings()
        assert settings.judge_backend == "stub"


# ── gemini_client.generate_text — routing ────────────────────────────────────


class TestGenerateTextRouting:
    def test_aistudio_backend_calls_generate_aistudio(self) -> None:
        """generate_text(backend='aistudio') must delegate to _generate_aistudio."""
        with patch("agentops_platform.gemini_client._generate_aistudio", return_value="hello from aistudio") as mock_fn:
            result = generate_text("test prompt", "gemini-2.0-flash", backend="aistudio")
        assert result == "hello from aistudio"
        mock_fn.assert_called_once_with("test prompt", "gemini-2.0-flash")

    def test_vertex_backend_calls_generate_vertex(self) -> None:
        """generate_text(backend='vertex') must delegate to _generate_vertex."""
        with patch("agentops_platform.gemini_client._generate_vertex", return_value="hello from vertex") as mock_fn:
            result = generate_text(
                "test prompt",
                "gemini-2.0-flash",
                backend="vertex",
                project="my-project",
                location="us-central1",
            )
        assert result == "hello from vertex"
        mock_fn.assert_called_once_with("test prompt", "gemini-2.0-flash", project="my-project", location="us-central1")

    def test_default_backend_is_aistudio(self) -> None:
        """Calling generate_text without backend= routes to _generate_aistudio."""
        with patch("agentops_platform.gemini_client._generate_aistudio", return_value="default backend") as mock_fn:
            result = generate_text("test prompt", "gemini-2.0-flash")
        assert result == "default backend"
        mock_fn.assert_called_once()


# ── gemini_client — error handling ───────────────────────────────────────────


class TestGenerateTextErrors:
    def test_aistudio_import_error_returns_empty(self) -> None:
        """If google-genai is not installed, _generate_aistudio returns ''."""
        with patch.dict("sys.modules", {"google.genai": None}):
            result = _generate_aistudio("prompt", "gemini-2.0-flash")
        assert result == ""

    def test_vertex_import_error_returns_empty(self) -> None:
        """If google-genai is not installed, _generate_vertex returns ''."""
        with patch.dict("sys.modules", {"google.genai": None}):
            result = _generate_vertex("prompt", "gemini-2.0-flash")
        assert result == ""

    def test_aistudio_api_error_returns_empty(self) -> None:
        """API errors from AI Studio are swallowed; return ''."""
        mock_genai = _make_genai_client_mock()
        mock_genai.Client.side_effect = RuntimeError("API unavailable")
        with patch.dict("sys.modules", {"google.genai": mock_genai}):
            result = _generate_aistudio("prompt", "gemini-2.0-flash")
        assert result == ""

    def test_vertex_api_error_returns_empty(self) -> None:
        """API errors from the Gemini Enterprise Agent Platform are swallowed; return ''."""
        mock_genai = _make_genai_client_mock()
        mock_genai.Client.side_effect = RuntimeError("ADC not configured")
        with patch.dict("sys.modules", {"google.genai": mock_genai}):
            result = _generate_vertex("prompt", "gemini-2.0-flash")
        assert result == ""


# ── GeminiJudge uses gemini_client ───────────────────────────────────────────


class TestGeminiJudgeBackendSwitch:
    """GeminiJudge._score_drift must delegate to gemini_client.generate_text."""

    def _make_eval_run(self) -> EvaluationRun:
        from datetime import datetime, timezone
        return EvaluationRun(
            evaluationId="eval-test-001",
            versionId="v1",
            suiteId="suite-1",
            state="running",
            startedAt=datetime.now(tz=timezone.utc),
        )

    def _make_suite(self) -> EvalSuite:
        from datetime import datetime, timezone
        return EvalSuite(
            suiteId="suite-1",
            name="smoke",
            adkEvalSetRef="gs://bucket/eval.json",
            createdAt=datetime.now(tz=timezone.utc),
        )

    def _make_judge(self, settings: Settings) -> GeminiJudge:
        """Build GeminiJudge and inject test settings + mock generate function."""
        with patch("config.defaults.get_settings", return_value=settings):
            judge = GeminiJudge(model_id="gemini-2.0-flash")
        # Override _settings so _score_drift uses the test settings
        judge._settings = settings
        return judge

    def test_gemini_judge_calls_generate_text_aistudio(self) -> None:
        """GeminiJudge with aistudio backend passes backend='aistudio' to generate_text."""
        settings = _make_settings(AGENTOPS_GEMINI_BACKEND="aistudio")
        mock_gen = MagicMock(return_value="0.85")

        judge = self._make_judge(settings)
        judge._generate = mock_gen
        score = judge._score_drift("gemini-2.0-flash")

        assert score == pytest.approx(0.85)
        mock_gen.assert_called_once()
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("backend") == "aistudio"

    def test_gemini_judge_calls_generate_text_vertex(self) -> None:
        """GeminiJudge with vertex backend passes backend='vertex' + project/location."""
        settings = _make_settings(
            AGENTOPS_GEMINI_BACKEND="vertex",
            AGENTOPS_GOOGLE_CLOUD_PROJECT="my-proj",
            AGENTOPS_GOOGLE_CLOUD_LOCATION="us-central1",
        )
        mock_gen = MagicMock(return_value="0.90")

        judge = self._make_judge(settings)
        judge._generate = mock_gen
        score = judge._score_drift("gemini-2.0-flash")

        assert score == pytest.approx(0.90)
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("backend") == "vertex"
        assert call_kwargs.get("project") == "my-proj"
        assert call_kwargs.get("location") == "us-central1"

    def test_gemini_judge_empty_response_returns_neutral_score(self) -> None:
        """Empty response from generate_text returns the neutral fallback score 0.80."""
        settings = _make_settings()
        mock_gen = MagicMock(return_value="")

        judge = self._make_judge(settings)
        judge._generate = mock_gen
        score = judge._score_drift("gemini-2.0-flash")

        assert score == pytest.approx(0.80)

    def test_gemini_judge_evaluate_returns_all_axes(self) -> None:
        """GeminiJudge.evaluate returns AxisScore for all four axes."""
        settings = _make_settings()
        mock_gen = MagicMock(return_value="0.92")

        judge = self._make_judge(settings)
        judge._generate = mock_gen
        scores = judge.evaluate(self._make_eval_run(), self._make_suite())

        axes = {s.axis for s in scores}
        assert axes == {"drift", "trajectory", "cost", "latency"}
        drift = next(s for s in scores if s.axis == "drift")
        assert drift.score == pytest.approx(0.92)


# ── MetaAgentCycle backend integration ───────────────────────────────────────


class TestMetaAgentBackendIntegration:
    """_call_gemini_judge must route through gemini_client, not import genai directly."""

    def test_call_gemini_judge_aistudio_path(self) -> None:
        """With aistudio backend, _call_gemini_judge calls generate_text(backend='aistudio').

        We patch agentops_platform.gemini_client.generate_text because
        _call_gemini_judge does `from .gemini_client import generate_text` at
        call time, so the live reference lives in the gemini_client module.
        """
        from agentops_platform.meta_agent import _call_gemini_judge

        settings = _make_settings(AGENTOPS_GEMINI_BACKEND="aistudio")
        resp = '{"action": "advance", "rationale": "looks good"}'

        with patch("agentops_platform.gemini_client.generate_text", return_value=resp) as mock_gen:
            action, rationale = _call_gemini_judge("test prompt", "gemini-2.0-flash", settings=settings)

        assert action == "advance"
        assert "looks good" in rationale
        mock_gen.assert_called_once()
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("backend") == "aistudio"

    def test_call_gemini_judge_vertex_path(self) -> None:
        """With vertex backend, _call_gemini_judge calls generate_text(backend='vertex')."""
        from agentops_platform.meta_agent import _call_gemini_judge

        settings = _make_settings(
            AGENTOPS_GEMINI_BACKEND="vertex",
            AGENTOPS_GOOGLE_CLOUD_PROJECT="my-proj",
            AGENTOPS_GOOGLE_CLOUD_LOCATION="us-central1",
        )
        resp = '{"action": "hold", "rationale": "uncertain"}'

        with patch("agentops_platform.gemini_client.generate_text", return_value=resp) as mock_gen:
            action, rationale = _call_gemini_judge("test prompt", "gemini-2.0-flash", settings=settings)

        assert action == "hold"
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs.get("backend") == "vertex"
        assert call_kwargs.get("project") == "my-proj"
        assert call_kwargs.get("location") == "us-central1"

    def test_call_gemini_judge_empty_response_defaults_to_hold(self) -> None:
        """Empty response from generate_text causes safe fallback to hold."""
        from agentops_platform.meta_agent import _call_gemini_judge

        settings = _make_settings()
        with patch("agentops_platform.gemini_client.generate_text", return_value=""):
            action, rationale = _call_gemini_judge("test prompt", "gemini-2.0-flash", settings=settings)

        assert action == "hold"
        assert "defaulting to hold" in rationale.lower()

    def test_call_gemini_judge_invalid_json_defaults_to_hold(self) -> None:
        """Non-JSON response causes safe fallback to hold."""
        from agentops_platform.meta_agent import _call_gemini_judge

        settings = _make_settings()
        with patch("agentops_platform.gemini_client.generate_text", return_value="not json at all"):
            action, rationale = _call_gemini_judge("test prompt", "gemini-2.0-flash", settings=settings)

        assert action == "hold"


# ── Stub default: existing green remains ─────────────────────────────────────


class TestStubDefaultRemains:
    """Confirm that stub backend is the default and requires no real LLM."""

    def test_get_judge_stub_default(self) -> None:
        judge = get_judge("stub")
        assert isinstance(judge, StubJudge)

    def test_stub_judge_no_network_call(self) -> None:
        """StubJudge.evaluate never touches the network."""
        from datetime import datetime, timezone

        judge = StubJudge()
        run = EvaluationRun(
            evaluationId="e1",
            versionId="v1",
            suiteId="s1",
            state="running",
            startedAt=datetime.now(tz=timezone.utc),
        )
        suite = EvalSuite(
            suiteId="s1",
            name="smoke",
            adkEvalSetRef="gs://bucket/eval.json",
            createdAt=datetime.now(tz=timezone.utc),
        )
        # No mock needed — no network calls in StubJudge
        scores = judge.evaluate(run, suite)
        assert len(scores) == 4
        axes = {s.axis for s in scores}
        assert axes == {"drift", "trajectory", "cost", "latency"}
