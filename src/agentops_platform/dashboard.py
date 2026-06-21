"""
Read-only web dashboard — three views served as a single HTML page.

Views:
  1. Evaluation score timeline — SVG line/area chart with threshold lines.
  2. Canary status             — cards with traffic progress bars.
  3. Rollback history          — timeline with color-coded badges and rationale.

Design constraints (from spec):
  - Read-only: no operation buttons.  Humans observe; the meta-agent acts.
  - Minimal: served as a single self-contained HTML page with inline JS/CSS.
  - Data: fetches from the platform's own REST API endpoints.
  - Charts: hand-written SVG/Canvas — no external CDN dependencies.

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
    /* ── Reset & base ── */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg-base:      #080b10;
      --bg-surface:   #0d1117;
      --bg-panel:     #111827;
      --bg-panel-alt: #131c2b;
      --border:       #1e2d3d;
      --border-subtle:#161f2e;

      --ink-primary:  #e2eaf4;
      --ink-secondary:#8a9bb0;
      --ink-tertiary: #4a5568;

      --accent:       #3b82f6;
      --accent-dim:   rgba(59,130,246,0.12);
      --green:        #10b981;
      --green-dim:    rgba(16,185,129,0.12);
      --amber:        #f59e0b;
      --amber-dim:    rgba(245,158,11,0.12);
      --red:          #ef4444;
      --red-dim:      rgba(239,68,68,0.12);
      --purple:       #8b5cf6;
      --purple-dim:   rgba(139,92,246,0.12);

      --font-mono: "SFMono-Regular", "Cascadia Code", "Fira Code", "Consolas", monospace;
      --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;

      --radius-sm: 4px;
      --radius-md: 8px;
      --radius-lg: 12px;
      --radius-xl: 16px;
    }

    body {
      font-family: var(--font-sans);
      background: var(--bg-base);
      color: var(--ink-primary);
      min-height: 100vh;
      padding: 0;
    }

    /* ── Header ── */
    .header {
      display: flex;
      align-items: center;
      gap: 1rem;
      padding: 1rem 1.75rem;
      border-bottom: 1px solid var(--border);
      background: var(--bg-surface);
    }
    .header-logo {
      font-family: var(--font-mono);
      font-size: 0.7rem;
      color: var(--accent);
      background: var(--accent-dim);
      border: 1px solid rgba(59,130,246,0.3);
      border-radius: var(--radius-sm);
      padding: 0.2rem 0.5rem;
      letter-spacing: 0.02em;
    }
    .header-title {
      font-size: 0.95rem;
      font-weight: 600;
      color: var(--ink-primary);
      letter-spacing: -0.01em;
    }
    .header-subtitle {
      font-family: var(--font-mono);
      font-size: 0.65rem;
      color: var(--ink-tertiary);
      margin-left: auto;
      letter-spacing: 0.03em;
    }
    .header-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 0 2px var(--green-dim);
      animation: pulse 2.5s ease-in-out infinite;
      flex-shrink: 0;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.45; }
    }

    /* ── Layout ── */
    .dashboard-body { padding: 1.5rem 1.75rem; }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.25rem;
      max-width: 1360px;
      margin: 0 auto;
    }
    .panel {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      overflow: hidden;
    }
    .panel.wide { grid-column: 1 / -1; }

    .panel-header {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      padding: 0.85rem 1.1rem;
      border-bottom: 1px solid var(--border-subtle);
      background: var(--bg-panel-alt);
    }
    .panel-icon {
      font-size: 0.8rem;
      opacity: 0.7;
    }
    .panel-title {
      font-family: var(--font-mono);
      font-size: 0.65rem;
      font-weight: 600;
      color: var(--ink-secondary);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .panel-count {
      margin-left: auto;
      font-family: var(--font-mono);
      font-size: 0.6rem;
      color: var(--ink-tertiary);
      background: var(--border-subtle);
      padding: 0.1rem 0.4rem;
      border-radius: var(--radius-sm);
    }
    .panel-body { padding: 1.1rem; }

    /* ── Status bar ── */
    .status-bar {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      max-width: 1360px;
      margin: 1rem auto 0;
      font-family: var(--font-mono);
      font-size: 0.62rem;
      color: var(--ink-tertiary);
    }
    .status-bar-sep { opacity: 0.3; }

    /* ── Empty state ── */
    .no-data {
      font-family: var(--font-mono);
      font-size: 0.72rem;
      color: var(--ink-tertiary);
      padding: 2rem 0;
      text-align: center;
      letter-spacing: 0.04em;
    }

    /* ── Badges ── */
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      padding: 0.15rem 0.55rem;
      border-radius: 9999px;
      font-family: var(--font-mono);
      font-size: 0.62rem;
      font-weight: 600;
      letter-spacing: 0.03em;
      white-space: nowrap;
    }
    .badge::before { content: ''; display: inline-block; width: 5px; height: 5px; border-radius: 50%; }
    .badge-canary    { background: rgba(59,130,246,0.15); color: #93c5fd; border: 1px solid rgba(59,130,246,0.3); }
    .badge-canary::before { background: #93c5fd; }
    .badge-promoted  { background: var(--green-dim); color: #6ee7b7; border: 1px solid rgba(16,185,129,0.3); }
    .badge-promoted::before { background: #6ee7b7; }
    .badge-rolled_back { background: var(--red-dim); color: #fca5a5; border: 1px solid rgba(239,68,68,0.3); }
    .badge-rolled_back::before { background: #fca5a5; }
    .badge-pending   { background: rgba(100,116,139,0.15); color: #94a3b8; border: 1px solid rgba(100,116,139,0.25); }
    .badge-pending::before { background: #94a3b8; }
    .badge-advance   { background: var(--green-dim); color: #6ee7b7; border: 1px solid rgba(16,185,129,0.3); }
    .badge-advance::before { background: #6ee7b7; }
    .badge-hold      { background: var(--amber-dim); color: #fde68a; border: 1px solid rgba(245,158,11,0.3); }
    .badge-hold::before { background: #fde68a; }
    .badge-rollback  { background: var(--red-dim); color: #fca5a5; border: 1px solid rgba(239,68,68,0.3); }
    .badge-rollback::before { background: #fca5a5; }
    .badge-failed    { background: var(--purple-dim); color: #c4b5fd; border: 1px solid rgba(139,92,246,0.3); }
    .badge-failed::before { background: #c4b5fd; }
    .badge-ok        { background: var(--green-dim); color: #6ee7b7; border: 1px solid rgba(16,185,129,0.3); }
    .badge-ok::before { background: #6ee7b7; }

    /* ── SVG chart ── */
    .chart-wrap { position: relative; width: 100%; }
    .chart-svg { display: block; width: 100%; }

    .chart-legend {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-top: 0.75rem;
    }
    .legend-item {
      display: flex;
      align-items: center;
      gap: 0.35rem;
      font-family: var(--font-mono);
      font-size: 0.6rem;
      color: var(--ink-secondary);
    }
    .legend-swatch {
      width: 20px; height: 2px;
      border-radius: 1px;
      flex-shrink: 0;
    }
    .legend-swatch.dashed {
      background: repeating-linear-gradient(
        90deg, currentColor 0, currentColor 4px, transparent 4px, transparent 8px
      );
    }

    /* ── Canary cards ── */
    .canary-list { display: flex; flex-direction: column; gap: 0.75rem; }
    .canary-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 0.85rem 1rem;
    }
    .canary-card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 0.65rem;
    }
    .canary-id {
      font-family: var(--font-mono);
      font-size: 0.68rem;
      color: var(--ink-secondary);
    }
    .canary-version {
      font-family: var(--font-mono);
      font-size: 0.6rem;
      color: var(--ink-tertiary);
    }
    .traffic-bar-wrap {
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }
    .traffic-bar-track {
      flex: 1;
      height: 6px;
      background: var(--border);
      border-radius: 3px;
      overflow: hidden;
    }
    .traffic-bar-fill {
      height: 100%;
      border-radius: 3px;
      transition: width 0.6s ease;
      background: linear-gradient(90deg, var(--accent), #60a5fa);
    }
    .traffic-label {
      font-family: var(--font-mono);
      font-size: 0.62rem;
      color: var(--ink-secondary);
      min-width: 2.5rem;
      text-align: right;
    }

    /* ── Rollback timeline ── */
    .timeline { display: flex; flex-direction: column; gap: 0; }
    .timeline-item {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 0 1rem;
    }
    .timeline-spine {
      display: flex;
      flex-direction: column;
      align-items: center;
      padding-top: 0.15rem;
    }
    .timeline-dot {
      width: 10px; height: 10px;
      border-radius: 50%;
      background: var(--red);
      border: 2px solid var(--bg-panel);
      box-shadow: 0 0 0 2px var(--red-dim);
      flex-shrink: 0;
    }
    .timeline-line {
      width: 1px;
      flex: 1;
      min-height: 1.5rem;
      background: var(--border-subtle);
      margin: 0.25rem 0;
    }
    .timeline-item:last-child .timeline-line { display: none; }
    .timeline-content {
      padding-bottom: 1.25rem;
    }
    .timeline-meta {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      margin-bottom: 0.4rem;
    }
    .timeline-time {
      font-family: var(--font-mono);
      font-size: 0.62rem;
      color: var(--ink-tertiary);
    }
    .timeline-dep {
      font-family: var(--font-mono);
      font-size: 0.62rem;
      color: var(--ink-secondary);
    }
    .timeline-rationale {
      font-size: 0.78rem;
      color: var(--ink-secondary);
      line-height: 1.55;
      margin-top: 0.3rem;
    }
    .timeline-pr {
      margin-top: 0.4rem;
    }
    .timeline-pr a {
      font-family: var(--font-mono);
      font-size: 0.62rem;
      color: var(--accent);
      text-decoration: none;
      border-bottom: 1px solid rgba(59,130,246,0.3);
    }
    .timeline-pr a:hover { border-color: var(--accent); }

    /* ── Axis colors ── */
    .color-drift      { color: #60a5fa; }
    .color-trajectory { color: #34d399; }
    .color-cost       { color: #f59e0b; }
    .color-latency    { color: #a78bfa; }
  </style>
</head>
<body>
  <!-- Header -->
  <header class="header">
    <span class="header-logo">AGENTOPS</span>
    <span class="header-title">AgentOps Platform &mdash; Read-only Dashboard</span>
    <div class="header-dot" id="live-dot" title="Live"></div>
    <span class="header-subtitle" id="status">Connecting&hellip;</span>
  </header>

  <div class="dashboard-body">
    <div class="grid">

      <!-- Panel 1: Evaluation score timeline -->
      <div class="panel">
        <div class="panel-header">
          <span class="panel-icon">&#9676;</span>
          <span class="panel-title">Evaluation Score Timeline</span>
          <span class="panel-count" id="eval-count">&#8212;</span>
        </div>
        <div class="panel-body">
          <div id="chart-area">
            <p class="no-data">Waiting for data&hellip;</p>
          </div>
        </div>
      </div>

      <!-- Panel 2: Canary status -->
      <div class="panel">
        <div class="panel-header">
          <span class="panel-icon">&#9685;</span>
          <span class="panel-title">Canary Status</span>
          <span class="panel-count" id="canary-count">&#8212;</span>
        </div>
        <div class="panel-body">
          <div id="canary-area">
            <p class="no-data">Waiting for data&hellip;</p>
          </div>
        </div>
      </div>

      <!-- Panel 3: Rollback history & decisions -->
      <div class="panel wide">
        <div class="panel-header">
          <span class="panel-icon">&#9651;</span>
          <span class="panel-title">Rollback History &amp; Meta-agent Decisions</span>
          <span class="panel-count" id="rollback-count">&#8212;</span>
        </div>
        <div class="panel-body">
          <div id="rollback-area">
            <p class="no-data">Waiting for data&hellip;</p>
          </div>
        </div>
      </div>

    </div><!-- .grid -->
  </div><!-- .dashboard-body -->

  <script>
  (function() {
    'use strict';

    // ── Constants ──────────────────────────────────────────────────────────────
    var DRIFT_THRESHOLD      = 0.75;   // below = warning
    var TRAJECTORY_THRESHOLD = 0.70;   // below = warning
    var CHART_HEIGHT         = 160;    // px (SVG coordinate space)
    var CHART_PAD_L          = 38;
    var CHART_PAD_R          = 12;
    var CHART_PAD_T          = 12;
    var CHART_PAD_B          = 28;

    // ── Helpers ────────────────────────────────────────────────────────────────
    function esc(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function badge(cls, text) {
      return '<span class="badge badge-' + cls + '">' + esc(text) + '</span>';
    }

    function fmtTime(iso) {
      if (!iso) return '&mdash;';
      var d = new Date(iso);
      return d.toLocaleString('en-GB', {hour12: false,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit'});
    }

    function fmtTimeShort(iso) {
      if (!iso) return '';
      var d = new Date(iso);
      return d.toLocaleTimeString('en-GB', {hour12: false,
        hour: '2-digit', minute: '2-digit'});
    }

    // ── SVG chart ─────────────────────────────────────────────────────────────
    /**
     * Build a hand-written SVG line/area chart for drift and trajectory scores.
     * Points where the score falls below a threshold are highlighted in red.
     *
     * @param {Array} evals  — sorted oldest-first array of evaluation objects
     * @returns {string}      — innerHTML string
     */
    function buildChart(evals) {
      if (!evals || evals.length === 0) {
        return '<p class="no-data">No evaluations yet.</p>';
      }

      // Extract series (oldest first for left-to-right display)
      var sorted = evals.slice().reverse();
      var n = Math.min(sorted.length, 30);
      var items = sorted.slice(sorted.length - n);

      var driftSeries = items.map(function(e) {
        var s = {}; (e.scores || []).forEach(function(x) { s[x.axis] = x.score; });
        return s.drift !== undefined ? s.drift : null;
      });
      var trajSeries = items.map(function(e) {
        var s = {}; (e.scores || []).forEach(function(x) { s[x.axis] = x.score; });
        return s.trajectory !== undefined ? s.trajectory : null;
      });

      // SVG viewport
      var W = 460;  // internal SVG units; CSS scales to 100%
      var H = CHART_HEIGHT;
      var pl = CHART_PAD_L, pr = CHART_PAD_R, pt = CHART_PAD_T, pb = CHART_PAD_B;
      var cw = W - pl - pr;  // chart area width
      var ch = H - pt - pb;  // chart area height

      function xOf(i) {
        return pl + (n <= 1 ? cw / 2 : i / (n - 1) * cw);
      }
      function yOf(v) {
        return pt + (1 - v) * ch;  // v in [0,1], 1 = top
      }

      var svg = '<svg class="chart-svg" viewBox="0 0 ' + W + ' ' + H + '" ' +
                'preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">';

      // Grid lines (0.25, 0.50, 0.75, 1.00)
      [0, 0.25, 0.5, 0.75, 1.0].forEach(function(v) {
        var y = yOf(v);
        svg += '<line x1="' + pl + '" y1="' + y + '" x2="' + (W - pr) + '" y2="' + y + '" ' +
               'stroke="#1e2d3d" stroke-width="1" />';
        svg += '<text x="' + (pl - 4) + '" y="' + (y + 3.5) + '" ' +
               'font-size="7" fill="#4a5568" text-anchor="end" font-family="monospace">' +
               v.toFixed(2) + '</text>';
      });

      // Threshold lines
      var driftY = yOf(DRIFT_THRESHOLD);
      svg += '<line x1="' + pl + '" y1="' + driftY + '" x2="' + (W - pr) + '" y2="' + driftY + '" ' +
             'stroke="#3b82f6" stroke-width="1" stroke-dasharray="4 3" opacity="0.5" />';
      var trajY = yOf(TRAJECTORY_THRESHOLD);
      svg += '<line x1="' + pl + '" y1="' + trajY + '" x2="' + (W - pr) + '" y2="' + trajY + '" ' +
             'stroke="#10b981" stroke-width="1" stroke-dasharray="4 3" opacity="0.5" />';

      // Helper: build polyline path for a series
      function buildPath(series) {
        var pts = [];
        series.forEach(function(v, i) {
          if (v !== null) pts.push(xOf(i) + ',' + yOf(v));
        });
        return pts.join(' ');
      }

      // Helper: build area fill path (above x-axis)
      function buildArea(series) {
        var pts = [];
        var validIndices = [];
        series.forEach(function(v, i) { if (v !== null) validIndices.push(i); });
        if (validIndices.length === 0) return '';
        var first = validIndices[0], last = validIndices[validIndices.length - 1];
        pts.push(xOf(first) + ',' + (pt + ch));
        series.forEach(function(v, i) {
          if (v !== null) pts.push(xOf(i) + ',' + yOf(v));
        });
        pts.push(xOf(last) + ',' + (pt + ch));
        return pts.join(' ');
      }

      // Drift area + line
      var driftPath = buildPath(driftSeries);
      var driftArea = buildArea(driftSeries);
      if (driftArea) {
        svg += '<polygon points="' + driftArea + '" fill="rgba(59,130,246,0.07)" />';
      }
      if (driftPath) {
        svg += '<polyline points="' + driftPath + '" fill="none" stroke="#3b82f6" stroke-width="1.5" stroke-linejoin="round" />';
      }

      // Trajectory area + line
      var trajPath = buildPath(trajSeries);
      var trajArea = buildArea(trajSeries);
      if (trajArea) {
        svg += '<polygon points="' + trajArea + '" fill="rgba(16,185,129,0.06)" />';
      }
      if (trajPath) {
        svg += '<polyline points="' + trajPath + '" fill="none" stroke="#10b981" stroke-width="1.5" stroke-linejoin="round" />';
      }

      // Data points — red when below threshold
      driftSeries.forEach(function(v, i) {
        if (v === null) return;
        var below = v < DRIFT_THRESHOLD;
        svg += '<circle cx="' + xOf(i) + '" cy="' + yOf(v) + '" r="2.5" ' +
               'fill="' + (below ? '#ef4444' : '#3b82f6') + '" ' +
               'stroke="' + (below ? 'rgba(239,68,68,0.3)' : 'rgba(59,130,246,0.3)') + '" ' +
               'stroke-width="' + (below ? 3 : 2) + '" />';
      });
      trajSeries.forEach(function(v, i) {
        if (v === null) return;
        var below = v < TRAJECTORY_THRESHOLD;
        svg += '<circle cx="' + xOf(i) + '" cy="' + yOf(v) + '" r="2.5" ' +
               'fill="' + (below ? '#ef4444' : '#10b981') + '" ' +
               'stroke="' + (below ? 'rgba(239,68,68,0.3)' : 'rgba(16,185,129,0.3)') + '" ' +
               'stroke-width="' + (below ? 3 : 2) + '" />';
      });

      // Time axis labels (up to 5 evenly spaced)
      var labelStep = Math.max(1, Math.floor(n / 5));
      for (var i = 0; i < n; i += labelStep) {
        var ts = items[i].finishedAt || items[i].startedAt;
        if (ts) {
          svg += '<text x="' + xOf(i) + '" y="' + (H - 2) + '" ' +
                 'font-size="6.5" fill="#4a5568" text-anchor="middle" font-family="monospace">' +
                 esc(fmtTimeShort(ts)) + '</text>';
        }
      }

      svg += '</svg>';

      // Legend
      svg += '<div class="chart-legend">';
      svg += '<div class="legend-item"><span class="legend-swatch" style="background:#3b82f6"></span>Drift</div>';
      svg += '<div class="legend-item"><span class="legend-swatch" style="background:#10b981"></span>Trajectory</div>';
      svg += '<div class="legend-item"><span class="legend-swatch dashed" style="color:#3b82f6"></span>Drift threshold (' + DRIFT_THRESHOLD + ')</div>';
      svg += '<div class="legend-item"><span class="legend-swatch dashed" style="color:#10b981"></span>Trajectory threshold (' + TRAJECTORY_THRESHOLD + ')</div>';
      svg += '<div class="legend-item" style="color:#ef4444">&#9679; Below threshold</div>';
      svg += '</div>';

      // Recent metrics table (last 8 rows)
      var recent = items.slice(-8).reverse();
      svg += '<div style="margin-top:0.9rem;overflow-x:auto">';
      svg += '<table style="width:100%;border-collapse:collapse;font-size:0.73rem;">';
      svg += '<thead><tr>';
      ['Time','Drift','Traj','Cost','Latency','State'].forEach(function(h) {
        svg += '<th style="color:#4a5568;font-weight:500;text-align:left;padding:0.35rem 0.5rem;' +
               'border-bottom:1px solid #1e2d3d;font-family:monospace;font-size:0.6rem;letter-spacing:0.04em">' + h + '</th>';
      });
      svg += '</tr></thead><tbody>';
      recent.forEach(function(e) {
        var sc = {}; (e.scores || []).forEach(function(x) { sc[x.axis] = x.score; });
        var drift    = sc.drift     !== undefined ? sc.drift.toFixed(3)          : '&mdash;';
        var traj     = sc.trajectory !== undefined ? sc.trajectory.toFixed(3)    : '&mdash;';
        var cost     = sc.cost      !== undefined ? sc.cost.toFixed(4)           : '&mdash;';
        var latency  = sc.latency   !== undefined ? sc.latency.toFixed(0)+' ms'  : '&mdash;';
        var driftLow  = sc.drift     !== undefined && sc.drift     < DRIFT_THRESHOLD;
        var trajLow   = sc.trajectory !== undefined && sc.trajectory < TRAJECTORY_THRESHOLD;
        var stateBadge = e.state === 'succeeded' ? badge('ok', 'ok') : badge('failed', e.state || 'unknown');
        svg += '<tr style="border-bottom:1px solid #161f2e">';
        svg += '<td style="padding:0.3rem 0.5rem;font-family:monospace;font-size:0.62rem;color:#4a5568">' + fmtTime(e.finishedAt || e.startedAt) + '</td>';
        svg += '<td style="padding:0.3rem 0.5rem;font-family:monospace;font-size:0.65rem;color:' + (driftLow ? '#ef4444' : '#60a5fa') + '">' + drift + '</td>';
        svg += '<td style="padding:0.3rem 0.5rem;font-family:monospace;font-size:0.65rem;color:' + (trajLow  ? '#ef4444' : '#34d399') + '">' + traj + '</td>';
        svg += '<td style="padding:0.3rem 0.5rem;font-family:monospace;font-size:0.65rem;color:#f59e0b">' + cost + '</td>';
        svg += '<td style="padding:0.3rem 0.5rem;font-family:monospace;font-size:0.65rem;color:#a78bfa">' + latency + '</td>';
        svg += '<td style="padding:0.3rem 0.5rem">' + stateBadge + '</td>';
        svg += '</tr>';
      });
      svg += '</tbody></table></div>';

      return '<div class="chart-wrap">' + svg + '</div>';
    }

    // ── Canary panel ──────────────────────────────────────────────────────────
    function renderCanary(deployments) {
      var active = deployments.filter(function(d) {
        return d.state === 'canary' || d.state === 'pending';
      });

      document.getElementById('canary-count').textContent = active.length + ' active';

      if (active.length === 0) {
        document.getElementById('canary-area').innerHTML =
          '<p class="no-data">[ no active canary deployments ]</p>';
        return;
      }

      var html = '<div class="canary-list">';
      active.forEach(function(d) {
        var pct = d.currentTrafficPercent || 0;
        var stateColor = d.state === 'canary' ? '#3b82f6' : '#94a3b8';
        html += '<div class="canary-card">';
        html += '<div class="canary-card-header">';
        html += '<div>';
        html += '<div class="canary-id">&#x2022; ' + esc(d.deploymentId.substring(0,12)) + '&hellip;</div>';
        if (d.versionId) {
          html += '<div class="canary-version">version&nbsp;' + esc(d.versionId.substring(0,8)) + '&hellip;</div>';
        }
        html += '</div>';
        html += badge(d.state, d.state);
        html += '</div>';
        // Traffic progress bar
        html += '<div class="traffic-bar-wrap">';
        html += '<div class="traffic-bar-track">';
        html += '<div class="traffic-bar-fill" style="width:' + pct + '%;background:linear-gradient(90deg,' + stateColor + ',#93c5fd)"></div>';
        html += '</div>';
        html += '<span class="traffic-label">' + pct + '%</span>';
        html += '</div>';
        html += '</div>';
      });
      html += '</div>';

      // Also show all deployments in a compact summary table
      var all = deployments.filter(function(d) { return d.state !== 'canary' && d.state !== 'pending'; });
      if (all.length > 0) {
        html += '<div style="margin-top:1rem">';
        html += '<div style="font-family:monospace;font-size:0.6rem;color:#4a5568;letter-spacing:0.06em;margin-bottom:0.5rem;text-transform:uppercase">Recent</div>';
        html += '<table style="width:100%;border-collapse:collapse;font-size:0.72rem">';
        html += '<thead><tr>';
        ['ID','State','Traffic','Version'].forEach(function(h) {
          html += '<th style="color:#4a5568;font-weight:500;text-align:left;padding:0.3rem 0.4rem;border-bottom:1px solid #1e2d3d;font-family:monospace;font-size:0.58rem;letter-spacing:0.04em">' + h + '</th>';
        });
        html += '</tr></thead><tbody>';
        all.slice(-5).reverse().forEach(function(d) {
          html += '<tr style="border-bottom:1px solid #161f2e">';
          html += '<td style="padding:0.3rem 0.4rem;font-family:monospace;font-size:0.62rem;color:#4a5568">' + esc(d.deploymentId.substring(0,8)) + '&hellip;</td>';
          html += '<td style="padding:0.3rem 0.4rem">' + badge(d.state, d.state) + '</td>';
          html += '<td style="padding:0.3rem 0.4rem;font-family:monospace;font-size:0.62rem;color:#8a9bb0">' + (d.currentTrafficPercent || 0) + '%</td>';
          html += '<td style="padding:0.3rem 0.4rem;font-family:monospace;font-size:0.62rem;color:#4a5568">' + (d.versionId ? esc(d.versionId.substring(0,8)) + '&hellip;' : '&mdash;') + '</td>';
          html += '</tr>';
        });
        html += '</tbody></table></div>';
      }

      document.getElementById('canary-area').innerHTML = html;
    }

    // ── Rollback panel ─────────────────────────────────────────────────────────
    function renderRollbacks(decisions, prDrafts) {
      // Show all decisions (not only rollbacks), rollbacks highlighted
      var rollbacks = decisions.filter(function(d) { return d.action === 'rollback'; });
      var others    = decisions.filter(function(d) { return d.action !== 'rollback'; });

      document.getElementById('rollback-count').textContent =
        rollbacks.length + ' rollback' + (rollbacks.length !== 1 ? 's' : '');

      if (decisions.length === 0) {
        document.getElementById('rollback-area').innerHTML =
          '<p class="no-data">[ no meta-agent decisions recorded yet ]</p>';
        return;
      }

      var prMap = {};
      (prDrafts || []).forEach(function(p) { prMap[p.prDraftId] = p; });

      // Build unified list sorted newest-first
      var allDecisions = decisions.slice().sort(function(a, b) {
        return new Date(b.decidedAt || 0) - new Date(a.decidedAt || 0);
      });

      var html = '<div class="timeline">';
      allDecisions.forEach(function(d) {
        var isRollback = d.action === 'rollback';
        var pr = d.prDraftId ? prMap[d.prDraftId] : null;
        var dotColor = isRollback ? '#ef4444' : (d.action === 'advance' ? '#10b981' : '#f59e0b');
        var dotGlow  = isRollback ? 'rgba(239,68,68,0.3)' : (d.action === 'advance' ? 'rgba(16,185,129,0.3)' : 'rgba(245,158,11,0.3)');

        html += '<div class="timeline-item">';
        html += '<div class="timeline-spine">';
        html += '<div class="timeline-dot" style="background:' + dotColor + ';box-shadow:0 0 0 2px ' + dotGlow + '"></div>';
        html += '<div class="timeline-line"></div>';
        html += '</div>';
        html += '<div class="timeline-content">';
        html += '<div class="timeline-meta">';
        html += '<span class="timeline-time">' + fmtTime(d.decidedAt) + '</span>';
        html += badge(d.action, d.action);
        html += '<span class="timeline-dep">' + esc(d.deploymentId.substring(0,8)) + '&hellip;</span>';
        html += '</div>';
        if (d.rationale) {
          html += '<div class="timeline-rationale">' + esc(d.rationale.substring(0, 200)) +
                  (d.rationale.length > 200 ? '&hellip;' : '') + '</div>';
        }
        if (pr) {
          html += '<div class="timeline-pr">';
          if (pr.prUrl) {
            html += '<a href="' + esc(pr.prUrl) + '" target="_blank" rel="noopener noreferrer">' +
                    '&#x2197; View improvement PR</a>';
          } else {
            html += '<span style="font-family:monospace;font-size:0.62rem;color:#4a5568">[PR draft: ' + esc(pr.title) + ']</span>';
          }
          html += '</div>';
        }
        html += '</div>';  // .timeline-content
        html += '</div>';  // .timeline-item
      });
      html += '</div>';

      document.getElementById('rollback-area').innerHTML = html;
    }

    // ── Polling ────────────────────────────────────────────────────────────────
    function refresh() {
      fetch('/dashboard/data')
        .then(function(r) { return r.json(); })
        .then(function(data) {
          var evals = data.evaluations || [];
          document.getElementById('eval-count').textContent = evals.length + ' eval' + (evals.length !== 1 ? 's' : '');
          document.getElementById('chart-area').innerHTML = buildChart(evals);

          renderCanary(data.deployments || []);
          renderRollbacks(data.decisions || [], data.pr_drafts || []);

          var now = new Date().toLocaleTimeString('en-GB', {hour12: false});
          document.getElementById('status').textContent = 'Updated ' + now + ' · polling every 10s';
          document.getElementById('live-dot').style.background = '#10b981';
        })
        .catch(function(err) {
          document.getElementById('status').textContent = 'Error: ' + err;
          document.getElementById('live-dot').style.background = '#ef4444';
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
