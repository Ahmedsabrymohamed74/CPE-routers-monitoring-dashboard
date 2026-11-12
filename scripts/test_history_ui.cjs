const nodeTest = require('node:test');
// Node 16's test context predates t.after; close each DOM even on assertion failure.
function test(name, body) {
  return nodeTest(name, async () => {
    const cleanup = [];
    try { await body({after: fn => cleanup.push(fn)}); }
    finally { cleanup.forEach(fn => fn()); }
  });
}
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const html = fs.readFileSync(path.join(__dirname, '../resources/templates/index.html'), 'utf8');
const inline = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(match => match[1]).join('\n');
// Register real event handlers, but control initialization and network completions in tests.
const script = inline.slice(0, inline.lastIndexOf('\n    applyTheme(currentTheme);'))
  .replace(/\{\{[\s\S]*?\}\}/g, '"test"');
const tick = () => new Promise(resolve => setImmediate(resolve));
const groups = [
  {cell_id: '100', pci: '0', enodeb_id: '10', tac: '20', sample_count: 2, avg_rsrp: -80, first_seen: '2026-01-01T00:00:00Z'},
  {cell_id: '100', pci: '0', enodeb_id: '11', tac: '21', sample_count: 1, avg_rsrp: -90},
  {cell_id: '200', pci: '', enodeb_id: '', tac: '', sample_count: 1},
];
function setup(t) {
  const dom = new JSDOM(html, {url: 'https://dashboard.example/', runScripts: 'outside-only'});
  const w = dom.window;
  const requests = [];
  w.CPETheme = {current: {mode: 'dark'}};
  w.AbortController = AbortController;
  w.Request = class Request {};
  w.fetch = (url, options) => new Promise((resolve, reject) => requests.push({url, options, reject,
    complete(payload, status = 200) {resolve({status, ok: status < 400, json: async () => payload});}}));
  w.HTMLCanvasElement.prototype.getContext = () => new Proxy({measureText: () => ({width: 20})}, {
    get(target, key) {return key in target ? target[key] : () => {};},
  });
  w.HTMLElement.prototype.getBoundingClientRect = () => ({left: 0, top: 0, width: 640, height: 220});
  w.eval(script + `\nwindow.historyTest = {
    refreshHistory, refreshData, showChartTooltip, historyMetrics, lanMetrics, renderSavedProfileOptions,
    setGroups(groups) {currentCellGroups = groups; renderCellFilter(groups); renderCellGroups(groups);},
    setVendor(vendor) {selectedRouterVendor = vendor; selectedHistoryCellId = ''; selectedHistoryProfile = '';},
    setSaved(profiles) {savedCellProfiles = profiles; renderSavedProfileOptions();},
    get items() {return currentHistoryItems;}
  };`);
  const api = w.historyTest;
  const el = id => w.document.getElementById(id);
  const change = (id, value) => {el(id).value = value; el(id).dispatchEvent(new w.Event('change'));};
  api.setGroups(groups);
  t.after(() => dom.window.close());
  return {w, api, el, change, requests};
}

test('Cell ID choices are unique; sector selection sends exact PCI, eNodeB and TAC; all sectors removes filters', async t => {
  const {api, el, change, requests} = setup(t);
  assert.deepEqual([...el('historyCellFilter').options].map(o => o.value), ['', '100', '200']);
  change('historyCellFilter', '100');
  assert.equal(requests.length, 1);
  assert.equal(el('historyProfileFilter').options.length, 3);
  requests[0].complete({items: [], cell_groups: groups}); await tick();
  const key = JSON.stringify(['0', '10', '20']);
  change('historyProfileFilter', key);
  const params = new URL(requests[1].url, 'https://test/').searchParams;
  assert.equal(params.get('cell_id'), '100'); assert.equal(params.get('pci'), '0');
  assert.equal(params.get('enodeb_id'), '10'); assert.equal(params.get('tac'), '20');
  assert.match(el('historyProfileDetails').textContent, /eNodeB 10.*TAC 20/);
  requests[1].complete({items: [], cell_groups: []}); await tick();
  assert.equal(el('historyCellFilter').value, '100');
  assert.equal(el('historyProfileFilter').value, key);
  assert.match(el('historyProfileDetails').textContent, /No samples/);
  change('historyProfileFilter', '');
  const all = new URL(requests[2].url, 'https://test/').searchParams;
  assert.equal(all.get('cell_id'), '100');
  for (const key of ['pci', 'enodeb_id', 'tac']) assert.equal(all.has(key), false);
  requests[2].complete({items: [], cell_groups: groups}); await tick();
});

test('loading never blocks range, cell, sector or group selection; only newest selection renders', async t => {
  const {api, el, change, requests, w} = setup(t);
  const first = api.refreshHistory(); const duplicate = api.refreshHistory();
  assert.equal(requests.length, 1);
  assert.equal(el('historyLoading').hidden, false);
  for (const id of ['historyRangeSelect', 'historyCellFilter', 'historyStart', 'historyEnd', 'historyApplyCustom']) {
    assert.equal(el(id).disabled, false, id);
  }
  w.document.querySelector('#cellGroupList .cell-group-sort').click();
  for (const button of w.document.querySelectorAll('#cellGroupList button[data-cell-id]')) assert.equal(button.disabled, false);
  change('historyRangeSelect', '360');
  assert.equal(requests.length, 2);
  assert.equal(requests[0].options.signal.aborted, true);
  change('historyCellFilter', '100');
  assert.equal(requests.length, 3);
  assert.equal(el('historyProfileFilter').disabled, false);
  change('historyProfileFilter', JSON.stringify(['0', '10', '20']));
  assert.equal(requests.length, 4);
  assert.equal(requests[2].options.signal.aborted, true);
  requests[3].complete({items: [{...groups[0], time: '2026-01-01T00:00:00Z', rsrp: -80}], cell_groups: groups});
  await tick();
  for (const old of requests.slice(0, 3)) old.complete({items: [{cell_id: 'stale'}], cell_groups: []});
  await Promise.all([first, duplicate]); await tick();
  assert.equal(api.items[0].cell_id, '100');
  assert.equal(el('historyLoading').hidden, true);
  assert.equal(el('historyRangeSelect').value, '360');
  assert.equal(el('historyCellFilter').value, '100');
  assert.equal(el('historyProfileFilter').value, JSON.stringify(['0', '10', '20']));
});

test('request failure unlocks controls, escapes the error, and permits retry', async t => {
  const {api, el, requests} = setup(t);
  const first = api.refreshHistory();
  requests[0].complete({message: '<img src=x onerror=alert(1)>'}, 500); await first;
  assert.equal(el('historyLoading').hidden, true);
  assert.equal(el('historyCellFilter').disabled, false);
  assert.equal(el('historyRows').querySelector('img'), null);
  const retry = api.refreshHistory(); assert.equal(requests.length, 2);
  requests[1].complete({items: [], cell_groups: groups}); await retry;
  assert.equal(el('historyRangeStatus').classList.contains('error'), false);
});

test('stale router response cannot replace new data or unlock a newer request', async t => {
  const {api, el, requests} = setup(t);
  const old = api.refreshHistory();
  api.setVendor('zte'); const current = api.refreshHistory();
  assert.equal(requests.length, 2);
  assert.equal(requests[0].options.signal.aborted, true);
  // Simulate a response that finished despite abort, before the replacement completes.
  requests[0].complete({items: [{cell_id: 'stale'}], cell_groups: []}); await old;
  assert.equal(api.items.length, 0);
  assert.equal(el('historyLoading').hidden, false);
  requests[1].complete({items: [{cell_id: 'fresh'}], cell_groups: []}); await current;
  assert.equal(api.items[0].cell_id, 'fresh');
  assert.equal(el('historyLoading').hidden, true);
});

test('cellular tooltips put escaped profile first; LAN tooltips stay unchanged', t => {
  const {api, el} = setup(t);
  const items = [{...groups[0], cell_tag: '<img src=x>', time: '2026-01-01T00:00:00Z', rsrp: -80, rsrq: -9, sinr: 12}];
  for (const metric of api.historyMetrics) {
    api.showChartTooltip(metric, 0, {clientX: 10, clientY: 10}, items);
    const tooltip = el(metric.tooltipId);
    assert.match(tooltip.querySelector('strong').textContent, /^Cell\/Tower Profile:/);
    assert.match(tooltip.textContent, /PCI 0.*eNodeB 10.*TAC 20/);
    assert.equal(tooltip.querySelector('img'), null);
  }
  const lan = api.lanMetrics[0];
  api.showChartTooltip(lan, 0, {clientX: 10, clientY: 10}, [{time: items[0].time, latency_ms: 20}]);
  assert.doesNotMatch(el(lan.tooltipId).textContent, /Cell\/Tower Profile/);
});

test('saved profile browser cascades by cell and shows eNodeB/TAC metadata', t => {
  const {api, el, change} = setup(t);
  api.setSaved([
    {cell_id: '100', pci: '0', profile_enodeb_id: '10', profile_tac: '20'},
    {cell_id: '100', pci: '1', profile_enodeb_id: '10', profile_tac: '20'},
    {cell_id: '200', pci: '2', profile_enodeb_id: '11', profile_tac: '21'},
  ]);
  assert.equal(el('savedProfileCellSelect').options.length, 2);
  assert.equal(el('savedProfileSelect').options.length, 2);
  change('savedProfileCellSelect', '200');
  assert.equal(el('savedProfileSelect').options.length, 1);
  assert.equal(el('savedProfileSelect').value, '2');
  assert.match(el('savedProfileMeta').textContent, /Cell 200.*PCI 2.*eNodeB 11.*TAC 21/);
});

test('live metric polling refreshes history quietly at most once every 30 seconds', async t => {
  const {api, el, requests, w} = setup(t);
  let clock = 100000;
  w.Date.now = () => clock;
  const initial = api.refreshHistory();
  requests[0].complete({items: [], cell_groups: groups}); await initial;
  const live = api.refreshData();
  requests[1].complete({rsrp: -80, cell_id: '100'}); await live;
  assert.equal(requests.length, 2); // Live update did not trigger another history fetch.
  clock += 30000;
  const nextLive = api.refreshData();
  requests[2].complete({rsrp: -79, cell_id: '100'}); await nextLive;
  assert.equal(requests.length, 4);
  assert.match(requests[3].url, /api\/history/);
  assert.equal(el('historyLoading').hidden, true);
  assert.equal(el('historyRangeSelect').disabled, false);
  await api.refreshHistory({background: true});
  assert.equal(requests.length, 4); // A pending background request is never duplicated.
  const option = el('historyCellFilter').options[1];
  requests[3].complete({items: [], cell_groups: groups}); await tick();
  assert.equal(el('historyCellFilter').options[1], option); // Refresh does not rebuild an unchanged menu.
});

test('a stalled request times out, clears loading, and permits an immediate filter change', async t => {
  const {api, el, change, requests, w} = setup(t);
  const timers = [];
  const realSetTimeout = w.setTimeout.bind(w);
  w.setTimeout = (callback, delay) => {
    if (delay === 15000) {timers.push(callback); return 10000 + timers.length;}
    return realSetTimeout(callback, delay);
  };
  const pending = api.refreshHistory();
  assert.equal(timers.length, 1);
  timers[0](); await pending;
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(el('historyLoading').hidden, true);
  assert.match(el('historyRows').textContent, /timed out/);
  assert.equal(el('historyRangeSelect').disabled, false);
  change('historyRangeSelect', '360');
  assert.equal(requests.length, 2);
  requests[1].complete({items: [], cell_groups: groups}); await tick();
  assert.equal(el('historyRangeStatus').classList.contains('error'), false);
});
