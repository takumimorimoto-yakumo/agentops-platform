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
      /* Surfaces — near-black with a faint cool cast, layered for depth */
      --bg-0:  #0a0b0f;
      --bg-1:  #0f1117;
      --bg-2:  #14161e;
      --bg-3:  #1b1e27;
      --line:  #20232c;
      --line-2:#2b3039;

      /* Ink — calm, high-legibility grayscale */
      --ink-1: #f5f7fa;
      --ink-2: #aab2c2;
      --ink-3: #717a8c;
      --ink-4: #4a5160;

      /* Accents — restrained; indigo is the single signature hue */
      --blue:   #6a8dff;
      --green:  #35d6a0;
      --amber:  #f5bf63;
      --red:    #fb7185;
      --violet: #b4a2ff;

      --blue-d:   rgba(106,141,255,0.12);
      --green-d:  rgba(53,214,160,0.12);
      --amber-d:  rgba(245,191,99,0.12);
      --red-d:    rgba(251,113,133,0.12);
      --violet-d: rgba(180,162,255,0.13);

      --mono: ui-monospace, "SF Mono", "SFMono-Regular", "JetBrains Mono", "Cascadia Code", "Fira Code", Menlo, Consolas, monospace;
      --sans: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;

      /* Soft, layered elevation — editorial, never heavy */
      --shadow-1: 0 1px 2px rgba(0,0,0,0.28);
      --shadow-2: 0 8px 30px -12px rgba(0,0,0,0.55), 0 1px 2px rgba(0,0,0,0.3);
      /* 1px top inner-highlight — the "lit from above" premium cue */
      --hi: inset 0 1px 0 rgba(255,255,255,0.045);
      --ease: cubic-bezier(.22,.61,.36,1);

      --r-sm: 8px;  --r-md: 12px;  --r-lg: 16px;  --r-xl: 22px;
      --sp-1: 4px;  --sp-2: 8px;   --sp-3: 12px;  --sp-4: 18px;
      --sp-5: 24px; --sp-6: 34px;  --sp-7: 52px;

      --maxw: 1320px;
    }

    html { -webkit-text-size-adjust: 100%; }
    body {
      font-family: var(--sans);
      background:
        radial-gradient(1000px 520px at 88% -12%, rgba(106,141,255,0.05), transparent 62%),
        radial-gradient(820px 460px at -8% -4%, rgba(180,162,255,0.04), transparent 58%),
        var(--bg-0);
      background-attachment: fixed;
      color: var(--ink-1);
      min-height: 100dvh;
      line-height: 1.5;
      letter-spacing: -0.005em;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }
    a { color: inherit; }

    /* ── Entrance motion ── */
    @keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
    .rise { opacity: 0; animation: rise .55s var(--ease) forwards; }

    /* ── Header ── */
    .hdr {
      position: sticky; top: 0; z-index: 20;
      display: flex; align-items: center; gap: var(--sp-3);
      padding: var(--sp-4) var(--sp-5);
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(13,15,22,0.9), rgba(13,15,22,0.72));
      backdrop-filter: saturate(140%) blur(14px);
      -webkit-backdrop-filter: saturate(140%) blur(14px);
    }
    .brand { display: flex; align-items: center; gap: var(--sp-3); min-width: 0; }
    .brand-mark {
      width: 34px; height: 34px; flex-shrink: 0;
      display: grid; place-items: center;
      border-radius: 11px;
      background: linear-gradient(150deg, rgba(106,141,255,0.22), rgba(180,162,255,0.16));
      border: 1px solid var(--line-2);
      box-shadow: var(--hi), 0 4px 14px -6px rgba(106,141,255,0.5);
      color: #cdd8ff;
    }
    .brand-name { font-weight: 650; font-size: 1.02rem; letter-spacing: -0.02em; white-space: nowrap; }
    .brand-sub  { font-family: var(--mono); font-size: 0.64rem; color: var(--ink-3); letter-spacing: 0.05em; text-transform: uppercase; white-space: nowrap; margin-top: 1px; }
    .brand-divider { width: 1px; height: 22px; background: var(--line); margin: 0 2px; }
    .hdr-right { margin-left: auto; display: flex; align-items: center; gap: var(--sp-3); }
    .live {
      display: inline-flex; align-items: center; gap: var(--sp-2);
      font-family: var(--mono); font-size: 0.66rem; color: var(--ink-2);
      padding: 6px 12px; border: 1px solid var(--line); border-radius: 9999px;
      background: var(--bg-1); box-shadow: var(--hi);
    }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 3px var(--green-d); animation: pulse 2.4s ease-in-out infinite; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .4; } }
    .lang { display: inline-flex; border: 1px solid var(--line); border-radius: 9999px; overflow: hidden; background: var(--bg-1); box-shadow: var(--hi); }
    .lang-btn { font-family: var(--mono); font-size: 0.64rem; color: var(--ink-3); background: transparent; border: 0; padding: 6px 13px; cursor: pointer; transition: color .18s var(--ease), background .18s var(--ease); }
    .lang-btn + .lang-btn { border-left: 1px solid var(--line); }
    .lang-btn:hover { color: var(--ink-1); }
    .lang-btn.on { color: #cdd8ff; background: var(--blue-d); box-shadow: inset 0 0 0 1px rgba(106,141,255,0.3); }
    .lang-btn:focus-visible { outline: 2px solid var(--blue); outline-offset: -2px; }

    /* ── Layout ── */
    .wrap { max-width: var(--maxw); margin: 0 auto; padding: var(--sp-6) var(--sp-5) var(--sp-7); }

    /* ── KPI strip ── */
    .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--sp-3); margin-bottom: var(--sp-3); }
    .kpi {
      position: relative; overflow: hidden;
      background: linear-gradient(180deg, var(--bg-2), var(--bg-1));
      border: 1px solid var(--line); border-radius: var(--r-lg);
      padding: var(--sp-4);
      box-shadow: var(--hi), var(--shadow-1);
      transition: transform .25s var(--ease), border-color .25s var(--ease), box-shadow .25s var(--ease);
    }
    .kpi:hover { transform: translateY(-2px); border-color: var(--line-2); box-shadow: var(--hi), var(--shadow-2); }
    /* soft accent bloom in the corner instead of a hard bar */
    .kpi::after { content:''; position:absolute; top:-40%; right:-20%; width:60%; height:120%;
      background: radial-gradient(closest-side, var(--accent-d, transparent), transparent 70%); opacity:.9; pointer-events:none; }
    .kpi.k-blue   { --accent: var(--blue);   --accent-d: var(--blue-d); }
    .kpi.k-green  { --accent: var(--green);  --accent-d: var(--green-d); }
    .kpi.k-red    { --accent: var(--red);    --accent-d: var(--red-d); }
    .kpi.k-violet { --accent: var(--violet); --accent-d: var(--violet-d); }
    .kpi-top { display: flex; align-items: center; gap: var(--sp-2); color: var(--ink-2); }
    .kpi-ico {
      width: 30px; height: 30px; flex-shrink: 0; padding: 7px; border-radius: 9px;
      color: var(--accent, var(--ink-2)); background: var(--accent-d, var(--bg-3));
      border: 1px solid var(--line); box-shadow: var(--hi);
    }
    .kpi-label { font-size: 0.74rem; font-weight: 500; color: var(--ink-2); letter-spacing: 0; text-transform: none; }
    .kpi-val { font-size: 2.4rem; font-weight: 600; line-height: 1.05; margin-top: var(--sp-3); font-variant-numeric: tabular-nums; letter-spacing: -0.03em; color: var(--ink-1); }
    .kpi-hint { font-size: 0.7rem; color: var(--ink-3); margin-top: 4px; }

    /* ── Panels ── */
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3); }
    .panel { background: var(--bg-1); border: 1px solid var(--line); border-radius: var(--r-lg); overflow: hidden; display: flex; flex-direction: column; box-shadow: var(--hi), var(--shadow-1); }
    .panel.wide { grid-column: 1 / -1; }
    .p-head { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-3) var(--sp-4); border-bottom: 1px solid var(--line); background: linear-gradient(180deg, var(--bg-2), rgba(20,22,30,0.4)); }
    .p-ico { width: 15px; height: 15px; color: var(--ink-3); flex-shrink: 0; }
    .p-title { font-size: 0.82rem; font-weight: 550; color: var(--ink-1); letter-spacing: -0.01em; }
    .p-count { margin-left: auto; font-family: var(--mono); font-size: 0.6rem; color: var(--ink-3); background: var(--bg-3); border: 1px solid var(--line); padding: 3px 9px; border-radius: 9999px; }
    .p-body { padding: var(--sp-4); flex: 1; }

    /* ── Empty states ── */
    .empty { display: flex; flex-direction: column; align-items: center; gap: var(--sp-2); padding: var(--sp-7) var(--sp-4); text-align: center; color: var(--ink-3); }
    .empty svg { width: 28px; height: 28px; color: var(--ink-4); }
    .empty-t { font-size: 0.85rem; color: var(--ink-2); }
    .empty-s { font-size: 0.74rem; color: var(--ink-4); }

    /* ── Badges ── */
    .badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 9999px; font-family: var(--mono); font-size: 0.62rem; font-weight: 600; letter-spacing: 0.01em; white-space: nowrap; border: 1px solid transparent; }
    .badge::before { content: ''; width: 5px; height: 5px; border-radius: 50%; background: currentColor; box-shadow: 0 0 6px currentColor; }
    .b-canary, .b-pending { color: #acc4ff; background: var(--blue-d); border-color: rgba(106,141,255,0.26); }
    .b-promoted, .b-advance, .b-ok { color: #7fe9c4; background: var(--green-d); border-color: rgba(53,214,160,0.26); }
    .b-hold { color: #ffd592; background: var(--amber-d); border-color: rgba(245,191,99,0.26); }
    .b-rollback, .b-rolled_back, .b-failed { color: #ffa6b3; background: var(--red-d); border-color: rgba(251,113,133,0.26); }

    /* ── "Judged by" pill (the autonomy proof) ── */
    .jb { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px 4px 8px; border-radius: 9999px; font-family: var(--mono); font-size: 0.62rem; font-weight: 600; white-space: nowrap; border: 1px solid transparent; }
    .jb svg { width: 12px; height: 12px; }
    .jb-gemini { color: #d2c6ff; background: var(--violet-d); border-color: rgba(180,162,255,0.4); box-shadow: 0 0 18px rgba(180,162,255,0.22); }
    .jb-gemini_failed { color: #ffd592; background: var(--amber-d); border-color: rgba(245,191,99,0.34); }
    .jb-heuristic, .jb-auto_advance, .jb-missing_baseline { color: var(--ink-2); background: var(--bg-3); border-color: var(--line-2); }
    .jb-safety_floor { color: #ffa6b3; background: var(--red-d); border-color: rgba(251,113,133,0.3); }

    /* ── Chart ── */
    .chart { width: 100%; display: block; }
    .legend { display: flex; flex-wrap: wrap; gap: var(--sp-4); margin-top: var(--sp-3); padding-top: var(--sp-3); border-top: 1px solid var(--line); }
    .lg { display: inline-flex; align-items: center; gap: 7px; font-size: 0.68rem; color: var(--ink-2); }
    .lg-line { width: 22px; height: 0; border-top: 2px solid currentColor; }
    .lg-line.dash { border-top-style: dashed; }
    .lg-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; box-shadow: 0 0 7px currentColor; }

    /* ── Tables ── */
    table { width: 100%; border-collapse: collapse; }
    th { color: var(--ink-3); font-weight: 500; text-align: left; padding: 8px 8px; border-bottom: 1px solid var(--line); font-family: var(--mono); font-size: 0.58rem; letter-spacing: 0.05em; text-transform: uppercase; }
    td { padding: 9px 8px; border-bottom: 1px solid var(--bg-3); font-family: var(--mono); font-size: 0.68rem; color: var(--ink-2); font-variant-numeric: tabular-nums; }
    tbody tr { transition: background .15s var(--ease); }
    tbody tr:hover { background: rgba(255,255,255,0.015); }
    tr:last-child td { border-bottom: none; }
    .tbl-wrap { margin-top: var(--sp-4); overflow-x: auto; }
    .tbl-cap { font-size: 0.7rem; font-weight: 500; color: var(--ink-3); margin-bottom: var(--sp-2); }
    .c-drift { color: #9fbcff; } .c-traj { color: #7ce6c6; } .c-cost { color: #ffd592; } .c-lat { color: #cbbcff; } .c-bad { color: var(--red); }

    /* ── Deployment rows ── */
    .dep { background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--sp-3) var(--sp-4); box-shadow: var(--hi); transition: border-color .25s var(--ease), transform .25s var(--ease); }
    .dep:hover { border-color: var(--line-2); transform: translateX(2px); }
    .dep + .dep { margin-top: var(--sp-3); }
    .dep-top { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-3); margin-bottom: var(--sp-3); }
    .dep-id { font-family: var(--mono); font-size: 0.72rem; color: var(--ink-1); }
    .dep-ver { font-family: var(--mono); font-size: 0.6rem; color: var(--ink-3); margin-top: 3px; }
    .track { display: flex; align-items: center; gap: var(--sp-3); }
    .bar { flex: 1; height: 6px; background: var(--bg-3); border-radius: 9999px; overflow: hidden; box-shadow: inset 0 0 0 1px var(--line); }
    .fill { height: 100%; border-radius: 9999px; transition: width .7s var(--ease); }
    .pct { font-family: var(--mono); font-size: 0.64rem; color: var(--ink-2); min-width: 38px; text-align: right; font-variant-numeric: tabular-nums; }

    /* ── Decision cards ── */
    .dec { position: relative; background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--sp-4); padding-left: calc(var(--sp-4) + 5px); box-shadow: var(--hi); transition: border-color .25s var(--ease); }
    .dec:hover { border-color: var(--line-2); }
    .dec + .dec { margin-top: var(--sp-3); }
    .dec::before { content:''; position:absolute; left:0; top:12px; bottom:12px; width:3px; border-radius:0 3px 3px 0; background: var(--mark, var(--ink-4)); box-shadow: 0 0 12px var(--mark, transparent); }
    .dec.m-rollback { --mark: var(--red); } .dec.m-advance { --mark: var(--green); } .dec.m-hold { --mark: var(--amber); }
    .dec-top { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
    .dec-time { font-family: var(--mono); font-size: 0.62rem; color: var(--ink-3); margin-left: auto; }
    .dec-dep { font-family: var(--mono); font-size: 0.62rem; color: var(--ink-3); }
    .chips { display: flex; flex-wrap: wrap; gap: var(--sp-2); margin-top: var(--sp-3); }
    .chip { display: inline-flex; align-items: baseline; gap: 6px; font-family: var(--mono); font-size: 0.6rem; color: var(--ink-2); background: var(--bg-3); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 4px 9px; font-variant-numeric: tabular-nums; }
    .chip b { color: var(--ink-1); font-weight: 600; }
    .chip.warn b { color: var(--amber); }
    .rationale { font-size: 0.86rem; color: var(--ink-2); line-height: 1.65; margin-top: var(--sp-3); max-width: 76ch; }
    .pr { margin-top: var(--sp-3); display: inline-flex; align-items: center; gap: 7px; font-family: var(--mono); font-size: 0.64rem; color: var(--ink-3); background: var(--bg-1); border: 1px dashed var(--line-2); border-radius: var(--r-sm); padding: 6px 10px; }
    .pr svg { width: 12px; height: 12px; color: var(--blue); }
    .pr a { color: #9fbcff; text-decoration: none; border-bottom: 1px solid rgba(106,141,255,0.35); transition: border-color .18s var(--ease); }
    .pr a:hover { border-color: var(--blue); }

    /* ── Focus / responsive / motion ── */
    a:focus-visible, [tabindex]:focus-visible, button:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; border-radius: 6px; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } .kpis { grid-template-columns: 1fr 1fr; } }
    @media (max-width: 560px) { .kpis { grid-template-columns: 1fr; } .brand-sub, .brand-divider { display: none; } }
    @media (prefers-reduced-motion: reduce) { *, ::before, ::after { animation: none !important; transition: none !important; } .rise { opacity: 1; } }
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
        <div class="brand-sub" data-i18n="sub">Autonomous Canary Control</div>
      </div>
    </div>
    <div class="hdr-right">
      <div class="lang" role="group" aria-label="Language">
        <button type="button" class="lang-btn" data-lang="en">EN</button>
        <button type="button" class="lang-btn" data-lang="ja">日本語</button>
      </div>
      <span class="live"><span class="dot" id="dot"></span><span id="status">connecting…</span></span>
    </div>
  </header>

  <div class="wrap">

    <!-- KPI strip -->
    <section class="kpis" id="kpis" aria-label="Key metrics">
      <div class="kpi k-blue rise" style="animation-delay:0s">
        <div class="kpi-top"><svg class="kpi-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 13l3-3 3 3 4-5"/></svg><span class="kpi-label" data-i18n="kEval">Evaluations</span></div>
        <div class="kpi-val" id="k-evals">—</div>
        <div class="kpi-hint" id="k-evals-h">&nbsp;</div>
      </div>
      <div class="kpi k-green rise" style="animation-delay:.05s">
        <div class="kpi-top"><svg class="kpi-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18"/></svg><span class="kpi-label" data-i18n="kCanary">Active canaries</span></div>
        <div class="kpi-val" id="k-canary">—</div>
        <div class="kpi-hint" id="k-canary-h">&nbsp;</div>
      </div>
      <div class="kpi k-red rise" style="animation-delay:.1s">
        <div class="kpi-top"><svg class="kpi-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg><span class="kpi-label" data-i18n="kRb">Auto rollbacks</span></div>
        <div class="kpi-val" id="k-rb">—</div>
        <div class="kpi-hint" id="k-rb-h">&nbsp;</div>
      </div>
      <div class="kpi k-violet rise" style="animation-delay:.15s">
        <div class="kpi-top"><svg class="kpi-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="7" width="14" height="12" rx="2"/><path d="M9 7V5a3 3 0 0 1 6 0v2"/><path d="M9 13h0M15 13h0"/></svg><span class="kpi-label" data-i18n="kLlm">LLM-judged</span></div>
        <div class="kpi-val" id="k-llm">—</div>
        <div class="kpi-hint" id="k-llm-h">&nbsp;</div>
      </div>
    </section>

    <div class="grid">
      <!-- Evaluation timeline -->
      <div class="panel rise" style="animation-delay:.2s">
        <div class="p-head">
          <svg class="p-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M19 9l-5 5-3-3-4 4"/></svg>
          <span class="p-title" data-i18n="pEval">Evaluation Score Timeline</span>
          <span class="p-count" id="c-eval">—</span>
        </div>
        <div class="p-body"><div id="chart-area"></div></div>
      </div>

      <!-- Deployment status -->
      <div class="panel rise" style="animation-delay:.26s">
        <div class="p-head">
          <svg class="p-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 2 7l10 5 10-5-10-5Z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/></svg>
          <span class="p-title" data-i18n="pDep">Deployment Status</span>
          <span class="p-count" id="c-dep">—</span>
        </div>
        <div class="p-body"><div id="dep-area"></div></div>
      </div>

      <!-- Decisions -->
      <div class="panel wide rise" style="animation-delay:.32s">
        <div class="p-head">
          <svg class="p-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a9 9 0 1 0 9 9"/><path d="M12 7v5l3 2"/><path d="M16 3l5 5"/></svg>
          <span class="p-title" data-i18n="pDec">Meta-agent Decisions</span>
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

    // ── i18n ──
    var L = {
      en: {
        sub:'Autonomous Canary Control',
        kEval:'Evaluations', kCanary:'Active canaries', kRb:'Auto rollbacks', kLlm:'LLM-judged',
        pEval:'Evaluation Score Timeline', pDep:'Deployment Status', pDec:'Meta-agent Decisions',
        hScored:'scored across axes', hAwait:'awaiting runs', hInprog:'in progress', hNoCanary:'none in progress', hNoHuman:'no human in the path', hNoRb:'none yet',
        empEvalT:'No evaluations yet', empEvalS:'scores will plot here once runs land',
        empDepT:'No deployments yet', empDepS:'canary deployments will appear here',
        empDecT:'No decisions yet', empDecS:'the meta-agent has not acted',
        lgDrift:'Drift', lgTraj:'Trajectory', lgTh:'Threshold', lgBelow:'Below threshold',
        tblCap:'Recent evaluations', thTime:'Time', thDrift:'Drift', thTraj:'Traj', thCost:'Cost', thLat:'Latency', thState:'State',
        jb_gemini:'Judged by Gemini', jb_gemini_failed:'Gemini failed \\u2192 fallback', jb_heuristic:'Rule-based', jb_safety_floor:'Safety floor', jb_auto_advance:'Auto-advance', jb_missing_baseline:'No baseline \\u2192 hold',
        chDrift:'drift \\u0394', chTraj:'traj \\u0394', chCost:'cost', chP95:'p95', pr:'improvement PR', ver:'version',
        updated:'updated', poll:'10s', error:'error: ', connecting:'connecting\\u2026',
        bd_rollback:'rollback', bd_advance:'advance', bd_hold:'hold', bd_canary:'canary', bd_pending:'pending', bd_promoted:'promoted', bd_rolled_back:'rolled back', bd_failed:'failed', bd_ok:'ok', bd_unknown:'unknown'
      },
      ja: {
        sub:'自律カナリア制御',
        kEval:'評価', kCanary:'稼働カナリア', kRb:'自動ロールバック', kLlm:'LLM 判断',
        pEval:'評価スコア タイムライン', pDep:'デプロイ状況', pDec:'メタエージェントの判断',
        hScored:'各軸で採点', hAwait:'実行待ち', hInprog:'進行中', hNoCanary:'進行中なし', hNoHuman:'人手を介さず', hNoRb:'まだ無し',
        empEvalT:'評価はまだありません', empEvalS:'実行されるとここにプロットされます',
        empDepT:'デプロイはまだありません', empDepS:'カナリアデプロイがここに出ます',
        empDecT:'判断はまだありません', empDecS:'メタエージェントはまだ動いていません',
        lgDrift:'ドリフト', lgTraj:'トラジェクトリ', lgTh:'しきい値', lgBelow:'しきい値割れ',
        tblCap:'直近の評価', thTime:'時刻', thDrift:'ドリフト', thTraj:'軌跡', thCost:'コスト', thLat:'レイテンシ', thState:'状態',
        jb_gemini:'Gemini が判断', jb_gemini_failed:'Gemini 失敗 \\u2192 フォールバック', jb_heuristic:'ルールベース', jb_safety_floor:'安全床', jb_auto_advance:'自動前進', jb_missing_baseline:'基準なし \\u2192 保留',
        chDrift:'ドリフト\\u0394', chTraj:'軌跡\\u0394', chCost:'コスト', chP95:'p95', pr:'改善PR', ver:'バージョン',
        updated:'更新', poll:'10秒', error:'エラー: ', connecting:'接続中\\u2026',
        bd_rollback:'ロールバック', bd_advance:'前進', bd_hold:'保留', bd_canary:'カナリア', bd_pending:'保留中', bd_promoted:'昇格', bd_rolled_back:'ロールバック済', bd_failed:'失敗', bd_ok:'ok', bd_unknown:'不明'
      }
    };
    var lang = localStorage.getItem('aolang') || (((navigator.language||'').slice(0,2)==='ja') ? 'ja' : 'en');
    function t(k){ var v=(L[lang]||{})[k]; if(v===undefined) v=L.en[k]; return v===undefined ? k : v; }
    function loc(){ return lang==='ja' ? 'ja-JP' : 'en-GB'; }
    function countLabel(n, kind){
      if (lang==='ja') return n + ' 件';
      if (kind==='dep') return n + ' total';
      if (kind==='dec') return n + ' decision' + (n!==1?'s':'');
      return n + ' eval' + (n!==1?'s':'');
    }

    function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
    function badge(cls, key) { return '<span class="badge b-' + cls + '">' + esc(t('bd_'+key) || key) + '</span>'; }
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
      var icon = { gemini: BRAIN, gemini_failed: BRAIN, heuristic: GEAR, safety_floor: SHIELD, auto_advance: GEAR, missing_baseline: SHIELD };
      var key = jb || 'heuristic';
      return '<span class="jb jb-' + esc(key) + '" title="' + esc(key) + '">' + (icon[key] || GEAR) + esc(t('jb_'+key) || key) + '</span>';
    }

    // ── KPIs ──
    function renderKpis(d) {
      var evals = d.evaluations || [], deps = d.deployments || [], decs = d.decisions || [];
      var active = deps.filter(function(x){ return x.state==='canary' || x.state==='pending'; }).length;
      var rb = decs.filter(function(x){ return x.action==='rollback'; }).length;
      var llm = decs.filter(function(x){ return x.judgedBy==='gemini'; }).length;
      function set(id, v) { document.getElementById(id).textContent = v; }
      set('k-evals', evals.length); set('k-canary', active); set('k-rb', rb); set('k-llm', llm);
      document.getElementById('k-evals-h').textContent = evals.length ? t('hScored') : t('hAwait');
      document.getElementById('k-canary-h').textContent = active ? t('hInprog') : t('hNoCanary');
      document.getElementById('k-rb-h').textContent = rb ? t('hNoHuman') : t('hNoRb');
      document.getElementById('k-llm-h').textContent = (lang==='ja')
        ? (decs.length + ' 件中 ' + llm + ' 件を LLM が判断')
        : ((llm ? llm + ' of ' : '') + decs.length + ' decisions by LLM');
    }

    // ── Chart ──
    function buildChart(evals) {
      var area = document.getElementById('chart-area');
      document.getElementById('c-eval').textContent = countLabel(evals.length, 'eval');
      if (!evals.length) { area.innerHTML = emptyState(t('empEvalT'), t('empEvalS')); return; }

      var items = evals.slice().reverse();           // oldest-first
      var n = Math.min(items.length, 30);
      items = items.slice(items.length - n);
      var drift = items.map(function(e){ var s=scoresOf(e); return s.drift!==undefined?s.drift:null; });
      var traj  = items.map(function(e){ var s=scoresOf(e); return s.trajectory!==undefined?s.trajectory:null; });

      var W=600, H=210, pl=32, pr=14, pt=16, pb=28, cw=W-pl-pr, ch=H-pt-pb, baseY=pt+ch;
      function X(i){ return pl + (n<=1?cw/2:i/(n-1)*cw); }
      function Y(v){ return pt + (1-v)*ch; }

      var s = '<svg class="chart" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img" aria-label="Drift and trajectory scores over time">';
      // defs: area gradients + soft line glow
      s += '<defs>'
        + '<linearGradient id="gDrift" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6a8dff" stop-opacity="0.22"/><stop offset="1" stop-color="#6a8dff" stop-opacity="0"/></linearGradient>'
        + '<linearGradient id="gTraj" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#35d6a0" stop-opacity="0.16"/><stop offset="1" stop-color="#35d6a0" stop-opacity="0"/></linearGradient>'
        + '<filter id="glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="2.4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
        + '</defs>';
      // below-threshold band (lowest threshold downward)
      var bandTop = Y(Math.min(DRIFT_TH, TRAJ_TH));
      s += '<rect x="'+pl+'" y="'+bandTop+'" width="'+cw+'" height="'+(baseY-bandTop)+'" fill="rgba(251,113,133,0.05)" />';
      // gridlines (faint, dotted)
      [0,0.25,0.5,0.75,1.0].forEach(function(v){
        var y=Y(v);
        s += '<line x1="'+pl+'" y1="'+y+'" x2="'+(W-pr)+'" y2="'+y+'" stroke="#20232c" stroke-width="1" stroke-dasharray="1 4"/>';
        s += '<text x="'+(pl-6)+'" y="'+(y+3)+'" font-size="8" fill="#717a8c" text-anchor="end" font-family="monospace">'+v.toFixed(2)+'</text>';
      });
      // threshold reference lines
      s += '<line x1="'+pl+'" y1="'+Y(DRIFT_TH)+'" x2="'+(W-pr)+'" y2="'+Y(DRIFT_TH)+'" stroke="#6a8dff" stroke-width="1" stroke-dasharray="4 4" opacity="0.4"/>';
      s += '<line x1="'+pl+'" y1="'+Y(TRAJ_TH)+'" x2="'+(W-pr)+'" y2="'+Y(TRAJ_TH)+'" stroke="#35d6a0" stroke-width="1" stroke-dasharray="4 4" opacity="0.4"/>';

      // smooth path (Catmull-Rom -> cubic bezier); nulls drop out of the series
      function pts(series){ var p=[]; series.forEach(function(v,i){ if(v!==null) p.push([X(i),Y(v)]); }); return p; }
      function smooth(p){
        if(!p.length) return ''; if(p.length===1) return 'M'+p[0][0]+','+p[0][1];
        var d='M'+p[0][0]+','+p[0][1];
        for(var i=0;i<p.length-1;i++){
          var p0=p[i-1]||p[i], p1=p[i], p2=p[i+1], p3=p[i+2]||p2;
          var c1x=p1[0]+(p2[0]-p0[0])/6, c1y=p1[1]+(p2[1]-p0[1])/6;
          var c2x=p2[0]-(p3[0]-p1[0])/6, c2y=p2[1]-(p3[1]-p1[1])/6;
          d+='C'+c1x.toFixed(1)+','+c1y.toFixed(1)+' '+c2x.toFixed(1)+','+c2y.toFixed(1)+' '+p2[0].toFixed(1)+','+p2[1].toFixed(1);
        }
        return d;
      }
      var dP=pts(drift), tP=pts(traj), dL=smooth(dP), tL=smooth(tP);
      // drift = solid + gradient fill, trajectory = dashed (distinguish by style, not color alone)
      if (dP.length) s += '<path d="'+dL+' L'+dP[dP.length-1][0].toFixed(1)+','+baseY+' L'+dP[0][0].toFixed(1)+','+baseY+' Z" fill="url(#gDrift)" stroke="none"/>';
      if (dL) s += '<path d="'+dL+'" fill="none" stroke="#6a8dff" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" filter="url(#glow)"/>';
      if (tL) s += '<path d="'+tL+'" fill="none" stroke="#35d6a0" stroke-width="2" stroke-dasharray="5 4" stroke-linejoin="round" stroke-linecap="round" opacity="0.95"/>';

      function dots(series, th, color){
        series.forEach(function(v,i){ if(v===null) return; var low=v<th; var ts=fmtHM(items[i].finishedAt||items[i].startedAt);
          if(low) s += '<circle cx="'+X(i)+'" cy="'+Y(v)+'" r="7" fill="none" stroke="#fb7185" stroke-width="1" opacity="0.45"/>';
          s += '<circle cx="'+X(i)+'" cy="'+Y(v)+'" r="'+(low?3.6:2.8)+'" fill="'+(low?'#fb7185':color)+'" stroke="#0f1117" stroke-width="1.6">'+
               '<title>'+esc(v.toFixed(3))+(ts?' @ '+esc(ts):'')+(low?' (below threshold)':'')+'</title></circle>';
        });
      }
      dots(drift, DRIFT_TH, '#6a8dff'); dots(traj, TRAJ_TH, '#35d6a0');

      var step=Math.max(1,Math.floor(n/5));
      for (var i=0;i<n;i+=step){ var tick=fmtHM(items[i].finishedAt||items[i].startedAt); if(tick) s += '<text x="'+X(i)+'" y="'+(H-5)+'" font-size="8" fill="#717a8c" text-anchor="middle" font-family="monospace">'+esc(tick)+'</text>'; }
      s += '</svg>';

      s += '<div class="legend">' +
        '<span class="lg" style="color:#4f8cff"><span class="lg-line"></span>'+esc(t('lgDrift'))+'</span>' +
        '<span class="lg" style="color:#2dd4a7"><span class="lg-line dash"></span>'+esc(t('lgTraj'))+'</span>' +
        '<span class="lg" style="color:#5c6b82"><span class="lg-line dash"></span>'+esc(t('lgTh'))+'</span>' +
        '<span class="lg" style="color:#ff6b6b"><span class="lg-dot"></span>'+esc(t('lgBelow'))+'</span>' +
      '</div>';

      // recent table
      var recent = items.slice(-6).reverse();
      s += '<div class="tbl-wrap"><div class="tbl-cap">'+esc(t('tblCap'))+'</div><table><thead><tr>';
      [t('thTime'),t('thDrift'),t('thTraj'),t('thCost'),t('thLat'),t('thState')].forEach(function(h){ s += '<th>'+esc(h)+'</th>'; });
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
      document.getElementById('c-dep').textContent = countLabel(deps.length, 'dep');
      if (!deps.length) { area.innerHTML = emptyState(t('empDepT'), t('empDepS')); return; }
      var order = { canary:0, pending:1, promoted:2, rolled_back:3, failed:4 };
      var sorted = deps.slice().sort(function(a,b){ return (order[a.state]||9)-(order[b.state]||9); });
      var fillColor = { canary:'#6a8dff', pending:'#717a8c', promoted:'#35d6a0', rolled_back:'#fb7185', failed:'#b4a2ff' };
      var html = '';
      sorted.forEach(function(d){
        var pct = d.currentTrafficPercent || 0;
        var col = fillColor[d.state] || '#5c6b82';
        html += '<div class="dep">' +
          '<div class="dep-top"><div><div class="dep-id">'+esc((d.deploymentId||'').substring(0,18))+'</div>' +
          (d.versionId?'<div class="dep-ver">'+esc(t('ver'))+' '+esc(d.versionId.substring(0,8))+'…</div>':'') + '</div>' +
          badge(d.state, d.state||'unknown') + '</div>' +
          '<div class="track"><div class="bar"><div class="fill" style="width:'+pct+'%;background:linear-gradient(90deg,'+col+',rgba(255,255,255,0.25))"></div></div>' +
          '<span class="pct">'+pct+'%</span></div></div>';
      });
      area.innerHTML = html;
    }

    // ── Decisions ──
    function renderDecs(decs, prs) {
      var area = document.getElementById('dec-area');
      var rb = decs.filter(function(x){ return x.action==='rollback'; }).length;
      document.getElementById('c-dec').textContent = countLabel(decs.length, 'dec');
      if (!decs.length) { area.innerHTML = emptyState(t('empDecT'), t('empDecS')); return; }
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
        if (sig.drift_drop) chips.push('<span class="chip'+(sig.drift_drop>0.05?' warn':'')+'">'+esc(t('chDrift'))+' <b>−'+Number(sig.drift_drop).toFixed(3)+'</b></span>');
        if (sig.trajectory_drop) chips.push('<span class="chip'+(sig.trajectory_drop>0.05?' warn':'')+'">'+esc(t('chTraj'))+' <b>−'+Number(sig.trajectory_drop).toFixed(3)+'</b></span>');
        if (sig.cost_increase_ratio) chips.push('<span class="chip">'+esc(t('chCost'))+' <b>+'+(Number(sig.cost_increase_ratio)*100).toFixed(1)+'%</b></span>');
        if (sig.canary_latency_ms) chips.push('<span class="chip">'+esc(t('chP95'))+' <b>'+Number(sig.canary_latency_ms).toFixed(0)+' ms</b></span>');
        if (chips.length) html += '<div class="chips">'+chips.join('')+'</div>';

        if (d.rationale) html += '<div class="rationale">'+esc(d.rationale)+'</div>';

        if (pr) {
          html += '<div class="pr"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M6 9v6"/><path d="M13 6h5a2 2 0 0 1 2 2v7"/><path d="m16 12-3 3 3 3"/></svg>';
          if (pr.prUrl) html += '<a href="'+esc(pr.prUrl)+'" target="_blank" rel="noopener noreferrer">'+esc(pr.title)+'</a>';
          else html += '<span>'+esc(t('pr'))+' · <span style="color:var(--ink-2)">'+esc(pr.title)+'</span></span>';
          html += '</div>';
        }
        html += '</div>';
      });
      area.innerHTML = html;
    }

    var lastData = null, lastAt = null;
    function applyStatics() {
      var els = document.querySelectorAll('[data-i18n]');
      for (var i=0;i<els.length;i++) els[i].textContent = t(els[i].getAttribute('data-i18n'));
      var btns = document.querySelectorAll('.lang-btn');
      for (var j=0;j<btns.length;j++) btns[j].classList.toggle('on', btns[j].getAttribute('data-lang')===lang);
      document.documentElement.lang = lang;
    }
    function setStatus() {
      var el = document.getElementById('status');
      if (!lastAt) { el.textContent = t('connecting'); return; }
      el.textContent = t('updated') + ' ' + lastAt.toLocaleTimeString(loc(),{hour12:false}) + ' \\u00b7 ' + t('poll');
    }
    function render(d) {
      lastData = d;
      renderKpis(d);
      buildChart(d.evaluations || []);
      renderDeps(d.deployments || []);
      renderDecs(d.decisions || [], d.pr_drafts || []);
      setStatus();
    }
    function setLang(l) {
      if (l===lang) return;
      lang = l; localStorage.setItem('aolang', l);
      applyStatics();
      if (lastData) render(lastData); else setStatus();
    }
    var lbs = document.querySelectorAll('.lang-btn');
    for (var b=0;b<lbs.length;b++) (function(btn){ btn.addEventListener('click', function(){ setLang(btn.getAttribute('data-lang')); }); })(lbs[b]);

    function refresh() {
      fetch('/dashboard/data').then(function(r){ return r.json(); }).then(function(d){
        lastAt = new Date();
        render(d);
        document.getElementById('dot').style.background = '#35d6a0';
      }).catch(function(err){
        document.getElementById('status').textContent = t('error') + err;
        document.getElementById('dot').style.background = '#fb7185';
      });
    }
    applyStatics();
    setStatus();
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
