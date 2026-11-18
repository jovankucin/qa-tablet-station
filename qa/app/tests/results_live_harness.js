// results_live_harness.js: runs static/results.js in JavaScriptCore's jsc with a fake DOM and a fake window.QA, and
// checks the Results page of 2026-11-04 (one test per page; tests/2026-11-04-results-redesign/SPEC.md screens 1-4 over
// the JSON of API.md): the header, the box with its one Sun Orange button (File ticket > Post comment > Report issue >
// Plan again > Add result), Mark done… and Plan again, the case rows with a chip per device and the opened row, the live
// box while the test runs (a look every 4 s at ?task=<id>, the result box in its place when the test ends), and the
// flows of 2026-11-03 moved there: Report issue on a row (ask the worker for a draft, wait for it) and Add result and
// Post comment in the box (the worker's comment, read, Post comment with the Touch ID wait, posted). Since 2026-11-05
// (tests/2026-11-05-wingmate-debug/FINDINGS.md; checks 15b and 23-27): the test read again after Post comment, a
// newer plan in Plan again's place, refusals that go when the draft or comment came, '–' live cells, open_write, and
// ?case=X without an engine run.
// Run by test_results_live.py:
//   jsc results_live_harness.js -- <path of results.js>
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
  get nextSibling() {
    const p = this.parentNode;
    return p ? p.childNodes[p.childNodes.indexOf(this) + 1] || null : null;
  }
  appendChild(n) { return this.insertBefore(n, null); }
  insertBefore(n, ref) {
    if (ref && ref.parentNode !== this) throw new Error('insertBefore: the reference node is not a child');
    if (n.parentNode) n.parentNode.removeChild(n);
    if (ref) this.childNodes.splice(this.childNodes.indexOf(ref), 0, n);
    else this.childNodes.push(n);
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
  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return n === document.body;
  }
  get textContent() { return this.childNodes.map(function (k) { return k.textContent; }).join(''); }
}

class Text extends Node {
  constructor(t) { super(); this.data = String(t); }
  get textContent() { return this.data; }
}

class ClassList {
  constructor(e) { this.e = e; }
  _get() { return this.e.className.split(/\s+/).filter(Boolean); }
  contains(c) { return this._get().indexOf(c) >= 0; }
  add(c) { if (!this.contains(c)) this.e.className = this._get().concat([c]).join(' '); }
  remove(c) { this.e.className = this._get().filter(function (x) { return x !== c; }).join(' '); }
  toggle(c, on) {
    const want = on === undefined ? !this.contains(c) : Boolean(on);
    if (want) this.add(c); else this.remove(c);
    return want;
  }
}

class Element extends Node {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this.attrs = {};
    this.className = '';
    this.classList = new ClassList(this);
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.open = false;
  }
  setAttribute(k, v) { if (k === 'class') this.className = String(v); else this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  click() { for (const fn of this.listeners.click || []) fn({target: this}); }
  focus() { document.activeElement = this; }
  scrollIntoView() { this.scrolled = true; }
  set textContent(t) {
    this.childNodes.slice().forEach(function (k) { this.removeChild(k); }, this);
    this.appendChild(new Text(t));
  }
  get textContent() { return super.textContent; }
  querySelectorAll(sel) {
    const cls = sel.replace(/^\./, '');
    const out = [];
    (function walk(n) {
      for (const k of n.childNodes) {
        if (!(k instanceof Element)) continue;
        if (k.classList.contains(cls)) out.push(k);
        walk(k);
      }
    })(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

const document = {
  body: new Element('body'),
  activeElement: null,
  createElement: function (tag) { return new Element(tag); },
  createElementNS: function (ns, tag) { return new Element(tag); },
  createTextNode: function (t) { return new Text(t); },
};
window.document = document;
window.Node = Node;
window.location = {hash: '#/results'};

// ---- fake timers and fetch ----

let timers = [];
window.setTimeout = function (fn, ms) { timers.push({fn: fn, ms: ms}); return timers.length; };

function flush() {
  for (let i = 0; i < 40; i++) drainMicrotasks();
}

function fireTimers() {
  const due = timers;
  timers = [];
  for (const t of due) t.fn();
  flush();
}

const net = {sends: [], gets: [], send: null, answer: null, getError: null, fetches: [], fetch: null};

window.fetch = function (url) {
  net.fetches.push(url);
  return new Promise(function (resolve, reject) { net.fetch = {resolve: resolve, reject: reject}; });
};

// ---- fake window.QA (the app.js helpers results.js uses) ----

function el(tag, props) {
  const n = document.createElement(tag);
  add(n, Array.prototype.slice.call(arguments, 2));
  for (const k of Object.keys(props || {})) {
    const v = props[k];
    if (v === null || v === undefined || v === false || k === 'style') continue;
    if (k === 'class') n.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
    else if (k.slice(0, 2) === 'on' && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (['checked', 'disabled', 'selected', 'hidden', 'open'].indexOf(k) >= 0) n[k] = Boolean(v);
    else n.setAttribute(k, v === true ? '' : String(v));
  }
  return n;
}

function add(parent, kids) {
  for (const k of kids) {
    if (k === null || k === undefined || k === false) continue;
    if (Array.isArray(k)) add(parent, k);
    else if (k instanceof Node) parent.appendChild(k);
    else parent.appendChild(document.createTextNode(String(k)));
  }
  return parent;
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

function copy(x) {
  return JSON.parse(JSON.stringify(x));
}

const QA = {
  el: el, add: add, put: put, qs: qs,
  list: function (x) { return Array.isArray(x) ? x : []; },
  loading: function (t) { return el('p', {class: 'loading'}, t); },
  viewHead: function (t) { return el('div', {class: 'view-head'}, el('h1', {}, t)); },
  routeHash: function (name, params) { return '#/' + name + qs(params); },
  chip: function (cls, text) { return el('span', {class: 'chip ' + cls}, text); },
  errorBox: function (e, prefix) {
    return el('div', {class: 'msg msg-error', role: 'alert'}, (prefix ? prefix + ': ' : '') + (e && e.message ? e.message : String(e)));
  },
  copyButton: function (text, label) {
    const b = el('button', {type: 'button', class: 'btn btn-secondary btn-small'}, label);
    b.copyText = text;
    return b;
  },
  getJSON: function (path) {
    net.gets.push(path);
    if (net.getError) return Promise.reject(net.getError);
    return Promise.resolve(copy(net.answer));
  },
  sendJSON: function (method, path, body) {
    net.sends.push({method: method, path: path, body: body});
    return new Promise(function (resolve, reject) { net.send = {resolve: resolve, reject: reject}; });
  },
};

function httpError(status, message) {
  const e = new Error(message);
  e.status = status;
  return e;
}

load(arguments[0]);

// ---- data (shaped as API.md section 2) ----

function pad(n) {
  return String(n).padStart(2, '0');
}

// a time today, so the page writes it as "HH:MM" (and "Today HH:MM") whenever the harness runs
function todayAt(h, m) {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m, 5).toISOString();
}

function hm(isoText) {
  const d = new Date(isoText);
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}

const RUN_AT = todayAt(9, 36);
const RESULTS_AT = todayAt(10, 25);
const CU = {task_id: '86c0abc', custom_id: null, url: 'https://app.clickup.com/t/86c0abc', title: 'Custom terrain'};
const RESULT_ERR = {verdict: 'not_decided', verdict_label: 'Test error', word: 'test error',
  counts: {failed: 0, failed_suspected: 0, blocked: 0, not_decided: 2, partly: 0, passed: 0, not_tested: 0},
  case_count: 2, sentence: 'Test error in 2 of 2 cases, on TA5 and ET65',
  meaning: 'The test itself stopped before it could check the app. This says nothing about the app.'};
const RESULT_FAIL = {verdict: 'failed_suspected', verdict_label: 'Failed, suspected', word: 'failed (suspected)',
  counts: {failed: 0, failed_suspected: 1, blocked: 0, not_decided: 0, partly: 0, passed: 1, not_tested: 0},
  case_count: 2, sentence: 'Failed (suspected) in 1 of 2 cases, on TA5; 1 passed',
  meaning: 'The app did not do what the test expects. It is not confirmed yet.'};
const RESULT_PASS = {verdict: 'passed', verdict_label: 'Passed', word: 'passed',
  counts: {failed: 0, failed_suspected: 0, blocked: 0, not_decided: 0, partly: 0, passed: 1, not_tested: 0},
  case_count: 1, sentence: 'Passed: 1 of 1 case, on TA5', meaning: null};
const PLAN_AGAIN = {intake: true, prepare: true, task_id: '86c0abc', guidance: 'Android\nTest the 28 m limit',
  targets: ['TA5', 'ET65'], answers_from: 'W-20261104-015'};
const TEXT = 'Tested terrain import on TA5 and ET65 (staging 1.76.0).\nTA5: failed at step 13. Ticket CL-12345.';

function comment(extra) {
  return Object.assign({state: 'none', text: null, sha256: null, posted_at: null, error: null,
    clickup_url: 'https://app.clickup.com/t/86c0abc'}, extra || {});
}

function testOf(extra) {
  return Object.assign({task: 'T-1', state: 'results_ready', group: 'tested',
    title: 'Android · Offline - Download a flight plan offline with… · Spain', clickup: CU, devices: ['TA5', 'ET65'],
    not_tested_devices: [{tag: 'W044', why: 'not in this test'}], build: '1.76.0 staging', native: '144',
    run_at: RUN_AT, results_at: RESULTS_AT, stopped_early: false, result: RESULT_FAIL,
    done_note: '2 of 3 devices; TA5: failure on TERR-012 (suspected), ET65: pass.',
    asked: {guidance: 'Android\nTest the 28 m limit', plan_summary: 'Test: TERR-012 Spain on TA5, ET65.',
      not_covered: ['custom terrain still used offline after reopening the flight']},
    comment: comment(), outputs: {summary: null, comment_text: null, comment_file: null}, plan_again: null,
    next: null, can_mark_done: true, mark_done_why: null, done_how: null, waiting: 0, also_running: []}, extra || {});
}

function dev(tag, verdict, extra) {
  const labels = {passed: 'Passed', failed: 'Failed', failed_suspected: 'Failed, suspected', not_decided: 'Test error',
    partly: 'Partly passed'};
  return Object.assign({tag: tag, verdict: verdict, verdict_label: labels[verdict], word: verdict.replace('_', ' '),
    failed_step: verdict === 'passed' ? null : {n: 57, text: 'Read the list'}, duration_s: 685, runs: 1,
    started: todayAt(9, 38), ended: todayAt(9, 49), run: 'R-20261104-' + tag + '-TERR-012-01'}, extra || {});
}

// a failed case (TA5 failed, suspected; ET65 passed): open, with its ticket part
function card(extra) {
  const c = Object.assign({key: 'T-1|TERR-012', task: 'T-1', case: 'TERR-012', case_version: 'c0',
    title: 'Custom terrain 28 m limit at Spain', case_title: 'Custom terrain 28 m limit at Spain', area: 'TERR',
    verdict: 'failed_suspected', verdict_label: 'Failed, suspected', when: RESULTS_AT, runs: 2, same_result: true,
    build: '1.76.0 staging', native: '144', duration_s: 685, clickup: CU,
    devices: [dev('TA5', 'failed_suspected'), dev('ET65', 'passed', {duration_s: 30})],
    not_tested_devices: [{tag: 'W044', why: 'not in this test'}],
    explain: {tested: 'New site at Spain', happened: 'TA5 listed the 29 m map as selectable.',
      why: 'Failed, suspected: the 29 m map was selectable on TA5.', not_tested: 'Web not in scope.'},
    auto: {tested: 'auto tested', happened: 'auto happened', why: 'auto why'},
    proof: [{url: '/files/tests/x/TA5/shots/R-20261104-TA5-TERR-012-01_end.png',
      name: 'R-20261104-TA5-TERR-012-01_end.png', kind: 'end', run: 'R-20261104-TA5-TERR-012-01'},
    {url: '/files/tests/x/TA5/shots/R-20261104-TA5-TERR-012-01_result.png',
      name: 'R-20261104-TA5-TERR-012-01_result.png', kind: 'result', run: 'R-20261104-TA5-TERR-012-01'},
    {url: '/files/tests/x/ET65/shots/R-20261104-ET65-TERR-012-01_end.png',
      name: 'R-20261104-ET65-TERR-012-01_end.png', kind: 'end', run: 'R-20261104-ET65-TERR-012-01'}],
    flight_plans: [], state: 'results_ready', outputs: {summary: null, comment_text: null, comment_file: null},
    actions: ['details'], run_ids: ['R-20261104-TA5-TERR-012-01', 'R-20261104-ET65-TERR-012-01'], tasks: ['T-1'],
    tickets: [], report: {enabled: true, why: null}, comment: comment(),
    what_it_checks: 'Spain 40.4N: 28 m map selectable, 29 m greyed out',
    why_line: 'the 29 m map was selectable on TA5.', rows_from: 'checks',
    rows: [{n: 23, text: 'Read the file card', cells: {TA5: 'as_expected', ET65: 'as_expected'}},
      {n: 57, text: 'Read the list', cells: {TA5: 'not_as_expected', ET65: 'as_expected'}},
      {n: 70, text: 'Read the dialog', cells: {TA5: 'not_run', ET65: null}}],
    open: true}, extra || {});
  return c;
}

// a case that passed on both tablets: closed, no why, no ticket part
function passedCard(extra) {
  return card(Object.assign({key: 'T-1|TERR-009', case: 'TERR-009', title: 'Custom terrain 28 m limit at Alaska',
    case_title: 'Custom terrain 28 m limit at Alaska', verdict: 'passed', verdict_label: 'Passed',
    devices: [dev('TA5', 'passed'), dev('ET65', 'passed')], why_line: null, open: false,
    report: {enabled: false, why: 'Nothing went wrong: every device passed.'},
    explain: {tested: 'x', happened: 'Both tablets did it.', why: 'Passed: as expected.', not_tested: null}}, extra || {}));
}

// a test error case: closed, its why line
function errorCard(extra) {
  return card(Object.assign({key: 'T-1|TERR-013', case: 'TERR-013', title: 'Custom terrain stays in use offline',
    case_title: 'Custom terrain stays in use offline', verdict: 'not_decided', verdict_label: 'Test error',
    devices: [dev('TA5', 'not_decided'), dev('ET65', 'not_decided')], open: false,
    why_line: 'the test tapped the \'Import file\' text, which is covered in that dialog.',
    report: {enabled: false, why: 'Nothing failed in the app: the test itself stopped, which says nothing about the app.'}},
  extra || {}));
}

function ticket(extra) {
  return Object.assign({title: '[CT] Terrain import fails offline', description: 'Issue Summary\n...', n: 1,
    dir: 'tests/2026-11-04-x/tickets/1', filed: false, state: 'draft', offline: false, target: 'TA5'}, extra || {});
}

function answer(test, cards, running) {
  return {test: test, cards: cards || [], running: running || null};
}

// ---- helpers for the checks ----

let alive = true;
let firstGet = null;

function show(data, params) {
  document.body = new Element('body');
  timers = [];
  net.sends = [];
  net.gets = [];
  net.send = null;
  net.fetches = [];
  net.fetch = null;
  net.getError = null;
  net.answer = data;
  document.activeElement = null;
  window.location.hash = '#/results';
  alive = true;
  const root = document.body.appendChild(new Element('main'));
  window.QAResults.render(root, {get: function (k) { return (params || {})[k] || null; }}, function () { return alive; },
    QA);
  flush();
  check(net.gets.length === 1, 'render reads the test once');
  firstGet = net.gets[0];
  net.gets = [];  // the page's own load; the checks count the looks after it
  return root;
}

function all(root, cls) {
  return root.querySelectorAll('.' + cls);
}

function one(root, cls) {
  return root.querySelector('.' + cls);
}

function byText(root, tag, text) {
  const out = [];
  (function walk(n) {
    for (const k of n.childNodes) {
      if (!(k instanceof Element)) continue;
      if (k.tagName === tag && k.textContent === text) out.push(k);
      walk(k);
    }
  })(root);
  return out;
}

function tags(root, tag) {
  const out = [];
  (function walk(n) {
    for (const k of n.childNodes) {
      if (!(k instanceof Element)) continue;
      if (k.tagName === tag) out.push(k);
      walk(k);
    }
  })(root);
  return out;
}

function button(root, text) {
  return byText(root, 'BUTTON', text)[0] || null;
}

function link(root, text) {
  return byText(root, 'A', text)[0] || null;
}

// shown on screen: the node and every parent are not hidden, and no closed <details> folds it away
function visible(n) {
  for (let x = n; x; x = x.parentNode) {
    if (x.hidden) return false;
    const p = x.parentNode;
    if (p && p.tagName === 'DETAILS' && !p.open && x.tagName !== 'SUMMARY') return false;
  }
  return true;
}

function isPrimary(b) {
  return Boolean(b) && b.classList.contains('btn') && !b.classList.contains('btn-secondary') && visible(b);
}

function primaries(root) {
  return all(root, 'btn').filter(isPrimary);
}

function onlyPrimary(root, text, what) {
  const p = primaries(root);
  check(p.length === 1 && p[0].textContent === text, what + ': the one primary button is ' + text + ', got ' +
    JSON.stringify(p.map(function (b) { return b.textContent; })));
}

function noPrimary(root, what) {
  eq(primaries(root).map(function (b) { return b.textContent; }), [], what + ': no primary button');
}

function errorIn(n) {
  const m = n && n.querySelector('.msg-error');
  return m ? m.textContent : '';
}

function box(root) {
  return one(root, 'rv-box');
}

function rows(root) {
  return all(root, 'rv-case');
}

function rowOf(root, title) {
  return rows(root).find(function (r) { return one(r, 'rv-case-name').textContent === title; }) || null;
}

function commentStatus(root) {
  const s = one(box(root), 'rv-status');
  return s && visible(s) ? s.textContent : '';
}

function commentText(root) {
  const t = one(root, 'rv-comment-text');
  return t && visible(t) ? t.textContent : null;
}

function ticketPanel(root) {
  return one(root, 'rv-ticket-panel');
}

function stepTexts(root) {
  return all(root, 'rv-run-step').map(function (n) { return n.textContent; });
}

function cellsOf(row) {
  return one(row, 'rv-case-line').querySelectorAll('.rv-cell').map(function (c) { return c.textContent; });
}

// ---- the one-test page ----

// 1. #/results: the newest test (?task=current); the header, the box, the rows; later looks name the test
(function () {
  const t = testOf({result: RESULT_ERR, waiting: 9, plan_again: PLAN_AGAIN,
    next: {press: 'plan_again', label: 'Plan again', case: null}});
  const c1 = errorCard({key: 'T-1|TERR-009', case: 'TERR-009', title: 'Custom terrain 28 m limit at Alaska',
    why_line: 'on ET65 the test could not find its new site by name after the import.'});
  const c2 = errorCard();
  const root = show(answer(t, [c1, c2]));
  eq(firstGet, '/api/results/cards?task=current', 'which test: #/results asks for the current test');
  const h1 = tags(root, 'H1');
  check(h1.length === 1 && h1[0].classList.contains('rv-test-title') &&
    h1[0].textContent === 'Android · Offline - Download a flight plan offline with… · Spain' &&
    h1[0].getAttribute('title') === h1[0].textContent, 'header: the title is the page heading, its whole text as a hint');
  const meta = one(one(root, 'rv-head-card'), 'rv-meta');
  eq(meta.textContent, 'ClickUp: Custom terrain ↗ · TA5, ET65 · 2 of 3 devices (W044: not in this test) · ' +
    '1.76.0 staging, native 144 · Run 09:36 → results 10:25', 'header: the grey line');
  const ck = link(meta, 'ClickUp: Custom terrain ↗');
  check(ck && ck.getAttribute('href') === CU.url && ck.getAttribute('target') === '_blank' &&
    ck.getAttribute('rel') === 'noopener noreferrer', 'header: the ClickUp task opens in a new tab');
  const times = tags(meta, 'TIME').map(function (x) { return x.getAttribute('datetime'); });
  eq(times, [RUN_AT, RESULTS_AT], 'header: the Run press and the results as times');
  const asked = one(root, 'rv-asked');
  check(asked && !asked.open && asked.firstChild.textContent === 'What you asked and the plan',
    'header: "What you asked and the plan" is folded');
  eq(tags(asked, 'DT').map(function (x) { return x.textContent; }), ['What you asked', 'The plan',
    'Not covered by this run'], 'header: the fold has the description, the plan and what it does not cover');
  check(one(asked, 'rv-pre').textContent === 'Android\nTest the 28 m limit', 'header: the description as written');
  const top = one(root, 'rv-top');
  const wait = link(top, '9 other tests wait for you · History ›');
  check(wait && wait.getAttribute('href') === '#/history', 'top line: the other tests wait in History');
  const b = box(root);
  eq(one(b, 'rv-verdict').textContent, 'Test error in 2 of 2 cases, on TA5 and ET65', 'box: the sentence');
  check(one(b, 'rv-verdict-icon').classList.contains('rv-v-not_decided'), 'box: the hatched test-error marker');
  eq(one(b, 'rv-meaning').textContent, 'The test itself stopped before it could check the app. This says nothing ' +
    'about the app.', 'box: the fixed meaning');
  eq(one(b, 'rv-note').textContent, 'Hover: 2 of 3 devices; TA5: failure on TERR-012 (suspected), ET65: pass.',
    'box: Hover\'s done note');
  const acts = one(b, 'rv-actions').childNodes.filter(function (n) { return n.tagName === 'BUTTON' && visible(n); })
    .map(function (n) { return n.textContent; });
  eq(acts, ['Plan again', 'Add result', 'Mark done…'], 'box: the buttons in order');
  onlyPrimary(root, 'Plan again', 'box: no case gave an app result');
  // the rows: a chip per device, the why line, closed
  eq(rows(root).map(function (r) { return one(r, 'rv-case-name').textContent; }),
    ['Custom terrain 28 m limit at Alaska', 'Custom terrain stays in use offline'], 'rows: the case titles in order');
  eq(cellsOf(rows(root)[0]), ['TA5: Test error', 'ET65: Test error'], 'rows: a chip per device, named for a phone');
  check(rows(root)[0].querySelectorAll('.rv-badge').every(function (x) { return x.classList.contains('rv-v-not_decided'); }),
    'rows: test error chips are hatched');
  eq(one(rows(root)[1], 'rv-why-line').textContent, 'the test tapped the \'Import file\' text, which is covered in ' +
    'that dialog.', 'rows: one line of why');
  check(rows(root).every(function (r) { return !r.open; }), 'rows: a test error row stays closed');
  eq(all(root, 'rv-case-head')[0].textContent, 'CaseTA5ET65', 'rows: the column heads');
  // gone: the older fold, the case ids and versions, "runs, same result", "run took", the question mark word (in the
  // page's own words: Hover's done note and plan summary may name a case)
  const text = [one(root, 'rv-top'), one(root, 'rv-test-title'), one(root, 'rv-meta'), one(root, 'rv-cases')]
    .map(function (n) { return n.textContent; }).join(' ') + b.textContent.replace(one(b, 'rv-note').textContent, '');
  for (const gone of ['Older results', 'TERR-0', ' c0', 'same result', 'run took', 'Not decided', 'not decided',
    'Running now']) {
    check(text.indexOf(gone) < 0, 'gone: "' + gone + '" is not on the page');
  }
  check(timers.length === 0, 'no looks while nothing is open');
})();

// 2. ?task=T: that test; an opened row: what it checks, the step x device table, the facts, the shots, each
// device's run time, the ticket part and Run records
(function () {
  const root = show(answer(testOf(), [card(), passedCard()]), {task: 'T-1'});
  eq(firstGet, '/api/results/cards?task=T-1', 'which test: ?task=T asks for that test');
  const row = rowOf(root, 'Custom terrain 28 m limit at Spain');
  check(row.open, 'opened row: a row where a device failed opens');
  check(!rowOf(root, 'Custom terrain 28 m limit at Alaska').open, 'opened row: a passed row stays closed');
  eq(cellsOf(row), ['TA5: Failed, suspected', 'ET65: Passed'], 'opened row: a device that passed is not hidden');
  check(one(rowOf(root, 'Custom terrain 28 m limit at Alaska'), 'rv-why-line') === null, 'passed row: no why line');
  eq(one(row, 'rv-checks').textContent, 'What it checks: Spain 40.4N: 28 m map selectable, 29 m greyed out',
    'opened row: what it checks');
  const table = one(row, 'rv-step-table');
  eq(tags(tags(table, 'THEAD')[0], 'TH').map(function (x) { return x.textContent; }), ['Check', 'TA5', 'ET65'],
    'table: a column per device');
  const trs = tags(tags(table, 'TBODY')[0], 'TR');
  eq(trs.map(function (tr) { return tr.childNodes[0].textContent; }), ['Read the file card', 'Read the list',
    'Read the dialog'], 'table: the check steps');
  eq(trs.map(function (tr) { return tr.childNodes.slice(1).map(function (td) { return td.textContent; }); }),
    [['as expected', 'as expected'], ['not as expected', 'as expected'], ['not reached', '']],
    'table: each cell in words (a device without the step stays empty)');
  check(trs[1].childNodes[1].classList.contains('rv-o-not_as_expected') &&
    trs[1].childNodes[1].getAttribute('title') === 'TA5: not as expected', 'table: the cell class and hint');
  eq(one(row, 'rv-legend').textContent, 'as expectednot as expectedthe test stopped herenot reached',
    'table: the legend');
  eq(tags(one(row, 'rv-facts'), 'DT').map(function (x) { return x.textContent; }), ['What happened', 'Why', 'Not tested'],
    'opened row: What happened, Why and Not tested, always');
  eq(tags(one(row, 'rv-facts'), 'DD').map(function (x) { return x.textContent; }), ['TA5 listed the 29 m map as ' +
    'selectable.', 'Failed, suspected: the 29 m map was selectable on TA5.', 'Web not in scope.'],
  'opened row: Hover\'s words');
  eq(all(row, 'rv-shot').map(function (f) { return tags(f, 'FIGCAPTION')[0].textContent; }),
    ['Result, TA5', 'End of the run, TA5', 'End of the run, ET65'], 'opened row: the result shot first');
  eq(one(row, 'rv-times').textContent, 'TA5 11 min · ET65 30 s', 'opened row: each device\'s run time');
  check(one(row, 'rv-times').childNodes[0].getAttribute('title') === 'TA5 ran ' + hm(todayAt(9, 38)) + ':05 to ' +
    hm(todayAt(9, 49)) + ':05', 'opened row: the run window as a hint');
  const rec = link(row, 'Run records ›');
  check(rec && rec.getAttribute('href') === '#/results?view=detail&task=T-1&case=TERR-012&run=R-20261104-TA5-TERR-012-01',
    'opened row: Run records opens the detailed page, got ' + (rec && rec.getAttribute('href')));
  check(button(row, 'Report issue') !== null, 'opened row: the ticket part where a device failed');
  check(button(rowOf(root, 'Custom terrain 28 m limit at Alaska'), 'Report issue') === null,
    'passed row: no Report issue, and no greyed line for it');
  onlyPrimary(root, 'Report issue', 'a failure and no ticket');
  check(button(box(root), 'Add result').classList.contains('btn-secondary'), 'a failure: Add result is secondary');
  check(button(box(root), 'Plan again') === null, 'a failure: no Plan again (an app result came in)');
})();

// 3. ?case=X: the newest test that ran X, X open and marked; a run from a chat has no test buttons
(function () {
  let root = show(answer(testOf(), [passedCard(), card()]), {case: 'TERR-009'});
  eq(firstGet, '/api/results/cards?case=TERR-009', 'which test: ?case=X asks for the newest test that ran X');
  const row = rowOf(root, 'Custom terrain 28 m limit at Alaska');
  check(row.open && row.classList.contains('rv-target') && row.scrolled === true, '?case=X: that row open and marked');
  root = show(answer(testOf({task: null, state: null, group: null, clickup: null, asked: null, comment: null, outputs: null,
    plan_again: null, done_note: null, can_mark_done: false, title: 'Custom terrain 28 m limit at Spain',
    mark_done_why: 'A run from a chat is not a test, so it cannot be marked done.'}),
  [card({task: null, key: 'tests/x|TERR-012|abc', tickets: [ticket()], report: {enabled: true, why: null}})]),
  {case: 'TERR-012'});
  check(button(root, 'Add result') === null && button(root, 'Mark done…') === null && button(root, 'Plan again') === null &&
    button(root, 'Report issue') === null, 'chat run: no Add result, Mark done, Plan again or Report issue');
  onlyPrimary(root, 'File ticket', 'chat run: its drafted ticket can be filed');
  button(root, 'File ticket').click();
  flush();
  net.send.resolve({request: {id: 'W-60', state: 'new', type: 'file'}});
  flush();
  fireTimers();
  eq(net.gets, ['/api/results/cards?case=TERR-012'], 'chat run: the looks name the case');
})();

// 4. no test yet; an unknown test; the server not reachable
(function () {
  let root = show({test: null, cards: [], running: null});
  const empty = one(root, 'rv-empty');
  eq(empty && empty.textContent, 'No results yet. Start a test on New test, or see your tests in History.',
    'empty: no results yet');
  check(link(empty, 'New test').getAttribute('href') === '#/new' && link(empty, 'History').getAttribute('href') ===
    '#/history', 'empty: links to New test and History');
  check(timers.length === 0, 'empty: no looks');
  document.body = new Element('body');
  net.answer = null;
  net.getError = httpError(404, 'no test T-9: it has no runs and is not running');
  root = document.body.appendChild(new Element('main'));
  window.QAResults.render(root, {get: function (k) { return k === 'task' ? 'T-9' : null; }}, function () { return true; },
    QA);
  flush();
  eq(one(root, 'rv-empty').textContent, 'No test T-9: it has no runs and is not running. Start a test on New test, ' +
    'or see your tests in History.', 'unknown test: the server\'s words');
  document.body = new Element('body');
  net.getError = new Error('Cannot reach the app server.');
  root = document.body.appendChild(new Element('main'));
  window.QAResults.render(root, {get: function () { return null; }}, function () { return true; }, QA);
  flush();
  eq(errorIn(root), 'The results did not load: Cannot reach the app server.', 'no server: the error');
  net.getError = null;
})();

// ---- Plan again and Mark done… ----

// 5. Plan again sends the plan screen's Prepare again body unchanged, then opens the new plan
(function () {
  const t = testOf({result: RESULT_ERR, plan_again: PLAN_AGAIN});
  let root = show(answer(t, [errorCard()]));
  const plan = button(root, 'Plan again');
  plan.click();
  flush();
  check(net.sends.length === 1 && net.sends[0].path === '/api/worker/check', 'plan again: POST /api/worker/check');
  eq(net.sends[0].body, PLAN_AGAIN, 'plan again: the body as the server gave it');
  check(plan.disabled, 'plan again: off while it is sent');
  plan.click();
  flush();
  check(net.sends.length === 1, 'plan again: a second press sends nothing');
  net.send.resolve({id: 'W-20261104-020', type: 'check', state: 'new'});
  flush();
  eq(window.location.hash, '#/new?show=plan&req=W-20261104-020', 'plan again: the new plan\'s screen opens');
  [[httpError(409, 'W-20261104-015 for this test is still open'), 'W-20261104-015 for this test is still open'],
    [httpError(400, 'your answers no longer fit (N2); answer the first plan again'),
      'your answers no longer fit (N2); answer the first plan again'],
    [httpError(403, 'missing or wrong token'), 'The app server restarted after this page was opened. Reload the page.'],
    [new Error('Cannot reach the app server.'), 'Plan again did not go through: Cannot reach the app server.'],
  ].forEach(function (pair) {
    root = show(answer(t, [errorCard()]));
    button(root, 'Plan again').click();
    flush();
    net.send.reject(pair[0]);
    flush();
    eq(errorIn(box(root)), pair[1], 'plan again refused ' + pair[0].message);
    check(!button(root, 'Plan again').disabled && document.activeElement === button(root, 'Plan again') &&
      window.location.hash === '#/results', 'plan again refused: the button back, focused, the page stays');
  });
})();

// 6. Mark done…: it asks first, then closes the task and reads the test again (now in Done)
(function () {
  const t = testOf({result: RESULT_PASS, devices: ['TA5'], waiting: 3});
  let root = show(answer(t, [passedCard({devices: [dev('TA5', 'passed')]})]));
  const md = button(root, 'Mark done…');
  check(md && visible(md) && !md.disabled && md.classList.contains('btn-secondary'), 'mark done: a secondary button');
  const ask = one(root, 'rv-confirm');
  check(!visible(ask), 'mark done: nothing asked before the press');
  md.click();
  flush();
  eq(ask.childNodes[0].textContent, 'It moves to Done in History; Add result is off after this.', 'mark done: it asks');
  check(visible(ask) && net.sends.length === 0 && document.activeElement === button(ask, 'Mark done'),
    'mark done: the question shows, nothing sent');
  button(ask, 'Cancel').click();
  check(!visible(ask) && document.activeElement === md && net.sends.length === 0, 'mark done: Cancel puts it away');
  md.click();
  button(ask, 'Mark done').click();
  flush();
  check(net.sends.length === 1 && net.sends[0].path === '/api/tasks/T-1/state', 'mark done: POST /api/tasks/<id>/state');
  eq(net.sends[0].body, {to: 'closed'}, 'mark done: the body');
  net.answer = answer(testOf({result: RESULT_PASS, devices: ['TA5'], state: 'closed', group: 'done',
    can_mark_done: false, mark_done_why: 'It is in Done already.', done_how: {how: 'marked', at: RESULTS_AT,
      by_task: null, url: null}}), [passedCard({devices: [dev('TA5', 'passed')]})]);
  net.send.resolve({id: 'T-1', state: 'closed'});
  flush();
  eq(net.gets, ['/api/results/cards?task=T-1'], 'mark done: the test is read again');
  const line = one(root, 'rv-done-line');
  check(line && line.textContent === 'Marked done' && document.activeElement === line, 'mark done: "Marked done"');
  check(button(root, 'Mark done…') === null && !visible(button(root, 'Add result')), 'mark done: no Mark done or Add ' +
    'result on a test in Done');
  noPrimary(root, 'done');
  // refused, and off with the server's why
  root = show(answer(t, [passedCard()]));
  button(root, 'Mark done…').click();
  button(one(root, 'rv-confirm'), 'Mark done').click();
  flush();
  net.send.reject(httpError(409, 'T-1 is drafted; it cannot go to closed'));
  flush();
  eq(errorIn(box(root)), 'T-1 is drafted; it cannot go to closed', 'mark done refused: the server\'s words');
  check(!button(one(root, 'rv-confirm'), 'Mark done').disabled, 'mark done refused: it can be pressed again');
  root = show(answer(testOf({result: RESULT_PASS, can_mark_done: false, mark_done_why: 'Hover is still working on ' +
    'this test (a comment, a ticket draft or filing a ticket).'}), [passedCard()]));
  check(button(root, 'Mark done…').disabled, 'mark done off: disabled');
  const why = all(box(root), 'rv-why').filter(visible).map(function (x) { return x.textContent; });
  eq(why, ['Hover is still working on this test (a comment, a ticket draft or filing a ticket).'],
    'mark done off: why, in plain words');
  root = show(answer(testOf({result: RESULT_PASS, group: 'done', can_mark_done: false,
    done_how: {how: 'replaced', at: todayAt(11, 2), by_task: 'T-2', url: null}}), [passedCard()]));
  check(button(root, 'Mark done…') === null, 'done: no Mark done');
  const rep = link(one(root, 'rv-done-line'), 'Replaced by the 11:02 run ›');
  check(rep && rep.getAttribute('href') === '#/results?task=T-2', 'done: replaced, with a link to the newer test');
  // a test in Done offers no Plan again, not even with a body, and no Report issue on its failed row (review
  // 2026-11-04); a drafted ticket still shows with File ticket, never as the orange button
  root = show(answer(testOf({result: RESULT_ERR, group: 'done', can_mark_done: false, plan_again: PLAN_AGAIN,
    done_how: {how: 'replaced', at: todayAt(11, 2), by_task: 'T-2', url: null}}), [errorCard()]));
  check(button(root, 'Plan again') === null, 'done: no Plan again');
  noPrimary(root, 'done, replaced');
  const failedAgain = {how: 'failed_again', at: todayAt(11, 15), by_task: 'T-3', url: null};
  root = show(answer(testOf({group: 'done', can_mark_done: false, done_how: failedAgain}), [card()]));
  check(rows(root)[0].open && button(root, 'Report issue') === null, 'done: the failed row opens, without Report issue');
  const again = link(one(root, 'rv-done-line'), 'Failed again in the 11:15 run ›');
  check(again && again.getAttribute('href') === '#/results?task=T-3', 'done: failed again, with a link to the test');
  noPrimary(root, 'done, failed again');
  root = show(answer(testOf({group: 'done', can_mark_done: false, done_how: failedAgain}), [card({tickets: [ticket()]})]));
  check(visible(button(root, 'File ticket')) && button(root, 'Report issue') === null,
    'done: a drafted ticket keeps File ticket');
  noPrimary(root, 'done, with a draft');
})();

// ---- the live box ----

function running(extra) {
  return Object.assign({task: 'T-2', title: 'Offline sites', cases: ['SITE-001', 'SITE-002'], started: todayAt(9, 36),
    state: 'running', request: {id: 'W-20261104-016', state: 'claimed'}, note: 'Running SITE-001',
    devices: [{tag: 'ET65', state: 'running', step: 4, of: 12, text: 'Open the map', shot_url: '/files/runs/e/s1.png',
      updated: todayAt(9, 50), case: 'SITE-001', run: 'R-1', verdict: null, part: null, waits_at: null},
    {tag: 'TA5', state: 'running', step: 3, of: 12, text: 'Open the map', shot_url: '/files/runs/t/s1.png',
      updated: todayAt(9, 51), case: 'SITE-001', run: 'R-2', verdict: null, part: null, waits_at: null}],
    wingmate: 'working', current_case: {n: 1, of: 2, id: 'SITE-001'},
    case_rows: [{case: 'SITE-001', title: 'Offline site, then a flight', devices: {TA5: 'running', ET65: 'running'}},
      {case: 'SITE-002', title: 'Offline site named on the tablet', devices: {TA5: 'waiting', ET65: 'waiting'}}]},
  extra || {});
}

function liveTest(extra) {
  return testOf(Object.assign({task: 'T-2', state: 'running', group: 'ongoing', title: 'Offline sites: QA_ site',
    build: null, native: null, results_at: null, result: null, done_note: null, next: null, can_mark_done: false,
    mark_done_why: 'The test is still running.', plan_again: null, waiting: 4}, extra || {}));
}

// 7. a running test: the header, each device's step and newest screenshot, the case rows, no buttons, a look every 4 s
(function () {
  const root = show(answer(liveTest(), [], running()), {task: 'T-2'});
  eq(one(one(root, 'rv-head-card'), 'rv-meta').textContent, 'ClickUp: Custom terrain ↗ · TA5, ET65 · Run 09:36',
    'live header: the ClickUp task, the devices, the Run press');
  const lb = one(root, 'rv-live');
  eq(one(lb, 'rv-verdict').textContent, 'Running · case 1 of 2', 'live: running, case 1 of 2');
  eq(stepTexts(root), ['Step 3 of 12: Open the map', 'Step 4 of 12: Open the map'], 'live: the step per device, TA5 ' +
    'first');
  eq(all(lb, 'rv-run-dev').map(function (d) { return one(d, 'strong').textContent; }), ['TA5', 'ET65'], 'live: device ' +
    'names');
  const shots = all(root, 'rv-run-shot');
  check(shots.length === 2 && shots[0].getAttribute('href') === '/files/runs/t/s1.png' &&
    shots[0].getAttribute('target') === '_blank' && shots[0].firstChild.getAttribute('src') === '/files/runs/t/s1.png' &&
    shots[0].firstChild.getAttribute('alt') === 'Newest screenshot from TA5', 'live: the newest screenshot opens');
  eq(one(lb, 'rv-run-note').textContent, 'Updated 09:51 · the results show here when the test ends',
    'live: when it was updated');
  const live = all(root, 'rv-live-row');
  eq(live.map(function (r) { return one(r, 'rv-case-name').textContent; }), ['Offline site, then a flight',
    'Offline site named on the tablet'], 'live rows: the cases');
  eq(live.map(function (r) { return r.querySelectorAll('.rv-cell').map(function (c) { return c.textContent; }); }),
    [['TA5: running', 'ET65: running'], ['TA5: waiting', 'ET65: waiting']], 'live rows: running and waiting, no verdict');
  eq(all(root, 'btn').length, 0, 'live: no button while the test runs');
  check(rows(root).length === 0 && one(root, 'rv-badge') === null, 'live: no verdict before the end');
  check(link(one(root, 'rv-top'), '4 other tests wait for you · History ›') !== null, 'live: the top line');
  check(timers.length === 1 && timers[0].ms === 4000, 'live: one look armed, every 4 s');

  // the next look: the steps move on; the same picture keeps its node, a new one gets a new node; a part maps
  const etShot = shots[1];
  const r2 = running({current_case: {n: 2, of: 2, id: 'SITE-002'}});
  r2.devices[1] = Object.assign({}, r2.devices[1], {step: 5, text: 'Tap Save', shot_url: '/files/runs/t/s2.png',
    part: {n: 3, of: 6, text: 'Maps import, 29 m DSM: accepted'}});
  r2.case_rows = [{case: 'SITE-001', title: 'Offline site, then a flight', devices: {TA5: 'done', ET65: 'done'}},
    {case: 'SITE-002', title: 'Offline site named on the tablet', devices: {TA5: 'running', ET65: 'waiting'}}];
  net.answer = answer(liveTest(), [], r2);
  fireTimers();
  eq(net.gets, ['/api/results/cards?task=T-2'], 'look: the test by its id, never "current"');
  eq(stepTexts(root)[0], 'Step 3 of 6: Maps import, 29 m DSM: accepted', 'look: a case\'s part');
  eq(one(lb, 'rv-verdict').textContent, 'Running · case 2 of 2', 'look: the next case');
  const shots2 = all(root, 'rv-run-shot');
  check(shots2[1] === etShot, 'look: the same picture is not loaded again');
  check(shots2[0] !== shots[0] && shots2[0].getAttribute('href') === '/files/runs/t/s2.png', 'look: a new picture shows');
  eq(all(root, 'rv-live-row').map(function (r) {
    return r.querySelectorAll('.rv-cell').map(function (c) { return c.textContent; });
  }), [['TA5: done', 'ET65: done'], ['TA5: running', 'ET65: waiting']], 'look: the case rows move on');
  check(timers.length === 1, 'look: armed again while the test runs');

  // a device waits for the person's OK
  const r3 = running();
  r3.devices[1] = Object.assign({}, r3.devices[1], {state: 'finished', step: 12, waits_at: 7, verdict: 'BLOCKED'});
  net.answer = answer(liveTest(), [], r3);
  fireTimers();
  eq(stepTexts(root)[0], 'waits for your OK at step 7', 'look: TA5 waits for your OK at step 7');
  eq(one(all(root, 'rv-run-dev')[0], 'rv-run-text').textContent, 'TA5 waits for your OK at step 7', 'look: in words');

  // the test ends: the box becomes the result box in place; the looks stop
  net.answer = answer(testOf({task: 'T-2', title: 'Offline sites: QA_ site'}), [card({key: 'T-2|SITE-001',
    task: 'T-2', case: 'SITE-001', title: 'Offline site, then a flight'})]);
  net.gets = [];
  fireTimers();
  check(one(root, 'rv-live') === null && all(root, 'rv-live-row').length === 0, 'ended: the live box is gone');
  eq(one(box(root), 'rv-verdict').textContent, 'Failed (suspected) in 1 of 2 cases, on TA5; 1 passed',
    'ended: the result box in its place');
  eq(rows(root).map(function (r) { return one(r, 'rv-case-name').textContent; }), ['Offline site, then a flight'],
    'ended: the case rows with their verdicts');
  onlyPrimary(root, 'Report issue', 'ended');
  check(timers.length === 0, 'ended: no more looks');
})();

// 8. Waiting for Hover, Hover is off, waiting to start; another test that runs; a picture from outside /files/
(function () {
  let root = show(answer(liveTest(), [], running({wingmate: 'waiting', current_case: null, request: {id: 'W-1',
    state: 'new'}, devices: [{tag: 'TA5', state: 'waiting'}, {tag: 'ET65', state: 'waiting'}]})), {task: 'T-2'});
  eq(one(one(root, 'rv-live'), 'rv-verdict').textContent, 'Waiting for Hover', 'waiting: Waiting for Hover');
  eq(stepTexts(root), ['Waiting to start', 'Waiting to start'], 'waiting: each device');
  eq(one(one(root, 'rv-live'), 'rv-run-note').textContent, 'The results show here when the test ends.',
    'waiting: no update yet');
  root = show(answer(liveTest(), [], running({wingmate: 'off', current_case: null, devices: []})), {task: 'T-2'});
  eq(one(one(root, 'rv-live'), 'rv-verdict').textContent, 'Hover is off', 'off: Hover is off');
  root = show(answer(liveTest(), [], running({state: 'queued', current_case: null, devices: [
    {tag: 'W044', state: 'waiting'}, {tag: 'ET65', state: 'running', step: null},
    {tag: 'TA5', state: 'finished', step: 12, of: 12, text: 'Done', shot_url: 'https://example.com/x.png'}]})),
  {task: 'T-2'});
  eq(stepTexts(root), ['Finished', 'Starting', 'Waiting to start'], 'states: finished, starting, waiting');
  check(all(root, 'rv-run-shot').length === 0 && all(root, 'rv-no-shot').length === 3, 'states: no picture from ' +
    'outside /files/');
  eq(one(one(root, 'rv-live'), 'rv-verdict').textContent, 'Waiting to start', 'states: a queued test waits to start');
  root = show(answer(testOf({also_running: [{task: 'T-3', title: 'Map import', started: todayAt(10, 0)}]}),
    [card()]));
  const also = link(one(root, 'rv-top'), 'Also running: Map import ›');
  check(also && also.getAttribute('href') === '#/results?task=T-3', 'also running: one line to the other test');
})();

// 9. the running test ends without results (404); a failed look tries again; the page changes: no more looks
(function () {
  let root = show(answer(liveTest(), [], running()), {task: 'T-2'});
  net.getError = new Error('Cannot reach the app server.');
  fireTimers();
  check(timers.length === 1 && one(root, 'rv-live') !== null, 'look failed: tries again');
  net.getError = httpError(404, 'no test T-2: it has no runs and is not running');
  fireTimers();
  eq(one(one(root, 'rv-live'), 'rv-verdict').textContent, 'Ended without results', 'gone: the box says so');
  check(one(root, 'rv-live').textContent.indexOf('This test is no longer running, and no result has come in: its ' +
    'start was cancelled or stopped before any run.') >= 0, 'gone: in plain words');
  check(timers.length === 0, 'gone: no more looks');
  root = show(answer(liveTest(), [], running()), {task: 'T-2'});
  alive = false;
  fireTimers();
  check(net.gets.length === 0 && timers.length === 0, 'page change: no look after the page changed');
})();

// ---- the one Sun Orange button: File ticket > Post comment > Report issue > Plan again > Add result ----

// 10. the order, case by case
(function () {
  const draft = comment({state: 'draft', text: TEXT, sha256: 'aa11'});
  let root = show(answer(testOf({comment: draft}), [card({tickets: [ticket()]})]));
  onlyPrimary(root, 'File ticket', 'order: a ticket draft first');
  check(isPrimary(button(root, 'File ticket')) && button(root, 'Post comment').classList.contains('btn-secondary') &&
    button(root, 'Report issue').classList.contains('btn-secondary'), 'order: Post comment and Report issue secondary');
  root = show(answer(testOf({comment: draft}), [card({tickets: [ticket({state: 'filed', filed: true, cl_id: 'CL-7'})]})]));
  onlyPrimary(root, 'Post comment', 'order: the ticket filed, then Post comment');
  root = show(answer(testOf({comment: draft}), [card()]));
  onlyPrimary(root, 'Post comment', 'order: Post comment before Report issue');
  root = show(answer(testOf(), [passedCard(), card({key: 'T-1|TERR-010', case: 'TERR-010'}),
    card({key: 'T-1|TERR-011', case: 'TERR-011'})]));
  onlyPrimary(root, 'Report issue', 'order: Report issue');
  check(isPrimary(button(rows(root)[1], 'Report issue')) && !isPrimary(button(rows(root)[2], 'Report issue')),
    'order: in the first row where something went wrong');
  root = show(answer(testOf({result: RESULT_ERR, plan_again: PLAN_AGAIN}), [errorCard()]));
  onlyPrimary(root, 'Plan again', 'order: Plan again before Add result');
  root = show(answer(testOf({result: RESULT_PASS}), [passedCard()]));
  onlyPrimary(root, 'Add result', 'order: Add result last');
  // closing the row of the File ticket gives the button to the next that shows
  root = show(answer(testOf({comment: draft}), [card({tickets: [ticket()]})]));
  rows(root)[0].open = false;
  for (const fn of rows(root)[0].listeners.toggle || []) fn({});
  onlyPrimary(root, 'Post comment', 'order: a closed row\'s File ticket does not count');
})();

// ---- Report issue on a row ----

// 11. no draft yet: Report issue is the one primary; the press asks for a draft, then waits for it
(function () {
  const root = show(answer(testOf(), [card()]));
  const row = rows(root)[0];
  const report = button(row, 'Report issue');
  onlyPrimary(root, 'Report issue', 'report next');
  report.click();
  flush();
  check(net.sends.length === 1 && net.sends[0].path === '/api/tickets/draft' &&
    JSON.stringify(net.sends[0].body) === JSON.stringify({task: 'T-1', case: 'TERR-012'}),
  'report: POST /api/tickets/draft {task, case}');
  check(report.disabled, 'report: disabled while the press is on its way');
  onlyPrimary(root, 'Report issue', 'report pressing');
  report.click();
  flush();
  check(net.sends.length === 1, 'report: a second press sends nothing');
  check(button(root, 'Mark done…').disabled, 'report: Mark done is off while a draft is asked for');
  net.send.resolve({request: {id: 'W-20261103-010', state: 'new', type: 'draft'}});
  flush();
  const panel = ticketPanel(row);
  check(visible(panel) && panel.querySelector('.rv-status').textContent === 'Drafting the ticket…',
    'report: "Drafting the ticket…", got ' + (panel && panel.textContent));
  check(document.activeElement === panel.querySelector('.rv-status'), 'report: focus on the drafting status');
  check(report.disabled && timers.length === 1 && timers[0].ms === 5000, 'report: disabled, one look armed');
  net.answer = answer(testOf(), [card({tickets: [{state: 'drafting', request: {id: 'W-20261103-010', state: 'claimed'}}]})]);
  fireTimers();
  eq(net.gets, ['/api/results/cards?task=T-1'], 'report: the look names the test');
  check(panel.querySelector('.rv-status').textContent === 'Drafting the ticket… Hover is on it.',
    'report: the worker took it, got ' + panel.querySelector('.rv-status').textContent);
  check(timers.length === 1, 'report: still looking');
  net.answer = answer(testOf(), [card({tickets: [ticket()]})]);
  fireTimers();
  check(timers.length === 0, 'report: the looks stop when the draft came');
  check(!visible(panel.querySelector('.rv-status')), 'report: the drafting line is gone');
  check(visible(one(row, 'rv-ticket')) && one(row, 'rv-ticket-title').textContent === '[CT] Terrain import fails offline',
    'report: the draft shows in the row');
  onlyPrimary(root, 'File ticket', 'report drafted');
  check(!report.disabled && report.classList.contains('btn-secondary'), 'report drafted: Report issue back, secondary');
  check(!button(root, 'Mark done…').disabled, 'report drafted: Mark done back');
})();

// 12. a draft already there: shown in its row; Report issue points at it and sends nothing
(function () {
  const root = show(answer(testOf(), [card({tickets: [ticket()]})]));
  check(visible(one(root, 'rv-ticket')), 'draft: shown in the opened row');
  onlyPrimary(root, 'File ticket', 'draft');
  button(root, 'Report issue').click();
  flush();
  check(net.sends.length === 0 && document.activeElement === button(root, 'File ticket'),
    'draft: Report issue focuses File ticket, nothing sent');
})();

// 13. the draft press refused: 409 (with a look right away), stale page, no server
[[httpError(409, 'A ticket draft for this run is already being written.'),
  'A ticket draft for this run is already being written.'],
[httpError(403, 'missing or wrong token'), 'The app server restarted after this page was opened. Reload the page.'],
// no Touch ID on this press, so a 403 is never "Touch ID cancelled"
[httpError(403, 'Forbidden'), 'Report issue did not go through: Forbidden'],
[httpError(400, 'case: not a case of this task'), 'Report issue did not go through: case: not a case of this task'],
[new Error('Cannot reach the app server.'), 'Report issue did not go through: Cannot reach the app server.'],
].forEach(function (pair) {
  const root = show(answer(testOf(), [card()]));
  const report = button(root, 'Report issue');
  report.click();
  flush();
  net.answer = answer(testOf(), [card({tickets: [{state: 'drafting', request: {id: 'W-9', state: 'new'}}]})]);
  net.send.reject(pair[0]);
  flush();
  check(errorIn(ticketPanel(root)) === pair[1], 'draft refused ' + pair[0].message + ': got ' +
    errorIn(ticketPanel(root)));
  check(visible(ticketPanel(root)), 'draft refused ' + pair[0].message + ': the error shows');
  if (pair[0].status === 409) {
    eq(net.gets, ['/api/results/cards?task=T-1'], '409: one look right away, at the test');
    check(ticketPanel(root).querySelector('.rv-status').textContent === 'Drafting the ticket… Waiting for Hover.',
      '409: the open request shows');
    check(report.disabled && timers.length === 1, '409: waits for the draft');
  } else {
    check(!report.disabled && document.activeElement === report, 'draft refused ' + pair[0].message +
      ': the button back, focused');
    check(net.gets.length === 0 && timers.length === 0, 'draft refused ' + pair[0].message + ': no looks');
  }
});

// 14. the draft request ends without a draft: the worker's words, else after two looks the plain note; a draft
// request open on load is shown and waited for; one that failed before the page opened shows the worker's words
(function () {
  let root = show(answer(testOf(), [card()]));
  button(root, 'Report issue').click();
  flush();
  net.send.resolve({request: {id: 'W-11', state: 'new'}});
  flush();
  net.answer = answer(testOf(), [card({tickets: [{state: 'failed', error: 'The worker could not read the run record.'}]})]);
  fireTimers();
  check(errorIn(ticketPanel(root)) === 'The worker could not read the run record.', 'draft failed: the worker\'s words');
  check(!button(root, 'Report issue').disabled && timers.length === 0, 'draft failed: the button back, no looks');
  onlyPrimary(root, 'Report issue', 'draft failed');

  root = show(answer(testOf(), [card()]));
  button(root, 'Report issue').click();
  flush();
  net.send.resolve({request: {id: 'W-12', state: 'new'}});
  flush();
  net.answer = answer(testOf(), [card()]);
  fireTimers();
  check(timers.length === 1 && errorIn(ticketPanel(root)) === '', 'draft gone: one empty look is not yet the end');
  fireTimers();
  check(errorIn(ticketPanel(root)) === 'The ticket was not drafted. Press Report issue to try again.',
    'draft gone: the note after two looks, got ' + errorIn(ticketPanel(root)));
  check(timers.length === 0 && !button(root, 'Report issue').disabled, 'draft gone: the button back');

  root = show(answer(testOf(), [card({tickets: [{state: 'drafting', request: {id: 'W-13', state: 'new'}}]})]));
  check(visible(ticketPanel(root)) &&
    ticketPanel(root).querySelector('.rv-status').textContent === 'Drafting the ticket… Waiting for Hover.',
  'drafting on load: shown');
  check(button(root, 'Report issue').disabled && timers.length === 1, 'drafting on load: waits');
  net.answer = answer(testOf(), [card({tickets: [ticket()]})]);
  fireTimers();
  check(visible(one(root, 'rv-ticket')), 'drafting on load: the draft shows when it comes');
  onlyPrimary(root, 'File ticket', 'drafting on load');

  root = show(answer(testOf(), [card({tickets: [{title: '', state: 'failed', error: 'Suspected, not confirmed yet: ' +
    're-run the test 3 times on each device.', request: {id: 'W-50', state: 'failed'}}]})]));
  check(visible(ticketPanel(root)) && errorIn(ticketPanel(root)) === 'Suspected, not confirmed yet: re-run the test 3 ' +
    'times on each device.', 'failed draft on load: the worker\'s words show');
  check(!button(root, 'Report issue').disabled && timers.length === 0, 'failed draft on load: Report issue, no looks');
  onlyPrimary(root, 'Report issue', 'failed draft on load');
})();

// ---- Add result and Post comment, once per test in the box ----

// 15. Add result: the worker writes the comment; it opens; Post comment with the Touch ID wait; posted
(function () {
  const t = function (extra) { return testOf(Object.assign({result: RESULT_PASS}, extra || {})); };
  const root = show(answer(t(), [passedCard()]));
  const add = button(root, 'Add result');
  onlyPrimary(root, 'Add result', 'add next');
  check(!visible(one(root, 'rv-comment')) && !visible(button(root, 'Post comment')), 'add: no comment before the press');
  add.click();
  flush();
  check(net.sends.length === 1 && net.sends[0].path === '/api/results/comment' &&
    JSON.stringify(net.sends[0].body) === JSON.stringify({task: 'T-1'}), 'add: POST /api/results/comment {task}');
  check(add.disabled, 'add: disabled while the press is on its way');
  net.send.resolve({request: {id: 'W-20261103-020', state: 'new', type: 'write'}});
  flush();
  check(commentStatus(root) === 'Writing the comment…', 'add: "Writing the comment…", got ' + commentStatus(root));
  check(add.disabled && timers.length === 1 && timers[0].ms === 5000, 'add: waits for the comment');
  check(button(root, 'Mark done…').disabled, 'add: Mark done is off while the comment is written');
  net.answer = answer(t({comment: comment({state: 'drafting'})}), [passedCard()]);
  fireTimers();
  eq(net.gets, ['/api/results/cards?task=T-1'], 'add: the look names the test');
  check(commentStatus(root) === 'Writing the comment…' && timers.length === 1, 'add: still writing');
  net.answer = answer(t({comment: comment({state: 'draft', text: TEXT, sha256: 'aa11'})}), [passedCard()]);
  fireTimers();
  check(timers.length === 0, 'add: the looks stop when the comment came');
  eq(commentText(root), TEXT, 'add: the comment opens with its text');
  eq(one(root, 'rv-comment').firstChild.textContent, 'Results comment, not posted yet', 'add: its fold says so');
  const post = button(root, 'Post comment');
  const copyB = button(root, 'Copy ClickUp comment');
  onlyPrimary(root, 'Post comment', 'add drafted');
  check(!visible(add), 'add drafted: Add result goes once the comment is written');
  check(copyB && visible(copyB) && copyB.classList.contains('btn-secondary') && copyB.copyText() === TEXT,
    'add drafted: Copy ClickUp comment, a secondary, with the text');
  check(post.nextSibling === copyB, 'add drafted: Copy ClickUp comment next to Post comment');
  check(commentStatus(root) === 'Not posted yet. Post comment asks for Touch ID on the Mac, then the app posts it to ' +
    'the ClickUp test task.', 'add drafted: says it is not posted, got ' + commentStatus(root));
  check(net.sends.length === 1, 'add drafted: nothing posted without a press');
  check(!button(root, 'Mark done…').disabled, 'add drafted: Mark done back');

  post.click();
  flush();
  check(net.sends.length === 2 && net.sends[1].path === '/api/results/comment/post' &&
    JSON.stringify(net.sends[1].body) === JSON.stringify({task: 'T-1', sha256: 'aa11'}),
  'post: POST /api/results/comment/post {task, sha256}');
  check(commentStatus(root) === 'Confirm with Touch ID on the Mac…' && post.disabled, 'post: the Touch ID wait');
  post.click();
  flush();
  check(net.sends.length === 2, 'post: a second press while waiting sends nothing');
  const at = todayAt(10, 40);
  // the server has the posted marker before it answers: the test reads as posted and in Done from then on
  net.answer = answer(t({group: 'done', can_mark_done: false, mark_done_why: 'It is in Done already.',
    comment: comment({state: 'posted', text: TEXT, sha256: 'aa11', posted_at: at}),
    done_how: {how: 'posted', at: at, by_task: null, url: CU.url}}), [passedCard()]);
  net.gets = [];
  net.send.resolve({posted_at: at, comment_id: '90001'});
  flush();
  eq(net.gets, ['/api/results/cards?task=T-1'], 'post: the test is read again, as after Mark done');
  check(commentStatus(root) === 'Posted Today 10:40 ↗', 'post: posted with the time, got ' + commentStatus(root));
  const open = link(box(root), 'Posted Today 10:40 ↗');
  check(open && open.getAttribute('href') === CU.url && open.getAttribute('target') === '_blank', 'post: the task link');
  check(!visible(post) && commentText(root) === null, 'post: the text and Post comment are put away');
  const shownButton = function (text) { const b = button(root, text); return b !== null && visible(b); };
  check(!visible(add) && !shownButton('Add result') && !shownButton('Post comment'), 'post: no Add result once posted');
  check(!shownButton('Mark done…'), 'post: the test is in Done, no Mark done');
  noPrimary(root, 'post');
  check(document.activeElement === one(box(root), 'rv-status'), 'post: focus on the posted status');
})();

// 15b. FINDINGS #11: a posted comment moves the test to Done, and a test in Done shows no Plan again and no Report
// issue: the page reads the test again (the server's plan_again is null there); when that look fails, the page puts
// both away itself
(function () {
  const at = todayAt(10, 40);
  const draft = comment({state: 'draft', text: TEXT, sha256: 'aa11'});
  const posted = comment({state: 'posted', text: TEXT, sha256: 'aa11', posted_at: at});
  const doneHow = {how: 'posted', at: at, by_task: null, url: CU.url};
  const postIt = function (root) {
    const post = button(root, 'Post comment');
    post.click();  // the first press opens the folded text
    post.click();
    flush();
  };
  // a test error test: Plan again is there before the post
  let root = show(answer(testOf({result: RESULT_ERR, plan_again: PLAN_AGAIN, comment: draft}), [errorCard()]));
  check(visible(button(root, 'Plan again')), 'posted, Plan again: there before the post');
  postIt(root);
  net.answer = answer(testOf({result: RESULT_ERR, group: 'done', can_mark_done: false, plan_again: null,
    comment: posted, done_how: doneHow}), [errorCard()]);
  net.send.resolve({posted_at: at, comment_id: '90002'});
  flush();
  check(button(root, 'Plan again') === null, 'posted: no Plan again once the test is in Done');
  check(commentStatus(root) === 'Posted Today 10:40 ↗', 'posted: the posted status, got ' + commentStatus(root));
  noPrimary(root, 'posted, read again');
  // the look after the post fails: Plan again is put away here
  root = show(answer(testOf({result: RESULT_ERR, plan_again: PLAN_AGAIN, comment: draft}), [errorCard()]));
  postIt(root);
  net.getError = new Error('Cannot reach the app server.');
  net.send.resolve({posted_at: at, comment_id: '90003'});
  flush();
  check(!visible(button(root, 'Plan again')), 'posted, no newer copy: Plan again put away');
  check(commentStatus(root) === 'Posted Today 10:40 ↗', 'posted, no newer copy: posted, got ' + commentStatus(root));
  noPrimary(root, 'posted, no newer copy');
  net.getError = null;
  // a look that still has the draft (an older copy) never brings Post comment back
  root = show(answer(testOf({result: RESULT_ERR, plan_again: PLAN_AGAIN, comment: draft}), [errorCard()]));
  postIt(root);
  net.send.resolve({posted_at: at, comment_id: '90006'});
  flush();
  check(commentStatus(root) === 'Posted Today 10:40 ↗' && !visible(button(root, 'Post comment')) &&
    !visible(button(root, 'Plan again')), 'posted, an older copy: still posted, no Post comment, no Plan again');
  noPrimary(root, 'posted, an older copy');
  // a failure with no ticket yet: Report issue on its row goes as well
  root = show(answer(testOf({comment: draft}), [card()]));
  check(visible(button(root, 'Report issue')), 'posted, Report issue: there before the post');
  postIt(root);
  net.getError = new Error('Cannot reach the app server.');
  net.send.resolve({posted_at: at, comment_id: '90004'});
  flush();
  check(!visible(button(root, 'Report issue')), 'posted, no newer copy: Report issue put away');
  net.getError = null;
  root = show(answer(testOf({comment: draft}), [card()]));
  postIt(root);
  net.answer = answer(testOf({group: 'done', can_mark_done: false, comment: posted, done_how: doneHow}), [card()]);
  net.send.resolve({posted_at: at, comment_id: '90005'});
  flush();
  check(button(root, 'Report issue') === null && rows(root)[0].open, 'posted, read again: the failed row, without ' +
    'Report issue');
})();

// 16. a written comment on load: folded; Post comment is the orange button; a press while it is folded opens it and
// sends nothing; the next press posts the sha256 of the text shown. Refusals: the person's no, a stale page, a changed
// text (409, looked at again), ClickUp's no (502)
[[httpError(403, 'not approved'), 'Not approved (Touch ID cancelled).'],
[httpError(403, 'Not approved: no answer to the Touch ID prompt within 90 s'),
  'Not approved: no answer to the Touch ID prompt within 90 s'],
[httpError(403, 'missing or wrong token'), 'The app server restarted after this page was opened. Reload the page.'],
[httpError(409, 'The comment changed since you read it. Read it again.'),
  'The comment changed since you read it. Read it again.'],
[httpError(502, 'ClickUp refused the comment: Token invalid (HTTP 401)'),
  'Not posted: ClickUp refused the comment: Token invalid (HTTP 401)'],
[new Error('Cannot reach the app server.'), 'Post comment did not go through: Cannot reach the app server.'],
].forEach(function (pair) {
  const root = show(answer(testOf({comment: comment({state: 'draft', text: TEXT, sha256: 'aa11'})}), [card()]));
  check(commentText(root) === null && one(root, 'rv-comment').firstChild.textContent ===
    'Results comment, not posted yet', 'refused ' + pair[0].message + ': the comment is folded on load');
  check(button(root, 'Add result') === null || !visible(button(root, 'Add result')),
    'refused ' + pair[0].message + ': no Add result for a written comment');
  onlyPrimary(root, 'Post comment', 'refused ' + pair[0].message + ': on load');
  const post = button(root, 'Post comment');
  post.click();
  flush();
  check(net.sends.length === 0 && commentText(root) === TEXT, 'refused ' + pair[0].message + ': the first press opens ' +
    'the text and sends nothing');
  check(document.activeElement === post, 'refused ' + pair[0].message + ': focus on Post comment when it opens');
  post.click();
  flush();
  check(net.sends.length === 1 && JSON.stringify(net.sends[0].body) === JSON.stringify({task: 'T-1', sha256: 'aa11'}),
    'refused ' + pair[0].message + ': the press sends the sha256 of the text shown');
  net.answer = answer(testOf({comment: comment({state: 'draft', text: 'New text', sha256: 'bb22'})}), [card()]);
  net.send.reject(pair[0]);
  flush();
  check(errorIn(box(root)) === pair[1], 'refused ' + pair[0].message + ': got ' + errorIn(box(root)));
  check(visible(post) && !post.disabled && document.activeElement === post, 'refused ' + pair[0].message +
    ': Post comment back, focused');
  onlyPrimary(root, 'Post comment', 'refused ' + pair[0].message);
  if (pair[0].status === 409) {
    check(net.gets.length === 1 && commentText(root) === 'New text', '409: looked again, the new text shows');
    post.click();
    flush();
    check(net.sends[net.sends.length - 1].body.sha256 === 'bb22', '409: the next press sends the new text\'s sha256');
  } else {
    check(net.gets.length === 0 && commentText(root) === TEXT, 'refused ' + pair[0].message + ': the same text stays');
  }
});

// 17. the comment on load: posted, posting, drafting, failed with and without a text, a draft without a sha256
(function () {
  const at = todayAt(8, 12);
  let root = show(answer(testOf({result: RESULT_PASS, group: 'done', comment: comment({state: 'posted', text: TEXT,
    sha256: 'aa11', posted_at: at}), done_how: {how: 'posted', at: at, by_task: null, url: CU.url}}), [passedCard()]));
  check(commentStatus(root) === 'Posted Today 08:12 ↗', 'posted on load: the status, got ' + commentStatus(root));
  check((button(root, 'Add result') === null || !visible(button(root, 'Add result'))) && timers.length === 0,
    'posted on load: no Add result, no looks');
  check(one(root, 'rv-done-line') === null, 'posted on load: the comment part says it, once');
  noPrimary(root, 'posted on load');

  root = show(answer(testOf({comment: comment({state: 'posting', text: TEXT, sha256: 'aa11'})}), [card()]));
  check(commentStatus(root) === 'Posting the comment…' && !visible(button(root, 'Post comment')),
    'posting on load: the status, no Post comment');
  check(timers.length === 1, 'posting on load: waits');
  net.answer = answer(testOf({comment: comment({state: 'posted', text: TEXT, sha256: 'aa11', posted_at: at})}), [card()]);
  fireTimers();
  check(commentStatus(root).indexOf('Posted Today ') === 0 && timers.length === 0, 'posting on load: then posted');

  root = show(answer(testOf({comment: comment({state: 'drafting'})}), [card()]));
  check(commentStatus(root) === 'Writing the comment…' && timers.length === 1, 'drafting on load: shown, waits');
  check(button(root, 'Add result').disabled, 'drafting on load: Add result off');
  net.answer = answer(testOf({comment: comment({state: 'draft', text: TEXT, sha256: 'aa11'})}), [card()]);
  fireTimers();
  check(commentText(root) === TEXT, 'drafting on load: the text opens when it comes');

  // review 2026-11-03: "failed" with a text is a comment the server will not post (views.py comment_draft: too long, a
  // token in it), sent without a sha256; Post comment would only get a 400, so Add result asks for a new one
  const tooLong = 'The comment has 1180 characters; at most 1000 can be posted. Press Add result for a shorter one.';
  root = show(answer(testOf({result: RESULT_PASS, comment: comment({state: 'failed', text: TEXT, error: tooLong})}),
    [passedCard()]));
  check(errorIn(box(root)) === tooLong, 'failed with a text: the error');
  one(root, 'rv-comment').open = true;
  for (const fn of one(root, 'rv-comment').listeners.toggle || []) fn({});
  check(commentText(root) === TEXT, 'failed with a text: the text can be read');
  check(!visible(button(root, 'Post comment')), 'failed with a text: no Post comment, the server would refuse it');
  check(commentStatus(root).indexOf('Not posted yet') < 0, 'failed with a text: no "Not posted yet" line');
  onlyPrimary(root, 'Add result', 'failed with a text');
  button(root, 'Add result').click();
  flush();
  check(net.sends.length === 1 && net.sends[0].path === '/api/results/comment' &&
    JSON.stringify(net.sends[0].body) === JSON.stringify({task: 'T-1'}), 'failed with a text: Add result asks again');
  if (net.send) net.send.resolve({request: {id: 'W-40', state: 'new'}});
  flush();
  check(commentStatus(root) === 'Writing the comment…' && commentText(root) === null,
    'failed with a text: then "Writing the comment…"');

  // a draft without a sha256 (an older server) is never sent with Post comment
  root = show(answer(testOf({comment: comment({state: 'draft', text: TEXT})}), [card()]));
  one(root, 'rv-comment').open = true;
  for (const fn of one(root, 'rv-comment').listeners.toggle || []) fn({});
  check(net.sends.length === 0 && commentText(root) === TEXT && !visible(button(root, 'Post comment')),
    'draft without a sha256: readable, no Post comment');

  root = show(answer(testOf({result: RESULT_PASS, comment: comment({state: 'failed', error: 'The worker could not ' +
    'write the comment.'})}), [passedCard()]));
  check(errorIn(box(root)) === 'The worker could not write the comment.', 'failed without a text: the error');
  check(!button(root, 'Add result').disabled, 'failed without a text: Add result back');
  onlyPrimary(root, 'Add result', 'failed without a text');
})();

// 18. Add result refused (409 looks again) and a request that ends without a comment
(function () {
  let root = show(answer(testOf({result: RESULT_PASS}), [passedCard()]));
  button(root, 'Add result').click();
  flush();
  net.answer = answer(testOf({result: RESULT_PASS, comment: comment({state: 'drafting'})}), [passedCard()]);
  net.send.reject(httpError(409, 'The comment for this test is already being written.'));
  flush();
  check(errorIn(box(root)) === 'The comment for this test is already being written.', 'add 409: the words');
  check(net.gets.length === 1 && commentStatus(root) === 'Writing the comment…' && timers.length === 1,
    'add 409: looked again, waits for the comment');

  root = show(answer(testOf({result: RESULT_PASS}), [passedCard()]));
  button(root, 'Add result').click();
  flush();
  net.send.resolve({request: {id: 'W-30', state: 'new'}});
  flush();
  net.answer = answer(testOf({result: RESULT_PASS}), [passedCard()]);
  fireTimers();
  fireTimers();
  check(errorIn(box(root)) === 'The comment was not written. Press Add result to try again.',
    'add gone: the note, got ' + errorIn(box(root)));
  check(!button(root, 'Add result').disabled && timers.length === 0, 'add gone: the button back, no looks');
})();

// 19. no ClickUp task: Add result greyed out with the reason; a test linked only by its custom id (a CL link) gets
// Add result too (the server takes the custom id)
(function () {
  let root = show(answer(testOf({clickup: null, result: RESULT_PASS}), [passedCard()]));
  const add = button(root, 'Add result');
  check(add.disabled, 'no ClickUp: Add result disabled');
  const why = all(box(root), 'rv-why').filter(visible).map(function (x) { return x.textContent; });
  eq(why, ['Add result: This test has no ClickUp task to add the result to.'], 'no ClickUp: the reason');
  check(add.getAttribute('aria-describedby') === all(box(root), 'rv-why').filter(visible)[0].getAttribute('id'),
    'no ClickUp: the reason describes the button');
  root = show(answer(testOf({result: RESULT_PASS, clickup: {task_id: null, custom_id: 'CL-80002',
    url: 'https://app.clickup.com/t/1919192/CL-80002', title: null}}), [passedCard()]));
  check(!button(root, 'Add result').disabled && all(box(root), 'rv-why').filter(visible).length === 0,
    'CL link: Add result, no reason line');
  check(one(root, 'rv-meta').textContent.indexOf('ClickUp: CL-80002 ↗') === 0, 'CL link: the custom id in the header');
})();

// 20. review 2026-11-03: a draft of exactly 1000 characters is shown and posted as the server sent it (its sha256),
// never as the file read again; a "failed" comment too long to post is read whole from its file, to read only, and a
// failed read links the file
(function () {
  const long = 'x'.repeat(1000);
  const file = 'tests/2026-11-03-x/COMMENT_86c0abc_T-1.md';
  const outputs = {summary: null, comment_text: null, comment_file: file};
  let root = show(answer(testOf({outputs: outputs, comment: comment({state: 'draft', text: long, sha256: 'cc33'})}),
    [card()]));
  check(net.fetches.length === 0, 'draft of 1000: the file is not read again');
  button(root, 'Post comment').click();
  flush();
  check(commentText(root) === long && !button(root, 'Post comment').disabled,
    'draft of 1000: the server\'s text and Post comment');
  check(button(root, 'Copy ClickUp comment').copyText() === long, 'draft of 1000: Copy takes the server\'s text');
  button(root, 'Post comment').click();
  flush();
  check(net.sends.length === 1 && net.sends[0].body.sha256 === 'cc33', 'draft of 1000: posts the sha256 it showed');

  const tooLong = 'The comment has 1003 characters; at most 1000 can be posted. Press Add result for a shorter one.';
  root = show(answer(testOf({result: RESULT_PASS, outputs: outputs, comment: comment({state: 'failed', text: long,
    error: tooLong})}), [passedCard()]));
  check(net.fetches.length === 1 && net.fetches[0] === '/files/' + file, 'too long: the whole file is read');
  check(commentStatus(root) === 'Loading the whole comment…', 'too long: loading the whole comment');
  net.fetch.resolve({ok: true, status: 200, text: function () { return Promise.resolve(long + 'END'); }});
  flush();
  one(root, 'rv-comment').open = true;
  for (const fn of one(root, 'rv-comment').listeners.toggle || []) fn({});
  check(commentText(root) === long + 'END', 'too long: the whole text shows');
  check(!visible(button(root, 'Post comment')) && errorIn(box(root)) === tooLong,
    'too long: no Post comment, the reason shows');
  check(button(root, 'Copy ClickUp comment').copyText() === long + 'END', 'too long: Copy takes the whole text');
  // Add result: the worker writes a new one, here the first 1000 characters; that draft shows as sent, not the old file
  button(root, 'Add result').click();
  flush();
  check(net.sends.length === 1 && net.sends[0].path === '/api/results/comment', 'too long: Add result asks again');
  if (net.send) net.send.resolve({request: {id: 'W-41', state: 'new'}});
  flush();
  net.answer = answer(testOf({result: RESULT_PASS, outputs: outputs, comment: comment({state: 'draft', text: long,
    sha256: 'dd44'})}), [passedCard()]);
  fireTimers();
  check(commentText(root) === long && visible(button(root, 'Post comment')),
    'too long, then a draft: the draft\'s own text and Post comment');
  check(button(root, 'Copy ClickUp comment').copyText() === long, 'too long, then a draft: Copy takes the draft');

  root = show(answer(testOf({result: RESULT_PASS, outputs: outputs, comment: comment({state: 'failed', text: long,
    error: tooLong})}), [passedCard()]));
  net.fetch.reject(new Error('HTTP 404'));
  flush();
  const whole = link(root, 'Open the whole comment');
  check(whole && whole.getAttribute('href') === '/files/' + file, 'too long, read failed: the file is linked');
  check(!visible(button(root, 'Post comment')) && !button(root, 'Add result').disabled,
    'too long, read failed: Add result, no Post comment');
})();

// 21. a test error test has Add result too, and no Report issue anywhere
(function () {
  const root = show(answer(testOf({result: RESULT_ERR}), [errorCard()]));
  check(button(root, 'Report issue') === null && button(root, 'Add result') !== null, 'test error: Add result, no ' +
    'Report issue');
  onlyPrimary(root, 'Add result', 'test error without Plan again');
  rows(root)[0].open = true;
  check(tags(one(rows(root)[0], 'rv-facts'), 'DT').map(function (x) { return x.textContent; }).indexOf('Why') >= 0,
    'test error: Why is always shown');
})();

// 22. a test that stopped early: said in the box; Add result only where the server takes it (results in)
(function () {
  const root = show(answer(testOf({state: 'running', stopped_early: true, can_mark_done: false,
    mark_done_why: 'Only a test with results can be marked done; this one is running.'}), [card()]));
  eq(one(box(root), 'rv-verdict').textContent, 'Failed (suspected) in 1 of 2 cases, on TA5; 1 passed' + 'Stopped early',
    'stopped early: said next to the sentence');
  check(!visible(button(root, 'Add result')), 'stopped early: no Add result before the results are in');
  check(button(root, 'Mark done…').disabled, 'stopped early: Mark done off, with its why');
  onlyPrimary(root, 'Report issue', 'stopped early');
})();

// ---- the debug pass of 2026-11-05 (tests/2026-11-05-wingmate-debug/FINDINGS.md) ----

// 23. FINDINGS #21: a newer plan of the same ClickUp task (plan_again.newer). While it can still run, the box links it
// in Plan again's place and Plan again is not the next press; once it expired, Plan again is a secondary button and its
// press still sends the Prepare again body only
(function () {
  const fresh = Object.assign({}, PLAN_AGAIN, {newer: {id: 'W-20261105-001', fresh: true}});
  let root = show(answer(testOf({result: RESULT_ERR, plan_again: fresh, next: {press: 'add_result',
    label: 'Add result', case: null}}), [errorCard()]));
  check(button(root, 'Plan again') === null, 'newer plan, fresh: no Plan again');
  const to = link(box(root), 'A newer plan of this test exists ›');
  check(to !== null && to.getAttribute('href') === '#/new?show=plan&req=W-20261105-001',
    'newer plan, fresh: the link to it, got ' + (to && to.getAttribute('href')));
  onlyPrimary(root, 'Add result', 'newer plan, fresh: Plan again is not the next press');
  const expired = Object.assign({}, PLAN_AGAIN, {newer: {id: 'W-20261105-001', fresh: false}});
  root = show(answer(testOf({result: RESULT_ERR, plan_again: expired}), [errorCard()]));
  const plan = button(root, 'Plan again');
  check(plan !== null && visible(plan) && plan.classList.contains('btn-secondary'),
    'newer plan, expired: Plan again, secondary');
  check(link(box(root), 'A newer plan of this test exists ›') === null, 'newer plan, expired: no link');
  onlyPrimary(root, 'Add result', 'newer plan, expired');
  plan.click();
  flush();
  eq(net.sends.map(function (s) { return [s.path, s.body]; }), [['/api/worker/check', PLAN_AGAIN]],
    'newer plan, expired: the press sends the Prepare again body, without newer');
  root = show(answer(testOf({result: RESULT_ERR, plan_again: Object.assign({}, PLAN_AGAIN, {newer: null})}),
    [errorCard()]));
  onlyPrimary(root, 'Plan again', 'no newer plan: Plan again is the next press');
})();

// 24. FINDINGS #22: a refusal of the press goes when what it waited for came: Report issue's 409, then the draft;
// Add result's 409, then the comment (a Post comment 409 whose text changed stays: test 16)
(function () {
  let root = show(answer(testOf(), [card()]));
  button(root, 'Report issue').click();
  flush();
  net.answer = answer(testOf(), [card({tickets: [{state: 'drafting', request: {id: 'W-20261105-090', state: 'claimed'}}]})]);
  net.send.reject(httpError(409, 'W-20261105-090 is still drafting a ticket for this test'));
  flush();
  eq(errorIn(ticketPanel(root)), 'W-20261105-090 is still drafting a ticket for this test', 'report 409: the words');
  net.answer = answer(testOf(), [card({tickets: [ticket()]})]);
  fireTimers();
  check(visible(one(root, 'rv-ticket')), 'report 409, then the draft: the draft shows');
  eq(errorIn(ticketPanel(root)), '', 'report 409, then the draft: the 409 is gone');
  onlyPrimary(root, 'File ticket', 'report 409, then the draft');
  // the look right after the 409 has the draft already
  root = show(answer(testOf(), [card()]));
  button(root, 'Report issue').click();
  flush();
  net.answer = answer(testOf(), [card({tickets: [ticket()]})]);
  net.send.reject(httpError(409, 'W-20261105-091 is still drafting a ticket for this test'));
  flush();
  check(visible(one(root, 'rv-ticket')) && errorIn(ticketPanel(root)) === '',
    'report 409, the draft in the next look: no stale 409, got ' + errorIn(ticketPanel(root)));

  root = show(answer(testOf({result: RESULT_PASS}), [passedCard()]));
  button(root, 'Add result').click();
  flush();
  net.answer = answer(testOf({result: RESULT_PASS, comment: comment({state: 'drafting'})}), [passedCard()]);
  net.send.reject(httpError(409, 'the drafts for this task are already being written'));
  flush();
  eq(errorIn(box(root)), 'the drafts for this task are already being written', 'add 409: the words');
  net.answer = answer(testOf({result: RESULT_PASS, comment: comment({state: 'draft', text: TEXT, sha256: 'aa11'})}),
    [passedCard()]);
  fireTimers();
  check(commentText(root) === TEXT, 'add 409, then the comment: the comment shows');
  eq(errorIn(box(root)), '', 'add 409, then the comment: the 409 is gone');
  onlyPrimary(root, 'Post comment', 'add 409, then the comment');
})();

// 25. FINDINGS #25: a live case row says '–' for a device the plan does not run that case on (null), not 'waiting'
(function () {
  const root = show(answer(liveTest(), [], running({case_rows: [
    {case: 'SITE-001', title: 'Offline site, then a flight', devices: {TA5: 'running', ET65: null}},
    {case: 'SITE-002', title: 'Offline site named on the tablet', devices: {TA5: null, ET65: 'waiting'}}]})),
  {task: 'T-2'});
  const live = all(root, 'rv-live-row');
  eq(live.map(function (r) { return r.querySelectorAll('.rv-cell').map(function (c) { return c.textContent; }); }),
    [['TA5: running', 'ET65: –does not run this case'], ['TA5: –does not run this case', 'ET65: waiting']],
    'live rows: – for a device that does not run the case');
  check(live[0].querySelectorAll('.rv-cell')[1].classList.contains('rv-cell-none') &&
    live[0].querySelectorAll('.rv-live-waiting').length === 0, 'live rows: the – cell is not a waiting cell');
})();

// 26. FINDINGS #31: while a write request of the test is open (test.open_write), Add result is off and not the orange
// button; the page looks again and gives it back when the request ended
(function () {
  const root = show(answer(testOf({result: RESULT_PASS, open_write: true, can_mark_done: false,
    mark_done_why: 'Hover is still working on this test (a comment, a ticket draft or filing a ticket).'}),
  [passedCard()]));
  const add = button(root, 'Add result');
  check(visible(add) && add.disabled && add.classList.contains('btn-secondary') && !add.classList.contains('rv-next'),
    'open write: Add result off, not orange');
  noPrimary(root, 'open write');
  check(timers.length === 1 && timers[0].ms === 5000, 'open write: the page looks again');
  net.answer = answer(testOf({result: RESULT_PASS, open_write: false}), [passedCard()]);
  fireTimers();
  eq(net.gets, ['/api/results/cards?task=T-1'], 'open write: the look names the test');
  check(!add.disabled, 'open write ended: Add result back');
  onlyPrimary(root, 'Add result', 'open write ended');
  check(timers.length === 0, 'open write ended: no more looks');
  // a page that did not know yet: the press gets the 409, the look sees the open write; the refusal goes with it
  const r2 = show(answer(testOf({result: RESULT_PASS}), [passedCard()]));
  const add2 = button(r2, 'Add result');
  add2.click();
  flush();
  net.answer = answer(testOf({result: RESULT_PASS, open_write: true}), [passedCard()]);
  net.send.reject(httpError(409, 'the drafts for this task are already being written'));
  flush();
  check(add2.disabled && !isPrimary(add2) && errorIn(box(r2)) === 'the drafts for this task are already being written',
    'open write found by the 409: Add result off, the words');
  net.answer = answer(testOf({result: RESULT_PASS, open_write: false}), [passedCard()]);
  fireTimers();
  check(!add2.disabled && errorIn(box(r2)) === '', 'open write ended after the 409: Add result back, the 409 gone');
  onlyPrimary(r2, 'Add result', 'open write ended after the 409');
})();

// 27. FINDINGS #12: ?case=X with no engine run (404 "no run of X"): its back-test records are on Run records
(function () {
  document.body = new Element('body');
  net.answer = null;
  net.getError = httpError(404, 'no run of UPG-001');
  const root = document.body.appendChild(new Element('main'));
  window.QAResults.render(root, {get: function (k) { return k === 'case' ? 'UPG-001' : null; }},
    function () { return true; }, QA);
  flush();
  net.getError = null;
  const empty = one(root, 'rv-empty');
  eq(empty && tags(empty, 'P').map(function (p) { return p.textContent; }), ['No engine run of UPG-001. Its back-test ' +
    'records, if any, are in Run records ›', 'Start a test on New test, or see your tests in History.'],
  '?case=X without an engine run: the words');
  const rec = link(empty, 'Run records ›');
  check(rec !== null && rec.getAttribute('href') === '#/results?view=detail&case=UPG-001',
    '?case=X without an engine run: Run records filters the case, got ' + (rec && rec.getAttribute('href')));
  check(empty.textContent.indexOf('No run of UPG-001') < 0, '?case=X without an engine run: not "No run of"');
})();

print(JSON.stringify({failures: failures, checks: checks}));
