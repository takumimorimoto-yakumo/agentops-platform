"""
Read-only web dashboard — meigara 計器盤スタイルの監視コンソール。

構造:
  1. KPI grid         — gap:1px + border-line 背景で線を描く meigara パターン
  2. 2-column main    — 左: Score Timeline + Deployment Status
                        右: Meta-agent Decisions フィード (amber アクセント)
  3. Managed Agents   — full-width テーブル

ファイル分離:
  - src/agentops_platform/static/tokens.css   — デザイントークン SSOT
  - src/agentops_platform/static/dashboard.css — レイアウト・コンポーネント
  - src/agentops_platform/static/dashboard.js  — ポーリング・描画ロジック
  - src/agentops_platform/static/dashboard.html — HTML テンプレート

The router exposes:
  GET /dashboard        — HTML テンプレート (静的ファイルから読み込み)
  GET /dashboard/data   — JSON snapshot used by the page's JS polling loop
  /static/*             — 上記 CSS / JS ファイル (StaticFiles)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .meta_agent import list_decisions, list_pr_drafts
from .repository import MemoryStore
from .routers.deps import StoreDep

router = APIRouter(tags=["dashboard"])

# Static files directory
_STATIC_DIR = Path(__file__).parent / "static"
_DASHBOARD_HTML_PATH = _STATIC_DIR / "dashboard.html"


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/dashboard", include_in_schema=False)
def dashboard_html() -> FileResponse:
    """Serve the read-only monitoring dashboard (meigara 計器盤スタイル).

    HTML / CSS / JS は static/ に分離済み。
    StaticFiles は main.py で /static にマウントされる。
    """
    return FileResponse(str(_DASHBOARD_HTML_PATH), media_type="text/html")


@router.get("/dashboard/data", include_in_schema=False)
def dashboard_data(store: StoreDep) -> JSONResponse:
    """Return a JSON snapshot for the dashboard's polling loop.

    Aggregates:
      - All evaluations (across all agents)
      - All deployments (across all agents)
      - All meta-agent decision records
      - All PR drafts
    """
    all_evals = []
    all_deployments = []

    for agent in store.list_agents():
        evals = store.list_evaluations(agent.agentId)
        all_evals.extend(evals)
        deps = store.list_deployments(agent.agentId)
        all_deployments.extend(deps)

    # Sort evaluations by finishedAt descending
    all_evals.sort(
        key=lambda e: e.finishedAt or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    decisions = list_decisions()
    pr_drafts = list_pr_drafts()

    agents = store.list_agents()

    payload = {
        "evaluations": [_eval_to_dict(e) for e in all_evals[:50]],
        "deployments": [_dep_to_dict(d) for d in all_deployments],
        "decisions": [_decision_to_dict(dr) for dr in decisions],
        "pr_drafts": [_pr_to_dict(p) for p in pr_drafts],
        "agents": [_agent_summary_to_dict(a, store) for a in agents],
    }
    return JSONResponse(content=payload)


# ── Serialization helpers ─────────────────────────────────────────────────────


def _eval_to_dict(e: object) -> dict:  # type: ignore[type-arg]
    return json.loads(e.model_dump_json())  # type: ignore[attr-defined]


def _dep_to_dict(d: object) -> dict:  # type: ignore[type-arg]
    return json.loads(d.model_dump_json())  # type: ignore[attr-defined]


def _decision_to_dict(dr: object) -> dict:  # type: ignore[type-arg]
    return json.loads(dr.model_dump_json())  # type: ignore[attr-defined]


def _pr_to_dict(p: object) -> dict:  # type: ignore[type-arg]
    return json.loads(p.model_dump_json())  # type: ignore[attr-defined]


def _compute_video_qa_summary(metrics_list: list) -> dict:  # type: ignore[type-arg]
    """Aggregate video_qa_* fields from MetricIngest.extra across all ingest batches.

    Returns a dict with:
      pass_count   — number of batches where video_qa_pass == 1.0
      fail_count   — number of batches where video_qa_pass == 0.0
      latest_pass  — True/False/None for the most recent verdict
      latest_at    — ISO timestamp of the most recent QA batch (None if none)
      latest_reason — video_qa_visual_reason from the most recent batch (None if absent)
    """
    qa_batches = [
        m for m in metrics_list
        if m.extra is not None and "video_qa_pass" in m.extra
    ]
    if not qa_batches:
        return {
            "pass_count": 0,
            "fail_count": 0,
            "latest_pass": None,
            "latest_at": None,
            "latest_reason": None,
        }

    pass_count = sum(1 for m in qa_batches if m.extra.get("video_qa_pass") == 1.0)
    fail_count = len(qa_batches) - pass_count

    # Most recent batch = last element (store appends in arrival order)
    latest = qa_batches[-1]
    latest_pass = latest.extra.get("video_qa_pass") == 1.0
    latest_reason = latest.extra.get("video_qa_visual_reason")

    # Derive timestamp from samples if available
    latest_at: str | None = None
    if latest.samples:
        latest_at = max(s.observedAt for s in latest.samples).isoformat()

    return {
        "pass_count": pass_count,
        "fail_count": fail_count,
        "latest_pass": latest_pass,
        "latest_at": latest_at,
        "latest_reason": latest_reason,
    }


def _agent_summary_to_dict(agent: object, store: MemoryStore) -> dict:  # type: ignore[type-arg]
    """Return a dashboard-friendly summary for a single managed agent."""
    agent_id: str = agent.agentId  # type: ignore[attr-defined]
    versions = store.list_versions(agent_id) or []
    metrics_list = store.list_metrics(agent_id)

    latest_version_dict: dict | None = None
    last_activity: datetime = agent.createdAt  # type: ignore[attr-defined]

    if versions:
        latest = versions[-1]
        latest_version_dict = {
            "versionId": latest.versionId,
            "gitCommit": latest.gitCommit,
            "createdAt": latest.createdAt.isoformat(),
        }
        last_activity = max(last_activity, latest.createdAt)

    metric_sample_count = sum(len(m.samples) for m in metrics_list)
    video_qa_summary = _compute_video_qa_summary(metrics_list)

    return {
        "agentId": agent_id,
        "name": agent.name,  # type: ignore[attr-defined]
        "runtime": agent.runtime,  # type: ignore[attr-defined]
        "createdAt": agent.createdAt.isoformat(),  # type: ignore[attr-defined]
        "versionCount": len(versions),
        "latestVersion": latest_version_dict,
        "lastActivityAt": last_activity.isoformat(),
        "metricSampleCount": metric_sample_count,
        "videoQaSummary": video_qa_summary,
    }
