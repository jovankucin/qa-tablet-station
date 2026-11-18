// plan_pre_harness.js: runs static/app.js in JavaScriptCore's jsc with a fake DOM, window, location and fetch, and
// checks what a New test plan's cases take as given on the Plan screen (2026-11-10, after the custom-terrain test
// 0000fake009, where Run approved a plan that rested on a precondition "set by the person" the screen never showed):
// under each case's parts the list "Before the run, this test takes as given" with the case's preconditions as the
// case page shows them (id, text, kind of device), in the version the plan pinned; nothing for a case without any; a
// line from the person (check "person", or a text that says "by the person" or "by you") as a warning box with the
// chip "Set by you". The list changes nothing about Run. It also calls fromPerson and preItem, cut out of app.js.
// Run by test_plan_pre.py:
//   jsc plan_pre_harness.js -- <path of app.js>
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

// ---- fake DOM (as in needs_page_harness.js, without the parts only its forms use) ----

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
  }
  setAttribute(k, v) { if (k === 'class') this.className = String(v); else this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    const l = this.listeners[type] || [];
    if (l.indexOf(fn) >= 0) l.splice(l.indexOf(fn), 1);
  }
  focus() { document.activeElement = this; }
  scrollIntoView() {}
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

window.setTimeout = function () { return 0; };  // a finished plan is not polled again
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

// cases: the answers of GET /api/cases/<id> (none: 404); fileJSON: the answers of /files/<path> (a case file the plan
// pinned); writes: every request that is not a GET (the list is read-only, so it stays empty)
const net = {worker: null, cases: {}, fileJSON: {}, gets: [], writes: []};

function copy(x) {
  return JSON.parse(JSON.stringify(x));
}

function respond(status, body) {
  return Promise.resolve({ok: status >= 200 && status < 300, status: status, statusText: '',
    text: function () { return Promise.resolve(JSON.stringify(body)); }});
}

window.fetch = function (url, init) {
  const method = (init && init.method) || 'GET';
  if (method !== 'GET') {
    net.writes.push(method + ' ' + url);
    return respond(404, {error: 'no route: ' + method + ' ' + url});
  }
  net.gets.push(url);
  if (url === '/api/meta') {
    return respond(200, {env: 'staging', account_label: 'qa-stag', targets: [{tag: 'TA5'}, {tag: 'ET65'}, {tag: 'W044'},
      {tag: 'TA3'}]});
  }
  if (url === '/api/worker') return respond(200, copy(net.worker));
  if (url.indexOf('/api/cases/') === 0) {
    const id = decodeURIComponent(url.slice('/api/cases/'.length));
    return id in net.cases ? respond(200, copy(net.cases[id])) : respond(404, {error: 'not found: ' + url});
  }
  if (url.indexOf('/files/') === 0 && url in net.fileJSON) return respond(200, copy(net.fileJSON[url]));
  return respond(404, {error: 'not found: ' + url});
};

// ---- data: the plan of 2026-11-10 (case OFFL-003 c2), cut to what the screen reads ----

function iso(secondsAgo) {
  return new Date(Date.now() - (secondsAgo || 0) * 1000).toISOString();
}

const LABEL = 'Before the run, this test takes as given';
const CU = {task_id: '0000fake009', custom_id: null, title: 'Download custom terrain flight',
  url: 'https://app.clickup.com/t/0000fake009', priority: 'high', status: 'to do'};
const ROWS = [{tag: 'TA5', ok: true, connected: true, account: true, app_ok: true, app: '1.77.1-staging-8203f39'},
  {tag: 'ET65', ok: true, connected: true, account: true, app_ok: true, app: '1.76.0-staging-82e23ff'}];
const P1 = {id: 'P1', applies_to: 'tablet', check: 'run.py --check-account', text: 'qa-stag account logged in'};
const P2 = {id: 'P2', applies_to: 'both', check: 'act.py ready (offline false)', text: 'device online'};
const P3 = {id: 'P3', applies_to: 'tablet', check: 'person', text: 'Customterrain_02 in Hedingen2026 has ' +
  '20261020_10462_survey61_ground chosen as custom terrain (set by the person)'};
// how the Plan screen shows P3: [text, class, chips]
const P3_LINE = ['P3 ' + P3.text + ' tablet Set by you', 'msg msg-warn', ['chip s-warn']];
const PARTS = [{n: 1, text: 'Open flight Customterrain_02 in Hedingen2026 online', check: false,
  quote: ['Use Hedingen2026 site', 'Use Customterrain_02 as a flight plan']},
{n: 2, text: 'Online: the flight uses custom terrain 20261020_10462_survey61_ground', check: true,
  quote: 'use custom terrain from an already processed mission'}];
const STEPS = [{n: 1, text: 'Top bar: Sites', part: 1}, {n: 2, text: 'Read the sidebar', part: 2,
  assert: {kind: 'scripted'}}, {n: 3, text: 'Clean-up: Sites (always)', always: true}];

// the answer of GET /api/cases/<id> for a case whose newest version is the one a plan pins
function record(id, fields) {
  const file = 'qa/cases/' + id + '.json';
  return {case: Object.assign({id: id, targets: 'tablet', parts: PARTS, steps: STEPS,
    expected: 'The sidebar shows the custom terrain'}, fields), latest_file: file,
  versions: [{file: file, sha256: 'a'.repeat(64)}]};
}

function planCase(id, fields) {
  return Object.assign({id: id, file: 'qa/cases/' + id + '.json', sha256: 'a'.repeat(64), new: false,
    targets: ['TA5', 'ET65'], text: 'Custom terrain stays selected offline'}, fields || {});
}

function plan(id, cases) {
  return {id: id, kind: 'worker_request', type: 'check', state: 'done', intake: true, prepare: true, clickup: CU,
    clickup_cases: [], guidance: 'Download the flight plan offline while having the custom terrain selected.',
    targets: ['TA5', 'ET65'], cases: [], engine_cases: [], fix_commits: [], case_versions: [], files: [],
    created: iso(400), done_at: iso(60), notes: [], result: {checked_at: iso(60),
      summary: 'Test: custom terrain stays selected offline.', targets: ROWS, plan_cases: cases, not_covered: [],
      needs: []}};
}

// ---- helpers for the checks ----

function view() {
  return viewEl.firstChild;
}

// opens a plan screen the way a link does, with the answers of GET /api/cases/<id> it reads
function open(rid, cases, records) {
  net.cases = records;
  net.worker = {worker: {state: 'ready'}, requests: [plan(rid, cases)], now: iso(0)};
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

function texts(nodes) {
  return nodes.map(function (n) { return n.textContent; });
}

function cardOf(root, title) {
  return find(root, function (n) {
    return n.tagName === 'SECTION' && byTag(n, 'H2').some(function (h) { return h.textContent === title; });
  });
}

function runButton(root) {
  return byTag(byClass(cardOf(root, 'Devices'), 'form-actions')[0], 'BUTTON')[0];
}

// the plan's cases, one list item each, in the "What I will do" card
function caseItems(root) {
  const ul = byClass(cardOf(root, 'What I will do'), 'list-plain')[0];
  return ul ? ul.childNodes : [];
}

// a case's lines under its title: what kind each is, in order
function slotKinds(caseItem) {
  const slot = caseItem.childNodes[caseItem.childNodes.length - 1];
  return slot.childNodes.map(function (n) { return n.className || n.tagName; });
}

// the lines of one "takes as given" list: [text, class, chips]
function lines(block) {
  return byTag(block, 'LI').map(function (li) {
    return [li.textContent, li.className, byClass(li, 'chip').map(function (c) { return c.className; })];
  });
}

// a node as [tag, class, children], a text as itself
function shape(n) {
  return n instanceof Element ? [n.tagName, n.className, n.childNodes.map(shape)] : n.textContent;
}

// ---- the named functions, cut out of app.js ----

const SRC = readFile(APP);

function cut(name) {
  const start = SRC.indexOf('\n  function ' + name + '(');
  if (start < 0) throw new Error('app.js has no function ' + name);
  return SRC.slice(start, SRC.indexOf('\n  }\n', start) + 4);
}

const F = new Function(['el', 'add', 'badge', 'chip', 'fromPerson', 'preItem'].map(cut).join('') +
  '\nreturn {el: el, badge: badge, chip: chip, fromPerson: fromPerson, preItem: preItem};')();

// 1. fromPerson: the check is "person" (also "the person", or with more after the word), or the text says something
// was done by the person ("by the person", "by you"); a line that only names the person, or a check that only holds
// the word somewhere, is not from the person
(function () {
  const yes = [{check: 'person'}, {check: ' Person '}, {check: 'PERSON', text: 'Flight saved'}, P3,
    {check: 'the person'}, {check: 'person: answered yes in W-20261110-004'}, {check: 'person\'s answer'},
    {check: null, text: 'Customterrain_02 has the map chosen as custom terrain (set by the person)'},
    {check: 'API GET /flights/:id', text: 'Map chosen and saved by  the Person'}, {text: 'Site made\nby the person.'},
    {check: null, text: 'Map chosen and saved (set by you)'}, 'Flight saved by the person'];
  const no = [P1, P2, {check: 'personal note', text: 'x'}, {check: 'a person in the office', text: 'x'},
    {check: null, text: 'The person\'s site exists'}, {check: null, text: 'Checked by the personnel office'},
    {check: null, text: 'Opened by your team'}, {check: ['person'], text: ['by the person']}, {check: null}, {},
    'device online', '', null, undefined, 7];
  for (const p of yes) check(F.fromPerson(p) === true, 'fromPerson: from the person, ' + JSON.stringify(p));
  for (const p of no) check(F.fromPerson(p) === false, 'fromPerson: not from the person, ' + JSON.stringify(p));
})();

// 2. preItem: the case page's line is what it was before 2026-11-10; the Plan screen's mark goes on and behind it
(function () {
  const before = function (p) {  // the case page's own code until the line moved into preItem
    if (typeof p !== 'object') return F.el('li', {}, String(p));
    return F.el('li', {}, p.id ? F.el('span', {class: 'code strong'}, p.id + ' ') : null, p.text || '',
      p.applies_to && p.applies_to !== 'both' ? [' ', F.badge('', p.applies_to)] : null);
  };
  for (const p of [P1, P2, P3, {text: 'No id'}, {id: 'P9'}, 'Logged in', 7]) {
    eq(shape(F.preItem(p)), shape(before(p)), 'preItem: the case page\'s line as before, ' + JSON.stringify(p));
  }
  eq(shape(F.preItem(P1)), ['LI', '', [['SPAN', 'code strong', ['P1 ']], 'qa-stag account logged in', ' ',
    ['SPAN', 'badge ', ['tablet']]]], 'preItem: the id, the text and the kind of device');
  eq(shape(F.preItem(P2)), ['LI', '', [['SPAN', 'code strong', ['P2 ']], 'device online']],
    'preItem: no badge for a line that applies to both');
  const marked = F.preItem(P3, 'msg msg-warn', [' ', F.chip('s-warn', 'Set by you')]);
  eq([marked.className, shape(marked)[2].slice(-2)], ['msg msg-warn', [' ', ['SPAN', 'chip s-warn', ['Set by you']]]],
    'preItem: the mark is the line\'s class and what follows the line');
  eq(shape(marked)[2].slice(0, -2), shape(before(P3))[2], 'preItem: the marked line keeps the case page\'s content');
})();

// ---- the Plan screen in the app ----

const FIRST = 'W-20261110-101';
net.cases = {'OFFL-003': record('OFFL-003', {preconditions: [P1, P2, P3]})};
net.worker = {worker: {state: 'ready'}, requests: [plan(FIRST, [planCase('OFFL-003')])], now: iso(0)};
window.location._hash = '#/new?show=plan&req=' + FIRST;  // the screen the app opens on
load(APP);
flush();

// 3. the plan of 2026-11-10: the list sits under the case's parts, above the folded engine steps; each line as on the
// case page; the line set by the person is a warning box with the chip, and the only one
(function () {
  const root = view();
  const blocks = byClass(root, 'plan-pre');
  eq(blocks.length, 1, 'list: one per case with preconditions');
  if (blocks.length !== 1) return;
  const block = blocks[0];
  const items = caseItems(root);
  check(items.length === 1 && items[0].contains(block), 'list: inside its case, in "What I will do"');
  eq(slotKinds(items[0]), ['plan-parts', 'plan-pre', 'plan-steps'], 'list: under the parts, above "All engine steps"');
  eq(shape(block.childNodes[0]), ['DIV', 'field-label', [LABEL]], 'list: titled in plain words');
  eq([block.childNodes.length, block.childNodes[1].tagName], [2, 'UL'], 'list: the title and one list');
  eq(lines(block), [['P1 qa-stag account logged in tablet', '', []], ['P2 device online', '', []], P3_LINE],
    'list: every precondition in the case\'s order; only the person\'s is a warning box with a chip');
  const li = byTag(block, 'LI');
  eq(li.map(function (x) { return texts(byClass(x, 'code')); }), [['P1 '], ['P2 '], ['P3 ']], 'list: the ids');
  eq(li.map(function (x) { return texts(byClass(x, 'badge')); }), [['tablet'], [], ['tablet']],
    'list: the kind of device, as on the case page');
  const chip = li[2].childNodes[li[2].childNodes.length - 1];
  eq([chip.textContent, chip.className], ['Set by you', 'chip s-warn'], 'mark: "Set by you" ends the line');
  eq(shape(li[0]), shape(F.preItem(P1)), 'list: a line is the case page\'s line');
  eq(texts(byTag(root, 'H2')), ['What I will do', 'Devices'], 'list: no new card on the screen');
  eq(root.textContent.split(LABEL).length - 1, 1, 'list: the title shows once');
  eq(byClass(root, 'plan-parts').length, 1, 'parts: still one list of parts');
  eq(byClass(root, 'plan-steps').map(function (f) { return f.childNodes[0].textContent; }), ['All engine steps (3)'],
    'engine steps: still folded under their count');
  const run = runButton(root);
  check(!run.disabled && run.textContent === 'Run on TA5, ET65 (2 of 3 devices)', 'Run: as without the list, got ' +
    run.textContent);
})();

// 4. the same plan with a case that has no preconditions: no title, no list, and Run reads the same
(function () {
  [undefined, [], null, 'qa-stag account logged in', {id: 'P1', text: 'not a list'},
    [null, {id: 'P1'}, {id: 'P2', text: ''}, '']].forEach(function (none, i) {
    const what = 'no preconditions (' + JSON.stringify(none) + ')';
    const fields = none === undefined ? {} : {preconditions: none};
    const root = open('W-20261110-20' + i, [planCase('OFFL-004')], {'OFFL-004': record('OFFL-004', fields)});
    check(byClass(root, 'plan-pre').length === 0 && root.textContent.indexOf(LABEL) < 0, what + ': nothing shows');
    eq(slotKinds(caseItems(root)[0]), ['plan-parts', 'plan-steps'], what + ': the parts, then the engine steps');
    eq(runButton(root).textContent, 'Run on TA5, ET65 (2 of 3 devices)', what + ': Run');
  });
})();

// 5. the mark follows the rule, not the place in the list: check "person" without the words, the words without the
// check, and lines that only name the person
(function () {
  const pre = [{id: 'P1', applies_to: 'both', check: 'person', text: 'Flight Demo is saved with the map'},
    {id: 'P2', applies_to: 'web', check: null, text: 'Map chosen and saved (Set by the Person)'},
    {id: 'P3', applies_to: 'both', check: 'API GET /sites/:id', text: 'The person\'s site Hedingen2026 exists'},
    {id: 'P4', applies_to: 'both', check: 'run.py --check-account', text: 'qa-stag account logged in'}];
  const root = open('W-20261110-301', [planCase('OFFL-005')], {'OFFL-005': record('OFFL-005', {preconditions: pre})});
  eq(lines(byClass(root, 'plan-pre')[0] || root), [['P1 Flight Demo is saved with the map Set by you', 'msg msg-warn',
    ['chip s-warn']], ['P2 Map chosen and saved (Set by the Person) web Set by you', 'msg msg-warn', ['chip s-warn']],
  ['P3 The person\'s site Hedingen2026 exists', '', []], ['P4 qa-stag account logged in', '', []]],
  'mark: by the check or by the words, on any line');
})();

// 6. each case has its own list; a case without preconditions has none; a case written before the parts rule shows
// the list under its check steps, and a case without steps still shows it
(function () {
  const checksOnly = record('OFFL-007', {parts: undefined, preconditions: [P3], steps: [{n: 1, text: 'Open the site'},
    {n: 2, text: 'Check the site is available offline', assert: {kind: 'text'}}]});
  const noSteps = record('OFFL-008', {parts: undefined, steps: [], preconditions: [P2]});
  const root = open('W-20261110-401', [planCase('OFFL-005'), planCase('OFFL-006'), planCase('OFFL-007'),
    planCase('OFFL-008')], {'OFFL-005': record('OFFL-005', {preconditions: [P1]}),
    'OFFL-006': record('OFFL-006', {preconditions: []}), 'OFFL-007': checksOnly, 'OFFL-008': noSteps});
  const items = caseItems(root);
  eq(items.length, 4, 'four cases: four list items');
  if (items.length !== 4) return;
  eq(items.map(function (li) { return byClass(li, 'plan-pre').map(lines); }), [
    [[['P1 qa-stag account logged in tablet', '', []]]], [],
    [[P3_LINE]], [[['P2 device online', '', []]]]],
  'four cases: each its own list, none for the case without preconditions');
  eq(slotKinds(items[2]), ['field-label', 'plan-parts', 'plan-pre', 'plan-steps'],
    'check steps: the list comes under them');
  eq(slotKinds(items[3]), ['muted', 'plan-pre', 'muted'], 'no steps: the list still shows');
  eq(byClass(root, 'msg-warn').length, 1, 'four cases: one warning box, on the person\'s line');
})();

// 7. the plan pinned an earlier version of the case: the list is that version's (read from its file), not the newest
// version's
(function () {
  const OLD = 'e'.repeat(64);
  const NEW = 'f'.repeat(64);
  const newest = record('OFFL-009', {preconditions: [P1]});
  newest.latest_file = 'qa/cases/OFFL-009.c1.json';
  newest.versions = [{file: 'qa/cases/OFFL-009.json', sha256: OLD}, {file: 'qa/cases/OFFL-009.c1.json', sha256: NEW}];
  net.fileJSON = {'/files/qa/cases/OFFL-009.json': Object.assign({}, newest.case, {preconditions: [P2, P3]})};
  net.gets = [];
  let root = open('W-20261110-501', [planCase('OFFL-009', {sha256: OLD})], {'OFFL-009': newest});
  check(net.gets.indexOf('/files/qa/cases/OFFL-009.json') >= 0 && root.textContent.indexOf('a newer one exists') >= 0,
    'pinned version: read from its own file, and the screen says so');
  eq(lines(byClass(root, 'plan-pre')[0] || root), [['P2 device online', '', []], P3_LINE],
    'pinned version: its own preconditions');
  // the plan pinned the newest version: the record of /api/cases, no file read
  net.gets = [];
  root = open('W-20261110-502', [planCase('OFFL-009', {file: 'qa/cases/OFFL-009.c1.json', sha256: NEW})],
    {'OFFL-009': newest});
  eq(net.gets.filter(function (u) { return u.indexOf('/files/') === 0; }), [], 'newest pinned: no file read');
  eq(lines(byClass(root, 'plan-pre')[0] || root), [['P1 qa-stag account logged in tablet', '', []]],
    'newest pinned: the newest version\'s preconditions');
  net.fileJSON = {};
})();

// 8. older shapes and Hover's own words: a precondition that is a plain text shows as text and is marked by its
// words; markup in a text stays text
(function () {
  const pre = ['Logged in', 'Flight saved by the person', {id: 'P3', applies_to: 'both', check: 'person',
    text: 'Map <b>chosen</b> & saved'}];
  const root = open('W-20261110-601', [planCase('OFFL-010')], {'OFFL-010': record('OFFL-010', {preconditions: pre})});
  eq(lines(byClass(root, 'plan-pre')[0] || root), [['Logged in', '', []], ['Flight saved by the person Set by you',
    'msg msg-warn', ['chip s-warn']], ['P3 Map <b>chosen</b> & saved Set by you', 'msg msg-warn', ['chip s-warn']]],
  'plain texts: shown and marked like the records');
  eq(byTag(root, 'B').length, 0, 'markup: a precondition\'s text stays text');
})();

// 9. a case that cannot be read: the screen says the steps did not load and shows no list
(function () {
  const root = open('W-20261110-701', [planCase('OFFL-011')], {});
  check(root.textContent.indexOf('The steps did not load: see ') >= 0 && byClass(root, 'plan-pre').length === 0,
    'not loaded: the steps line, no list');
})();

// 10. the list is read-only: the screens above sent nothing but GETs
eq(net.writes, [], 'read-only: no request other than GET');

print(JSON.stringify({failures: failures, checks: checks}));
