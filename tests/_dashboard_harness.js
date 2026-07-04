// Node harness for tests/test_dashboard_render.py.
//
// Loads the dashboard's inline JS (path in argv[2]) under a minimal DOM shim
// with a representative dataset that forces the non-empty render path (chart +
// deployments + decisions), then inspects the resulting element state.
//
// The dashboard catches its own render errors into the #status element, so an
// uncaught error never surfaces as a rejection — we assert on #status and the
// panel contents instead. Exit 0 = healthy, non-zero = regression.
const fs = require('fs');
const js = fs.readFileSync(process.argv[2], 'utf8');

const DATA = {
  evaluations: [
    {finishedAt: '2026-06-26T04:05:00Z', startedAt: '2026-06-26T04:05:00Z', state: 'succeeded',
     scores: [{axis: 'drift', score: 0.68}, {axis: 'trajectory', score: 0.66},
              {axis: 'cost', score: 0.0038}, {axis: 'latency', score: 920}]},
    {finishedAt: '2026-06-26T03:05:00Z', startedAt: '2026-06-26T03:05:00Z', state: 'succeeded',
     scores: [{axis: 'drift', score: 0.72}, {axis: 'trajectory', score: 0.74},
              {axis: 'cost', score: 0.0031}, {axis: 'latency', score: 690}]},
    {finishedAt: '2026-06-26T02:05:00Z', startedAt: '2026-06-26T02:05:00Z', state: 'succeeded',
     scores: [{axis: 'drift', score: 0.87}, {axis: 'trajectory', score: 0.88},
              {axis: 'cost', score: 0.0021}, {axis: 'latency', score: 410}]},
    {finishedAt: '2026-06-26T01:05:00Z', startedAt: '2026-06-26T01:05:00Z', state: 'succeeded',
     scores: [{axis: 'drift', score: 0.91}, {axis: 'trajectory', score: 0.93},
              {axis: 'cost', score: 0.0018}, {axis: 'latency', score: 360}]}
  ],
  deployments: [
    {deploymentId: 'dep-canary-001', versionId: 'v2', state: 'canary', currentTrafficPercent: 50}
  ],
  decisions: [
    {action: 'rollback', judgedBy: 'gemini', deploymentId: 'dep-canary-001',
     decidedAt: '2026-06-26T09:06:00Z', prDraftId: 'pr-1',
     signal: {drift_drop: 0.21, trajectory_drop: 0.18, canary_latency_ms: 920},
     rationale: 'drift dropped'}
  ],
  pr_drafts: [{prDraftId: 'pr-1', title: 'fix', prUrl: ''}],
  agents: [
    {agentId: 'agent-demo-001', name: 'demo-managed-agent', runtime: 'adk-cloud-run',
     createdAt: '2026-06-25T00:00:00Z', versionCount: 1,
     latestVersion: {versionId: 'v1', gitCommit: 'aabbccd', createdAt: '2026-06-25T01:00:00Z'},
     lastActivityAt: '2026-06-25T01:00:00Z', metricSampleCount: 0},
    {agentId: 'agent-mktg-001', name: 'marketing-shorts-agent', runtime: 'adk-cloud-run',
     createdAt: '2026-06-26T00:00:00Z', versionCount: 2,
     latestVersion: {versionId: 'v2', gitCommit: 'deadbeef1234', createdAt: '2026-06-26T08:00:00Z'},
     lastActivityAt: '2026-06-26T08:00:00Z', metricSampleCount: 3}
  ]
};

function makeEl() {
  return {
    textContent: '', innerHTML: '', style: {}, _attrs: {},
    classList: {toggle() {}, add() {}, remove() {}},
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return this._attrs[k]; },
    addEventListener() {}, appendChild() {}
  };
}
const elements = {};
global.document = {
  getElementById(id) { return elements[id] || (elements[id] = makeEl()); },
  querySelectorAll() { return []; },
  documentElement: {},
  addEventListener() {}
};
global.localStorage = {getItem() { return null; }, setItem() {}};
global.navigator = {language: 'en'};
global.window = global;
global.setInterval = function () { return 0; };
global.setTimeout = function () { return 0; };
global.fetch = function () {
  return Promise.resolve({json() { return Promise.resolve(DATA); }});
};

try {
  eval(js);
} catch (e) {
  console.log('SYNC_THROW:' + (e && e.message));
  process.exit(2);
}

// flush the fetch().then(render)[.catch] microtask chain, then inspect
setImmediate(() => { setImmediate(() => {
  const get = (id, f) => (elements[id] ? elements[id][f] : '') || '';
  const status = get('status', 'textContent');
  const chart = get('chart-area', 'innerHTML');
  const decs = get('dec-area', 'innerHTML');
  const deps = get('dep-area', 'innerHTML');
  const agts = get('agent-area', 'innerHTML');
  const fail = [];
  if (/error/i.test(status)) fail.push('status reported error: ' + status);
  if (!chart.includes('class="chart"')) fail.push('chart SVG not rendered');
  if (!deps.includes('class="dep"')) fail.push('deployment rows not rendered');
  if (!decs.includes('class="dec ')) fail.push('decision cards not rendered');
  if (!decs.includes('jb-gemini')) fail.push('judged-by (autonomy) pill not rendered');
  if (!agts.includes('marketing-shorts-agent')) fail.push('agent cards not rendered');
  if (!agts.includes('deadbeef')) fail.push('agent gitCommit not rendered');
  if (fail.length) { console.log('FAIL:' + fail.join(' | ')); process.exit(3); }
  console.log('OK:' + status);
  process.exit(0);
}); });
