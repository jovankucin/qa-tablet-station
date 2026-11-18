// history_tests_harness.js: runs static/history.js in JavaScriptCore's jsc with a fake DOM and a fake window.QA, and
// checks History's first card, 'Your tests' (Ongoing · Tested · Done over GET /api/results/tests, 2026-11-04;
// tests/2026-11-04-results-redesign/SPEC.md screen 5, API.md section 4): the rows and their links, the next press,
// how a test in Done ended, what an ongoing test waits for, and the 'n more' and 'Show all' labels. Since 2026-11-05
// (check 5): the charts' words for ERROR runs (test errors, not "not decided"), over a small GET /api/stats.
// Run by test_history_tests.py:
//   jsc history_tests_harness.js -- <path of history.js>
// Prints one JSON line {"failures": [...], "checks": n}. Nothing here touches a server, ClickUp or a device.
/* global arguments, load, print, drainMicrotasks */
'use strict';

var window = globalThis;
const failures = [];
let checks = 0;

function check(cond, what) {
  checks++;
  if (!cond) failures.push(what);
}

function eq(got, want, what) {
  check(JSON.stringify(got) === JSON.stringify(want), what + ': want ' + JSON.stringify(want) + ', got ' +
    JSON.stringify(got));
}

// ---- fake DOM ----

class Node {
  constructor() { this.parentNode = null; this.childNodes = []; }
  get firstChild() { return this.childNodes[0] || null; }
  appendChild(n) {
    if (n.parentNode) n.parentNode.removeChild(n);
    this.childNodes.push(n);
    n.parentNode = this;
    return n;
  }
  removeChild(n) {
    const i = this.childNodes.indexOf(n);
    if (i < 0) throw new Error('removeChild: not a child');
    this.childNodes.splice(i, 1);
    n.parentNode = null;
    return n;
  }
  get textContent() { return this.childNodes.map(function (k) { return k.textContent; }).join(''); }
}

class Text extends Node {
  constructor(t) { super(); this.data = String(t); }
  get textContent() { return this.data; }
}

class Element extends Node {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this.attrs = {};
    this.className = '';
    this.listeners = {};
    this.hidden = false;
    this.style = {};
  }
  setAttribute(k, v) { if (k === 'class') this.className = String(v); else this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  click() { for (const fn of this.listeners.click || []) fn({target: this}); }
  set textContent(t) {
    this.childNodes.slice().forEach(function (k) { this.removeChild(k); }, this);
    this.appendChild(new Text(t));
  }
  get textContent() { return super.textContent; }
  has(cls) { return this.className.split(/\s+/).indexOf(cls) >= 0; }
  all(cls) {
    const out = [];
    (function walk(n) {
      for (const k of n.childNodes) {
        if (!(k instanceof Element)) continue;
        if (k.has(cls)) out.push(k);
        walk(k);
      }
    })(this);
    return out;
  }
}

const document = {
  body: new Element('body'),
  documentElement: {clientWidth: 1200},
  createElement: function (tag) { return new Element(tag); },
  createElementNS: function (ns, tag) { return new Element(tag); },
  createTextNode: function (t) { return new Text(t); },
};
window.document = document;
window.Node = Node;
window.innerWidth = 1200;
window.innerHeight = 800;
window.addEventListener = function () {};
window.removeEventListener = function () {};

function flush() {
  for (let i = 0; i < 40; i++) drainMicrotasks();
}

// ---- fake window.QA (the app.js helpers history.js uses) ----

function add(parent, kids) {
  for (const k of kids) {
    if (k === null || k === undefined || k === false) continue;
    if (Array.isArray(k)) add(parent, k);
    else if (k instanceof Node) parent.appendChild(k);
    else parent.appendChild(document.createTextNode(String(k)));
  }
  return parent;
}

function el(tag, props) {
  const n = document.createElement(tag);
  add(n, Array.prototype.slice.call(arguments, 2));
  for (const k of Object.keys(props || {})) {
    const v = props[k];
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
    else if (k === 'hidden') n.hidden = Boolean(v);
    else if (k.slice(0, 2) === 'on' && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : String(v));
  }
  return n;
}

function put(n) {
  n.childNodes.slice().forEach(function (k) { n.removeChild(k); });
  return add(n, Array.prototype.slice.call(arguments, 1));
}

function qs(params) {
  const parts = [];
  for (const k of Object.keys(params || {})) {
    if (params[k]) parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
  }
  return parts.length ? '?' + parts.join('&') : '';
}

let answer = null;
let stats = null;  // GET /api/stats, when a check sets it; else the numbers never load
const QA = {
  el: el, add: add, put: put, qs: qs,
  list: function (x) { return Array.isArray(x) ? x : []; },
  loading: function (t) { return el('p', {class: 'loading'}, t); },
  viewHead: function (t) { return el('div', {class: 'view-head'}, el('h1', {}, t)); },
  routeHash: function (name, params) { return '#/' + name + qs(params); },
  chip: function (cls, text) { return el('span', {class: 'chip ' + cls}, text); },
  errorBox: function (e, prefix) { return el('div', {class: 'msg msg-error'}, (prefix ? prefix + ': ' : '') + String(e)); },
  // 'Your tests' answers, and the numbers when a check sets them; the lists below them never load here
  getJSON: function (path) {
    if (path === '/api/results/tests') return Promise.resolve(JSON.parse(JSON.stringify(answer)));
    if (path.indexOf('/api/stats?') === 0 && stats) return Promise.resolve(JSON.parse(JSON.stringify(stats)));
    return new Promise(function () {});
  },
  readBugRegister: function () { return new Promise(function () {}); },
};

load(arguments[0]);

function show(a) {
  answer = a;
  const root = document.body.appendChild(el('main'));
  window.QAHistory.render(root, {get: function () { return null; }}, function () { return true; }, QA);
  flush();
  return root.all('hx-tests')[0] || null;
}

// ---- data (shaped as API.md section 4) ----

function pad(n) { return String(n).padStart(2, '0'); }

function todayAt(h, m) {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m, 5).toISOString();
}

function hm(iso) {
  const d = new Date(iso);
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}

const CU = {task_id: '86c0abc', custom_id: null, url: 'https://app.clickup.com/t/86c0abc',
  title: 'Custom terrain - final tests'};

function row(id, extra) {
  return Object.assign({task_id: id, title: 'Test ' + id, clickup: CU, devices: ['TA5', 'ET65'],
    created: todayAt(9, 0), last_change: todayAt(10, 25), verdict: 'passed', verdict_label: 'Passed',
    results: '1 passed', next: null, stopped_early: false, unreported: 0, done_how: null, running: null}, extra || {});
}

function dev(tag, extra) {
  return Object.assign({tag: tag, state: 'waiting', step: null, of: null, text: null, shot_url: null, updated: null,
    case: null, run: null, verdict: null, part: null, waits_at: null}, extra || {});
}

function running(extra) {
  return Object.assign({task: 'T-9', title: 'Test T-9', cases: ['TERR-012'], started: todayAt(11, 0), state: 'running',
    request: {id: 'W-1', state: 'claimed'}, note: null, devices: [dev('TA5'), dev('ET65')], wingmate: 'working',
    current_case: null, case_rows: []}, extra || {});
}

function ongoingRow(id, run) {
  return row(id, {verdict: null, verdict_label: null, results: null, running: run});
}

function groups(o) {
  return Object.assign({ongoing: {count: 0, rows: []}, tested: {count: 0, rows: [], more_unreported: 0},
    done: {count: 0, rows: []}, shown: 5, rows_max: 100}, o || {});
}

function rowsOf(card) {
  return card.all('hx-test-row');
}

function texts(nodes) {
  return nodes.map(function (n) { return n.textContent; });
}

function groupNode(card, name) {
  return card.all('hx-test-group').find(function (g) { return g.all('hx-group-title')[0].textContent.indexOf(name) === 0; });
}

function link(n) {
  const a = n.all('hx-test-title')[0];
  return a ? a.getAttribute('href') : null;
}

// 1. the three groups, a Tested row and a Done row; nothing running
(function () {
  const tested = [row('T-1', {title: 'Android · Offline - Download a flight plan offline with…', results: '5 test errors',
    verdict: 'not_decided', verdict_label: 'Test error', next: {press: 'plan_again', label: 'Plan again', case: null}})];
  const done = [row('T-2', {verdict: 'not_decided', verdict_label: 'Test error', results: '1 test error',
    done_how: {how: 'replaced', at: todayAt(13, 44), by_task: 'T-3', url: null}})];
  const card = show(groups({tested: {count: 1, rows: tested, more_unreported: 0}, done: {count: 1, rows: done}}));
  check(card !== null, 'the card is drawn');
  eq(card.all('hx-group-title').map(function (n) { return n.textContent; }), ['Ongoingnothing running', 'Tested (1)',
    'Done (1)'], 'groups');
  const t1 = rowsOf(groupNode(card, 'Tested'))[0];
  eq(link(t1), '#/results?task=T-1', 'a Tested row opens its test');
  eq(t1.all('hx-test-sub')[0].textContent, 'Custom terrain - final tests · TA5, ET65 · today 10:25 · 5 test errors',
    'Tested row: ClickUp name · devices · last change · results');
  eq(t1.all('hx-test-next')[0].textContent, 'Next: Plan again ›', 'Tested row: the next press');
  eq(t1.all('hx-test-next')[0].getAttribute('href'), '#/results?task=T-1', 'the next press opens the test');
  const d2 = rowsOf(groupNode(card, 'Done'))[0];
  eq(d2.all('hx-test-sub')[0].textContent, 'TA5, ET65 · today 10:25 · Replaced by the ' + hm(todayAt(13, 44)) +
    ' run ›', 'Done row: devices · last change · how it ended');
  check(d2.all('hx-test-next').length === 0, 'a Done row has no next press');
})();

// 2. how a test in Done ended: posted, marked, failed again
(function () {
  const done = [
    row('T-4', {done_how: {how: 'posted', at: todayAt(12, 5), by_task: null, url: 'https://app.clickup.com/t/86c0abc'}}),
    row('T-5', {done_how: {how: 'marked', at: todayAt(12, 6), by_task: null, url: null}}),
    row('T-6', {verdict: 'failed_suspected', verdict_label: 'Failed, suspected', results: '1 failed (suspected)',
      unreported: 1, done_how: {how: 'failed_again', at: todayAt(11, 15), by_task: 'T-7', url: null}})];
  const card = show(groups({done: {count: 3, rows: done}}));
  const subs = texts(rowsOf(groupNode(card, 'Done')).map(function (r) { return r.all('hx-test-sub')[0]; }));
  eq(subs, ['TA5, ET65 · today 10:25 · Posted today 12:05 ↗', 'TA5, ET65 · today 10:25 · Marked done',
    'TA5, ET65 · today 10:25 · Failed again in the ' + hm(todayAt(11, 15)) + ' run ›'], 'done words');
  const again = rowsOf(groupNode(card, 'Done'))[2].all('hx-test-sub')[0].childNodes.filter(function (k) {
    return k instanceof Element && k.tagName === 'A';
  })[0];
  eq(again.getAttribute('href'), '#/results?task=T-7', 'Failed again links to the newer test');
})();

// 3. what an ongoing test waits for (API.md 2.5 running): Hover, a device, the person
(function () {
  const rows = [
    ongoingRow('T-10', running({wingmate: 'waiting', request: {id: 'W-2', state: 'new'}})),
    ongoingRow('T-11', running({wingmate: 'off', request: {id: 'W-3', state: 'new'}})),
    ongoingRow('T-12', running({devices: [dev('TA5', {state: 'finished', step: 109, of: 109, verdict: 'ERROR'}),
      dev('ET65', {state: 'finished', step: 109, of: 109, verdict: 'ERROR'})]})),
    ongoingRow('T-13', running({devices: [dev('TA5', {state: 'running', step: 57, of: 109, text: 'Wait'}), dev('ET65')]})),
    ongoingRow('T-14', running({devices: [dev('TA5', {state: 'running', step: 9, of: 30,
      part: {n: 3, of: 6, text: 'Maps import, 29 m DSM: accepted'}})]})),
    ongoingRow('T-15', running({devices: [dev('TA5', {state: 'running', step: 4, of: 9}),
      dev('ET65', {state: 'finished', verdict: 'BLOCKED', waits_at: 2})]}))];
  const card = show(groups({ongoing: {count: rows.length, rows: rows}}));
  eq(card.all('hx-group-title')[0].textContent, 'Ongoing (6)', 'Ongoing counts its tests');
  eq(texts(card.all('hx-test-state')), ['Waiting for Hover', 'Hover is off',
    'Running', 'Running: TA5 step 57 of 109', 'Running: TA5 step 3 of 6', 'ET65 waits for your OK at step 2'],
    'ongoing words: waiting, off, working between two cases, a step, a part, handed back');
})();

// 4. 'n more' and 'Show all': the rows the server sent, never more
(function () {
  const many = function (n, from) {
    const out = [];
    for (let i = 0; i < n; i++) out.push(row('T-' + (from + i)));
    return out;
  };
  let card = show(groups({tested: {count: 10, rows: many(10, 100), more_unreported: 2},
    done: {count: 8, rows: many(8, 200)}}));
  const more = function (name) {
    return groupNode(card, name).all('hx-more')[0];
  };
  eq(rowsOf(groupNode(card, 'Tested')).filter(function (r) { return !r.parentNode.hidden; }).length, 5,
    'Tested shows 5 rows');
  eq(more('Tested').textContent, '5 more ▾ (2 of them failed and are not reported)', 'n more, with the unreported');
  eq(more('Done').textContent, 'Show all 8 ▾', 'Show all');
  more('Tested').click();
  eq(rowsOf(groupNode(card, 'Tested')).filter(function (r) { return !r.parentNode.hidden; }).length, 10,
    'n more shows the rest');
  eq(more('Tested').textContent, 'Show fewer ▴', 'and folds them again');
  card = show(groups({tested: {count: 6, rows: many(6, 300), more_unreported: 1}, done: {count: 6, rows: many(6, 400)}}));
  eq(more('Tested').textContent, '1 more ▾ (1 of them failed and is not reported)', 'one: is');
  // more tests than the server sends (rows_max): the labels count the rows sent
  card = show(groups({tested: {count: 125, rows: many(100, 500), more_unreported: 0},
    done: {count: 130, rows: many(100, 700)}}));
  eq(more('Tested').textContent, '95 of 120 more ▾', 'Tested beyond rows_max');
  eq(more('Done').textContent, 'Show 100 of 130 ▾', 'Done beyond rows_max');
})();

// 5. FINDINGS #30 (2026-11-05): the charts call an ERROR run a test error, as the legend, Your tests and Results do;
// "not decided" is gone from them (titles, the causes card, the table twins)
(function () {
  function statsOf(perDay, causes, week) {
    return {window: {from: '2026-10-07', to: '2026-11-05'},
      app: {pass_rate: {value: 50, passed: 1, failed: 1, partly: 0}, fails: {confirmed: 0, suspected: 1},
        bugs: {found: 0, filed: 0, fixed: 0}, coverage: {cases_passed: 1, cases_active: 2, devices: {TA5: 2}}},
      engine: {decided_rate: {value: week.rate, target: 90, decided: week.decided, runs: week.runs},
        not_decided_causes: causes, false_fails: {}, hands_off: {}, times: {}},
      series: {per_day: perDay, decided_per_week: [week], per_device: [{tag: 'TA5', PASS: 1, FAIL: 1, ERROR: 4}],
        times: []},
      definitions: {}};
  }
  function render(s) {
    stats = s;
    answer = groups();
    const root = document.body.appendChild(el('main'));
    window.QAHistory.render(root, {get: function () { return null; }}, function () { return true; }, QA);
    flush();
    stats = null;
    return root;
  }
  function cardOf(root, kicker) {
    return root.all('hx-card').find(function (c) { return c.all('hx-kicker')[0].textContent === kicker; }) || null;
  }
  function twin(card) {
    const btn = card.all('hx-toggle')[0];
    btn.click();
    return card.all('hx-twin')[0];
  }
  let root = render(statsOf([{day: '2026-11-04', PASS: 1, FAIL: 1, ERROR: 3}, {day: '2026-11-05', ERROR: 1}],
    [{cause: 'engine', n: 3}, {cause: 'test case', n: 1}], {week: 'W45', from: '2026-11-03', decided: 2, runs: 6,
      rate: 33}));
  const day = cardOf(root, 'Runs per day');
  check(day !== null && /; 4 test errors$/.test(day.all('hx-title')[0].textContent),
    'charts: the day title counts test errors, got ' + (day && day.all('hx-title')[0].textContent));
  const cause = cardOf(root, 'Why runs ended in a test error');
  check(cause !== null, 'charts: the causes card is about test errors');
  if (cause) {
    eq(cause.all('hx-title')[0].textContent, 'Most runs that ended in a test error broke in the engine (3 of 4)',
      'charts: the causes title');
    const rows = twin(cause).all('hx-foot');
    eq(rows.map(function (r) { return r.textContent; }), ['All test errors4'], 'charts: the causes table foot');
  }
  const week = cardOf(root, 'Runs that decided, per week');
  check(week !== null && twin(week).textContent.indexOf('Test errors') >= 0, 'charts: the week table column');
  check(!/not decided|undecided/i.test(root.textContent), 'charts: no "not decided" left on the page');
  // one test error, and two causes with the same count
  const textOf = function (card, cls) { return card ? card.all(cls)[0].textContent : null; };
  root = render(statsOf([{day: '2026-11-04', PASS: 2, ERROR: 1}], [{cause: 'engine', n: 1}, {cause: 'test case', n: 1}],
    {week: 'W45', from: '2026-11-03', decided: 2, runs: 3, rate: 67}));
  check(/; 1 test error$/.test(textOf(cardOf(root, 'Runs per day'), 'hx-title')), 'charts: one test error');
  eq(textOf(cardOf(root, 'Why runs ended in a test error'), 'hx-title'),
    'Engine and test case stopped the most runs (1 each, of 2 test errors)', 'charts: two causes with the same count');
  root = render(statsOf([{day: '2026-11-04', PASS: 2}], [], {week: 'W45', from: '2026-11-03', decided: 2, runs: 2,
    rate: 100}));
  eq(textOf(cardOf(root, 'Why runs ended in a test error'), 'hx-empty'), 'No run ended in a test error in this period.',
    'charts: none');
})();

print(JSON.stringify({failures: failures, checks: checks}));
