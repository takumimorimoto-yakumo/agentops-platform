/**
 * AgentOps Platform — Dashboard JS
 *
 * 設計方針:
 *   - チャート色は getComputedStyle で CSS variables から読む。生 hex 禁止
 *   - i18n (EN/JA) / localStorage / 10秒ポーリング を維持
 *   - /dashboard/data の JSON 契約は一切変更しない
 */
(function () {
  'use strict';

  var DRIFT_TH = 0.75, TRAJ_TH = 0.70;

  // ── CSS variable 読み取りヘルパー ──
  // チャート色など JS で使う値はここで 1 箇所から取得する
  var _style = null;
  function cssVar(name) {
    if (!_style) _style = getComputedStyle(document.documentElement);
    return _style.getPropertyValue(name).trim();
  }

  // ── i18n ──
  var L = {
    en: {
      sub: 'Autonomous Canary Control',
      kEval: 'Evaluations', kCanary: 'Active canaries', kRb: 'Auto rollbacks', kLlm: 'LLM-judged',
      pEval: 'Evaluation Score Timeline', pDep: 'Deployment Status', pDec: 'Meta-agent Decisions', pAgents: 'Managed Agents',
      hScored: 'scored across axes', hAwait: 'awaiting runs', hInprog: 'in progress',
      hNoCanary: 'none in progress', hNoHuman: 'no human in the path', hNoRb: 'none yet',
      empEvalT: 'No evaluations yet', empEvalS: 'scores will plot here once runs land',
      empDepT: 'No deployments yet', empDepS: 'canary deployments will appear here',
      empDecT: 'No decisions yet', empDecS: 'the meta-agent has not acted',
      empAgentT: 'No agents registered', empAgentS: 'use POST /v1/agents to register your first agent',
      lgDrift: 'Drift', lgTraj: 'Trajectory', lgTh: 'Threshold', lgBelow: 'Below threshold',
      tblCap: 'Recent evaluations', thTime: 'Time', thDrift: 'Drift', thTraj: 'Traj',
      thCost: 'Cost', thLat: 'Latency', thState: 'State',
      jb_gemini: 'Judged by Gemini', jb_gemini_failed: 'Gemini failed → fallback',
      jb_heuristic: 'Rule-based', jb_safety_floor: 'Safety floor',
      jb_auto_advance: 'Auto-advance', jb_missing_baseline: 'No baseline → hold',
      chDrift: 'drift Δ', chTraj: 'traj Δ', chCost: 'cost', chP95: 'p95',
      pr: 'improvement PR', ver: 'version',
      updated: 'updated', poll: '10s', error: 'error: ', connecting: 'connecting…',
      bd_rollback: 'rollback', bd_advance: 'advance', bd_hold: 'hold',
      bd_canary: 'canary', bd_pending: 'pending', bd_promoted: 'promoted',
      bd_rolled_back: 'rolled back', bd_failed: 'failed', bd_ok: 'ok', bd_unknown: 'unknown',
      agVers: 'versions', agCommit: 'commit', agLastActive: 'last active',
      agNoVer: 'no version yet', agMetrics: 'metrics',
      agQaPass: 'pass', agQaFail: 'fail', agQaLabel: 'Video QA',
      agQaLatestPass: 'latest: ✅ pass', agQaLatestFail: 'latest: ❌ fail',
      agQaAt: 'at'
    },
    ja: {
      sub: '自律カナリア制御',
      kEval: '評価', kCanary: '稼働カナリア', kRb: '自動ロールバック', kLlm: 'LLM 判断',
      pEval: '評価スコア タイムライン',
      pDep: 'デプロイ状況',
      pDec: 'メタエージェントの判断',
      pAgents: '管理中のエージェント',
      hScored: '各軸で採点', hAwait: '実行待ち', hInprog: '進行中',
      hNoCanary: '進行中なし', hNoHuman: '人手を介さず', hNoRb: 'まだ無し',
      empEvalT: '評価はまだありません',
      empEvalS: '実行されるとここにプロットされます',
      empDepT: 'デプロイはまだありません',
      empDepS: 'カナリアデプロイがここに出ます',
      empDecT: '判断はまだありません',
      empDecS: 'メタエージェントはまだ動いていません',
      empAgentT: 'エージェントが未登録です',
      empAgentS: 'POST /v1/agents でエージェントを登録してください',
      lgDrift: 'ドリフト', lgTraj: 'トラジェクトリ', lgTh: 'しきい値', lgBelow: 'しきい値割れ',
      tblCap: '直近の評価', thTime: '時刻', thDrift: 'ドリフト', thTraj: '軌跡',
      thCost: 'コスト', thLat: 'レイテンシ', thState: '状態',
      jb_gemini: 'Gemini が判断', jb_gemini_failed: 'Gemini 失敗 → フォールバック',
      jb_heuristic: 'ルールベース', jb_safety_floor: '安全床',
      jb_auto_advance: '自動前進', jb_missing_baseline: '基準なし → 保留',
      chDrift: 'ドリフトΔ', chTraj: '軌跡Δ', chCost: 'コスト', chP95: 'p95',
      pr: '改善PR', ver: 'バージョン',
      updated: '更新', poll: '10秒', error: 'エラー: ', connecting: '接続中…',
      bd_rollback: 'ロールバック', bd_advance: '前進', bd_hold: '保留',
      bd_canary: 'カナリア', bd_pending: '保留中', bd_promoted: '昇格',
      bd_rolled_back: 'ロールバック済', bd_failed: '失敗', bd_ok: 'ok', bd_unknown: '不明',
      agVers: 'バージョン数', agCommit: 'コミット', agLastActive: '最終アクティビティ',
      agNoVer: 'バージョンなし', agMetrics: 'メトリクス',
      agQaPass: '合格', agQaFail: '不合格', agQaLabel: 'Video QA',
      agQaLatestPass: '直近: ✅ 合格', agQaLatestFail: '直近: ❌ 不合格',
      agQaAt: '時刻'
    }
  };

  var lang = localStorage.getItem('aolang') || (((navigator.language || '').slice(0, 2) === 'ja') ? 'ja' : 'en');
  function t(k) { var v = (L[lang] || {})[k]; if (v === undefined) v = L.en[k]; return v === undefined ? k : v; }
  function loc() { return lang === 'ja' ? 'ja-JP' : 'en-GB'; }
  function countLabel(n, kind) {
    if (lang === 'ja') return n + ' 件';
    if (kind === 'dep') return n + ' total';
    if (kind === 'dec') return n + ' decision' + (n !== 1 ? 's' : '');
    if (kind === 'agent') return n + ' agent' + (n !== 1 ? 's' : '');
    return n + ' eval' + (n !== 1 ? 's' : '');
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function badge(cls, key) {
    return '<span class="badge b-' + cls + '">' + esc(t('bd_' + key) || key) + '</span>';
  }
  function fmtTime(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    return d.toLocaleString('en-GB', { hour12: false, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  function fmtHM(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleTimeString('en-GB', { hour12: false, hour: '2-digit', minute: '2-digit' });
  }
  function scoresOf(e) {
    var s = {};
    (e.scores || []).forEach(function (x) { s[x.axis] = x.score; });
    return s;
  }

  function emptyState(title, sub) {
    return '<div class="empty">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' +
      '<circle cx="12" cy="12" r="9"/><path d="M9 10h.01M15 10h.01M9 15c.8-.7 1.9-1 3-1s2.2.3 3 1"/></svg>' +
      '<div class="empty-t">' + esc(title) + '</div>' +
      '<div class="empty-s">' + esc(sub) + '</div></div>';
  }

  // ── Judged-by pill ──
  var BRAIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 0 3 3 0 0 0 2-5 3 3 0 0 0-2-5 3 3 0 0 0-3-3Z"/><path d="M12 5v14"/></svg>';
  var GEAR  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/></svg>';
  var SHIELD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 4 5v6c0 5 3.5 8 8 11 4.5-3 8-6 8-11V5l-8-3Z"/></svg>';

  function judgedBy(jb) {
    var iconMap = { gemini: BRAIN, gemini_failed: BRAIN, heuristic: GEAR, safety_floor: SHIELD, auto_advance: GEAR, missing_baseline: SHIELD };
    var key = jb || 'heuristic';
    return '<span class="jb jb-' + esc(key) + '" title="' + esc(key) + '">' + (iconMap[key] || GEAR) + esc(t('jb_' + key) || key) + '</span>';
  }

  // ── KPIs ──
  function renderKpis(d) {
    var evals = d.evaluations || [], deps = d.deployments || [], decs = d.decisions || [];
    var active = deps.filter(function (x) { return x.state === 'canary' || x.state === 'pending'; }).length;
    var rb = decs.filter(function (x) { return x.action === 'rollback'; }).length;
    var llm = decs.filter(function (x) { return x.judgedBy === 'gemini'; }).length;
    function set(id, v) { var el = document.getElementById(id); if (el) el.textContent = v; }
    set('k-evals', evals.length); set('k-canary', active); set('k-rb', rb); set('k-llm', llm);
    set('k-evals-h', evals.length ? t('hScored') : t('hAwait'));
    set('k-canary-h', active ? t('hInprog') : t('hNoCanary'));
    set('k-rb-h', rb ? t('hNoHuman') : t('hNoRb'));
    set('k-llm-h', (lang === 'ja')
      ? (decs.length + ' 件中 ' + llm + ' 件を LLM が判断')
      : ((llm ? llm + ' of ' : '') + decs.length + ' decisions by LLM'));
  }

  // ── Chart (SVG) ──
  // 色は CSS variable から getComputedStyle 経由で読む
  function buildChart(evals) {
    var area = document.getElementById('chart-area');
    var cEval = document.getElementById('c-eval');
    if (cEval) cEval.textContent = countLabel(evals.length, 'eval');
    if (!evals.length) { area.innerHTML = emptyState(t('empEvalT'), t('empEvalS')); return; }

    // CSS variables から色を読む (生 hex 禁止)
    var COL_PRIMARY = cssVar('--color-primary') || '#2A7CF6';
    var COL_UP      = cssVar('--color-up')      || '#1FA060';
    var COL_DANGER  = cssVar('--color-danger')   || '#E84040';
    var COL_BORDER  = cssVar('--border-line')    || 'rgba(255,255,255,0.09)';
    var COL_LOW     = cssVar('--text-low')        || 'rgba(255,255,255,0.34)';

    var items = evals.slice().reverse();  // oldest-first
    var n = Math.min(items.length, 30);
    items = items.slice(items.length - n);
    var drift = items.map(function (e) { var s = scoresOf(e); return s.drift !== undefined ? s.drift : null; });
    var traj  = items.map(function (e) { var s = scoresOf(e); return s.trajectory !== undefined ? s.trajectory : null; });

    var W = 600, H = 200, pl = 30, pr = 10, pt = 12, pb = 24, cw = W - pl - pr, ch = H - pt - pb;
    var baseY = pt + ch;
    function X(i) { return pl + (n <= 1 ? cw / 2 : i / (n - 1) * cw); }
    function Y(v) { return pt + (1 - v) * ch; }

    var s = '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" aria-label="Drift and trajectory scores over time">';
    s += '<defs>';
    // Gradient fill areas — use CSS variable values
    s += '<linearGradient id="gDrift" x1="0" y1="0" x2="0" y2="1">';
    s += '<stop offset="0" stop-color="' + COL_PRIMARY + '" stop-opacity="0.2"/>';
    s += '<stop offset="1" stop-color="' + COL_PRIMARY + '" stop-opacity="0"/></linearGradient>';
    s += '<linearGradient id="gTraj" x1="0" y1="0" x2="0" y2="1">';
    s += '<stop offset="0" stop-color="' + COL_UP + '" stop-opacity="0.14"/>';
    s += '<stop offset="1" stop-color="' + COL_UP + '" stop-opacity="0"/></linearGradient>';
    s += '</defs>';

    // Below-threshold band
    var bandTop = Y(Math.min(DRIFT_TH, TRAJ_TH));
    s += '<rect x="' + pl + '" y="' + bandTop + '" width="' + cw + '" height="' + (baseY - bandTop) + '" fill="' + COL_DANGER + '" fill-opacity="0.05"/>';

    // Grid lines (faint)
    [0, 0.25, 0.5, 0.75, 1.0].forEach(function (v) {
      var y = Y(v);
      s += '<line x1="' + pl + '" y1="' + y + '" x2="' + (W - pr) + '" y2="' + y + '" stroke="' + COL_BORDER + '" stroke-width="1" stroke-dasharray="2 5"/>';
      s += '<text x="' + (pl - 4) + '" y="' + (y + 3) + '" font-size="7" fill="' + COL_LOW + '" text-anchor="end" font-family="monospace">' + v.toFixed(2) + '</text>';
    });

    // Threshold reference lines
    s += '<line x1="' + pl + '" y1="' + Y(DRIFT_TH) + '" x2="' + (W - pr) + '" y2="' + Y(DRIFT_TH) + '" stroke="' + COL_PRIMARY + '" stroke-width="1" stroke-dasharray="4 4" opacity="0.35"/>';
    s += '<line x1="' + pl + '" y1="' + Y(TRAJ_TH)  + '" x2="' + (W - pr) + '" y2="' + Y(TRAJ_TH)  + '" stroke="' + COL_UP + '" stroke-width="1" stroke-dasharray="4 4" opacity="0.35"/>';

    // Catmull-Rom smooth path
    function pts(series) {
      var p = [];
      series.forEach(function (v, i) { if (v !== null) p.push([X(i), Y(v)]); });
      return p;
    }
    function smooth(p) {
      if (!p.length) return '';
      if (p.length === 1) return 'M' + p[0][0] + ',' + p[0][1];
      var d = 'M' + p[0][0] + ',' + p[0][1];
      for (var i = 0; i < p.length - 1; i++) {
        var p0 = p[i - 1] || p[i], p1 = p[i], p2 = p[i + 1], p3 = p[i + 2] || p2;
        var c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
        var c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
        d += 'C' + c1x.toFixed(1) + ',' + c1y.toFixed(1) + ' ' + c2x.toFixed(1) + ',' + c2y.toFixed(1) + ' ' + p2[0].toFixed(1) + ',' + p2[1].toFixed(1);
      }
      return d;
    }
    var dP = pts(drift), tP = pts(traj), dL = smooth(dP), tL = smooth(tP);

    if (dP.length) s += '<path d="' + dL + ' L' + dP[dP.length - 1][0].toFixed(1) + ',' + baseY + ' L' + dP[0][0].toFixed(1) + ',' + baseY + ' Z" fill="url(#gDrift)" stroke="none"/>';
    if (dL) s += '<path d="' + dL + '" fill="none" stroke="' + COL_PRIMARY + '" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>';
    if (tL) s += '<path d="' + tL + '" fill="none" stroke="' + COL_UP + '" stroke-width="1.8" stroke-dasharray="5 4" stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>';

    function dots(series, th, color) {
      series.forEach(function (v, i) {
        if (v === null) return;
        var low = v < th;
        var ts = fmtHM(items[i].finishedAt || items[i].startedAt);
        if (low) s += '<circle cx="' + X(i) + '" cy="' + Y(v) + '" r="6" fill="none" stroke="' + COL_DANGER + '" stroke-width="1" opacity="0.4"/>';
        s += '<circle cx="' + X(i) + '" cy="' + Y(v) + '" r="' + (low ? 3.2 : 2.5) + '" fill="' + (low ? COL_DANGER : color) + '" stroke="' + cssVar('--paper') + '" stroke-width="1.4">' +
             '<title>' + esc(v.toFixed(3)) + (ts ? ' @ ' + esc(ts) : '') + (low ? ' (below threshold)' : '') + '</title></circle>';
      });
    }
    dots(drift, DRIFT_TH, COL_PRIMARY);
    dots(traj, TRAJ_TH, COL_UP);

    // X-axis time labels
    var step = Math.max(1, Math.floor(n / 5));
    for (var i = 0; i < n; i += step) {
      var tick = fmtHM(items[i].finishedAt || items[i].startedAt);
      if (tick) s += '<text x="' + X(i) + '" y="' + (H - 4) + '" font-size="7" fill="' + COL_LOW + '" text-anchor="middle" font-family="monospace">' + esc(tick) + '</text>';
    }
    s += '</svg>';

    // Legend
    s += '<div class="legend">' +
      '<span class="lg" style="color:' + COL_PRIMARY + '"><span class="lg-line"></span>' + esc(t('lgDrift')) + '</span>' +
      '<span class="lg" style="color:' + COL_UP + '"><span class="lg-line dash"></span>' + esc(t('lgTraj')) + '</span>' +
      '<span class="lg" style="color:' + COL_LOW + '"><span class="lg-line dash"></span>' + esc(t('lgTh')) + '</span>' +
      '<span class="lg" style="color:' + COL_DANGER + '"><span class="lg-dot"></span>' + esc(t('lgBelow')) + '</span>' +
    '</div>';

    // Recent table
    var recent = items.slice(-6).reverse();
    s += '<div class="tbl-wrap"><div class="tbl-cap">' + esc(t('tblCap')) + '</div><table><thead><tr>';
    [t('thTime'), t('thDrift'), t('thTraj'), t('thCost'), t('thLat'), t('thState')].forEach(function (h) { s += '<th>' + esc(h) + '</th>'; });
    s += '</tr></thead><tbody>';
    recent.forEach(function (e) {
      var c = scoresOf(e);
      var dl = c.drift !== undefined && c.drift < DRIFT_TH;
      var tl = c.trajectory !== undefined && c.trajectory < TRAJ_TH;
      s += '<tr>' +
        '<td>' + fmtTime(e.finishedAt || e.startedAt) + '</td>' +
        '<td class="' + (dl ? 'c-bad' : 'c-primary') + '">' + (c.drift !== undefined ? c.drift.toFixed(3) : '—') + '</td>' +
        '<td class="' + (tl ? 'c-bad' : 'c-up') + '">' + (c.trajectory !== undefined ? c.trajectory.toFixed(3) : '—') + '</td>' +
        '<td class="c-amber">' + (c.cost !== undefined ? c.cost.toFixed(4) : '—') + '</td>' +
        '<td class="c-violet">' + (c.latency !== undefined ? c.latency.toFixed(0) + ' ms' : '—') + '</td>' +
        '<td>' + (e.state === 'succeeded' ? badge('ok', 'ok') : badge('failed', e.state || '?')) + '</td>' +
      '</tr>';
    });
    s += '</tbody></table></div>';
    area.innerHTML = s;
  }

  // ── Deployments ──
  function renderDeps(deps) {
    var area = document.getElementById('dep-area');
    var cDep = document.getElementById('c-dep');
    if (cDep) cDep.textContent = countLabel(deps.length, 'dep');
    if (!deps.length) { area.innerHTML = emptyState(t('empDepT'), t('empDepS')); return; }

    // 色は CSS variable から読む
    var fillVarMap = {
      canary:      '--color-primary',
      pending:     '--text-low',
      promoted:    '--color-up',
      rolled_back: '--color-danger',
      failed:      '--color-danger'
    };

    var order = { canary: 0, pending: 1, promoted: 2, rolled_back: 3, failed: 4 };
    var sorted = deps.slice().sort(function (a, b) { return (order[a.state] || 9) - (order[b.state] || 9); });
    var html = '';
    sorted.forEach(function (d) {
      var pct = d.currentTrafficPercent || 0;
      var varName = fillVarMap[d.state] || '--text-low';
      var col = cssVar(varName) || '#5c6b82';
      html += '<div class="dep">' +
        '<div class="dep-top"><div>' +
        '<div class="dep-id">' + esc((d.deploymentId || '').substring(0, 18)) + '</div>' +
        (d.versionId ? '<div class="dep-ver">' + esc(t('ver')) + ' ' + esc(d.versionId.substring(0, 8)) + '…</div>' : '') +
        '</div>' + badge(d.state, d.state || 'unknown') + '</div>' +
        '<div class="track">' +
        '<div class="bar"><div class="fill" style="width:' + pct + '%;background:' + col + '"></div></div>' +
        '<span class="pct">' + pct + '%</span></div></div>';
    });
    area.innerHTML = html;
  }

  // ── Decisions (meta-agent) ──
  function renderDecs(decs, prs) {
    var area = document.getElementById('dec-area');
    var cDec = document.getElementById('c-dec');
    if (cDec) cDec.textContent = countLabel(decs.length, 'dec');
    if (!decs.length) { area.innerHTML = emptyState(t('empDecT'), t('empDecS')); return; }

    var prMap = {};
    (prs || []).forEach(function (p) { prMap[p.prDraftId] = p; });
    var sorted = decs.slice().sort(function (a, b) { return new Date(b.decidedAt || 0) - new Date(a.decidedAt || 0); });

    var html = '';
    sorted.forEach(function (d) {
      var sig = d.signal || {};
      var pr = d.prDraftId ? prMap[d.prDraftId] : null;
      html += '<div class="dec m-' + esc(d.action) + '">';
      html += '<div class="dec-top">' +
        badge(d.action, d.action) +
        judgedBy(d.judgedBy) +
        '<span class="dec-dep">' + esc((d.deploymentId || '').substring(0, 12)) + '…</span>' +
        '<span class="dec-time">' + fmtTime(d.decidedAt) + '</span></div>';

      var chips = [];
      if (sig.drift_drop) chips.push('<span class="chip' + (sig.drift_drop > 0.05 ? ' warn' : '') + '">' + esc(t('chDrift')) + ' <b>−' + Number(sig.drift_drop).toFixed(3) + '</b></span>');
      if (sig.trajectory_drop) chips.push('<span class="chip' + (sig.trajectory_drop > 0.05 ? ' warn' : '') + '">' + esc(t('chTraj')) + ' <b>−' + Number(sig.trajectory_drop).toFixed(3) + '</b></span>');
      if (sig.cost_increase_ratio) chips.push('<span class="chip">' + esc(t('chCost')) + ' <b>+' + (Number(sig.cost_increase_ratio) * 100).toFixed(1) + '%</b></span>');
      if (sig.canary_latency_ms) chips.push('<span class="chip">' + esc(t('chP95')) + ' <b>' + Number(sig.canary_latency_ms).toFixed(0) + ' ms</b></span>');
      if (chips.length) html += '<div class="chips">' + chips.join('') + '</div>';

      if (d.rationale) html += '<div class="rationale">' + esc(d.rationale) + '</div>';

      if (pr) {
        html += '<div class="pr"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M6 9v6"/><path d="M13 6h5a2 2 0 0 1 2 2v7"/><path d="m16 12-3 3 3 3"/></svg>';
        if (pr.prUrl) html += '<a href="' + esc(pr.prUrl) + '" target="_blank" rel="noopener noreferrer">' + esc(pr.title) + '</a>';
        else html += '<span>' + esc(t('pr')) + ' · <span style="color:var(--text-mid)">' + esc(pr.title) + '</span></span>';
        html += '</div>';
      }
      html += '</div>';
    });
    area.innerHTML = html;
  }

  // ── Agents ──
  function renderAgents(agents) {
    var area = document.getElementById('agent-area');
    var cAgent = document.getElementById('c-agent');
    if (cAgent) cAgent.textContent = countLabel(agents.length, 'agent');
    if (!agents.length) { area.innerHTML = emptyState(t('empAgentT'), t('empAgentS')); return; }

    var html = '';
    agents.forEach(function (a) {
      var lv = a.latestVersion;
      var commit = lv && lv.gitCommit ? lv.gitCommit.substring(0, 8) : null;
      var qa = a.videoQaSummary || {};
      var hasQa = qa.pass_count !== undefined && (qa.pass_count + qa.fail_count) > 0;

      // ── Video QA サマリ行を組み立てる ──
      var qaHtml = '';
      if (hasQa) {
        var verdictBadge = qa.latest_pass
          ? '<span class="badge b-ok">' + esc(t('agQaLatestPass')) + '</span>'
          : '<span class="badge b-failed">' + esc(t('agQaLatestFail')) + '</span>';
        var timeStr = qa.latest_at ? ' <span style="color:var(--text-low)">' + esc(t('agQaAt')) + ' ' + fmtTime(qa.latest_at) + '</span>' : '';
        var passPart = '<span style="color:var(--color-up)">✅ ' + esc(t('agQaPass')) + ' ' + qa.pass_count + '</span>';
        var failPart = '<span style="color:var(--color-danger)">❌ ' + esc(t('agQaFail')) + ' ' + qa.fail_count + '</span>';
        qaHtml += '<div style="margin-top:var(--sp-2);display:flex;align-items:center;flex-wrap:wrap;gap:var(--sp-2)">';
        qaHtml += '<span style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--text-mid)">' + esc(t('agQaLabel')) + ':</span>';
        qaHtml += passPart + ' / ' + failPart;
        qaHtml += ' ' + verdictBadge + timeStr;
        qaHtml += '</div>';
        if (qa.latest_reason) {
          qaHtml += '<div style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--text-low);margin-top:var(--sp-1);padding-left:var(--sp-2);border-left:2px solid var(--color-danger)">' + esc(qa.latest_reason) + '</div>';
        }
      }

      html += '<div class="dep">' +
        '<div class="dep-top"><div>' +
        '<div class="dep-id">' + esc(a.name) + '</div>' +
        '<div class="dep-ver">' +
        a.versionCount + ' ' + esc(t('agVers')) +
        (commit ? ' · ' + esc(t('agCommit')) + ' <span style="color:var(--text-high)">' + esc(commit) + '</span>' : '') +
        '</div></div>' +
        '<div style="display:flex;align-items:center;gap:var(--sp-2)">' +
        '<span class="badge b-canary">' + esc(a.runtime) + '</span>' +
        '<span style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--text-low)">' +
        esc(t('agLastActive')) + ' ' + fmtTime(a.lastActivityAt) +
        '</span></div></div>' +
        (a.metricSampleCount ? '<div style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--text-low);margin-top:var(--sp-2)">' + esc(t('agMetrics')) + ': ' + a.metricSampleCount + '</div>' : '') +
        qaHtml +
        '</div>';
    });
    area.innerHTML = html;
  }

  // ── State / polling ──
  var lastData = null, lastAt = null;

  function applyStatics() {
    var els = document.querySelectorAll('[data-i18n]');
    for (var i = 0; i < els.length; i++) els[i].textContent = t(els[i].getAttribute('data-i18n'));
    var btns = document.querySelectorAll('.lang-btn');
    for (var j = 0; j < btns.length; j++) btns[j].classList.toggle('on', btns[j].getAttribute('data-lang') === lang);
    document.documentElement.lang = lang;
  }

  function setStatus() {
    var el = document.getElementById('status');
    if (!el) return;
    if (!lastAt) { el.textContent = t('connecting'); return; }
    el.textContent = t('updated') + ' ' + lastAt.toLocaleTimeString(loc(), { hour12: false }) + ' · ' + t('poll');
  }

  function render(d) {
    lastData = d;
    renderKpis(d);
    buildChart(d.evaluations || []);
    renderDeps(d.deployments || []);
    renderDecs(d.decisions || [], d.pr_drafts || []);
    renderAgents(d.agents || []);
    setStatus();
  }

  function setLang(l) {
    if (l === lang) return;
    lang = l;
    localStorage.setItem('aolang', l);
    applyStatics();
    if (lastData) render(lastData);
    else setStatus();
  }

  var lbs = document.querySelectorAll('.lang-btn');
  for (var b = 0; b < lbs.length; b++) {
    (function (btn) {
      btn.addEventListener('click', function () { setLang(btn.getAttribute('data-lang')); });
    })(lbs[b]);
  }

  function refresh() {
    fetch('/dashboard/data')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        lastAt = new Date();
        render(d);
        var dot = document.getElementById('dot');
        if (dot) dot.style.background = cssVar('--color-up');
      })
      .catch(function (err) {
        var el = document.getElementById('status');
        if (el) el.textContent = t('error') + err;
        var dot = document.getElementById('dot');
        if (dot) dot.style.background = cssVar('--color-danger');
      });
  }

  applyStatics();
  setStatus();
  refresh();
  setInterval(refresh, 10000);
})();
