"""
Read-only web dashboard — a single self-contained operations console.

Sections:
  1. KPI strip            — at-a-glance counters, incl. how many decisions the
                            LLM judge actually made (the autonomy signal).
  2. Evaluation timeline  — hand-written SVG line chart with threshold band.
  3. Deployment status    — every deployment with its state and traffic share.
  4. Meta-agent decisions — rich cards showing WHO judged (judgedBy), the
                            gray-zone signal, the rationale and the improvement PR.

Design constraints (from spec):
  - Read-only: no operation buttons.  Humans observe; the meta-agent acts.
  - Self-contained: one HTML page, inline JS/CSS, no external CDN or fonts.
  - Charts: hand-written SVG — no chart library.
  - Clean-room: nothing lifted from private repos; generic CSS/SVG only.

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

    :root {
      /* Surfaces (deep OLED-friendly dark) */
      --bg-0:  #06080c;
      --bg-1:  #0b0f16;
      --bg-2:  #111722;
      --bg-3:  #161f2d;
      --line:  #1d2838;
      --line-2:#27344a;

      /* Ink */
      --ink-1: #eaf0f9;
      --ink-2: #9aabc2;
      --ink-3: #5c6b82;
      --ink-4: #3b475c;

      /* Accents */
      --blue:   #4f8cff;
      --green:  #2dd4a7;
      --amber:  #f5b042;
      --red:    #ff6b6b;
      --violet: #a78bfa;

      --blue-d:   rgba(79,140,255,0.13);
      --green-d:  rgba(45,212,167,0.13);
      --amber-d:  rgba(245,176,66,0.13);
      --red-d:    rgba(255,107,107,0.13);
      --violet-d: rgba(167,139,250,0.14);

      --mono: ui-monospace, "SF Mono", "SFMono-Regular", "JetBrains Mono", "Cascadia Code", "Fira Code", Menlo, Consolas, monospace;
      --sans: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;

      --r-sm: 6px;  --r-md: 10px;  --r-lg: 14px;  --r-xl: 20px;
      --sp-1: 4px;  --sp-2: 8px;   --sp-3: 12px;  --sp-4: 16px;
      --sp-5: 22px; --sp-6: 30px;  --sp-7: 44px;

      --maxw: 1380px;
    }

    html { -webkit-text-size-adjust: 100%; }
    body {
      font-family: var(--sans);
      background:
        radial-gradient(1200px 600px at 80% -10%, rgba(79,140,255,0.06), transparent 60%),
        radial-gradient(900px 500px at -5% 0%, rgba(167,139,250,0.05), transparent 55%),
        var(--bg-0);
      color: var(--ink-1);
      min-height: 100dvh;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }
    a { color: inherit; }

    /* ── Header ── */
    .hdr {
      position: sticky; top: 0; z-index: 20;
      display: flex; align-items: center; gap: var(--sp-3);
      padding: var(--sp-4) var(--sp-5);
      border-bottom: 1px solid var(--line);
      background: rgba(8,11,16,0.82);
      backdrop-filter: blur(10px);
    }
    .brand { display: flex; align-items: center; gap: var(--sp-3); min-width: 0; }
    .brand-mark {
      width: 30px; height: 30px; flex-shrink: 0;
      display: grid; place-items: center;
      border-radius: 9px;
      background: linear-gradient(150deg, var(--blue-d), var(--violet-d));
      border: 1px solid var(--line-2);
      color: var(--blue);
    }
    .brand-name { font-weight: 700; font-size: 0.98rem; letter-spacing: -0.01em; white-space: nowrap; }
    .brand-sub  { font-family: var(--mono); font-size: 0.64rem; color: var(--ink-3); letter-spacing: 0.06em; text-transform: uppercase; white-space: nowrap; }
    .brand-divider { width: 1px; height: 22px; background: var(--line); margin: 0 2px; }
    .hdr-right { margin-left: auto; display: flex; align-items: center; gap: var(--sp-3); }
    .live {
      display: inline-flex; align-items: center; gap: var(--sp-2);
      font-family: var(--mono); font-size: 0.66rem; color: var(--ink-2);
      padding: 5px 10px; border: 1px solid var(--line); border-radius: 9999px; background: var(--bg-1);
    }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 3px var(--green-d); animation: pulse 2.4s ease-in-out infinite; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .4; } }

    /* ── Layout ── */
    .wrap { max-width: var(--maxw); margin: 0 auto; padding: var(--sp-5); }

    /* ── KPI strip ── */
    .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--sp-3); margin-bottom: var(--sp-4); }
    .kpi {
      position: relative; overflow: hidden;
      background: linear-gradient(180deg, var(--bg-2), var(--bg-1));
      border: 1px solid var(--line); border-radius: var(--r-lg);
      padding: var(--sp-4);
    }
    .kpi::after { content:''; position:absolute; inset:0 auto 0 0; width:3px; background: var(--accent, var(--ink-4)); opacity:.8; }
    .kpi.k-blue   { --accent: var(--blue); }
    .kpi.k-green  { --accent: var(--green); }
    .kpi.k-red    { --accent: var(--red); }
    .kpi.k-violet { --accent: var(--violet); }
    .kpi-top { display: flex; align-items: center; gap: var(--sp-2); color: var(--ink-3); }
    .kpi-ico { width: 16px; height: 16px; color: var(--accent, var(--ink-3)); flex-shrink: 0; }
    .kpi-label { font-family: var(--mono); font-size: 0.62rem; letter-spacing: 0.06em; text-transform: uppercase; }
    .kpi-val { font-size: 2rem; font-weight: 700; line-height: 1.1; margin-top: var(--sp-2); font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
    .kpi-hint { font-family: var(--mono); font-size: 0.6rem; color: var(--ink-4); margin-top: 2px; }

    /* ── Panels ── */
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); }
    .panel { background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r-lg); overflow: hidden; display: flex; flex-direction: column; }
    .panel.wide { grid-column: 1 / -1; }
    .p-head { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-3) var(--sp-4); border-bottom: 1px solid var(--line); background: var(--bg-2); }
    .p-ico { width: 15px; height: 15px; color: var(--ink-3); flex-shrink: 0; }
    .p-title { font-family: var(--mono); font-size: 0.66rem; font-weight: 600; color: var(--ink-2); text-transform: uppercase; letter-spacing: 0.08em; }
    .p-count { margin-left: auto; font-family: var(--mono); font-size: 0.6rem; color: var(--ink-3); background: var(--bg-3); border: 1px solid var(--line); padding: 2px 8px; border-radius: 9999px; }
    .p-body { padding: var(--sp-4); flex: 1; }

    /* ── Empty states ── */
    .empty { display: flex; flex-direction: column; align-items: center; gap: var(--sp-2); padding: var(--sp-7) var(--sp-4); text-align: center; color: var(--ink-3); }
    .empty svg { width: 26px; height: 26px; color: var(--ink-4); }
    .empty-t { font-size: 0.82rem; color: var(--ink-2); }
    .empty-s { font-family: var(--mono); font-size: 0.64rem; color: var(--ink-4); }

    /* ── Badges ── */
    .badge { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 9999px; font-family: var(--mono); font-size: 0.62rem; font-weight: 600; letter-spacing: 0.02em; white-space: nowrap; border: 1px solid transparent; }
    .badge::before { content: ''; width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
    .b-canary, .b-pending { color: #9cc2ff; background: var(--blue-d); border-color: rgba(79,140,255,0.28); }
    .b-promoted, .b-advance, .b-ok { color: #7fe7cd; background: var(--green-d); border-color: rgba(45,212,167,0.28); }
    .b-hold { color: #ffd591; background: var(--amber-d); border-color: rgba(245,176,66,0.28); }
    .b-rollback, .b-rolled_back, .b-failed { color: #ff9d9d; background: var(--red-d); border-color: rgba(255,107,107,0.28); }

    /* ── "Judged by" pill (the autonomy proof) ── */
    .jb { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px 3px 7px; border-radius: 9999px; font-family: var(--mono); font-size: 0.62rem; font-weight: 600; white-space: nowrap; border: 1px solid transparent; }
    .jb svg { width: 12px; height: 12px; }
    .jb-gemini { color: #c9b9ff; background: var(--violet-d); border-color: rgba(167,139,250,0.4); box-shadow: 0 0 14px rgba(167,139,250,0.18); }
    .jb-gemini_failed { color: #ffce8a; background: var(--amber-d); border-color: rgba(245,176,66,0.35); }
    .jb-heuristic, .jb-auto_advance, .jb-missing_baseline { color: var(--ink-2); background: var(--bg-3); border-color: var(--line-2); }
    .jb-safety_floor { color: #ff9d9d; background: var(--red-d); border-color: rgba(255,107,107,0.3); }

    /* ── Chart ── */
    .chart { width: 100%; display: block; }
    .legend { display: flex; flex-wrap: wrap; gap: var(--sp-4); margin-top: var(--sp-3); padding-top: var(--sp-3); border-top: 1px solid var(--line); }
    .lg { display: inline-flex; align-items: center; gap: 7px; font-family: var(--mono); font-size: 0.6rem; color: var(--ink-2); }
    .lg-line { width: 22px; height: 0; border-top: 2px solid currentColor; }
    .lg-line.dash { border-top-style: dashed; }
    .lg-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }

    /* ── Tables ── */
    table { width: 100%; border-collapse: collapse; }
    th { color: var(--ink-3); font-weight: 500; text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); font-family: var(--mono); font-size: 0.58rem; letter-spacing: 0.05em; text-transform: uppercase; }
    td { padding: 8px; border-bottom: 1px solid var(--bg-3); font-family: var(--mono); font-size: 0.68rem; color: var(--ink-2); font-variant-numeric: tabular-nums; }
    tr:last-child td { border-bottom: none; }
    .tbl-wrap { margin-top: var(--sp-4); overflow-x: auto; }
    .tbl-cap { font-family: var(--mono); font-size: 0.58rem; color: var(--ink-3); letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: var(--sp-2); }
    .c-drift { color: #88b4ff; } .c-traj { color: #6fe3c5; } .c-cost { color: #ffce8a; } .c-lat { color: #c4b3ff; } .c-bad { color: var(--red); }

    /* ── Deployment rows ── */
    .dep { background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--sp-3) var(--sp-4); }
    .dep + .dep { margin-top: var(--sp-3); }
    .dep-top { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-3); margin-bottom: var(--sp-3); }
    .dep-id { font-family: var(--mono); font-size: 0.7rem; color: var(--ink-1); }
    .dep-ver { font-family: var(--mono); font-size: 0.6rem; color: var(--ink-3); margin-top: 2px; }
    .track { display: flex; align-items: center; gap: var(--sp-3); }
    .bar { flex: 1; height: 7px; background: var(--bg-3); border-radius: 9999px; overflow: hidden; }
    .fill { height: 100%; border-radius: 9999px; transition: width .6s cubic-bezier(.2,.7,.3,1); }
    .pct { font-family: var(--mono); font-size: 0.64rem; color: var(--ink-2); min-width: 38px; text-align: right; font-variant-numeric: tabular-nums; }

    /* ── Decision cards ── */
    .dec { position: relative; background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--sp-4); padding-left: calc(var(--sp-4) + 4px); }
    .dec + .dec { margin-top: var(--sp-3); }
    .dec::before { content:''; position:absolute; left:0; top:10px; bottom:10px; width:3px; border-radius:3px; background: var(--mark, var(--ink-4)); }
    .dec.m-rollback { --mark: var(--red); } .dec.m-advance { --mark: var(--green); } .dec.m-hold { --mark: var(--amber); }
    .dec-top { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
    .dec-time { font-family: var(--mono); font-size: 0.62rem; color: var(--ink-3); margin-left: auto; }
    .dec-dep { font-family: var(--mono); font-size: 0.62rem; color: var(--ink-3); }
    .chips { display: flex; flex-wrap: wrap; gap: var(--sp-2); margin-top: var(--sp-3); }
    .chip { display: inline-flex; align-items: baseline; gap: 5px; font-family: var(--mono); font-size: 0.6rem; color: var(--ink-2); background: var(--bg-3); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 3px 8px; font-variant-numeric: tabular-nums; }
    .chip b { color: var(--ink-1); font-weight: 600; }
    .chip.warn b { color: var(--amber); }
    .rationale { font-size: 0.83rem; color: var(--ink-2); line-height: 1.6; margin-top: var(--sp-3); }
    .pr { margin-top: var(--sp-3); display: inline-flex; align-items: center; gap: 6px; font-family: var(--mono); font-size: 0.64rem; color: var(--ink-3); background: var(--bg-1); border: 1px dashed var(--line-2); border-radius: var(--r-sm); padding: 5px 9px; }
    .pr svg { width: 12px; height: 12px; color: var(--blue); }
    .pr a { color: var(--blue); text-decoration: none; border-bottom: 1px solid rgba(79,140,255,0.35); }
    .pr a:hover { border-color: var(--blue); }

    /* ── Focus / responsive / motion ── */
    a:focus-visible, [tabindex]:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; border-radius: 4px; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } .kpis { grid-template-columns: 1fr 1fr; } }
    @media (max-width: 560px) { .kpis { grid-template-columns: 1fr; } .brand-sub, .brand-divider { display: none; } }
    @media (prefers-reduced-motion: reduce) { *, ::before, ::after { animation: none !important; transition: none !important; } }
  </style>
</head>
<body>
  <header class="hdr">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3 8 4-16 3 8h4"/></svg>
      </span>
      <div>
        <div class="brand-name">AgentOps Platform</div>
        <div class="brand-sub">Autonomous Canary Control</div>
      </div>
    </div>
    <div class="hdr-right">
      <span class="live"><span class="dot" id="dot"></span><span id="status">connecting…</span></span>
    </div>
  </header>

  <div class="wrap">

    <!-- KPI strip -->
    <section class="kpis" id="kpis" aria-label="Key metrics">
      <div class="kpi k-blue">
        <div class="kpi-top"><svg class="kpi-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 13l3-3 3 3 4-5"/></svg><span class="kpi-label">Evaluations</span></div>
        <div class="kpi-val" id="k-evals">—</div>
        <div class="kpi-hint" id="k-evals-h">&nbsp;</div>
      </div>
      <div class="kpi k-green">
        <div class="kpi-top"><svg class="kpi-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18"/></svg><span class="kpi-label">Active canaries</span></div>
        <div class="kpi-val" id="k-canary">—</div>
        <div class="kpi-hint" id="k-canary-h">&nbsp;</div>
      </div>
      <div class="kpi k-red">
        <div class="kpi-top"><svg class="kpi-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg><span class="kpi-label">Auto rollbacks</span></div>
        <div class="kpi-val" id="k-rb">—</div>
        <div class="kpi-hint" id="k-rb-h">&nbsp;</div>
      </div>
      <div class="kpi k-violet">
        <div class="kpi-top"><svg class="kpi-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="7" width="14" height="12" rx="2"/><path d="M9 7V5a3 3 0 0 1 6 0v2"/><path d="M9 13h0M15 13h0"/></svg><span class="kpi-label">LLM-judged</span></div>
        <div class="kpi-val" id="k-llm">—</div>
        <div class="kpi-hint" id="k-llm-h">&nbsp;</div>
      </div>
    </section>

    <div class="grid">
      <!-- Evaluation timeline -->
      <div class="panel">
        <div class="p-head">
          <svg class="p-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M19 9l-5 5-3-3-4 4"/></svg>
          <span class="p-title">Evaluation Score Timeline</span>
          <span class="p-count" id="c-eval">—</span>
        </div>
        <div class="p-body"><div id="chart-area"></div></div>
      </div>

      <!-- Deployment status -->
      <div class="panel">
        <div class="p-head">
          <svg class="p-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 2 7l10 5 10-5-10-5Z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/></svg>
          <span class="p-title">Deployment Status</span>
          <span class="p-count" id="c-dep">—</span>
        </div>
        <div class="p-body"><div id="dep-area"></div></div>
      </div>

      <!-- Decisions -->
      <div class="panel wide">
        <div class="p-head">
          <svg class="p-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a9 9 0 1 0 9 9"/><path d="M12 7v5l3 2"/><path d="M16 3l5 5"/></svg>
          <span class="p-title">Meta-agent Decisions</span>
          <span class="p-count" id="c-dec">—</span>
        </div>
        <div class="p-body"><div id="dec-area"></div></div>
      </div>
    </div>
  </div>

  <script>
  (function () {
    'use strict';

    var DRIFT_TH = 0.75, TRAJ_TH = 0.70;

    function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
    function badge(cls, text) { return '<span class="badge b-' + cls + '">' + esc(text) + '</span>'; }
    function fmtTime(iso) {
      if (!iso) return '—';
      var d = new Date(iso);
      return d.toLocaleString('en-GB', {hour12:false, day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'});
    }
    function fmtHM(iso) { if (!iso) return ''; return new Date(iso).toLocaleTimeString('en-GB', {hour12:false, hour:'2-digit', minute:'2-digit'}); }
    function scoresOf(e) { var s={}; (e.scores||[]).forEach(function(x){ s[x.axis]=x.score; }); return s; }

    function emptyState(title, sub) {
      return '<div class="empty">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9 10h.01M15 10h.01M9 15c.8-.7 1.9-1 3-1s2.2.3 3 1"/></svg>' +
        '<div class="empty-t">' + esc(title) + '</div><div class="empty-s">' + esc(sub) + '</div></div>';
    }

    // ── Judged-by pill ──
    var BRAIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 0 3 3 0 0 0 2-5 3 3 0 0 0-2-5 3 3 0 0 0-3-3Z"/><path d="M12 5v14"/></svg>';
    var GEAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/></svg>';
    var SHIELD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 4 5v6c0 5 3.5 8 8 11 4.5-3 8-6 8-11V5l-8-3Z"/></svg>';
    function judgedBy(jb) {
      var map = {
        gemini:           ['Judged by Gemini', BRAIN],
        gemini_failed:    ['Gemini failed → fallback', BRAIN],
        heuristic:        ['Rule-based', GEAR],
        safety_floor:     ['Safety floor', SHIELD],
        auto_advance:     ['Auto-advance', GEAR],
        missing_baseline: ['No baseline → hold', SHIELD]
      };
      var m = map[jb] || ['Rule-based', GEAR];
      return '<span class="jb jb-' + esc(jb || 'heuristic') + '" title="Decision provenance: ' + esc(jb || 'heuristic') + '">' + m[1] + esc(m[0]) + '</span>';
    }

    // ── KPIs ──
    function renderKpis(d) {
      var evals = d.evaluations || [], deps = d.deployments || [], decs = d.decisions || [];
      var active = deps.filter(function(x){ return x.state==='canary' || x.state==='pending'; }).length;
      var rb = decs.filter(function(x){ return x.action==='rollback'; }).length;
      var llm = decs.filter(function(x){ return x.judgedBy==='gemini'; }).length;
      function set(id, v) { document.getElementById(id).textContent = v; }
      set('k-evals', evals.length); set('k-canary', active); set('k-rb', rb); set('k-llm', llm);
      document.getElementById('k-evals-h').textContent = evals.length ? 'scored across axes' : 'awaiting runs';
      document.getElementById('k-canary-h').textContent = active ? 'in progress' : 'none in progress';
      document.getElementById('k-rb-h').textContent = rb ? 'no human in the path' : 'none yet';
      document.getElementById('k-llm-h').textContent = (llm ? llm + ' of ' : '') + decs.length + ' decisions by LLM';
    }

    // ── Chart ──
    function buildChart(evals) {
      var area = document.getElementById('chart-area');
      document.getElementById('c-eval').textContent = (evals.length) + ' eval' + (evals.length!==1?'s':'');
      if (!evals.length) { area.innerHTML = emptyState('No evaluations yet', 'scores will plot here once runs land'); return; }

      var items = evals.slice().reverse();           // oldest-first
      var n = Math.min(items.length, 30);
      items = items.slice(items.length - n);
      var drift = items.map(function(e){ var s=scoresOf(e); return s.drift!==undefined?s.drift:null; });
      var traj  = items.map(function(e){ var s=scoresOf(e); return s.trajectory!==undefined?s.trajectory:null; });

      var W=480, H=180, pl=34, pr=12, pt=14, pb=26, cw=W-pl-pr, ch=H-pt-pb;
      function X(i){ return pl + (n<=1?cw/2:i/(n-1)*cw); }
      function Y(v){ return pt + (1-v)*ch; }

      var s = '<svg class="chart" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img" aria-label="Drift and trajectory scores over time">';
      // below-threshold band (lowest threshold downward)
      var bandTop = Y(Math.min(DRIFT_TH, TRAJ_TH));
      s += '<rect x="'+pl+'" y="'+bandTop+'" width="'+cw+'" height="'+(pt+ch-bandTop)+'" fill="rgba(255,107,107,0.05)" />';
      // gridlines
      [0,0.25,0.5,0.75,1.0].forEach(function(v){
        var y=Y(v);
        s += '<line x1="'+pl+'" y1="'+y+'" x2="'+(W-pr)+'" y2="'+y+'" stroke="#1d2838" stroke-width="1"/>';
        s += '<text x="'+(pl-5)+'" y="'+(y+3)+'" font-size="7" fill="#5c6b82" text-anchor="end" font-family="monospace">'+v.toFixed(2)+'</text>';
      });
      // threshold reference lines
      s += '<line x1="'+pl+'" y1="'+Y(DRIFT_TH)+'" x2="'+(W-pr)+'" y2="'+Y(DRIFT_TH)+'" stroke="#4f8cff" stroke-width="1" stroke-dasharray="3 3" opacity="0.45"/>';
      s += '<line x1="'+pl+'" y1="'+Y(TRAJ_TH)+'" x2="'+(W-pr)+'" y2="'+Y(TRAJ_TH)+'" stroke="#2dd4a7" stroke-width="1" stroke-dasharray="3 3" opacity="0.45"/>';

      function path(series){ var p=[]; series.forEach(function(v,i){ if(v!==null) p.push(X(i)+','+Y(v)); }); return p.join(' '); }
      function area2(series){
        var idx=[]; series.forEach(function(v,i){ if(v!==null) idx.push(i); }); if(!idx.length) return '';
        var p=[X(idx[0])+','+(pt+ch)]; series.forEach(function(v,i){ if(v!==null) p.push(X(i)+','+Y(v)); }); p.push(X(idx[idx.length-1])+','+(pt+ch)); return p.join(' ');
      }
      // drift = solid, trajectory = dashed (distinguish by style, not color alone)
      if (area2(drift)) s += '<polygon points="'+area2(drift)+'" fill="rgba(79,140,255,0.08)"/>';
      if (path(drift))  s += '<polyline points="'+path(drift)+'" fill="none" stroke="#4f8cff" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
      if (path(traj))   s += '<polyline points="'+path(traj)+'" fill="none" stroke="#2dd4a7" stroke-width="2" stroke-dasharray="5 3" stroke-linejoin="round" stroke-linecap="round"/>';

      function dots(series, th, color){
        series.forEach(function(v,i){ if(v===null) return; var low=v<th; var ts=fmtHM(items[i].finishedAt||items[i].startedAt);
          s += '<circle cx="'+X(i)+'" cy="'+Y(v)+'" r="'+(low?3.4:2.6)+'" fill="'+(low?'#ff6b6b':color)+'" stroke="#0b0f16" stroke-width="1.4">'+
               '<title>'+esc(v.toFixed(3))+(ts?' @ '+esc(ts):'')+(low?' (below threshold)':'')+'</title></circle>';
          if(low) s += '<circle cx="'+X(i)+'" cy="'+Y(v)+'" r="6" fill="none" stroke="#ff6b6b" stroke-width="1" opacity="0.5"/>';
        });
      }
      dots(drift, DRIFT_TH, '#4f8cff'); dots(traj, TRAJ_TH, '#2dd4a7');

      var step=Math.max(1,Math.floor(n/5));
      for (var i=0;i<n;i+=step){ var t=fmtHM(items[i].finishedAt||items[i].startedAt); if(t) s += '<text x="'+X(i)+'" y="'+(H-4)+'" font-size="6.5" fill="#5c6b82" text-anchor="middle" font-family="monospace">'+esc(t)+'</text>'; }
      s += '</svg>';

      s += '<div class="legend">' +
        '<span class="lg" style="color:#4f8cff"><span class="lg-line"></span>Drift</span>' +
        '<span class="lg" style="color:#2dd4a7"><span class="lg-line dash"></span>Trajectory</span>' +
        '<span class="lg" style="color:#5c6b82"><span class="lg-line dash"></span>Threshold</span>' +
        '<span class="lg" style="color:#ff6b6b"><span class="lg-dot"></span>Below threshold</span>' +
      '</div>';

      // recent table
      var recent = items.slice(-6).reverse();
      s += '<div class="tbl-wrap"><div class="tbl-cap">Recent evaluations</div><table><thead><tr>';
      ['Time','Drift','Traj','Cost','Latency','State'].forEach(function(h){ s += '<th>'+h+'</th>'; });
      s += '</tr></thead><tbody>';
      recent.forEach(function(e){
        var c=scoresOf(e);
        var dl = c.drift!==undefined && c.drift<DRIFT_TH, tl = c.trajectory!==undefined && c.trajectory<TRAJ_TH;
        s += '<tr>' +
          '<td>'+fmtTime(e.finishedAt||e.startedAt)+'</td>' +
          '<td class="'+(dl?'c-bad':'c-drift')+'">'+(c.drift!==undefined?c.drift.toFixed(3):'—')+'</td>' +
          '<td class="'+(tl?'c-bad':'c-traj')+'">'+(c.trajectory!==undefined?c.trajectory.toFixed(3):'—')+'</td>' +
          '<td class="c-cost">'+(c.cost!==undefined?c.cost.toFixed(4):'—')+'</td>' +
          '<td class="c-lat">'+(c.latency!==undefined?c.latency.toFixed(0)+' ms':'—')+'</td>' +
          '<td>'+(e.state==='succeeded'?badge('ok','ok'):badge('failed',e.state||'?'))+'</td>' +
        '</tr>';
      });
      s += '</tbody></table></div>';
      area.innerHTML = s;
    }

    // ── Deployments (all states; never empty when data exists) ──
    function renderDeps(deps) {
      var area = document.getElementById('dep-area');
      document.getElementById('c-dep').textContent = deps.length + ' total';
      if (!deps.length) { area.innerHTML = emptyState('No deployments yet', 'canary deployments will appear here'); return; }
      var order = { canary:0, pending:1, promoted:2, rolled_back:3, failed:4 };
      var sorted = deps.slice().sort(function(a,b){ return (order[a.state]||9)-(order[b.state]||9); });
      var fillColor = { canary:'#4f8cff', pending:'#9aabc2', promoted:'#2dd4a7', rolled_back:'#ff6b6b', failed:'#a78bfa' };
      var html = '';
      sorted.forEach(function(d){
        var pct = d.currentTrafficPercent || 0;
        var col = fillColor[d.state] || '#5c6b82';
        html += '<div class="dep">' +
          '<div class="dep-top"><div><div class="dep-id">'+esc((d.deploymentId||'').substring(0,18))+'</div>' +
          (d.versionId?'<div class="dep-ver">version '+esc(d.versionId.substring(0,8))+'…</div>':'') + '</div>' +
          badge(d.state, (d.state||'').replace('_',' ')) + '</div>' +
          '<div class="track"><div class="bar"><div class="fill" style="width:'+pct+'%;background:linear-gradient(90deg,'+col+',rgba(255,255,255,0.25))"></div></div>' +
          '<span class="pct">'+pct+'%</span></div></div>';
      });
      area.innerHTML = html;
    }

    // ── Decisions ──
    function renderDecs(decs, prs) {
      var area = document.getElementById('dec-area');
      var rb = decs.filter(function(x){ return x.action==='rollback'; }).length;
      document.getElementById('c-dec').textContent = decs.length + ' decision' + (decs.length!==1?'s':'');
      if (!decs.length) { area.innerHTML = emptyState('No decisions yet', 'the meta-agent has not acted'); return; }
      var prMap = {}; (prs||[]).forEach(function(p){ prMap[p.prDraftId]=p; });
      var sorted = decs.slice().sort(function(a,b){ return new Date(b.decidedAt||0)-new Date(a.decidedAt||0); });

      var html = '';
      sorted.forEach(function(d){
        var sig = d.signal || {};
        var pr = d.prDraftId ? prMap[d.prDraftId] : null;
        html += '<div class="dec m-'+esc(d.action)+'">';
        html += '<div class="dec-top">' + badge(d.action, d.action) + judgedBy(d.judgedBy) +
                '<span class="dec-dep">'+esc((d.deploymentId||'').substring(0,12))+'…</span>' +
                '<span class="dec-time">'+fmtTime(d.decidedAt)+'</span></div>';

        // signal chips (only when meaningful)
        var chips = [];
        if (sig.drift_drop) chips.push('<span class="chip'+(sig.drift_drop>0.05?' warn':'')+'">drift Δ <b>−'+Number(sig.drift_drop).toFixed(3)+'</b></span>');
        if (sig.trajectory_drop) chips.push('<span class="chip'+(sig.trajectory_drop>0.05?' warn':'')+'">traj Δ <b>−'+Number(sig.trajectory_drop).toFixed(3)+'</b></span>');
        if (sig.cost_increase_ratio) chips.push('<span class="chip">cost <b>+'+(Number(sig.cost_increase_ratio)*100).toFixed(1)+'%</b></span>');
        if (sig.canary_latency_ms) chips.push('<span class="chip">p95 <b>'+Number(sig.canary_latency_ms).toFixed(0)+' ms</b></span>');
        if (chips.length) html += '<div class="chips">'+chips.join('')+'</div>';

        if (d.rationale) html += '<div class="rationale">'+esc(d.rationale)+'</div>';

        if (pr) {
          html += '<div class="pr"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M6 9v6"/><path d="M13 6h5a2 2 0 0 1 2 2v7"/><path d="m16 12-3 3 3 3"/></svg>';
          if (pr.prUrl) html += '<a href="'+esc(pr.prUrl)+'" target="_blank" rel="noopener noreferrer">'+esc(pr.title)+'</a>';
          else html += '<span>improvement PR · <span style="color:var(--ink-2)">'+esc(pr.title)+'</span></span>';
          html += '</div>';
        }
        html += '</div>';
      });
      area.innerHTML = html;
    }

    function refresh() {
      fetch('/dashboard/data').then(function(r){ return r.json(); }).then(function(d){
        renderKpis(d);
        buildChart(d.evaluations || []);
        renderDeps(d.deployments || []);
        renderDecs(d.decisions || [], d.pr_drafts || []);
        document.getElementById('status').textContent = 'updated ' + new Date().toLocaleTimeString('en-GB',{hour12:false}) + ' · 10s';
        document.getElementById('dot').style.background = '#2dd4a7';
      }).catch(function(err){
        document.getElementById('status').textContent = 'error: ' + err;
        document.getElementById('dot').style.background = '#ff6b6b';
      });
    }
    refresh();
    setInterval(refresh, 10000);
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
