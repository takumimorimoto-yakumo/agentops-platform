"""
Read-only web dashboard — three views served as a single HTML page.

Views:
  1. Evaluation score timeline — per-axis scores over time for a selected agent.
  2. Canary status             — current state of active canary deployments.
  3. Rollback history          — all rolled-back deployments and decision records.

Design constraints (from spec):
  - Read-only: no operation buttons.  Humans observe; the meta-agent acts.
  - Minimal: served as a single self-contained HTML page with inline JS/CSS.
  - Data: fetches from the platform's own REST API endpoints.

The router exposes:
  GET /dashboard        — the HTML shell
  GET /dashboard/data   — JSON snapshot used by the page's JS polling loop
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from .meta_agent import list_decisions, list_pr_drafts
from .repository import MemoryStore
from .routers.deps import StoreDep

router = APIRouter(tags=["dashboard"])

# ── HTML page ─────────────────────────────────────────────────────────────────

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AgentOps Platform — Dashboard</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f1117;
      color: #e2e8f0;
      padding: 1.5rem;
    }
    h1 { font-size: 1.4rem; font-weight: 700; margin-bottom: 1.5rem; color: #f8fafc; }
    h2 { font-size: 1rem; font-weight: 600; color: #94a3b8; text-transform: uppercase;
         letter-spacing: 0.05em; margin-bottom: 0.75rem; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem;
            max-width: 1200px; margin: 0 auto; }
    .panel {
      background: #1e2230;
      border: 1px solid #2d3348;
      border-radius: 10px;
      padding: 1.25rem;
    }
    .panel.wide { grid-column: 1 / -1; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th { color: #64748b; font-weight: 500; text-align: left;
         padding: 0.5rem 0.75rem; border-bottom: 1px solid #2d3348; }
    td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #1a1f2e; }
    tr:last-child td { border-bottom: none; }
    .badge {
      display: inline-block; padding: 0.15rem 0.55rem; border-radius: 9999px;
      font-size: 0.75rem; font-weight: 600;
    }
    .badge-canary    { background: #1e40af; color: #93c5fd; }
    .badge-promoted  { background: #14532d; color: #86efac; }
    .badge-rolled_back { background: #7f1d1d; color: #fca5a5; }
    .badge-pending   { background: #292524; color: #d6d3d1; }
    .badge-advance   { background: #14532d; color: #86efac; }
    .badge-hold      { background: #713f12; color: #fde68a; }
    .badge-rollback  { background: #7f1d1d; color: #fca5a5; }
    .badge-failed    { background: #4c1d95; color: #c4b5fd; }
    .score-bar {
      display: inline-block; height: 6px; border-radius: 3px;
      background: #3b82f6; vertical-align: middle; margin-right: 0.4rem;
    }
    .meta { font-size: 0.75rem; color: #475569; margin-top: 0.5rem; }
    .no-data { color: #475569; font-style: italic; font-size: 0.85rem; padding: 0.75rem 0; }
    #status { font-size: 0.75rem; color: #475569; text-align: right;
              max-width: 1200px; margin: 0.75rem auto 0; }
    .axis-drift     { color: #60a5fa; }
    .axis-trajectory { color: #34d399; }
    .axis-cost      { color: #fbbf24; }
    .axis-latency   { color: #a78bfa; }
  </style>
</head>
<body>
  <h1>AgentOps Platform &mdash; Read-only Dashboard</h1>
  <div class="grid">

    <!-- Panel 1: Evaluation score timeline -->
    <div class="panel">
      <h2>Evaluation Score Timeline</h2>
      <div id="scores-table">Loading&hellip;</div>
    </div>

    <!-- Panel 2: Canary status -->
    <div class="panel">
      <h2>Canary Status</h2>
      <div id="canary-table">Loading&hellip;</div>
    </div>

    <!-- Panel 3: Rollback history -->
    <div class="panel wide">
      <h2>Rollback History &amp; Meta-agent Decisions</h2>
      <div id="rollback-table">Loading&hellip;</div>
    </div>

  </div>
  <div id="status">Connecting&hellip;</div>

  <script>
  (function() {
    function badge(cls, text) {
      return '<span class="badge badge-' + cls + '">' + text + '</span>';
    }
    function scoreBar(score, max) {
      var pct = Math.min(100, Math.round(score / max * 100));
      return '<span class="score-bar" style="width:' + pct + 'px"></span>' +
             score.toFixed(3);
    }
    function fmtTime(iso) {
      if (!iso) return '—';
      var d = new Date(iso);
      return d.toLocaleString('en-GB', {hour12: false});
    }

    function renderScores(evals) {
      if (!evals || evals.length === 0) {
        document.getElementById('scores-table').innerHTML =
          '<p class="no-data">No evaluations yet.</p>';
        return;
      }
      var rows = evals.slice(-20).reverse().map(function(e) {
        var scores = {};
        (e.scores || []).forEach(function(s) { scores[s.axis] = s.score; });
        var drift    = scores.drift     !== undefined ? scoreBar(scores.drift,    1)  : '—';
        var traj     = scores.trajectory !== undefined ? scoreBar(scores.trajectory, 1) : '—';
        var cost     = scores.cost      !== undefined ? scores.cost.toFixed(4)     : '—';
        var latency  = scores.latency   !== undefined ? scores.latency.toFixed(0) + ' ms' : '—';
        return '<tr>' +
          '<td>' + fmtTime(e.finishedAt || e.startedAt) + '</td>' +
          '<td class="axis-drift">' + drift + '</td>' +
          '<td class="axis-trajectory">' + traj + '</td>' +
          '<td class="axis-cost">' + cost + '</td>' +
          '<td class="axis-latency">' + latency + '</td>' +
          '<td>' + (e.state === 'succeeded' ? badge('promoted', 'ok') : badge('failed', e.state)) + '</td>' +
          '</tr>';
      }).join('');
      document.getElementById('scores-table').innerHTML =
        '<table><thead><tr>' +
        '<th>Time</th><th>Drift</th><th>Trajectory</th><th>Cost</th><th>Latency</th><th>State</th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table>';
    }

    function renderCanary(deployments) {
      var active = deployments.filter(function(d) {
        return d.state === 'canary' || d.state === 'pending';
      });
      if (active.length === 0) {
        document.getElementById('canary-table').innerHTML =
          '<p class="no-data">No active canary deployments.</p>';
        return;
      }
      var rows = active.map(function(d) {
        return '<tr>' +
          '<td><code>' + d.deploymentId.substring(0,8) + '&hellip;</code></td>' +
          '<td>' + badge(d.state, d.state) + '</td>' +
          '<td>' + (d.currentTrafficPercent || 0) + '%</td>' +
          '<td>' + (d.versionId ? d.versionId.substring(0,8) + '&hellip;' : '—') + '</td>' +
          '</tr>';
      }).join('');
      document.getElementById('canary-table').innerHTML =
        '<table><thead><tr>' +
        '<th>Deployment</th><th>State</th><th>Traffic</th><th>Version</th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table>';
    }

    function renderRollbacks(decisions, prDrafts) {
      var rollbacks = decisions.filter(function(d) { return d.action === 'rollback'; });
      if (rollbacks.length === 0) {
        document.getElementById('rollback-table').innerHTML =
          '<p class="no-data">No rollbacks recorded yet.</p>';
        return;
      }
      var prMap = {};
      (prDrafts || []).forEach(function(p) { prMap[p.prDraftId] = p; });

      var rows = rollbacks.slice().reverse().map(function(d) {
        var pr = d.prDraftId ? prMap[d.prDraftId] : null;
        var prCell = pr
          ? (pr.prUrl
              ? '<a href="' + pr.prUrl + '" style="color:#60a5fa">#PR</a>'
              : '<span title="' + pr.title.replace(/"/g,'&quot;') + '">[draft]</span>')
          : '—';
        return '<tr>' +
          '<td>' + fmtTime(d.decidedAt) + '</td>' +
          '<td><code>' + d.deploymentId.substring(0,8) + '&hellip;</code></td>' +
          '<td>' + badge('rollback', 'rollback') + '</td>' +
          '<td style="max-width:320px;word-break:break-word">' + (d.rationale || '').substring(0,120) + '&hellip;</td>' +
          '<td>' + prCell + '</td>' +
          '</tr>';
      }).join('');
      document.getElementById('rollback-table').innerHTML =
        '<table><thead><tr>' +
        '<th>Time</th><th>Deployment</th><th>Action</th><th>Rationale</th><th>PR</th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table>';
    }

    function refresh() {
      fetch('/dashboard/data')
        .then(function(r) { return r.json(); })
        .then(function(data) {
          renderScores(data.evaluations || []);
          renderCanary(data.deployments || []);
          renderRollbacks(data.decisions || [], data.pr_drafts || []);
          document.getElementById('status').textContent =
            'Last updated: ' + new Date().toLocaleTimeString('en-GB', {hour12: false});
        })
        .catch(function(err) {
          document.getElementById('status').textContent = 'Error: ' + err;
        });
    }

    refresh();
    setInterval(refresh, 10000);  // poll every 10 s
  })();
  </script>
</body>
</html>
"""


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/dashboard", include_in_schema=False)
def dashboard_html() -> HTMLResponse:
    """Serve the read-only monitoring dashboard."""
    return HTMLResponse(content=_DASHBOARD_HTML)


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

    payload = {
        "evaluations": [_eval_to_dict(e) for e in all_evals[:50]],
        "deployments": [_dep_to_dict(d) for d in all_deployments],
        "decisions": [_decision_to_dict(dr) for dr in decisions],
        "pr_drafts": [_pr_to_dict(p) for p in pr_drafts],
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
