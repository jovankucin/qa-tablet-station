// needs_page_harness.js: runs static/app.js in JavaScriptCore's jsc with a fake DOM, window, location and fetch, and
// checks "Needs your input" on the New test plan (2026-11-04, DESIGN.md 8.14): the card and its rows, the body Send
// answers posts and the screen it opens, the Run rule, the empty-plan words, "Your answers" on a follow-up plan,
// Attach a file through the piece upload, and one files box per screen. It also calls runBlocked, answersBody and
// answerLines, cut out of app.js. Since 2026-11-04 (tests/2026-11-04-results-redesign/SPEC.md screen 6): the plan's
// title on top of the Plan screen with the ClickUp test under it, each case's parts (checks marked, the task's words
// quoted) or its check steps with every engine step folded, and New test's "Your plans" with the line to History.
// Since 2026-11-05 (tests/2026-11-05-wingmate-debug/FINDINGS.md, checks 13-15): a cancelled newer check hides no plan,
// the Plan screen shows the case version the plan pinned, and Run records (#/results?view=detail) shows its records
// before the filters and keeps view=detail on its filters, an opened run and Close.
// Run by test_needs_page.py:
//   jsc needs_page_harness.js -- <path of app.js>
// Prints one JSON line {"failures": [...], "checks": n}. Nothing here touches a server, ClickUp or a device.
/* global arguments, load, print, readFile, drainMicrotasks */
'use strict';

var window = globalThis;
const APP = arguments[0];
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
    n.parentNode = this;
    this.childNodes.push(n);
    return n;
  }
  removeChild(n) {
    const i = this.childNodes.indexOf(n);
    if (i < 0) throw new Error('removeChild: not a child');
    this.childNodes.splice(i, 1);
    n.parentNode = null;
    return n;
  }
  contains(n) {
    for (let x = n; x; x = x.parentNode) if (x === this) return true;
    return false;
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
    this.checked = false;
    this.value = '';
    this.files = null;
    this.clicks = 0;
    this.scrolled = false;
  }
  setAttribute(k, v) { if (k === 'class') this.className = String(v); else this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    const l = this.listeners[type] || [];
    if (l.indexOf(fn) >= 0) l.splice(l.indexOf(fn), 1);
  }
  dispatch(type) {
    const ev = {type: type, target: this, preventDefault: function () {}, stopPropagation: function () {}};
    for (const fn of (this.listeners[type] || []).slice()) fn(ev);
  }
  // as in a browser, a disabled control takes no click
  click() {
    this.clicks++;
    if (!this.disabled) this.dispatch('click');
  }
  focus() { document.activeElement = this; }
  scrollIntoView() { this.scrolled = true; }
  set textContent(t) {
    this.childNodes.slice().forEach(function (k) { this.removeChild(k); }, this);
    this.appendChild(new Text(t));
  }
  get textContent() { return super.textContent; }
}

function walk(root, pred) {
  const out = [];
  (function go(n) {
    for (const k of n.childNodes) {
      if (!(k instanceof Element)) continue;
      if (pred(k)) out.push(k);
      go(k);
    }
  })(root);
  return out;
}

function find(root, pred) {
  return walk(root, pred)[0] || null;
}

const meta = new Element('meta');
meta.setAttribute('content', 'tok-123');

const document = {
  body: new Element('body'), readyState: 'complete', visibilityState: 'visible', title: '', activeElement: null,
  listeners: {},
  createElement: function (tag) { return new Element(tag); },
  createElementNS: function (ns, tag) { return new Element(tag); },
  createTextNode: function (t) { return new Text(t); },
  addEventListener: function (type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
  getElementById: function (id) { return find(this.body, function (n) { return n.getAttribute('id') === id; }); },
  querySelector: function (sel) { return sel === 'meta[name="qa-token"]' ? meta : null; },
  querySelectorAll: function () { return []; },
};
window.document = document;
window.Node = Node;

const viewEl = document.body.appendChild(new Element('main'));
viewEl.setAttribute('id', 'view');
document.body.appendChild(new Element('span')).setAttribute('id', 'env-chip');

// ---- fake window: listeners, location, timers, URLSearchParams, AbortController, fetch ----

const winListeners = {};
window.addEventListener = function (type, fn) { (winListeners[type] = winListeners[type] || []).push(fn); };
window.removeEventListener = function (type, fn) {
  const l = winListeners[type] || [];
  if (l.indexOf(fn) >= 0) l.splice(l.indexOf(fn), 1);
};
window.scrollTo = function () {};

function handlers(type) {
  return (winListeners[type] || []).length;
}

// a new hash fires hashchange after the current task, as in a browser
window.location = {
  _hash: '',
  get hash() { return this._hash; },
  set hash(h) {
    const changed = h !== this._hash;
    this._hash = h;
    if (changed) {
      Promise.resolve().then(function () {
        for (const fn of (winListeners.hashchange || []).slice()) fn({type: 'hashchange'});
      });
    }
  },
  replace: function (h) { this.hash = h; },
};

let timers = [];
window.setTimeout = function (fn, ms) { timers.push({fn: fn, ms: ms}); return timers.length; };
window.clearTimeout = function () {};

function flush() {
  for (let i = 0; i < 50; i++) drainMicrotasks();
}

class URLSearchParams {
  constructor(init) {
    this.pairs = [];
    for (const part of typeof init === 'string' ? init.replace(/^\?/, '').split('&') : []) {
      if (!part) continue;
      const i = part.indexOf('=');
      const dec = function (s) { return decodeURIComponent(s.replace(/\+/g, ' ')); };
      this.pairs.push(i < 0 ? [dec(part), ''] : [dec(part.slice(0, i)), dec(part.slice(i + 1))]);
    }
  }
  get(k) {
    const p = this.pairs.find(function (x) { return x[0] === k; });
    return p ? p[1] : null;
  }
  set(k, v) {
    this.pairs = this.pairs.filter(function (x) { return x[0] !== k; }).concat([[k, String(v)]]);
  }
  toString() {
    return this.pairs.map(function (p) { return encodeURIComponent(p[0]) + '=' + encodeURIComponent(p[1]); }).join('&');
  }
  [Symbol.iterator]() { return this.pairs[Symbol.iterator](); }
}
window.URLSearchParams = URLSearchParams;

window.AbortController = function () {
  this.signal = {aborted: false};
  this.abort = function () { this.signal.aborted = true; };
};

const TASK = '86c0abc';
// fileJSON: answers of /files/<path> (a case file the plan pinned); json: other GET answers by their whole path; held:
// paths whose answer waits until release(path) (a slow route)
const net = {worker: null, files: [], gets: [], posts: [], puts: [], removes: [], answer: null, afterPut: null,
  cases: {}, tests: null, fileJSON: {}, json: {}, held: {}, replaced: []};

// the Run records page writes its address with history.replaceState (no hashchange)
window.history = {replaceState: function (state, title, url) { net.replaced.push(url); }};

function copy(x) {
  return JSON.parse(JSON.stringify(x));
}

function respond(status, body) {
  return Promise.resolve({ok: status >= 200 && status < 300, status: status, statusText: '',
    text: function () { return Promise.resolve(JSON.stringify(body)); }});
}

function release(url) {
  const go = net.held[url];
  delete net.held[url];
  if (typeof go === 'function') go();
}

window.fetch = function (url, init) {
  const method = (init && init.method) || 'GET';
  if (method === 'GET') {
    net.gets.push(url);
    if (url in net.held) {
      return new Promise(function (resolve) {
        net.held[url] = function () { resolve(respond(200, copy(net.json[url]))); };
      });
    }
    if (url in net.json) return respond(200, copy(net.json[url]));
    if (url.indexOf('/files/') === 0) {
      return url in net.fileJSON ? respond(200, copy(net.fileJSON[url])) : respond(404, {error: 'not found: ' + url});
    }
    if (url === '/api/meta') {
      return respond(200, {env: 'staging', account_label: 'ct', targets: [{tag: 'TA5'}, {tag: 'ET65'}, {tag: 'W044'},
        {tag: 'TA3'}]});
    }
    if (url === '/api/worker') return respond(200, copy(net.worker));
    if (url.indexOf('/api/cases/') === 0) {
      const id = decodeURIComponent(url.slice('/api/cases/'.length));
      return respond(200, net.cases[id] || {case: {steps: ['Open the map'], expected: 'It shows'}});
    }
    if (url === '/api/results/tests') {
      return net.tests ? respond(200, copy(net.tests)) : respond(500, {error: 'the tests could not be read'});
    }
    if (url === '/api/files/' + TASK) {
      return respond(200, {files: copy(net.files), max_bytes: 2147483648, piece_bytes: 8388608,
        extensions: ['.tif', '.tiff', '.las', '.laz', '.zip']});
    }
    return respond(404, {error: 'not found: ' + url});
  }
  if (method === 'POST' && url === '/api/worker/answer') {
    net.posts.push({body: JSON.parse(init.body), token: init.headers['X-QA-Token']});
    return respond(net.answer.status, net.answer.body);
  }
  if (method === 'POST' && url === '/api/files/' + TASK + '/remove') {
    const name = JSON.parse(init.body).name;
    net.removes.push(name);
    net.files = net.files.filter(function (f) { return f.name !== name; });
    return respond(200, {removed: name});
  }
  if (method === 'PUT') {
    net.puts.push(url);
    if (net.afterPut) net.afterPut(url);
    return respond(200, {done: true});
  }
  return respond(404, {error: 'no route: ' + method + ' ' + url});
};

// ---- data ----

function iso(secondsAgo) {
  return new Date(Date.now() - (secondsAgo || 0) * 1000).toISOString();
}

const CU = {task_id: TASK, custom_id: 'CL-80004', title: 'DSM import per latitude', url: 'https://app.clickup.com/t/86c0abc',
  priority: 'high', status: 'to do'};
const SHA = {site: 'a'.repeat(64), cloud: 'b'.repeat(64), fresh: 'c'.repeat(64), terr: 'd'.repeat(64)};
const FILES = [
  {name: 'site.tif', size: 2048, sha256: SHA.site, added: iso(900), path: 'tests/test-files-86c0abc/files/site.tif'},
  {name: 'cloud.laz', size: 4096, sha256: SHA.cloud, added: iso(800), path: 'tests/test-files-86c0abc/files/cloud.laz'},
];
const FRESH = {name: 'fresh.tif', size: 10, sha256: SHA.fresh, added: iso(0), path: 'tests/test-files-86c0abc/files/fresh.tif'};
// worker text with markup in it: it must show as text
const N1 = {id: 'N1', kind: 'choice', blocking: true, text: 'Which latitudes should the test DSMs cover? <b>All</b>',
  options: [{id: 'make', label: 'Make 8 test DSMs'}, {id: 'skip', label: 'Skip latitudes'}]};
const N2 = {id: 'N2', kind: 'file', blocking: true, accept: ['.tif', '.tiff'],
  text: 'Attach your own DSM of the site as a GeoTIFF, so the import runs on real data from the customer area.',
  options: [{id: 'make', label: 'Make one for me'}, {id: 'skip', label: 'Skip this part'}]};
const N3 = {id: 'N3', kind: 'text', blocking: false, text: 'Which flight speed do you mean by "fast"?',
  options: [{id: 'skip', label: 'Skip this part'}]};
const N4 = {id: 'N4', kind: 'file', blocking: false, accept: ['.las', '.laz'], text: 'Attach a point cloud if you have one.',
  options: []};
const ROWS = [{tag: 'TA5', ok: true, connected: true, account: true, app_ok: true, app: '1.74.0-staging-abc1234'},
  {tag: 'ET65', ok: true, connected: true, account: true, app_ok: true, app: '1.74.0-staging-abc1234'}];
const TERR = {id: 'TERR-004', file: 'qa/cases/TERR-004.json', sha256: SHA.terr, new: false, targets: ['TA5'],
  text: 'Imports the DSM and flies over it'};
const LONG = 'Fast means 15 m/s or more over the whole flight, on the straight lines and not in the turns at the ends';

function plan(id, result, extra) {
  return Object.assign({id: id, kind: 'worker_request', type: 'check', state: 'done', intake: true, prepare: true,
    clickup: CU, clickup_cases: [], guidance: 'Import a DSM per latitude and fly over it.', targets: ['TA5', 'ET65'],
    cases: [], engine_cases: [], fix_commits: [], case_versions: [], files: FILES, created: iso(400), done_at: iso(60),
    notes: [], result: Object.assign({checked_at: iso(60), summary: 'I need a DSM per latitude before I can test.',
      targets: ROWS, plan_cases: [], not_covered: [], needs: [N1, N2, N3]}, result || {})}, extra || {});
}

// ---- helpers for the checks ----

function view() {
  return viewEl.firstChild;
}

// opens a plan screen the way a link does; the requests come newest first, as from GET /api/worker
function open(rid, requests) {
  net.worker = {worker: {state: 'ready'}, requests: requests, now: iso(0)};
  window.location.hash = '#/new?show=plan&req=' + rid;
  flush();
  return view();
}

function byClass(root, cls) {
  return walk(root, function (n) { return n.classList.contains(cls); });
}

function byTag(root, tag) {
  return walk(root, function (n) { return n.tagName === tag; });
}

function button(root, text) {
  return find(root, function (n) { return n.tagName === 'BUTTON' && n.textContent === text; });
}

function cardOf(root, title) {
  return find(root, function (n) {
    return n.tagName === 'SECTION' && byTag(n, 'H2').some(function (h) { return h.textContent === title; });
  });
}

function runButton(root) {
  return byTag(byClass(cardOf(root, 'Devices'), 'form-actions')[0], 'BUTTON')[0];
}

function warnBox(root, start) {
  return find(root, function (n) { return n.classList.contains('msg-warn') && n.textContent.indexOf(start) === 0; });
}

function pressed(row) {
  return byClass(row, 'need-opt').filter(function (b) { return b.getAttribute('aria-pressed') === 'true'; })
    .map(function (b) { return b.textContent; });
}

function texts(nodes) {
  return nodes.map(function (n) { return n.textContent; });
}

function type(box, text) {
  box.value = text;
  box.dispatch('input');
}

function choose(sel, value) {
  sel.value = value;
  sel.dispatch('change');
}

function fileInput(row) {
  return find(row, function (n) { return n.tagName === 'INPUT' && n.getAttribute('type') === 'file'; });
}

// the error slot sits under the screen's head: back link, title, slot, body
function errSlot(root) {
  return root.childNodes[2];
}

// ---- the named functions, cut out of app.js ----

const SRC = readFile(APP);

function cut(name) {
  const start = SRC.indexOf('\n  function ' + name + '(');
  if (start < 0) throw new Error('app.js has no function ' + name);
  return SRC.slice(start, SRC.indexOf('\n  }\n', start) + 4);
}

const F = new Function(['list', 'compact', 'jsLen', 'planNeeds', 'runBlocked', 'answersBody', 'answerLines'].map(cut)
  .join('') + SRC.match(/\n {2}const ANSWER_LINE_MAX = [^;]+;/)[0] +
  '\nreturn {runBlocked: runBlocked, answersBody: answersBody, answerLines: answerLines};')();

// 1. the Run rule: a blocking question of a New test plan keeps Run off
(function () {
  check(F.runBlocked(plan('W-1')) === true, 'runBlocked: a blocking question');
  check(F.runBlocked(plan('W-1', {needs: [N3, N4]})) === false, 'runBlocked: only optional questions');
  check(F.runBlocked(plan('W-1', {needs: []})) === false, 'runBlocked: no questions');
  check(F.runBlocked(plan('W-1', {needs: undefined})) === false, 'runBlocked: needs missing');
  check(F.runBlocked(plan('W-1', {}, {intake: false})) === false, 'runBlocked: only a New test plan');
  check(F.runBlocked(plan('W-1', {needs: [Object.assign({}, N1, {blocking: 'true'})]})) === false,
    'runBlocked: blocking must be true itself');
  check(F.runBlocked(null) === false && F.runBlocked({intake: true}) === false, 'runBlocked: no request or no result');
})();

// 2. the body of Send answers: the plan's order, only answers that fit their question
(function () {
  const r = plan('W-20261104-001');
  const all = new Map([['N3', {text: '  Fast means 15 m/s\nat least  '}], ['N1', {choice: 'skip'}],
    ['N2', {file: {name: 'site.tif', sha256: SHA.site, size: 2048}}]]);
  eq(F.answersBody(r, all), {check: 'W-20261104-001', answers: [{need: 'N1', choice: 'skip'},
    {need: 'N2', file: {name: 'site.tif', sha256: SHA.site}}, {need: 'N3', text: 'Fast means 15 m/s\nat least'}]},
  'answersBody: in the plan\'s order, a file as name and checksum, a text trimmed with its line break');
  eq(F.answersBody(r, new Map([['N2', {choice: 'make'}], ['N3', {choice: 'skip'}]])).answers,
    [{need: 'N2', choice: 'make'}, {need: 'N3', choice: 'skip'}], 'answersBody: the options of a file and a text question');
  const misfits = [['N1', {choice: 'maybe'}], ['N1', {text: 'all of them'}], ['N1', {file: {name: 'site.tif', sha256: SHA.site}}],
    ['N3', {file: {name: 'site.tif', sha256: SHA.site}}], ['N3', {text: ' \n '}], ['N2', {text: 'site.tif'}],
    ['N2', {choice: 'later'}], ['N9', {choice: 'make'}], ['N2', {}]];
  for (const m of misfits) eq(F.answersBody(r, new Map([m])).answers, [], 'answersBody: leaves out ' + JSON.stringify(m));
  eq(F.answersBody(r, new Map()), {check: 'W-20261104-001', answers: []}, 'answersBody: nothing answered');
})();

// 3. "Your answers": the question and the answer, one line each, cut to 80 characters
(function () {
  const prev = plan('W-20261104-001');
  const r = plan('W-20261104-004', {needs: []}, {follows: prev.id, answers: [{need: 'N1', choice: 'skip'},
    {need: 'N2', file: {name: 'site.tif', sha256: SHA.site}}, {need: 'N3', text: LONG}]});
  const lines = F.answerLines(r, prev);
  eq(lines.map(function (x) { return x.need; }), [N1.text, N2.text.slice(0, 79) + '…', N3.text],
    'answerLines: the questions, cut to 80 characters');
  eq(lines.map(function (x) { return x.answer; }), ['Skip latitudes', 'site.tif', LONG.slice(0, 79) + '…'],
    'answerLines: the option label, the file name and the text cut to 80 characters');
  eq(F.answerLines(r, undefined).map(function (x) { return [x.need, x.answer]; }),
    [['N1', 'skip'], ['N2', 'site.tif'], ['N3', LONG.slice(0, 79) + '…']],
    'answerLines: without the answered plan, the ids');
  eq(F.answerLines({answers: [{need: 'N3', text: 'one\n\ntwo   three'}]}, prev)[0].answer, 'one two three',
    'answerLines: a text over several lines shows on one');
  eq(F.answerLines({}, prev), [], 'answerLines: a plan without answers');
})();

// ---- the Plan screen in the app ----

const A_ID = 'W-20261104-001';
const B_ID = 'W-20261104-002';
const PLAN_A = plan(A_ID);
net.files = FILES.slice();
net.worker = {worker: {state: 'ready'}, requests: [PLAN_A], now: iso(0)};
window.location._hash = '#/new?show=plan&req=' + A_ID;  // the screen the app opens on
load(APP);
const RENDER = winListeners.hashchange[0];  // boot's render: goTo calls it straight for the address it already shows
flush();

// 4. the card: under "What I will do", one row per question, worker text as text, Run off
(function () {
  const root = view();
  const card = cardOf(root, 'Needs your input');
  check(card !== null, 'card: Needs your input shows');
  if (!card) return;
  const order = walk(root, function (n) { return n.tagName === 'SECTION'; });
  eq([order.indexOf(cardOf(root, 'What I will do')), order.indexOf(card), order.indexOf(cardOf(root, 'Devices'))],
    [0, 1, 2], 'card: right after What I will do, before Devices');
  const head = byClass(card, 'card-head')[0];
  eq(texts(byClass(head, 'chip')), ['2 required'], 'card: the chip counts the blocking questions');
  check(byClass(head, 'chip')[0].classList.contains('s-warn'), 'card: the chip is a warning');
  const rows = byClass(card, 'need-row');
  eq(rows.map(function (r) { return r.childNodes[0].textContent; }), [N1.text, N2.text, N3.text + ' Optional'],
    'card: each question\'s text, an optional one marked');
  eq(byTag(root, 'B').length, 0, 'card: the worker\'s markup stays text');
  eq(texts(byClass(rows[0], 'need-opt')), ['Make 8 test DSMs', 'Skip latitudes'], 'choice: one button per option');
  eq(pressed(rows[0]), [], 'choice: nothing pressed at first');
  check(byClass(rows[0], 'need-opt').every(function (b) { return b.getAttribute('type') === 'button'; }),
    'choice: plain buttons');
  const sel = byTag(rows[1], 'SELECT')[0];
  eq(texts(byTag(sel, 'OPTION')), ['Choose an attached file', 'site.tif'], 'file: only the attached files it takes');
  eq(byTag(sel, 'OPTION').map(function (o) { return o.value; }), ['', 'site.tif'], 'file: the values are the names');
  check(!sel.disabled && sel.value === '', 'file: a file can be chosen, none is');
  check(button(rows[1], 'Attach a file') !== null, 'file: Attach a file');
  const pick = fileInput(rows[1]);
  check(pick && pick.classList.contains('sr-only') && pick.getAttribute('tabindex') === '-1' &&
    pick.getAttribute('accept') === '.tif,.tiff', 'file: a chooser out of sight, for .tif and .tiff');
  eq(texts(byClass(rows[1], 'need-opt')), ['Make one for me', 'Skip this part'], 'file: the question\'s options');
  check(byTag(rows[2], 'TEXTAREA').length === 1, 'text: a text box');
  eq(texts(byClass(rows[2], 'counter')), ['0/1000'], 'text: at most 1000 characters');
  eq(texts(byClass(rows[2], 'need-opt')), ['Skip this part'], 'text: the question\'s options');
  const run = runButton(root);
  check(run.disabled && run.textContent === 'Answer the questions first', 'Run: off, "Answer the questions first", got ' +
    run.textContent);
  const empty = warnBox(root, 'Nothing to run');
  eq(empty && empty.textContent, 'Nothing to run: the plan has no test case. Hover needs your input above.',
    'empty plan: the box points at the questions');
  const send = button(card, 'Send answers');
  check(send.disabled && send.className === 'btn', 'Send answers: the primary button, off until a question is answered');
  eq(texts(byClass(byClass(card, 'form-actions')[0], 'hint')), ['Hover writes a new plan with your answers. ' +
    'Nothing runs yet.'], 'Send answers: what happens next');
  const boxes = byClass(root, 'test-files');
  check(boxes.length === 1 && card.contains(boxes[0]), 'files: one files box, in the card');
  eq([handlers('drop'), handlers('dragover'), handlers('beforeunload'), handlers('hashchange')], [1, 1, 1, 2],
    'files: one set of page-wide handlers');
  eq(net.gets.filter(function (u) { return u === '/api/files/' + TASK; }).length, 1, 'files: the list is read once');
})();

// 5. choosing: one option at a time, pressing it again clears it; a file and an option, a text and an option
// exclude each other
(function () {
  const root = view();
  const card = cardOf(root, 'Needs your input');
  if (!card) return;
  const rows = byClass(card, 'need-row');
  const send = button(card, 'Send answers');
  button(rows[0], 'Make 8 test DSMs').click();
  check(pressed(rows[0]).join() === 'Make 8 test DSMs' && !send.disabled, 'choice: pressed and marked, Send answers on');
  button(rows[0], 'Make 8 test DSMs').click();
  check(pressed(rows[0]).length === 0 && send.disabled, 'choice: pressing it again clears it, Send answers off');
  button(rows[0], 'Make 8 test DSMs').click();
  button(rows[0], 'Skip latitudes').click();
  eq(pressed(rows[0]), ['Skip latitudes'], 'choice: one option at a time');

  const sel = byTag(rows[1], 'SELECT')[0];
  choose(sel, 'site.tif');
  check(sel.value === 'site.tif' && pressed(rows[1]).length === 0, 'file: chosen');
  button(rows[1], 'Make one for me').click();
  check(pressed(rows[1]).join() === 'Make one for me' && sel.value === '', 'file: an option clears the file');
  choose(sel, 'site.tif');
  check(pressed(rows[1]).length === 0 && sel.value === 'site.tif', 'file: a file clears the option');

  const box = byTag(rows[2], 'TEXTAREA')[0];
  const counter = byClass(rows[2], 'counter')[0];
  type(box, 'Fast means 15 m/s');
  check(counter.textContent === '17/1000' && pressed(rows[2]).length === 0, 'text: typed and counted');
  button(rows[2], 'Skip this part').click();
  check(pressed(rows[2]).join() === 'Skip this part' && box.value === '' && counter.textContent === '0/1000',
    'text: an option empties the box');
  type(box, ' ');
  eq(pressed(rows[2]), ['Skip this part'], 'text: a blank box keeps the option');
  type(box, 'Fast means 15 m/s');
  eq(pressed(rows[2]), [], 'text: typing clears the option');
  type(box, 'x'.repeat(1001));
  check(counter.classList.contains('over') && send.disabled, 'text: over 1000 characters turns Send answers off');
  type(box, 'Fast means 15 m/s');
  check(!counter.classList.contains('over') && !send.disabled, 'text: back under the limit, Send answers on');
})();

// 6. Attach a file: the chooser opens, the file goes up in pieces, the list refreshes; a removed file is no answer
(function () {
  const root = view();
  const card = cardOf(root, 'Needs your input');
  if (!card) return;
  const rows = byClass(card, 'need-row');
  const send = button(card, 'Send answers');
  const pick = fileInput(rows[1]);
  button(rows[1], 'Attach a file').click();
  eq(pick.clicks, 1, 'attach: the button opens the chooser');
  net.afterPut = function () { net.files = FILES.concat([FRESH]); };
  pick.files = [{name: 'fresh.tif', size: 10, slice: function (a, b) { return {size: b - a}; }}];
  pick.dispatch('change');
  check(send.disabled, 'attach: Send answers waits for the upload');
  eq(net.puts, ['/api/files/' + TASK + '/fresh.tif?offset=0&total=10'], 'attach: the piece upload of the files box');
  eq(pick.value, '', 'attach: the chooser is reset');
  flush();
  net.afterPut = null;
  const sel = byTag(rows[1], 'SELECT')[0];
  eq(texts(byTag(sel, 'OPTION')), ['Choose an attached file', 'site.tif', 'fresh.tif'], 'attach: the list shows the file');
  check(sel.value === 'site.tif' && !send.disabled, 'attach: the chosen file stays, Send answers on again');
  const box = byClass(root, 'test-files')[0];
  eq(byClass(box, 'file-row').length, 3, 'attach: the files box lists it');

  // the plan's files box has no Remove: removing a file the plan uses would make Run refuse it
  const siteRow = byClass(box, 'file-row').filter(function (n) { return n.textContent.indexOf('site.tif') === 0; })[0];
  check(!button(siteRow, 'Remove'), 'files: no Remove button on the plan screen');
  eq(net.removes, [], 'files: nothing removed');
  choose(sel, 'fresh.tif');
})();

// 7. Send answers: a refusal shows in the error slot and the answers stay; then the new plan's screen opens
(function () {
  const root = view();
  const card = cardOf(root, 'Needs your input');
  if (!card) return;
  const send = button(card, 'Send answers');
  const body = {check: A_ID, answers: [{need: 'N1', choice: 'skip'}, {need: 'N2', file: {name: 'fresh.tif',
    sha256: SHA.fresh}}, {need: 'N3', text: 'Fast means 15 m/s'}]};
  net.answer = {status: 400, body: {error: 'answers[1].file: not attached to the test'}};
  send.click();
  check(send.disabled, 'send: off while it is sent');
  flush();
  eq(net.posts.map(function (p) { return p.body; }), [body], 'send: the body');
  eq(net.posts[0].token, 'tok-123', 'send: with the page token');
  const slot = errSlot(root);
  check(byClass(slot, 'msg-error').length === 1 && slot.textContent === 'answers[1].file: not attached to the test',
    'send: the refusal shows in the error slot, got ' + slot.textContent);
  check(slot.scrolled, 'send: the error slot is scrolled into view');
  check(!send.disabled && window.location.hash === '#/new?show=plan&req=' + A_ID, 'send: on again, same screen');
  eq(pressed(byClass(card, 'need-row')[0]), ['Skip latitudes'], 'send: the answers stay');

  net.answer = {status: 403, body: {error: 'bad token'}};
  send.click();
  flush();
  eq(slot.textContent, 'The app server restarted after this page was opened. Reload the page.', 'send: a 403');

  const follow = {id: B_ID, kind: 'worker_request', type: 'check', state: 'new', intake: true, prepare: true, clickup: CU,
    guidance: PLAN_A.guidance, targets: ['TA5', 'ET65'], files: FILES, follows: A_ID, answers: body.answers,
    created: iso(0)};
  net.answer = {status: 201, body: follow};
  net.worker = {worker: {state: 'ready'}, requests: [follow, PLAN_A], now: iso(0)};
  send.click();
  flush();
  eq(net.posts.length, 3, 'send: three posts');
  eq(net.posts[2].body, body, 'send: the same body again');
  eq(window.location.hash, '#/new?show=plan&req=' + B_ID, 'send: the new plan\'s screen opens');
  const next = view();
  check(next !== root && cardOf(next, 'Needs your input') === null, 'send: the new screen, no questions yet');
  check(byClass(next, 'chip').some(function (c) { return c.textContent === 'waiting for Hover'; }),
    'send: the new plan waits for the worker');
  eq([handlers('drop'), handlers('dragover'), handlers('beforeunload'), handlers('hashchange')], [0, 0, 0, 1],
    'send: the old files box stopped');
})();

// 8. a plan whose questions are all optional: Run stays on, Send answers is not the primary button
(function () {
  const id = 'W-20261104-003';
  const root = open(id, [plan(id, {needs: [N3], plan_cases: [TERR]})]);
  const card = cardOf(root, 'Needs your input');
  check(card !== null, 'optional only: the card shows');
  if (!card) return;
  eq(texts(byClass(byClass(card, 'card-head')[0], 'chip')), [], 'optional only: no "required" chip');
  const run = runButton(root);
  check(!run.disabled && run.textContent === 'Run on TA5 (1 of 3 devices)', 'optional only: Run stays on, got ' +
    run.textContent);
  eq(button(card, 'Send answers').className, 'btn btn-secondary', 'optional only: Send answers is secondary');
  eq([byClass(root, 'test-files').length, handlers('drop')], [0, 0], 'optional only: no file question, no files box');
  check(warnBox(root, 'Nothing to run') === null, 'optional only: no empty-plan box');
})();

// 9. a follow-up plan lists the answers; the answered plan points to it and sends nothing more
(function () {
  const id = 'W-20261104-004';
  const follow = plan(id, {needs: [], plan_cases: [TERR], summary: 'Your answers: skip the latitudes, use fresh.tif.'},
    {follows: A_ID, answers: [{need: 'N1', choice: 'skip'}, {need: 'N2', file: {name: 'fresh.tif', sha256: SHA.fresh}},
      {need: 'N3', text: LONG}]});
  let root = open(id, [follow, PLAN_A]);
  const pc = cardOf(root, 'What I will do');
  const label = find(pc, function (n) { return n.classList.contains('field-label') && n.textContent === 'Your answers'; });
  check(label !== null, 'follow-up: Your answers in the plan card');
  if (label) {
    eq(texts(byTag(label.parentNode, 'LI')), [N1.text + ' → Skip latitudes', N2.text.slice(0, 79) + '… → fresh.tif',
      N3.text + ' → ' + LONG.slice(0, 79) + '…'], 'follow-up: one line per answer');
  }
  check(cardOf(root, 'Needs your input') === null && !runButton(root).disabled, 'follow-up: no questions, Run on');

  root = open(A_ID, [follow, PLAN_A]);
  const card = cardOf(root, 'Needs your input');
  check(card !== null, 'answered: the questions still show');
  if (!card) return;
  button(byClass(card, 'need-row')[0], 'Skip latitudes').click();
  check(button(card, 'Send answers').disabled, 'answered: Send answers stays off');
  const link = find(card, function (n) { return n.tagName === 'A' && n.textContent === 'open the newer plan'; });
  eq(link && link.getAttribute('href'), '#/new?show=plan&req=' + id, 'answered: the link to the newer plan');
})();

// 10. a plan without questions keeps its words; a blocking question with a case keeps Run off; one files box per
// screen, also from screen to screen
(function () {
  let id = 'W-20261104-005';
  let root = open(id, [plan(id, {needs: []})]);
  check(cardOf(root, 'Needs your input') === null, 'no questions: no card');
  eq(runButton(root).textContent, 'Nothing to run', 'no questions: Run says Nothing to run');
  const empty = warnBox(root, 'Nothing to run');
  eq(empty && empty.textContent, 'Nothing to run: the plan has no test case. The devices are ready; "What I will do" ' +
    'above says what the test needs.', 'no questions: the empty-plan words stay');

  id = 'W-20261104-006';
  root = open(id, [plan(id, {needs: [N2, N4], plan_cases: [TERR]})]);
  const run = runButton(root);
  check(run.disabled && run.textContent === 'Answer the questions first', 'case and question: Run off, got ' +
    run.textContent);
  check(warnBox(root, 'Nothing to run') === null, 'case and question: no empty-plan box');
  const rows = byClass(cardOf(root, 'Needs your input'), 'need-row');
  eq(texts(byTag(byTag(rows[1], 'SELECT')[0], 'OPTION')), ['Choose an attached file', 'cloud.laz'],
    'two file questions: each lists the files it takes');
  eq(texts(byClass(cardOf(root, 'Needs your input'), 'card-head')[0].childNodes.slice(1)), ['1 required'],
    'two file questions: one blocking');
  eq([byClass(root, 'test-files').length, handlers('drop')], [1, 1], 'two file questions: one files box');

  id = 'W-20261104-007';
  root = open(id, [plan(id, {needs: [N2], checked_at: iso(3600)})]);
  eq([byClass(root, 'test-files').length, handlers('drop'), handlers('hashchange')], [1, 1, 2],
    'next screen: still one files box');
  // the same screen drawn again without a hashchange (goTo to the address it shows): the old box stops
  RENDER();
  flush();
  check(view() !== root, 'same screen again: drawn anew');
  root = view();
  eq([byClass(root, 'test-files').length, handlers('drop'), handlers('dragover'), handlers('beforeunload'),
    handlers('hashchange')], [1, 1, 1, 1, 2], 'same screen again: still one files box');
  const card = cardOf(root, 'Needs your input');
  button(byClass(card, 'need-row')[0], 'Make one for me').click();
  check(!button(card, 'Send answers').disabled && runButton(root).disabled,
    'an old plan: answers can still be sent (they make a new plan)');
})();

// 11. the Plan screen (phase 2): the plan's title on top with the ClickUp test under it; each case's parts with the
// checks marked and the task's words quoted, every engine step folded; a case written before the rule shows its
// check steps (the steps with an assert, clean-up left out)
(function () {
  const id = 'W-20261104-008';
  const parts = {case: {id: 'TERR-012', steps: [{n: 1, text: 'Top bar: Sites', part: 1}, {n: 2, text: 'New site',
    part: 1}, {n: 3, text: 'Read the list', part: 2, assert: {kind: 'text'}}, {n: 4, text: 'Clean up', always: true}],
  parts: [{n: 1, text: 'New QA_ site at Spain 40.4N', quote: 'create new sites for all of them', check: false},
    {n: 2, text: 'Maps import, 28 m DSM: accepted', quote: ['accepts any resolution', '28 m resolution'], check: true}],
  expected: 'The 28 m map is accepted'}};
  const checksOnly = {case: {id: 'TERR-013', steps: [{n: 1, text: 'Open the site'},
    {n: 2, text: 'Check the site is available offline', assert: {kind: 'text'}},
    {n: 3, text: 'Wi-Fi back on', always: true, assert: {kind: 'text'}}], expected: null}};
  net.cases = {'TERR-012': parts, 'TERR-013': checksOnly};
  const c12 = Object.assign({}, TERR, {id: 'TERR-012', changed_in_plan: true});
  const c13 = Object.assign({}, TERR, {id: 'TERR-013', new: true});
  const title = 'Custom terrain on Android: 28 m limit at 4 latitudes, terrain kept offline';
  const root = open(id, [plan(id, {needs: [], plan_cases: [c12, c13, TERR], title: title}, {title: title})]);
  const h1 = byTag(root, 'H1');
  eq(h1.map(function (h) { return h.textContent; }), [title], 'plan title: on top of the Plan screen');
  const ck = find(root, function (n) { return n.tagName === 'A' && n.textContent === 'ClickUp: DSM import per latitude ↗'; });
  check(ck !== null && ck.getAttribute('href') === CU.url && ck.getAttribute('target') === '_blank',
    'plan title: the ClickUp test in grey under it');
  check(root.childNodes[2] === errSlot(root) && byClass(root.childNodes[1], 'head-sub').length === 1,
    'plan title: in the head, the error slot stays under it');
  const lists = byClass(root, 'plan-parts');
  eq(lists.length, 2, 'parts: a list for the case with parts and one for the case with checks');
  const items = byTag(lists[0], 'LI');
  eq(items.map(function (li) { return li.childNodes[0].textContent; }), ['New QA_ site at Spain 40.4N',
    'Maps import, 28 m DSM: accepted'], 'parts: each part\'s text, in order');
  eq(items.map(function (li) { return texts(byClass(li, 'chip')); }), [[], ['Check']], 'parts: the checks marked');
  eq(items.map(function (li) { return texts(byClass(li, 'plan-quote')); }), [['"create new sites for all of them"'],
    ['"accepts any resolution"', '"28 m resolution"']], 'parts: the task\'s words quoted');
  const folds = byClass(root, 'plan-steps');
  eq(folds.map(function (f) { return f.childNodes[0].textContent; }), ['All engine steps (4)', 'All engine steps (3)',
    'All engine steps (1)'], 'engine steps: folded under their count');
  check(folds.every(function (f) { return !f.open; }), 'engine steps: folded away');
  eq(texts(byTag(byTag(folds[0], 'OL')[0], 'LI')), ['Top bar: Sites', 'New site', 'Read the list', 'Clean up'],
    'engine steps: every step in the fold');
  check(folds[0].textContent.indexOf('Expected: The 28 m map is accepted') >= 0, 'engine steps: the expected result too');
  const label = find(root, function (n) { return n.classList.contains('field-label') && n.textContent === 'Checks'; });
  check(label !== null, 'checks: a case without parts shows its check steps');
  eq(texts(byTag(lists[1], 'LI')), ['Check the site is available offline'], 'checks: the steps with an assert, ' +
    'without clean-up');
  check(byTag(lists[1], 'LI')[0].getAttribute('value') === null && byTag(lists[1], 'LI')[0].value === '2',
    'checks: numbered by their step');
  check(find(root, function (n) {
    return n.tagName === 'P' && n.textContent === 'No checks in this version: the engine steps say what it does.';
  }) !== null, 'no checks: said so');
  net.cases = {};
})();

// 11b. while Hover writes the plan the title is the description cut; the plan's own title takes its place when the
// plan comes (Run approves what shows)
(function () {
  const id = 'W-20261104-010';
  const cut = 'Import a DSM per latitude and fly over it.';
  const own = 'DSM import at 8 latitudes, then a flight over each';
  const writing = plan(id, {}, {state: 'claimed', title: cut});
  delete writing.result;
  const root = open(id, [writing]);
  eq(byTag(root, 'H1').map(function (h) { return h.textContent; }), [cut], 'plan title: the description while it is ' +
    'written');
  net.worker = {worker: {state: 'ready'}, requests: [plan(id, {needs: [], plan_cases: [TERR], title: own},
    {title: own})], now: iso(0)};
  const due = timers;
  timers = [];
  for (const t of due) t.fn();
  flush();
  eq(byTag(view(), 'H1').map(function (h) { return h.textContent; }), [own], 'plan title: the plan\'s own when it comes');
  timers = [];
})();

// 12. New test: "Your plans" (plan rows only, titled by the server, the ClickUp name in grey) and the line to the
// tests that wait in History
(function () {
  const stopped = plan('W-20261104-013', {needs: []}, {state: 'failed', title: 'In a downloaded site, while offline, · ' +
    'open/edit/save an existing flight and check so…', notes: [{at: iso(60), text: 'Too old to start now.'}]});
  const ready = Object.assign(plan('W-20261104-014', {needs: [], plan_cases: [TERR]}, {title: null}),
    {clickup: Object.assign({}, CU, {task_id: 'other1', title: 'Other test'})});
  const start = {id: 'W-20261104-015', kind: 'worker_request', type: 'start', state: 'claimed', intake: true,
    check: 'W-20261104-009', task: 'T-20261104-003', clickup: Object.assign({}, CU, {task_id: 'other2'}),
    targets: ['TA5'], created: iso(30), title: 'Running test title'};
  net.worker = {worker: {state: 'ready'}, requests: [start, ready, stopped], now: iso(0)};
  net.tests = {ongoing: {count: 1, rows: []}, tested: {count: 10, rows: [], more_unreported: 2},
    done: {count: 8, rows: []}, shown: 5, rows_max: 100};
  window.location.hash = '#/new';
  flush();
  const root = view();
  const card = cardOf(root, 'Your plans');
  check(card !== null && cardOf(root, 'Your tests') === null, 'your plans: "Your tests" became "Your plans"');
  if (!card) return;
  const rows = byClass(card, 'test-row');
  eq(rows.map(function (r) { return [byClass(r, 'chip')[0].textContent, byClass(r, 'strong')[0].textContent]; }),
    [['Running', 'Running test title'], ['Plan ready', 'Other test'], ['Plan stopped', 'In a downloaded site, while ' +
      'offline, · open/edit/save an existing flight and check so…']], 'your plans: the rows, titled by the server');
  eq(byClass(rows[2], 'plan-sub')[0].textContent.split(' · ').slice(0, 2), ['DSM import per latitude', 'TA5, ET65'],
    'your plans: the ClickUp name and the devices in grey');
  eq(byClass(rows[2], 'plan-sub')[1].textContent, 'Too old to start now.', 'your plans: the note');
  const go = function (r) { return byTag(r, 'A').map(function (a) { return a.getAttribute('href'); }); };
  eq(go(rows[0]), ['#/results?task=T-20261104-003'], 'your plans: a Run on its way opens its test on Results');
  eq(go(rows[2]), ['#/new?show=plan&req=W-20261104-013'], 'your plans: a plan opens its screen');
  const line = find(root, function (n) { return n.classList.contains('tests-line'); });
  eq(line && line.textContent, 'Tests you ran: 10 wait for you in History ›', 'your plans: the line to History');
  eq(byTag(line, 'A')[0].getAttribute('href'), '#/history', 'your plans: it links History');
  check(net.gets.indexOf('/api/tasks') < 0, 'your plans: the tasks are no longer read for it');
  net.tests = null;
  window.location.hash = '#/new?x=1';
  flush();
  const again = find(view(), function (n) { return n.classList.contains('tests-line'); });
  eq(again && again.textContent, 'Tests you ran: see History ›', 'your plans: without the count, still the link');
})();

// ---- the debug pass of 2026-11-05 (tests/2026-11-05-wingmate-debug/FINDINGS.md) ----

// 13. FINDINGS #10: a newer check of a ClickUp task, cancelled before Hover took it, made no plan: the older plan of
// that task, which can still run, keeps its row in Your plans
(function () {
  const ready = plan('W-20261105-002', {needs: [], plan_cases: [TERR]}, {title: 'DSM import, ready to run'});
  const cancelled = {id: 'W-20261105-003', kind: 'worker_request', type: 'check', state: 'cancelled', intake: true,
    prepare: true, clickup: CU, guidance: ready.guidance, targets: ['TA5', 'ET65'], created: iso(10),
    title: 'DSM import, ready to run'};
  net.worker = {worker: {state: 'ready'}, requests: [cancelled, ready], now: iso(0)};
  window.location.hash = '#/new?x=2';
  flush();
  const card = cardOf(view(), 'Your plans');
  const rows = card ? byClass(card, 'test-row') : [];
  eq(rows.map(function (r) { return [byClass(r, 'chip')[0].textContent, byClass(r, 'strong')[0].textContent]; }),
    [['Plan ready', 'DSM import, ready to run']], 'your plans: a cancelled newer check hides no older plan');
  eq(rows.length ? byTag(rows[0], 'A').map(function (a) { return a.getAttribute('href'); }) : [],
    ['#/new?show=plan&req=W-20261105-002'], 'your plans: the row opens the plan that can run');
})();

// 14. FINDINGS #19: the Plan screen shows a case in the version the plan pinned; when a newer version came after the
// plan, it reads the pinned file and says so, instead of the newest version's parts and step count
(function () {
  const OLD = 'e'.repeat(64);
  const NEW = 'f'.repeat(64);
  const pinned = {id: 'TERR-009', file: 'qa/cases/TERR-009.json', sha256: OLD, new: false, targets: ['TA5'],
    text: 'Alaska 61.2N: Import file takes 28 m, refuses 29 m'};
  net.cases = {'TERR-009': {case: {id: 'TERR-009', steps: [{n: 1, text: 'Top bar: Sites', part: 1},
    {n: 2, text: 'Read the list', part: 2, assert: {kind: 'text'}}],
  parts: [{n: 1, text: 'New QA_ site at Alaska 61.2N', quote: 'create new sites for all of them', check: false},
    {n: 2, text: 'New flight, Use existing map: 28 m selectable', quote: 'Using an existing map', check: true}],
  expected: 'The newest version'}, latest_file: 'qa/cases/TERR-009.c1.json',
  versions: [{file: 'qa/cases/TERR-009.json', sha256: OLD}, {file: 'qa/cases/TERR-009.c1.json', sha256: NEW}]}};
  net.fileJSON = {'/files/qa/cases/TERR-009.json': {id: 'TERR-009', steps: [{n: 1, text: 'Open the planner'},
    {n: 2, text: 'Import file: read the 28 m map', assert: {kind: 'text'}},
    {n: 3, text: 'Import file: read the 29 m refusal', assert: {kind: 'text'}}], expected: 'The pinned version'}};
  net.gets = [];
  let id = 'W-20261105-010';
  let root = open(id, [plan(id, {needs: [], plan_cases: [pinned]})]);
  check(net.gets.indexOf('/files/qa/cases/TERR-009.json') >= 0, 'pinned version: read from its own file');
  check(find(root, function (n) {
    return n.tagName === 'P' && n.textContent === 'This plan runs an earlier version of this case; a newer one exists.';
  }) !== null, 'pinned version: the screen says a newer one exists');
  const lists = byClass(root, 'plan-parts');
  eq(lists.length ? texts(byTag(lists[0], 'LI')) : [], ['Import file: read the 28 m map',
    'Import file: read the 29 m refusal'], 'pinned version: its own checks');
  eq(byClass(root, 'plan-steps').map(function (f) { return f.childNodes[0].textContent; }), ['All engine steps (3)'],
    'pinned version: its own step count');
  check(root.textContent.indexOf('New QA_ site at Alaska 61.2N') < 0 && root.textContent.indexOf('The pinned version') >= 0,
    'pinned version: none of the newest version\'s parts, its own expected result');
  // the plan pinned the newest version: the record of /api/cases, no file read, no line
  net.gets = [];
  id = 'W-20261105-011';
  root = open(id, [plan(id, {needs: [], plan_cases: [Object.assign({}, pinned, {file: 'qa/cases/TERR-009.c1.json',
    sha256: NEW})]})]);
  check(net.gets.filter(function (u) { return u.indexOf('/files/') === 0; }).length === 0 &&
    root.textContent.indexOf('a newer one exists') < 0, 'newest pinned: no file read, no line');
  eq(byTag(byClass(root, 'plan-parts')[0], 'LI').map(function (li) { return li.childNodes[0].textContent; }),
    ['New QA_ site at Alaska 61.2N', 'New flight, Use existing map: 28 m selectable'], 'newest pinned: its parts');
  net.cases = {};
  net.fileJSON = {};
})();

// 15. FINDINGS #24 and #20, Run records (the older detailed page, #/results?view=detail): the records show as soon as GET
// /api/results answers, and the filters fill in when the case list comes; a filter and Close keep view=detail, so the
// person stays on Run records instead of landing on the one-test page
(function () {
  const results = '/api/results?case=TERR-012&task=T-20261104-002';
  net.json = {};
  net.json[results] = {cases: [{case: 'TERR-012', title: 'Custom terrain 28 m limit at Spain', rows: []}],
    backtest: null};
  net.json['/api/results?case=TERR-011&task=T-20261104-002'] = {cases: [], backtest: null};
  net.json['/api/cases'] = {cases: [{id: 'TERR-011', title: 'South Africa'}, {id: 'TERR-012', title: 'Spain'}]};
  net.json['/api/tasks'] = {tasks: [{id: 'T-20261104-002', text: 'Run from New test: Custom terrain. Cases TERR-012.'}]};
  net.held = {'/api/cases': true};
  window.location.hash = '#/results?view=detail&task=T-20261104-002&case=TERR-012';
  flush();
  let root = view();
  check(find(root, function (n) {
    return n.tagName === 'H2' && n.textContent === 'TERR-012 · Custom terrain 28 m limit at Spain';
  }) !== null, 'records: shown before the case list came');
  check(find(root, function (n) { return n.classList.contains('loading') && n.textContent === 'Loading filters…'; }) !==
    null && byTag(root, 'SELECT').length === 0, 'records: the filters wait for the case list');
  release('/api/cases');
  flush();
  const sels = byTag(root, 'SELECT');
  check(sels.length === 2 && sels[0].value === 'TERR-012' && sels[1].value === 'T-20261104-002',
    'records: the filters came, set to the case and the task');
  choose(sels[0], 'TERR-011');
  flush();
  eq(window.location.hash, '#/results?view=detail&case=TERR-011&task=T-20261104-002',
    'records: the Case filter stays on Run records');
  root = view();
  const cb = find(root, function (n) { return n.tagName === 'INPUT' && n.getAttribute('type') === 'checkbox'; });
  check(cb !== null, 'records: the harness box');
  if (cb) {
    cb.checked = true;
    cb.dispatch('change');
    flush();
  }
  eq(window.location.hash, '#/results?view=detail&case=TERR-011&task=T-20261104-002&harness=1',
    'records: "Show harness cases" stays on Run records');
  // a run opened from a link, then Close
  net.replaced = [];
  window.location.hash = '#/results?view=detail&task=T-20261104-002&case=TERR-012&run=R-20261104-TA5-TERR-012-01c1';
  flush();
  root = view();
  const close = button(root, 'Close');
  check(close !== null, 'records: the opened run has Close');
  if (close) {
    close.click();
    flush();
  }
  eq(net.replaced, ['#/results?view=detail&case=TERR-012&task=T-20261104-002&run=R-20261104-TA5-TERR-012-01c1',
    '#/results?view=detail&case=TERR-012&task=T-20261104-002'], 'records: the opened run and Close keep view=detail');
  net.json = {};
  net.held = {};
})();

print(JSON.stringify({failures: failures, checks: checks}));
