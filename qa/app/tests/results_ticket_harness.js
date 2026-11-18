// results_ticket_harness.js: runs static/results.js in JavaScriptCore's jsc with a small fake DOM and a fake window.QA,
// and checks the File ticket flow of a ticket draft (2026-11-03), since 2026-11-04 in the ticket part of the case row
// where a device failed (the one-test Results page, tests/2026-11-04-results-redesign/SPEC.md): the offline notice, one
// Sun Orange button on the page, the Touch ID wait, filing, filed, refused presses. Since 2026-11-05 (checks 8 to 10):
// a draft the server would not file (ticket.fileable false), a filing ticket the looks lost, and Report issue's wait
// for a new draft next to one set aside; since 2026-11-09 (check 11) the why on a failed row the server takes no
// ticket for. Run by test_results_tickets.py:
//   jsc results_ticket_harness.js -- <path of results.js>
// Prints one JSON line {"failures": [...], "checks": n}.
/* global arguments, load, print, drainMicrotasks */
'use strict';

var window = globalThis;
const failures = [];
let checks = 0;

function check(cond, what) {
  checks++;
  if (!cond) failures.push(what);
}

// ---- fake DOM ----

class Node {
  constructor() { this.parentNode = null; this.childNodes = []; }
  get firstChild() { return this.childNodes[0] || null; }
  appendChild(n) {
    if (n.parentNode) n.parentNode.removeChild(n);
    n.parentNode = this;
    this.childNodes.push(n);
    return n;
  }
  removeChild(n) {
    const i = this.childNodes.indexOf(n);
    if (i >= 0) this.childNodes.splice(i, 1);
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
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  click() { for (const fn of this.listeners.click || []) fn({target: this}); }
  focus() { document.activeElement = this; }
  scrollIntoView() {}
  set textContent(t) { this.childNodes = []; this.appendChild(new Text(t)); }
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

// ---- fake timers ----

let timers = [];
window.setTimeout = function (fn) { timers.push(fn); return timers.length; };

function flush() {
  for (let i = 0; i < 30; i++) drainMicrotasks();
}

function fireTimers() {
  const due = timers;
  timers = [];
  for (const fn of due) fn();
  flush();
}

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

const net = {sends: [], gets: [], send: null, answer: null};

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
  copyButton: function (text, label) { return el('button', {type: 'button', class: 'btn btn-secondary btn-small'}, label); },
  getJSON: function (path) {
    net.gets.push(path);
    return Promise.resolve(JSON.parse(JSON.stringify(net.answer)));
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

// ---- helpers for the checks ----

const CU = {task_id: '86c0abc', custom_id: 'CL-80002', url: 'https://app.clickup.com/t/86c0abc', title: 'Terrain'};

// the test (API.md section 2.3): a failure on TA5, results in, Mark done possible
function test() {
  return {task: 'T-1', state: 'results_ready', group: 'tested', title: 'Terrain import offline', clickup: CU,
    devices: ['TA5'], not_tested_devices: [], build: '1.76.0 staging', native: '144', run_at: '2026-11-04T08:00:00.000Z',
    results_at: '2026-11-04T08:40:00.000Z', stopped_early: false,
    result: {verdict: 'failed', verdict_label: 'Failed', word: 'failed', counts: {failed: 1}, case_count: 1,
      sentence: 'Failed in 1 of 1 case, on TA5', meaning: 'The app did not do what the test expects, and the failure ' +
        'is confirmed.'},
    done_note: null, asked: null, comment: {state: 'none', text: null, sha256: null, posted_at: null, error: null,
      clickup_url: null}, outputs: {summary: null, comment_text: null, comment_file: null}, plan_again: null,
    next: null, can_mark_done: true, mark_done_why: null, done_how: null, waiting: 0, also_running: []};
}

function card(tickets, extra) {
  return Object.assign({key: 'T-1|TERR-004', verdict: 'failed', verdict_label: 'Failed', title: 'Terrain import',
    case: 'TERR-004', task: 'T-1', clickup: CU, devices: [{tag: 'TA5', verdict: 'failed', verdict_label: 'Failed'}],
    actions: ['details'], tickets: tickets, report: {enabled: true, why: ''}, run_ids: ['R-20261103-TA5-TERR-004-01'],
    rows: [], open: true, comment: {state: 'none', text: null, sha256: null, posted_at: null, error: null,
      clickup_url: null}}, extra || {});
}

function ticket(extra) {
  return Object.assign({title: '[CT] Terrain import fails offline', description: 'Issue Summary\n...', n: 1,
    dir: 'tests/2026-11-03-x/tickets/1', filed: false, state: 'draft', offline: false, target: 'TA5'}, extra || {});
}

function answer(cards) {
  return {test: test(), cards: cards, running: null};
}

let alive = true;

function show(cards) {
  document.body = new Element('body');
  timers = [];
  net.sends = [];
  net.gets = [];
  net.send = null;
  net.answer = answer(cards);
  alive = true;
  const root = document.body.appendChild(new Element('main'));
  window.QAResults.render(root, {get: function (k) { return k === 'task' ? 'T-1' : null; }}, function () { return alive; },
    QA);
  flush();
  check(net.gets.length === 1 && net.gets[0] === '/api/results/cards?task=T-1', 'render reads the test once');
  net.gets = [];  // the page's own load; the checks count the polls
  return root;
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

function one(root, cls) {
  return root.querySelector('.' + cls);
}

function statusText(root) {
  return one(root, 'rv-ticket').querySelector('.rv-status').textContent;
}

function errorText(root) {
  const m = one(root, 'rv-ticket').querySelector('.msg-error');
  return m ? m.textContent : '';
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
  return b && b.classList.contains('btn') && !b.classList.contains('btn-secondary') && visible(b);
}

function primaries(root) {
  return root.querySelectorAll('btn').filter(isPrimary);
}

// ---- checks ----

// 1. an offline draft: in the opened row of the failed case, with the notice; File ticket is the page's one primary
// button, Report issue and Add result are secondary; Report issue points at the draft and sends nothing
(function () {
  const root = show([card([ticket({offline: true})])]);
  const row = one(root, 'rv-case');
  check(row && row.open, 'offline: the failed case\'s row is open');
  check(one(row, 'rv-ticket') !== null && visible(one(row, 'rv-ticket')), 'offline: the draft shows in the row');
  const report = byText(root, 'BUTTON', 'Report issue')[0];
  const addB = byText(root, 'BUTTON', 'Add result')[0];
  const note = one(root, 'rv-ticket-note');
  check(note && note.textContent === 'This test ran with TA5 offline. Connect TA5 to the internet, then press File ticket.',
    'offline: notice text, got ' + (note && note.textContent));
  const file = byText(root, 'BUTTON', 'File ticket')[0];
  check(isPrimary(file), 'offline: File ticket is primary');
  check(report.classList.contains('btn-secondary') && addB.classList.contains('btn-secondary'),
    'offline: Report issue and Add result are secondary');
  check(statusText(root).indexOf('Not filed yet.') === 0, 'offline: status says not filed, got ' + statusText(root));
  check(primaries(root).length === 1, 'offline: exactly one primary button, got ' + primaries(root).length);
  report.click();
  flush();
  check(net.sends.length === 0 && document.activeElement === file, 'offline: Report issue focuses File ticket, sends ' +
    'nothing');
})();

// 2. press File ticket: Touch ID wait, then filing with the request state, then filed after two looks
(function () {
  const root = show([card([ticket({offline: true})])]);
  const file = byText(root, 'BUTTON', 'File ticket')[0];
  const markDone = byText(root, 'BUTTON', 'Mark done…')[0];
  file.click();
  flush();
  check(net.sends.length === 1 && net.sends[0].method === 'POST' && net.sends[0].path === '/api/tickets/file' &&
    net.sends[0].body.dir === 'tests/2026-11-03-x/tickets/1', 'file: POST /api/tickets/file {dir}');
  check(statusText(root) === 'Confirm with Touch ID on the Mac…', 'file: Touch ID text, got ' + statusText(root));
  check(file.disabled === true, 'file: button disabled while waiting');
  check(isPrimary(file), 'file: File ticket keeps the look while it waits');
  check(one(root, 'rv-ticket-note') === null, 'file: offline notice gone while waiting');
  check(markDone.disabled, 'file: Mark done is off while the ticket is being filed');
  file.click();
  flush();
  check(net.sends.length === 1, 'file: a second press while waiting sends nothing');
  net.send.resolve({request: {id: 'W-20261103-001', state: 'new', type: 'file'}});
  flush();
  check(statusText(root) === 'Filing on TA5… Waiting for Hover.', 'file: filing text, got ' + statusText(root));
  check(file.hidden === true, 'file: button hidden while filing');
  check(timers.length === 1, 'file: one poll armed');
  net.answer = answer([card([ticket({state: 'filing', request: {id: 'W-20261103-001', state: 'claimed'}})])]);
  fireTimers();
  check(net.gets.length === 1 && net.gets[0] === '/api/results/cards?task=T-1', 'poll: reads the test');
  check(statusText(root) === 'Filing on TA5… Hover is on it.', 'poll: claimed text, got ' + statusText(root));
  check(timers.length === 1, 'poll: armed again while open');
  check(markDone.disabled, 'poll: Mark done still off while filing');
  net.answer = answer([card([ticket({state: 'filed', filed: true, cl_id: 'CL-12345',
    request: {id: 'W-20261103-001', state: 'done'}})])]);
  fireTimers();
  check(statusText(root) === 'Filed: CL-12345', 'poll: filed text, got ' + statusText(root));
  check(timers.length === 0, 'poll: stops when filed');
  check(file.hidden === true, 'poll: no File ticket once filed');
  check(isPrimary(byText(root, 'BUTTON', 'Add result')[0]), 'poll: Add result primary once filed');
  check(byText(root, 'BUTTON', 'Report issue')[0].classList.contains('btn-secondary'),
    'poll: Report issue secondary once filed');
  check(!markDone.disabled, 'poll: Mark done back once filed');
})();

// 3. the worker fails the request (device offline): the error text and the button back
(function () {
  const root = show([card([ticket({offline: true, state: 'filing', request: {id: 'W-1', state: 'new'}})])]);
  check(timers.length === 1, 'fail: a filing ticket polls from the start');
  net.answer = answer([card([ticket({offline: true, state: 'failed',
    error: 'Connect TA5 to the internet, then press File ticket again.', request: {id: 'W-1', state: 'failed'}})])]);
  fireTimers();
  check(errorText(root) === 'Connect TA5 to the internet, then press File ticket again.', 'fail: error, got ' +
    errorText(root));
  const file = byText(root, 'BUTTON', 'File ticket')[0];
  check(isPrimary(file) && file.disabled === false, 'fail: File ticket back and enabled');
  check(one(root, 'rv-ticket-note') === null, 'fail: no second offline notice under the error');
  check(timers.length === 0, 'fail: polling stopped');
})();

// 4. the press is refused: 403 Touch ID cancelled, 403 stale token, 409 the server's words, other errors
[[httpError(403, 'not approved'), 'Not approved (Touch ID cancelled).'],
  // the server's own words (approvals.NOT_APPROVED) say which no it was; they are shown as sent
  [httpError(403, 'Not approved: no answer to the Touch ID prompt within 90 s'),
    'Not approved: no answer to the Touch ID prompt within 90 s'],
  [httpError(403, 'Not approved: Touch ID and the account password are not available on this Mac'),
    'Not approved: Touch ID and the account password are not available on this Mac'],
  [httpError(403, 'missing or wrong token'), 'The app server restarted after this page was opened. Reload the page.'],
  [httpError(409, 'This ticket is already being filed.'), 'This ticket is already being filed.'],
  [new Error('Cannot reach the app server.'), 'File ticket did not go through: Cannot reach the app server.'],
].forEach(function (pair) {
  const root = show([card([ticket()])]);
  const file = byText(root, 'BUTTON', 'File ticket')[0];
  file.click();
  flush();
  net.send.reject(pair[0]);
  flush();
  check(errorText(root) === pair[1], 'refused ' + pair[0].message + ': got ' + errorText(root));
  check(!file.hidden && !file.disabled, 'refused ' + pair[0].message + ': button back');
  check(document.activeElement === file, 'refused ' + pair[0].message + ': focus on the button');
  check(timers.length === 0, 'refused ' + pair[0].message + ': no polling');
  check(!byText(root, 'BUTTON', 'Mark done…')[0].disabled, 'refused ' + pair[0].message + ': Mark done back');
  file.click();
  flush();
  check(errorText(root) === '', 'refused ' + pair[0].message + ': a new press clears the error');
});

// 5. the page changes: no more looks
(function () {
  show([card([ticket({state: 'filing', request: 'W-2'})])]);
  alive = false;
  fireTimers();
  check(net.gets.length === 0, 'page change: no poll after the page changed');
  check(timers.length === 0, 'page change: nothing armed');
})();

// 6. a filed ticket on load; two drafts; a draft without a folder
(function () {
  let root = show([card([ticket({state: 'filed', filed: true, cl_id: 'CL-7'})])]);
  check(statusText(root) === 'Filed: CL-7', 'filed on load: text');
  check(visible(one(root, 'rv-ticket')), 'filed on load: the ticket shows without a press');
  check(byText(root, 'BUTTON', 'File ticket')[0].hidden, 'filed on load: no File ticket');
  check(isPrimary(byText(root, 'BUTTON', 'Add result')[0]), 'filed on load: Add result primary');

  root = show([card([ticket(), ticket({dir: 'tests/2026-11-03-x/tickets/2', n: 2})])]);
  const files = byText(root, 'BUTTON', 'File ticket');
  check(files.length === 2 && isPrimary(files[0]) && files[1].classList.contains('btn-secondary'),
    'two drafts: only the first File ticket is primary');

  root = show([card([ticket({dir: null})], {actions: []})]);
  check(byText(root, 'BUTTON', 'File ticket').length === 0, 'no folder: no File ticket');
  check(statusText(root).indexOf('/sak-submit 1') >= 0, 'no folder: the Claude hint');
})();

// 7. the target falls back to the card's one device
(function () {
  const root = show([card([ticket({target: undefined, offline: true})], {devices: [{tag: 'ET65', verdict: 'failed'}]})]);
  check(one(root, 'rv-ticket-note').textContent.indexOf('with ET65 offline') >= 0, 'tag fallback: ET65');
})();

function primaryTexts(root) {
  return primaries(root).map(function (b) { return b.textContent; });
}

function openRow(row) {
  row.open = true;
  for (const fn of row.listeners.toggle || []) fn({});
}

// 8. FINDINGS #3: a draft whose run was corrected to a test error since (the server's fileable false, with its why):
// no File ticket, the why in its place, never the orange button; no Report issue on a row only it holds; a filed ticket
// (which the server never calls fileable) still reads "Filed"
(function () {
  const why = 'Not filed: its run was corrected to a test error, so there is nothing to file.';
  const corrected = {verdict: 'not_decided', verdict_label: 'Test error', open: false,
    devices: [{tag: 'TA5', verdict: 'not_decided', verdict_label: 'Test error'}],
    report: {enabled: false, why: 'Nothing failed in the app: the test itself stopped, which says nothing about the app.'}};
  let root = show([card([ticket({fileable: false, fileable_why: why, offline: true,
    error: 'Connect TA5 to the internet, then press File ticket again.'})], corrected)]);
  const row = one(root, 'rv-case');
  check(row && !row.open, 'unfileable: the row stays closed, as the server says');
  openRow(row);
  check(visible(one(root, 'rv-ticket')), 'unfileable: the draft shows in the opened row');
  const file = byText(root, 'BUTTON', 'File ticket')[0];
  check(!file || !visible(file), 'unfileable: no File ticket');
  check(statusText(root) === why, 'unfileable: the why in its place, got ' + statusText(root));
  check(one(root, 'rv-ticket-note') === null && errorText(root) === '',
    'unfileable: no offline notice and no "press File ticket again"');
  check(byText(root, 'BUTTON', 'Report issue').length === 0, 'unfileable: no Report issue on a row only it holds');
  check(JSON.stringify(primaryTexts(root)) === '["Add result"]', 'unfileable: the orange button is the next that ' +
    'applies, got ' + JSON.stringify(primaryTexts(root)));
  // without the server's why: the plain one
  root = show([card([ticket({fileable: false})], corrected)]);
  openRow(one(root, 'rv-case'));
  check(statusText(root) === why, 'unfileable without a why: the plain one, got ' + statusText(root));
  // a filed ticket, one being filed (approved: its request is open) and one without a folder keep their own words (the
  // server calls none of them fileable, without a why)
  root = show([card([ticket({state: 'filed', filed: true, cl_id: 'CL-7', fileable: false, fileable_why: null})])]);
  check(statusText(root) === 'Filed: CL-7', 'filed, not fileable: still "Filed", got ' + statusText(root));
  root = show([card([ticket({state: 'approved', request: {id: 'W-8', state: 'new'}, fileable: false,
    fileable_why: null})])]);
  check(statusText(root) === 'Filing on TA5… Waiting for Hover.', 'approved, not fileable: filing, got ' +
    statusText(root));
  root = show([card([ticket({dir: null, fileable: false, fileable_why: null})], {actions: []})]);
  check(statusText(root).indexOf('/sak-submit 1') >= 0, 'no folder, not fileable: the Claude hint, got ' +
    statusText(root));
  // another device still failed (Report issue stands): the set-aside draft keeps it secondary, and its press asks
  // Hover for a draft of the run that still failed
  root = show([card([ticket({fileable: false, fileable_why: why})])]);
  const report = byText(root, 'BUTTON', 'Report issue')[0];
  check(report && !report.disabled && report.classList.contains('btn-secondary'),
    'unfileable, a failure left: Report issue, secondary');
  check(JSON.stringify(primaryTexts(root)) === '["Add result"]', 'unfileable, a failure left: Add result orange, got ' +
    JSON.stringify(primaryTexts(root)));
  report.click();
  flush();
  check(net.sends.length === 1 && net.sends[0].path === '/api/tickets/draft' &&
    JSON.stringify(net.sends[0].body) === JSON.stringify({task: 'T-1', case: 'TERR-004'}),
  'unfileable, a failure left: Report issue asks for a draft');
  // a fileable draft (true, or a server without the field) keeps File ticket
  root = show([card([ticket({fileable: true, fileable_why: null})])]);
  check(isPrimary(byText(root, 'BUTTON', 'File ticket')[0]), 'fileable: File ticket, orange');
})();

// 9. FINDINGS #18: a filing ticket that the looks no longer have (a chat page asks ?case=X, whose answer can move on to
// another run of X): after MISSES_MAX looks without it the page stops looking and says so
(function () {
  const root = show([card([ticket()])]);
  byText(root, 'BUTTON', 'File ticket')[0].click();
  flush();
  net.send.resolve({request: {id: 'W-20261105-070', state: 'new', type: 'file'}});
  flush();
  check(statusText(root) === 'Filing on TA5… Waiting for Hover.', 'lost: filing, got ' + statusText(root));
  const other = {test: test(), cards: [card([], {key: 'T-1|TERR-005', case: 'TERR-005'})], running: null};
  net.answer = other;
  fireTimers();
  check(timers.length === 1 && errorText(root) === '', 'lost: one look without the ticket is not the end');
  net.answer = answer([card([ticket({state: 'filing', request: {id: 'W-20261105-070', state: 'claimed'}})])]);
  fireTimers();
  check(statusText(root) === 'Filing on TA5… Hover is on it.' && timers.length === 1,
    'lost: a look with the ticket counts again');
  net.answer = other;
  fireTimers();
  check(timers.length === 1 && errorText(root) === '', 'lost: one look without it again');
  fireTimers();
  check(errorText(root) === 'This result is no longer in the list. Reload the page to see where the ticket stands.',
    'lost: said after two looks, got ' + errorText(root));
  check(timers.length === 0, 'lost: no more looks');
  check(statusText(root) === '' && byText(root, 'BUTTON', 'File ticket')[0].hidden,
    'lost: no stale filing line, no File ticket, got ' + statusText(root));
})();

// 10. FINDINGS #22 with #3: Report issue's watch ends when a draft came that was not in the row before, whether File
// ticket can send it or not; a draft set aside before the press (its run corrected to a test error) never ends it
(function () {
  const why = 'Not filed: its run was corrected to a test error, so there is nothing to file.';
  const old = ticket({fileable: false, fileable_why: why});
  const drafting = {state: 'drafting', request: {id: 'W-20261109-001', state: 'claimed'}};
  const second = {dir: 'tests/2026-11-03-x/tickets/2', n: 2, title: '[CT] Terrain import fails on TA5 again'};
  const panel = function (r) { return one(r, 'rv-ticket-panel'); };
  // the panel: its drafting line, the drafts, its own error (Report issue's)
  const drafting1 = function (r) { const s = panel(r).childNodes[0]; return s.hidden ? '' : s.textContent; };
  const panelError = function (r) {
    const m = panel(r).childNodes[2].querySelector('.msg-error');
    return m ? m.textContent : '';
  };
  const draftWords = function (r) {
    return r.querySelectorAll('rv-ticket-one').map(function (b) { return b.querySelector('.rv-status').textContent; });
  };
  const press = function (r) {
    byText(r, 'BUTTON', 'Report issue')[0].click();
    flush();
    net.send.resolve({request: {id: 'W-20261109-001', state: 'new', type: 'draft'}});
    flush();
  };
  let root = show([card([old])]);
  press(root);
  net.answer = answer([card([old, drafting])]);
  fireTimers();
  check(drafting1(root) === 'Drafting the ticket… Hover is on it.' && timers.length === 1,
    'set-aside draft: the watch goes on while only it is there, got ' + drafting1(root));
  net.answer = answer([card([old, ticket(Object.assign({fileable: true, fileable_why: null}, second))])]);
  fireTimers();
  check(drafting1(root) === '' && timers.length === 0 && panelError(root) === '',
    'set-aside draft, then the new one: the watch ends, no error, got ' + panelError(root));
  check(JSON.stringify(primaryTexts(root)) === '["File ticket"]', 'the new draft: File ticket orange, got ' +
    JSON.stringify(primaryTexts(root)));
  // the draft that came cannot be filed either (its run was corrected while it was written): the watch ends there,
  // with its why and without "not drafted"
  root = show([card([old])]);
  press(root);
  net.answer = answer([card([old, ticket(Object.assign({fileable: false, fileable_why: why}, second))],
    {report: {enabled: false, why: 'Nothing failed in the app: the test itself stopped.'}})]);
  fireTimers();
  check(drafting1(root) === '' && timers.length === 0, 'a set-aside draft came: the watch ends at once');
  fireTimers();  // a look more, if one was left: still no "not drafted"
  check(panelError(root) === '', 'a set-aside draft came: no "not drafted", got ' + panelError(root));
  check(JSON.stringify(draftWords(root)) === JSON.stringify([why, why]) &&
    byText(root, 'BUTTON', 'File ticket').every(function (b) { return !visible(b); }),
  'a set-aside draft came: both drafts with their why, no File ticket, got ' + JSON.stringify(draftWords(root)));
})();

// 11. review 2026-11-09 (FINDINGS #8 on the page): a failure the server takes no ticket for (it ran on the earlier
// engine account) has no Report issue, and its row says why where the ticket part would be; not for a row whose
// Report issue stands, a row that did not fail, or a test in Done
(function () {
  const why = 'ran on the earlier engine account shared-stag; not filed';
  const old = {devices: [{tag: 'W044', verdict: 'failed', verdict_label: 'Failed'}], report: {enabled: false, why: why}};
  const reportWhy = function (r) {
    return r.querySelectorAll('rv-report-why').filter(visible).map(function (n) { return n.textContent; });
  };
  let root = show([card([], old)]);
  const row = one(root, 'rv-case');
  check(row && row.open, 'earlier account: the failed row is open');
  check(JSON.stringify(reportWhy(root)) === JSON.stringify(['Report issue: ' + why + '.']),
    'earlier account: the row says why, got ' + JSON.stringify(reportWhy(root)));
  check(byText(root, 'BUTTON', 'Report issue').length === 0 && byText(root, 'BUTTON', 'File ticket').length === 0,
    'earlier account: no Report issue, no File ticket');
  const line = one(row, 'rv-report-why');
  check(line !== null && line.parentNode === one(row, 'rv-case-body'), 'earlier account: the why is in the row');
  check(JSON.stringify(primaryTexts(root)) === '["Add result"]', 'earlier account: Add result is the orange button, ' +
    'got ' + JSON.stringify(primaryTexts(root)));
  // a why that ends a sentence keeps its own stop
  root = show([card([], Object.assign({}, old, {report: {enabled: false, why: 'Ran on TA3 only.'}}))]);
  check(JSON.stringify(reportWhy(root)) === '["Report issue: Ran on TA3 only."]', 'one stop, got ' +
    JSON.stringify(reportWhy(root)));
  // Report issue stands: its button, no why line
  root = show([card([])]);
  check(byText(root, 'BUTTON', 'Report issue').length === 1 && reportWhy(root).length === 0,
    'Report issue stands: no why line');
  // a row that did not fail: no line, whatever the why says
  root = show([card([], {verdict: 'passed', verdict_label: 'Passed', open: false,
    devices: [{tag: 'TA5', verdict: 'passed', verdict_label: 'Passed'}],
    report: {enabled: false, why: 'Nothing went wrong in these runs.'}})]);
  openRow(one(root, 'rv-case'));
  check(root.querySelectorAll('rv-report-why').length === 0, 'a passed row: no why line');
  // a test in Done offers no Report issue on any row: no why line there either
  document.body = new Element('body');
  timers = [];
  net.answer = {test: Object.assign(test(), {group: 'done', state: 'closed', done_how: 'Marked done',
    can_mark_done: false}), cards: [card([], old)], running: null};
  const done = document.body.appendChild(new Element('main'));
  window.QAResults.render(done, {get: function (k) { return k === 'task' ? 'T-1' : null; }}, function () {
    return true;
  }, QA);
  flush();
  check(one(done, 'rv-case') !== null && done.querySelectorAll('rv-report-why').length === 0,
    'a test in Done: no why line');
})();

print(JSON.stringify({failures: failures, checks: checks}));
