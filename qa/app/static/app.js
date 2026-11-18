/* QA app page (qa/app/DESIGN.md sections 2, 3, 4, 7).

Hash routes #/new (the home), #/results (results and tickets), #/history over the read API; #/today stays for old
links. Writes go only to the endpoints in
DESIGN.md section 4 and carry the per-start token from <meta name="qa-token"> in X-QA-Token. Every node is built
with createElement and textContent; nothing with data goes through innerHTML, and no inline style is set (CSP).
Links and image sources are limited to the app's own routes, /files/ and ClickUp task links.
*/
(function () {
  'use strict';

  const ROUTES = ['new', 'today', 'results', 'history'];
  const TITLES = {new: 'New test', today: 'Today', results: 'Results', history: 'History'};
  // Tabs removed on 2026-10-22; old links land on the tab that took their place.
  const ROUTE_ALIAS = {tasks: 'today', live: 'today', tickets: 'results', tests: 'history'};
  const VERDICTS = ['PASS', 'FAIL', 'BLOCKED', 'PARTIAL', 'ERROR', 'NOT_TESTED'];
  const ERROR_TIP = 'harness error: says nothing about the app';
  const DEVICE_STATE_CLASS = {
    READY: 's-ok', BUSY: 's-info', HANDED_OVER: 's-warn', OFFLINE_EXPECTED: 's-muted', LOST: 's-bad',
  };
  const SOURCE_TYPES = ['clickup_task', 'clickup_comment', 'spec_doc', 'user_chat'];
  const CASE_TARGETS = ['tablet', 'web', 'both'];
  const LINK_KINDS = ['none', 'mocklink', 'usb_radio', 'udp_sitl', 'real_drone'];
  const CL_RE = /^CL-\d{4,6}$/;
  const CLICKUP_RE = /^https:\/\/app\.clickup\.com\/t\/\S+$/;
  // The QA worker's own Claude session (worker.py LINK_RE), opened in the Claude app.
  const WORKER_LINK_RE = /^claude:\/\/claude\.ai\/epitaxy\/local_[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
  const IMAGE_RE = /\.(png|jpe?g|webp|gif)$/i;
  const LIVE_MS = 3000;
  const STATUS_OLD_S = 120;
  const CASE_TITLE_MAX = 80;
  const LAUNCHES_SHOWN = 5;

  const app = {gen: 0, route: null, meta: null, metaPromise: null, token: readToken()};
  const live = {active: false, inflight: false, timer: null, body: null, thumbs: new Map()};
  const lb = {node: null};

  // ---- DOM helpers ----

  function el(tag, props) {
    const n = document.createElement(tag);
    add(n, Array.prototype.slice.call(arguments, 2));
    if (props) {
      for (const k of Object.keys(props)) {
        const v = props[k];
        if (v === null || v === undefined || v === false || k === 'style') continue;
        if (k === 'class') n.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
        else if (k === 'text') n.textContent = String(v);
        else if (k === 'href') { const h = safeHref(v); if (h) n.setAttribute('href', h); }
        else if (k === 'src') { const s = safeSrc(v); if (s) n.setAttribute('src', s); }
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') n.addEventListener(k.slice(2), v);
        else if (k === 'value' && 'value' in n) n.value = String(v);
        else if (['checked', 'disabled', 'selected', 'hidden', 'open'].indexOf(k) >= 0) n[k] = Boolean(v);
        else n.setAttribute(k, v === true ? '' : String(v));
      }
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

  function clear(n) {
    while (n.firstChild) n.removeChild(n.firstChild);
    return n;
  }

  function put(n) {
    clear(n);
    return add(n, Array.prototype.slice.call(arguments, 1));
  }

  function safeHref(u) {
    const s = String(u);
    if (s.indexOf('#/') === 0 || s.indexOf('/files/') === 0 || s.indexOf('/static/') === 0) return s;
    return CLICKUP_RE.test(s) || WORKER_LINK_RE.test(s) ? s : null;
  }

  function safeSrc(u) {
    const s = String(u);
    return s.indexOf('/files/') === 0 ? s : null;
  }

  function fileUrl(rel) {
    if (!rel || typeof rel !== 'string') return null;
    return '/files/' + rel.replace(/^\/+/, '').split('/').map(encodeURIComponent).join('/');
  }

  function baseName(p) {
    const s = String(p || '');
    return decodeSafe(s.slice(s.lastIndexOf('/') + 1));
  }

  function decodeSafe(s) {
    try { return decodeURIComponent(s); } catch (e) { return s; }
  }

  // ---- value helpers ----

  function val(x) {
    return x && typeof x === 'object' && !Array.isArray(x) && 'v' in x ? x.v : x;
  }

  function verdictOf(x) {
    const v = val(x);
    return typeof v === 'string' && v ? v : null;
  }

  function isOn(x) {
    return Array.isArray(x) ? x.length > 0 : Boolean(x);
  }

  function list(x) {
    return Array.isArray(x) ? x : [];
  }

  function compact(x, max) {
    const limit = max || 400;
    let s;
    if (x === null || x === undefined) return '';
    if (typeof x === 'string') s = x;
    else {
      try { s = JSON.stringify(x); } catch (e) { s = String(x); }
    }
    return s.length > limit ? s.slice(0, limit - 1) + '…' : s;
  }

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
      pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function clockNow() {
    const d = new Date();
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  function fmtAge(s) {
    if (s === null || s === undefined || s === '' || isNaN(Number(s))) return 'unknown';
    const t = Math.max(0, Math.round(Number(s)));
    if (t < 60) return t + ' s';
    if (t < 3600) return Math.floor(t / 60) + ' min';
    if (t < 86400) return Math.floor(t / 3600) + ' h ' + Math.floor((t % 3600) / 60) + ' min';
    return Math.floor(t / 86400) + ' d';
  }

  function fmtBytes(n) {
    if (n === null || n === undefined || isNaN(Number(n))) return '';
    const b = Number(n);
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
    return (b / 1048576).toFixed(1) + ' MB';
  }

  function jsLen(s) {
    return String(s || '').trim().length;  // UTF-16 units after a JS trim, as the reporter form counts
  }

  function lines(s) {
    return String(s || '').split('\n').map(function (x) { return x.trim(); }).filter(Boolean);
  }

  function errText(e) {
    return e && e.message ? e.message : String(e);
  }

  function short(h, n) {
    const s = val(h);
    return typeof s === 'string' ? s.slice(0, n || 12) : '';
  }

  // ---- API ----

  function readToken() {
    const m = document.querySelector('meta[name="qa-token"]');
    const t = m ? m.getAttribute('content') || '' : '';
    return t.indexOf('{{') === 0 ? '' : t;
  }

  function qs(params) {
    const q = new URLSearchParams();
    for (const k of Object.keys(params || {})) if (params[k]) q.set(k, params[k]);
    const s = q.toString();
    return s ? '?' + s : '';
  }

  async function getJSON(path) {
    const init = {method: 'GET', headers: {Accept: 'application/json'}, cache: 'no-store', credentials: 'same-origin'};
    let r;
    try {
      r = await fetch(path, init);
    } catch (e) {
      // after the page sat idle the first request can hit a connection the server already closed: retry once
      await new Promise(function (done) { setTimeout(done, 500); });
      r = await reach(path, init);
    }
    return readBody(r);
  }

  // A write is never retried: it may have reached the server before the connection broke.
  const SEND_LOST = 'The app server did not answer. Reload to see whether it went through, then press again if needed.';

  async function reach(path, init, lost) {
    try {
      return await fetch(path, init);
    } catch (e) {
      throw new Error(lost || 'Cannot reach the app server (' + errText(e) + ').');
    }
  }

  async function sendJSON(method, path, body) {
    if (!app.token) throw new Error('This page has no write token. Open the address serve.py printed.');
    const r = await reach(path, {
      method: method,
      headers: {'Content-Type': 'application/json', 'X-QA-Token': app.token},
      body: JSON.stringify(body),
      cache: 'no-store',
      credentials: 'same-origin',
      referrerPolicy: 'same-origin',  // keeps the Origin header the server checks
    }, SEND_LOST);
    return readBody(r);
  }

  // One piece of a test file (qa/app/uploads.py): raw bytes, with the same token and Origin as sendJSON.
  async function sendPiece(path, blob, signal) {
    if (!app.token) throw new Error('This page has no write token. Open the address serve.py printed.');
    return readBody(await reach(path, {
      method: 'PUT', headers: {'Content-Type': 'application/octet-stream', 'X-QA-Token': app.token}, body: blob,
      cache: 'no-store', credentials: 'same-origin', referrerPolicy: 'same-origin', signal: signal,
    }));
  }

  async function readBody(r) {
    const text = await r.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = null; }
    }
    if (!r.ok) {
      const msg = data && typeof data.error === 'string' ? data.error : ('HTTP ' + r.status + ' ' + r.statusText).trim();
      const err = new Error(msg);
      err.status = r.status;
      throw err;
    }
    if (data === null || typeof data !== 'object') throw new Error('The server did not send JSON.');
    return data;
  }

  // ---- widgets ----

  function viewHead(title, sub) {
    return el('div', {class: 'view-head'}, el('h1', {text: title}), sub ? el('p', {text: sub}) : null);
  }

  function msgBox(kind, text) {
    return el('div', {class: 'msg msg-' + kind, role: kind === 'error' ? 'alert' : null}, text);
  }

  function errorBox(e, prefix) {
    return msgBox('error', (prefix ? prefix + ': ' : '') + errText(e));
  }

  function loading(text) {
    return el('p', {class: 'loading', text: text || 'Loading…'});
  }

  function tokenWarning() {
    return app.token ? null : msgBox('warn', 'This page has no write token, so saving is off. Open the address ' +
      'serve.py printed.');
  }

  function verdictChip(x, onclick) {
    const v = verdictOf(x) || 'PENDING';
    const known = VERDICTS.indexOf(v) >= 0;
    const cls = 'chip v-' + (known ? v : 'PENDING');
    const label = v === 'NOT_TESTED' ? 'NOT TESTED' : v;
    let title = null;
    if (v === 'ERROR') title = ERROR_TIP;
    else if (v === 'PENDING') title = 'no verdict yet';
    if (onclick) return el('button', {type: 'button', class: cls + ' chip-btn', title: title, onclick: onclick}, label);
    return el('span', {class: cls, title: title}, label);
  }

  function chip(cls, text, title) {
    return el('span', {class: 'chip ' + cls, title: title || null}, text);
  }

  function badge(cls, text, title) {
    return el('span', {class: 'badge ' + (cls || ''), title: title || null}, text);
  }

  function deviceStateChip(state) {
    return chip(DEVICE_STATE_CLASS[state] || 's-muted', state || 'unknown');
  }

  function evidenceBadge(kind) {
    const k = val(kind);
    if (k === 'device') return badge('ev-device', 'device', 'seen on the declared target, build and link');
    if (k === 'stand-in') return badge('ev-stand-in', 'stand-in', 'seen on a substitute (MockLink, other tablet or env)');
    if (k === 'code/API') return badge('ev-code', 'code/API', 'from source or a direct API read, nothing seen on screen');
    return badge('ev-none', k ? String(k) : 'no evidence');
  }

  function staleBadge(stale) {
    if (!stale || typeof stale !== 'object') return null;
    const why = list(stale.why).map(String);
    if (stale.v === true) return badge('badge-stale', 'stale', why.join('; ') || 'stale');
    if (stale.v === null || stale.v === undefined) {
      return badge('badge-unknown', 'stale?', why.join('; ') || 'staleness could not be checked');
    }
    return null;
  }

  function staleWhy(stale) {
    if (!stale || stale.v === false) return null;
    const why = list(stale.why).map(String);
    return why.length ? el('div', {class: 'small muted break'}, compact(why.join('; '), 200)) : null;
  }

  function okChip(ok, yes, no) {
    if (ok === true) return chip('s-ok', yes || 'ok');
    if (ok === false) return chip('s-bad', no || 'not ok');
    return chip('s-muted', 'unknown');
  }

  function kv(pairs) {
    const items = [];
    for (const p of pairs) {
      const v = p[1];
      if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) continue;
      items.push(el('dt', {}, p[0]), el('dd', {}, v instanceof Node || Array.isArray(v) ? v : String(v)));
    }
    return items.length ? el('dl', {class: 'kv'}, items) : null;
  }

  function timeEl(iso) {
    if (!iso) return null;
    return el('span', {class: 'nowrap', title: String(iso)}, fmtTime(iso));
  }

  function counterEl(n, max) {
    return el('span', {class: 'counter' + (n > max ? ' over' : ''), title: 'characters after trim'}, n + '/' + max);
  }

  function bindCounter(input, span, max) {
    const update = function () {
      const n = jsLen(input.value);
      span.textContent = n + '/' + max;
      span.classList.toggle('over', n > max);
    };
    input.addEventListener('input', update);
    update();
    return update;
  }

  function copyButton(text, label) {
    const name = label || 'Copy';
    const b = el('button', {type: 'button', class: 'btn btn-secondary btn-small'}, name);
    b.addEventListener('click', async function () {
      const ok = await copyText(typeof text === 'function' ? text() : String(text || ''));
      b.textContent = ok ? 'Copied' : 'Copy failed';
      setTimeout(function () { b.textContent = name; }, 1500);
    });
    return b;
  }

  async function copyText(t) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(t);
        return true;
      }
    } catch (e) { /* fall back below */ }
    const ta = el('textarea', {class: 'offscreen', readonly: true, 'aria-hidden': 'true'});
    ta.value = t;
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }

  function cmdLine(label, command) {
    return el('div', {class: 'cmd'}, label ? el('span', {class: 'small strong'}, label) : null,
      el('code', {}, command), copyButton(command));
  }

  function fileLink(rel, url, text) {
    const href = url || fileUrl(rel);
    if (!href || !safeHref(href)) return el('span', {class: 'break'}, text || rel || '');
    return el('a', {href: href, target: '_blank', rel: 'noopener noreferrer', class: 'break'}, text || rel);
  }

  function clickupLink(c) {
    if (!c || !c.url || !CLICKUP_RE.test(c.url)) return null;
    return el('a', {href: c.url, target: '_blank', rel: 'noopener noreferrer'}, c.cl || c.task_id || 'ClickUp task');
  }

  function runButton(runId, onOpen) {
    return el('button', {type: 'button', class: 'btn-link code', onclick: onOpen}, runId);
  }

  function resultsLink(caseId, runId, taskId) {
    return el('a', {href: routeHash('results', {case: caseId, task: taskId, run: runId}), class: 'code break'},
      runId || caseId);
  }

  // ---- thumbnails and lightbox ----

  function thumbStrip(urls, max, large) {
    const ok = list(urls).filter(function (u) { return typeof u === 'string' && safeSrc(u); });
    const shown = max ? ok.slice(0, max) : ok;
    if (!shown.length) return null;
    return el('div', {class: 'thumbs'}, shown.map(function (u, i) { return thumb(shown, i, large); }));
  }

  function thumb(urls, i, large) {
    const img = el('img', {src: urls[i], alt: baseName(urls[i]), loading: 'lazy'});
    const b = el('button', {type: 'button', class: 'thumb' + (large ? ' large' : ''), title: baseName(urls[i])}, img);
    b.addEventListener('click', function () { openLightbox(urls, i, b); });
    img.addEventListener('error', function () {
      b.classList.add('broken');
      b.disabled = true;
      b.title = 'image not served: ' + baseName(urls[i]);
      img.hidden = true;
      b.appendChild(el('span', {class: 'small muted'}, 'n/a'));
    });
    return b;
  }

  function buildLightbox() {
    lb.img = el('img', {alt: ''});
    lb.cap = el('div', {class: 'lb-caption'});
    lb.prev = el('button', {type: 'button', class: 'btn btn-secondary btn-small', onclick: function () { stepLightbox(-1); }},
      'Previous');
    lb.next = el('button', {type: 'button', class: 'btn btn-secondary btn-small', onclick: function () { stepLightbox(1); }},
      'Next');
    lb.full = el('a', {class: 'btn btn-secondary btn-small', target: '_blank', rel: 'noopener noreferrer'}, 'Open full size');
    lb.close = el('button', {type: 'button', class: 'btn btn-secondary btn-small', onclick: closeLightbox}, 'Close (Esc)');
    lb.node = el('div', {class: 'lightbox', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Screenshot', hidden: true},
      lb.img, lb.cap, el('div', {class: 'lb-bar'}, lb.prev, lb.next, lb.full, lb.close));
    lb.node.addEventListener('click', function (e) { if (e.target === lb.node) closeLightbox(); });
    document.addEventListener('keydown', function (e) {
      if (lb.node.hidden) return;
      if (e.key === 'Escape') closeLightbox();
      else if (e.key === 'ArrowLeft') stepLightbox(-1);
      else if (e.key === 'ArrowRight') stepLightbox(1);
    });
    document.body.appendChild(lb.node);
  }

  function openLightbox(urls, i, opener) {
    if (!lb.node) buildLightbox();
    lb.urls = urls;
    lb.i = i;
    lb.opener = opener || null;
    showLightbox();
    lb.node.hidden = false;
    lb.close.focus();
  }

  function showLightbox() {
    const u = lb.urls[lb.i];
    const src = safeSrc(u);
    if (src) {
      lb.img.setAttribute('src', src);
      lb.full.setAttribute('href', src);
    }
    lb.img.alt = baseName(u);
    lb.cap.textContent = baseName(u) + (lb.urls.length > 1 ? '  (' + (lb.i + 1) + ' of ' + lb.urls.length + ')' : '');
    lb.prev.disabled = lb.urls.length < 2;
    lb.next.disabled = lb.urls.length < 2;
  }

  function stepLightbox(d) {
    if (!lb.urls || lb.urls.length < 2) return;
    lb.i = (lb.i + d + lb.urls.length) % lb.urls.length;
    showLightbox();
  }

  function closeLightbox() {
    lb.node.hidden = true;
    lb.img.removeAttribute('src');
    if (lb.opener && lb.opener.isConnected) lb.opener.focus();
  }

  // ---- router ----

  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, '');
    const qi = raw.indexOf('?');
    const path = qi < 0 ? raw : raw.slice(0, qi);
    return {route: ROUTES.indexOf(path) >= 0 ? path : null, alias: ROUTE_ALIAS[path] || null,
      params: new URLSearchParams(qi < 0 ? '' : raw.slice(qi + 1))};
  }

  function routeHash(route, params) {
    return '#/' + route + qs(params);
  }

  function go(route, params) {
    goTo(routeHash(route, params));
  }

  function goTo(h) {
    if (location.hash === h) render();
    else location.hash = h;
  }

  function render() {
    const parsed = parseHash();
    if (!parsed.route) {
      location.replace(parsed.alias ? routeHash(parsed.alias, Object.fromEntries(parsed.params)) : '#/new');
      return;
    }
    liveStop();
    if (app.route !== parsed.route) window.scrollTo(0, 0);
    app.route = parsed.route;
    const gen = ++app.gen;
    const alive = function () { return gen === app.gen; };
    for (const a of document.querySelectorAll('.tabs a')) {
      const on = a.getAttribute('data-route') === parsed.route;
      a.classList.toggle('active', on);
      if (on) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    }
    document.title = TITLES[parsed.route] + ' · Hover';
    const root = el('div', {class: 'view view-' + parsed.route});
    put(document.getElementById('view'), root);
    VIEWS[parsed.route](root, parsed.params, alive).catch(function (e) {
      if (alive()) add(root, [errorBox(e, 'This view failed')]);
    });
  }

  async function loadMeta() {
    const envChip = document.getElementById('env-chip');
    try {
      const m = await getJSON('/api/meta');
      app.meta = m;
      envChip.textContent = (m.env || 'env unknown') + ' · ' + (m.account_label || 'account unknown');
      envChip.title = 'environment and engine account' + (m.web_origin ? ' · ' + m.web_origin : '');
      envChip.classList.toggle('bad', m.env === 'production');
      return m;
    } catch (e) {
      envChip.textContent = 'meta unavailable';
      envChip.title = errText(e);
      envChip.classList.add('bad');
      return null;
    }
  }

  // ---- Live ----

  async function viewLive(root) {
    const body = el('div', {}, loading('Loading live state…'));
    add(root, [viewHead('Live', 'Tablets and webpc044 as the engine sees them. Refreshes every 3 s while this ' +
      'tab is shown.'), body]);
    liveStart(body);
  }

  function liveStart(body) {
    live.active = true;
    live.body = body;
    livePoll();
  }

  function liveStop() {
    live.active = false;
    live.body = null;
    clearTimeout(live.timer);
    live.timer = null;
    live.thumbs.clear();
  }

  async function livePoll() {
    if (!live.active || live.inflight || document.visibilityState !== 'visible') return;
    clearTimeout(live.timer);
    live.timer = null;
    live.inflight = true;
    const body = live.body;
    try {
      const d = await getJSON('/api/live');
      if (live.active && live.body === body) renderLive(body, d);
    } catch (e) {
      if (live.active && live.body === body) {
        const old = body.querySelector('.live-error');
        const box = errorBox(e, 'Live state did not load (retrying)');
        box.classList.add('live-error');
        if (old) old.replaceWith(box);
        else body.insertBefore(box, body.firstChild);
      }
    } finally {
      live.inflight = false;
    }
    if (live.active && document.visibilityState === 'visible' && !live.timer) {
      live.timer = setTimeout(livePoll, live.body === body ? LIVE_MS : 0);
    }
  }

  function onVisibility() {
    if (document.visibilityState === 'visible') {
      livePoll();
    } else {
      clearTimeout(live.timer);
      live.timer = null;
    }
  }

  function renderLive(body, d) {
    const st = d.status && typeof d.status === 'object' ? d.status : null;
    const age = d.status_age_s;
    const parts = [];
    parts.push(el('div', {class: 'live-meta'}, el('span', {}, 'Updated ' + clockNow()),
      st ? el('span', {}, 'status.json refreshed ', timeEl(st.refreshed), ' (' + fmtAge(age) + ' ago)') : null));
    if (!st) {
      parts.push(msgBox('warn', 'No status.json yet: refresh it from the /qa-status pane or ask Claude.'));
    } else if (age === null || age === undefined || Number(age) > STATUS_OLD_S) {
      parts.push(msgBox('warn', 'status.json is ' + fmtAge(age) + ' old: refresh it from the /qa-status pane or ' +
        'ask Claude. Device states may be out of date; run progress below is live.'));
    }

    const progress = list(d.progress);
    const used = new Set();
    const cards = [];
    for (const t of list(st && st.tablets)) cards.push(tabletCard(t, d, progress, used));
    cards.push(webhostCard(st ? st.webpc044 : null, d, progress, used));
    for (const p of progress) {
      if (!used.has(p)) cards.push(el('article', {class: 'card device-card'},
        el('div', {class: 'card-head'}, el('h3', {}, String(targetName(p.target) || 'Run'))), progressBlock(p)));
    }
    parts.push(el('div', {class: 'grid'}, cards));
    parts.push(handbacksCard(list(d.handbacks)));
    parts.push(el('div', {class: 'grid'}, handoversCard(list(d.handovers)), locksCard(list(d.locks)), ledgerCard(st)));
    put(body, parts);
  }

  function targetName(x) {
    if (!x) return null;
    if (typeof x === 'string') return x;
    return x.tag || x.serial || x.host || null;
  }

  function matchesTarget(x, keys) {
    if (!x) return false;
    if (typeof x === 'string') return keys.indexOf(x) >= 0;
    return [x.tag, x.serial, x.host].some(function (k) { return k && keys.indexOf(k) >= 0; });
  }

  function lockLine(holder, lock) {
    const h = holder || (lock && lock.holder);
    if (!h && !lock) return 'free';
    const bits = [String(h || 'held')];
    if (lock && lock.case) bits.push(lock.case);
    return [bits.join(' · '), lock && lock.since ? [' since ', timeEl(lock.since)] : null];
  }

  function yesNo(v) {
    if (v === true) return chip('s-ok', 'yes');
    if (v === false) return chip('s-bad', 'no');
    return chip('s-muted', 'unknown');
  }

  // the lock a run holds on this device now; a stale lock file (its run was stopped) does not count
  function heldLock(d, keys) {
    return list(d.locks).find(function (l) { return !l.stale && keys.indexOf(l.name) >= 0; });
  }

  function tabletCard(t, d, progress, used) {
    const keys = [t.tag, t.serial].filter(Boolean);
    const lock = heldLock(d, keys);
    const runs = progress.filter(function (p) { return matchesTarget(p.target, keys); });
    runs.forEach(function (p) { used.add(p); });
    const handovers = list(d.handovers).filter(function (h) { return matchesTarget(h.target, keys); });
    const focus = t.focus || 'unknown';
    return el('article', {class: 'card device-card'},
      el('div', {class: 'card-head'}, el('h3', {}, t.tag || t.serial || 'tablet'),
        el('span', {class: 'small muted'}, [t.model, t.android ? 'Android ' + t.android : null].filter(Boolean).join(' · ')),
        el('span', {class: 'spacer'}), deviceStateChip(t.state)),
      t.why ? el('p', {class: 'small'}, t.why) : null,
      kv([
        ['Serial', el('span', {class: 'code'}, t.serial || '')],
        ['adb', chip(t.adb === 'device' ? 's-ok' : 's-bad', t.adb || 'missing')],
        ['Focus', chip(focus === 'app' ? 's-ok' : (focus === 'unknown' ? 's-muted' : 's-warn'), focus)],
        ['DevTools forward', yesNo(t.forward)],
        ['App pid', t.app_pid],
        ['CDP port', t.cdp_port],
        ['Lock', lockLine(t.lock, lock)],
      ]),
      handovers.map(function (h) {
        return msgBox('warn', 'Handed over: ' + (h.reason || 'to the tester') + (h.since ? ' (since ' + fmtTime(h.since) + ')' : ''));
      }),
      runs.length ? runs.map(progressBlock) : el('p', {class: 'small muted'}, 'No run in progress.'));
  }

  function webhostCard(w, d, progress, used) {
    const keys = ['W044', 'webpc044'];
    const lock = heldLock(d, keys);
    const runs = progress.filter(function (p) { return matchesTarget(p.target, keys); });
    runs.forEach(function (p) { used.add(p); });
    const x = w || {};
    const fc = x.full_check || {};
    const offset = fc.clock_offset_ms;
    return el('article', {class: 'card device-card'},
      el('div', {class: 'card-head'}, el('h3', {}, 'webpc044'), el('span', {class: 'small muted'}, 'web · W044'),
        el('span', {class: 'spacer'}), deviceStateChip(x.state)),
      w ? null : el('p', {class: 'small muted'}, 'Not in status.json.'),
      kv([
        ['Link', w ? yesNo(x.link) : null],
        ['SSH', w ? yesNo(x.ssh) : null],
        ['Chrome 9222', x.chrome_9222 ? String(x.chrome_9222) : (w ? chip('s-bad', 'down') : null)],
        ['Full check', fc.at ? [okChip(fc.ok, 'ok', 'failing'), ' ', timeEl(fc.at)] : null],
        ['Failing', list(fc.failing).length ? list(fc.failing).join(', ') : null],
        ['Browser', fc.browser],
        ['Clock offset', offset === null || offset === undefined ? null : offset + ' ms'],
        ['Lock', lockLine(null, lock)],
      ]),
      runs.length ? runs.map(progressBlock) : el('p', {class: 'small muted'}, 'No run in progress.'));
  }

  function progressBlock(p) {
    const total = Number(p.steps_total) || 0;
    const step = p.step && typeof p.step === 'object' ? p.step : null;
    const finished = p.state === 'finished';
    const n = finished ? total : (step ? Number(step.n) || 0 : 0);
    const shot = p.last_shot ? fileUrl(p.last_shot) : null;
    return el('div', {class: 'device-section'},
      el('div', {class: 'row'}, el('span', {class: 'strong'}, finished ? 'Last run' : 'Current run'),
        p.run ? resultsLink(p.case, p.run) : null, finished ? verdictChip(p.verdict) : chip('s-info', p.state || 'running')),
      kv([
        ['Case', p.case],
        ['Attempt', p.attempt],
        ['Step', step ? 'step ' + step.n + (total ? ' of ' + total : '') + ': ' + (step.text || '') :
          (finished ? 'finished' : 'not started')],
        ['Started', timeEl(p.started)],
        ['Updated', timeEl(p.updated)],
        ['Clock offset', p.clock_offset_ms === null || p.clock_offset_ms === undefined ? null : p.clock_offset_ms + ' ms'],
      ]),
      total ? el('progress', {max: total, value: Math.min(n, total), 'aria-label': 'steps done'}) : null,
      shot ? el('div', {}, el('div', {class: 'field-label'}, 'Last screenshot'), liveThumb(shot)) : null);
  }

  function liveThumb(url) {
    // Reuse the node between polls so the image is not fetched again every 3 s.
    if (!live.thumbs.has(url)) live.thumbs.set(url, el('div', {class: 'thumbs'}, thumb([url], 0, true)));
    return live.thumbs.get(url);
  }

  function handbacksCard(handbacks) {
    if (!handbacks.length) return null;
    return el('section', {class: 'card'},
      el('div', {class: 'card-head'}, el('h2', {}, 'Handbacks'), chip('s-warn', String(handbacks.length))),
      el('p', {class: 'small muted'}, 'Steps act.py refused under the runner. Claude runs the command with your OK.'),
      el('ul', {class: 'list-plain'}, handbacks.map(function (h) {
        return el('li', {}, el('div', {class: 'row'}, resultsLink(h.case, h.run), badge('', h.target || '?'),
          h.step !== undefined && h.step !== null ? el('span', {class: 'small'}, 'step ' + compact(h.step, 80)) : null,
          timeEl(h.created)),
        h.reason ? el('div', {class: 'small'}, h.reason) : null,
        h.command ? cmdLine(null, String(h.command)) : null);
      })));
  }

  function handoversCard(handovers) {
    return el('section', {class: 'card'}, el('h2', {}, 'Handovers'),
      handovers.length ? el('ul', {class: 'list-plain'}, handovers.map(function (h) {
        return el('li', {}, el('div', {class: 'row'}, badge('', targetName(h.target) || '?'), timeEl(h.since)),
          el('div', {class: 'small'}, h.reason || ''), h.file ? el('div', {class: 'small'}, fileLink(h.file)) : null);
      })) : el('p', {class: 'small muted'}, 'No target is handed over.'));
  }

  function locksCard(locks) {
    const stale = locks.some(function (l) { return l.stale; });
    return el('section', {class: 'card'}, el('h2', {}, 'Locks'),
      locks.length ? el('div', {class: 'table-wrap'}, el('table', {},
        el('thead', {}, el('tr', {}, ['Name', 'Holder', 'Case', 'Since'].map(function (h) { return el('th', {}, h); }))),
        el('tbody', {}, locks.map(function (l) {
          return el('tr', {}, el('td', {class: 'code'}, l.name || '', l.stale ? [' ', chip('s-warn', 'stale')] : null),
            el('td', {class: 'break'}, l.holder || ''), el('td', {}, l.case || ''), el('td', {}, timeEl(l.since)));
        })))) : el('p', {class: 'small muted'}, 'No locks held.'),
      stale ? el('p', {class: 'small muted'}, 'Stale: the file of a run that was stopped before it ended. No run holds ' +
        'the lock, so the device is free.') : null);
  }

  function ledgerCard(st) {
    const l = st && st.ledger ? st.ledger : null;
    const m = app.meta && app.meta.ledger ? app.meta.ledger : null;
    return el('section', {class: 'card'}, el('h2', {}, 'Ledger'),
      kv([
        ['At status refresh', l ? 'seq ' + l.seq + ' · head ' + (l.head || '') : null],
        ['At page load', m ? 'seq ' + m.seq + ' · head ' + (m.head12 || '') : null],
      ]) || el('p', {class: 'small muted'}, 'No ledger head known.'));
  }

  // ---- Results ----

  async function viewResults(root, params, alive) {
    const ctx = {
      caseId: params.get('case') || '', taskId: params.get('task') || '', runId: params.get('run') || '',
      harness: params.get('harness') === '1', detail: el('div', {}),
    };
    const filters = el('section', {class: 'card'}, loading('Loading filters…'));
    const body = el('div', {}, loading('Loading results…'));
    add(root, [viewHead('Results', 'Verdicts per case, target and build. ERROR is a harness error and says ' +
      'nothing about the app.'), filters, ctx.detail, body]);
    // the records show as soon as they come; the filters fill in when the case and task lists come (GET /api/cases
    // works out every case's hands-off check, about a second)
    Promise.allSettled([getJSON('/api/cases'), getJSON('/api/tasks')]).then(function (got) {
      if (!alive()) return;
      put(filters, resultFilters(ctx, got[0].status === 'fulfilled' ? list(got[0].value.cases) : [],
        got[1].status === 'fulfilled' ? list(got[1].value.tasks) : []));
    }).catch(function (e) {
      if (alive()) put(filters, errorBox(e, 'The filters did not load'));
    });
    let data = null;
    let failed = null;
    try {
      data = await getJSON('/api/results' + qs({case: ctx.caseId, task: ctx.taskId}));
    } catch (e) {
      failed = e;
    }
    if (!alive()) return;
    put(body, failed ? errorBox(failed, 'Results did not load') : renderResults(data, ctx));
    if (ctx.runId) openRun(ctx.runId, ctx);
  }

  function resultFilters(ctx, cases, tasks) {
    const caseSel = el('select', {}, el('option', {value: ''}, 'All cases'),
      cases.filter(function (c) { return ctx.harness || !c.harness || c.id === ctx.caseId; }).map(function (c) {
        return el('option', {value: c.id}, c.id + ' · ' + compact(c.title || '', 70));
      }));
    if (ctx.caseId && !cases.some(function (c) { return c.id === ctx.caseId; })) {
      caseSel.appendChild(el('option', {value: ctx.caseId}, ctx.caseId));
    }
    caseSel.value = ctx.caseId;
    const taskSel = el('select', {}, el('option', {value: ''}, 'All tasks'), tasks.map(function (t) {
      return el('option', {value: t.id}, t.id + ' · ' + compact(t.text || '', 60));
    }));
    if (ctx.taskId && !tasks.some(function (t) { return t.id === ctx.taskId; })) {
      taskSel.appendChild(el('option', {value: ctx.taskId}, ctx.taskId));
    }
    taskSel.value = ctx.taskId;
    const harness = el('input', {type: 'checkbox', checked: ctx.harness});
    // view=detail keeps the person on Run records: without it #/results is the one-test page
    const apply = function () {
      go('results', {view: 'detail', case: caseSel.value, task: taskSel.value, harness: harness.checked ? '1' : ''});
    };
    caseSel.addEventListener('change', apply);
    taskSel.addEventListener('change', apply);
    harness.addEventListener('change', apply);
    return el('div', {class: 'filters'},
      el('label', {class: 'field grow'}, el('span', {}, 'Case'), caseSel),
      el('label', {class: 'field grow'}, el('span', {}, 'Task'), taskSel),
      el('label', {class: 'checks'}, harness, 'Show harness cases'));
  }

  function renderResults(data, ctx) {
    const all = list(data.cases);
    const cases = all.filter(function (c) { return ctx.harness || !c.harness || c.case === ctx.caseId; });
    const parts = [];
    if (!cases.length) parts.push(msgBox('info', 'No runs match these filters.'));
    for (const c of cases) parts.push(caseResultCard(c, ctx));
    if (all.length > cases.length) {
      parts.push(el('p', {class: 'small muted'}, (all.length - cases.length) + ' harness case(s) hidden.'));
    }
    parts.push(backtestCard(data.backtest));
    return parts;
  }

  function caseResultCard(c, ctx) {
    const historySlot = el('div', {});
    const both = c.targets === 'both' || list(c.differentials).length > 0;
    const launches = launchBlock(c, ctx);
    return el('section', {class: 'card', id: 'case-' + c.case},
      el('div', {class: 'card-head'}, el('h2', {}, c.case + ' · ' + (c.title || '')),
        c.area ? badge('', c.area) : null, c.targets ? badge('', 'targets ' + c.targets) : null,
        c.harness ? badge('badge-harness', 'harness') : null, el('span', {class: 'spacer'}),
        el('a', {href: routeHash('history', {case: c.case}), class: 'small'}, 'Case details')),
      launches,
      launches ? el('h3', {class: 'section-title'}, 'By target and build') : null,
      buildMatrix(c, ctx, historySlot),
      historySlot,
      both ? differentialBlock(list(c.differentials), ctx) : null);
  }

  // Launches: runs launched together (same campaign, case and run number) against the case's three devices.

  function deviceClass(tag) {
    return tag === 'W044' ? 'web' : 'tablet';
  }

  function launchBlock(c, ctx) {
    const launches = list(c.launches);
    if (!launches.length) return null;
    const extra = list(c.extra_devices);
    const devices = list(c.devices).concat(extra);
    const head = el('tr', {}, el('th', {}, 'Launch'), devices.map(function (t) {
      return el('th', {class: 'col-device'}, el('div', {}, t),
        el('div', {class: 'small muted'}, extra.indexOf(t) >= 0 ? 'not one of the three' : deviceClass(t)));
    }));
    const bodies = launches.map(function (l, i) {
      return el('tbody', {class: 'launch', hidden: i >= LAUNCHES_SHOWN}, launchRows(l, devices, ctx));
    });
    let toggle = null;
    if (launches.length > LAUNCHES_SHOWN) {
      const more = 'Show all ' + launches.length + ' launches';
      const less = 'Show the newest ' + LAUNCHES_SHOWN;
      toggle = el('button', {type: 'button', class: 'btn btn-secondary btn-small', 'aria-expanded': 'false'}, more);
      toggle.addEventListener('click', function () {
        const all = toggle.getAttribute('aria-expanded') !== 'true';
        bodies.forEach(function (b, i) { b.hidden = !all && i >= LAUNCHES_SHOWN; });
        toggle.setAttribute('aria-expanded', all ? 'true' : 'false');
        toggle.textContent = all ? less : more;
      });
    }
    return el('div', {class: 'launch-block'},
      el('div', {class: 'row'}, el('h3', {}, 'Launches'), el('span', {class: 'small muted'},
        'Newest first. One row per launch, one column per device; a device that did not run is NOT TESTED.')),
      el('div', {class: 'table-wrap'}, el('table', {class: 'launches'}, el('thead', {}, head), bodies)),
      toggle ? el('div', {class: 'row'}, toggle) : null);
  }

  function launchRows(l, devices, ctx) {
    const cells = l.cells && typeof l.cells === 'object' ? l.cells : {};
    const conc = l.concurrency && typeof l.concurrency === 'object' ? l.concurrency : {};
    const main = el('tr', {},
      el('th', {class: 'row-launch'}, el('div', {class: 'strong'}, 'Run ' + (l.nn || '?')),
        el('div', {class: 'small muted break'}, l.campaign || ''),
        el('div', {class: 'cell-meta'}, l.mode && l.mode !== 'live' ? badge('', l.mode) : null,
          list(l.builds).map(function (h) { return el('span', {class: 'code muted'}, String(h)); }))),
      devices.map(function (t) { return launchCell(cells[t], ctx); }));
    const note = el('tr', {class: 'launch-note'}, el('td', {colspan: String(devices.length + 1)},
      el('div', {class: 'row'}, el('span', {class: 'strong'}, l.devices_ran || ''), concurrencyChip(conc.kind),
        el('span', {class: 'break'}, conc.text || '')),
      conc.kind === 'parallel launch' ? concurrencyDetails(conc, devices) : null));
    return [main, note];
  }

  function launchCell(cell, ctx) {
    if (!cell || typeof cell !== 'object') return el('td', {class: 'cell empty-cell'}, '–');
    if (!cell.run) {
      const why = cell.why ? String(cell.why) : null;
      const c = verdictChip(cell.verdict || 'NOT_TESTED');
      if (why) c.title = why;
      return el('td', {class: 'cell', title: why}, c);
    }
    const open = function () { openRun(cell.run, ctx); };
    const older = list(cell.older);
    return el('td', {class: 'cell'},
      el('div', {class: 'cell-main'}, verdictChip(cell.verdict, open), evidenceBadge(cell.evidence_kind),
        staleBadge(cell.stale)),
      el('div', {class: 'small'}, runButton(cell.run, open)),
      older.length ? el('div', {class: 'cell-older'}, el('span', {class: 'muted'}, 'Older records: '),
        older.map(function (o, i) {
          return [i ? ', ' : '', runButton(o, function () { openRun(o, ctx); })];
        })) : null);
  }

  function concurrencyChip(kind) {
    if (kind === 'parallel launch') return chip('s-info', kind);
    return kind ? chip('s-muted', String(kind)) : null;
  }

  function concurrencyDetails(conc, devices) {
    const windows = conc.windows && typeof conc.windows === 'object' ? conc.windows : {};
    const times = conc.deciding_times && typeof conc.deciding_times === 'object' ? conc.deciding_times : {};
    const tags = devices.filter(function (t) { return t in windows || t in times; });
    const ms = function (iso) { const d = new Date(iso || ''); return isNaN(d.getTime()) ? null : d.getTime(); };
    const reached = tags.map(function (t) { return ms(times[t]); }).filter(function (x) { return x !== null; });
    const first = reached.length ? Math.min.apply(null, reached) : null;
    const step = conc.deciding_step === null || conc.deciding_step === undefined ? '?' : String(conc.deciding_step);
    const rows = tags.map(function (t) {
      const w = windows[t] || null;
      const s = w ? ms(w.start) : null;
      const e = w ? ms(w.end) : null;
      const at = ms(times[t]);
      return el('tr', {},
        el('td', {}, t),
        el('td', {}, w ? clockEl(w.start) : el('span', {class: 'muted'}, 'not recorded')),
        el('td', {}, w ? clockEl(w.end) : null),
        el('td', {class: 'nowrap'}, s !== null && e !== null ? ((e - s) / 1000).toFixed(1) + ' s' : ''),
        el('td', {}, at !== null ? clockEl(times[t]) : el('span', {class: 'muted'}, 'not reached')),
        el('td', {class: 'nowrap'}, at !== null && first !== null ? '+' + ((at - first) / 1000).toFixed(3) + ' s' : ''));
    });
    const cw = conc.common_window;
    return el('details', {class: 'launch-details'}, el('summary', {}, 'Windows and deciding times'),
      el('div', {class: 'table-wrap'}, el('table', {},
        el('thead', {}, el('tr', {}, ['Target', 'Run start', 'Run end', 'Length', 'Step ' + step + ' first action',
          'After the first'].map(function (h) { return el('th', {}, h); }))),
        el('tbody', {}, rows))),
      kv([
        ['Common window', cw && typeof cw === 'object' ? [clockEl(cw.start), ' to ', clockEl(cw.end)] : 'no overlap'],
        ['Overlap', conc.overlap_s === null || conc.overlap_s === undefined ? 'none' : conc.overlap_s + ' s'],
        ['Deciding step', step + (conc.deciding_step_from ? ' (from the ' + conc.deciding_step_from + ')' : '')],
        ['Spread', conc.spread_s === null || conc.spread_s === undefined ? 'fewer than two runs reached it' :
          conc.spread_s + ' s'],
      ]),
      el('p', {class: 'small muted'}, 'Times are when each ledger line was written, not when the deciding read ' +
        'started, so the records never verify a simultaneous read. Clock times are local.'));
  }

  function fmtClock(iso) {
    const d = new Date(iso || '');
    if (isNaN(d.getTime())) return String(iso || '');
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) + '.' +
      String(d.getMilliseconds()).padStart(3, '0');
  }

  function clockEl(iso) {
    return iso ? el('span', {class: 'code nowrap', title: String(iso)}, fmtClock(iso)) : null;
  }

  function rowBuild(r) {
    const b = r.build || {};
    return {
      key: val(b.hash7) || val(b.about_label) || val(b.semver) || 'unknown',
      semver: val(b.semver), env: val(b.env), hash7: val(b.hash7), about: val(b.about_label),
    };
  }

  function byCreatedDesc(a, b) {
    return String(b.started || b.created || '').localeCompare(String(a.started || a.created || ''));
  }

  function cellOrder(a, b) {
    // a superseded run (corrected by a later record) never stands for the cell
    return (a.superseded_by ? 1 : 0) - (b.superseded_by ? 1 : 0) || byCreatedDesc(a, b);
  }

  function buildMatrix(c, ctx, historySlot) {
    const rows = list(c.rows).slice().sort(byCreatedDesc);
    if (!rows.length) return el('p', {class: 'muted'}, 'No runs yet.');
    const builds = [];
    const buildIndex = {};
    const targets = [];
    const targetIndex = {};
    const cells = {};
    for (const r of rows) {
      const b = rowBuild(r);
      if (!buildIndex[b.key]) {
        buildIndex[b.key] = b;
        builds.push(b);  // rows are newest first, so builds come out newest first
      }
      const tag = String(r.target || '?');
      if (!targetIndex[tag]) {
        targetIndex[tag] = {tag: tag, cls: r.target_class || ''};
        targets.push(targetIndex[tag]);
      }
      const k = tag + '\u0000' + b.key;
      (cells[k] = cells[k] || []).push(r);
    }
    targets.sort(function (a, b) {
      return (a.cls === 'web') - (b.cls === 'web') || a.tag.localeCompare(b.tag);
    });
    const head = el('tr', {}, el('th', {}, 'Target'), builds.map(function (b) {
      return el('th', {class: 'col-build', title: b.about || null},
        el('div', {}, [b.semver, b.env].filter(Boolean).join(' ') || 'build unknown'),
        b.hash7 ? el('div', {class: 'code muted'}, b.hash7) : null);
    }));
    const body = targets.map(function (t) {
      return el('tr', {},
        el('th', {class: 'row-target'}, el('div', {}, t.tag), el('div', {class: 'small muted'}, t.cls),
          el('button', {type: 'button', class: 'btn-link small', onclick: function () {
            showHistory(historySlot, c.case, t.tag, ctx);
          }}, 'History')),
        builds.map(function (b) { return matrixCell(cells[t.tag + '\u0000' + b.key] || [], ctx); }));
    });
    return el('div', {class: 'table-wrap'}, el('table', {class: 'matrix'}, el('thead', {}, head), el('tbody', {}, body)));
  }

  function matrixCell(cellRows, ctx) {
    if (!cellRows.length) return el('td', {class: 'cell empty-cell'}, '–');
    const rows = cellRows.slice().sort(cellOrder);
    const r = rows[0];
    const open = function () { openRun(r.run, ctx); };
    const b = r.build || {};
    const vc = val(b.version_code);
    return el('td', {class: 'cell'},
      el('div', {class: 'cell-main'}, verdictChip(r.verdict, open), evidenceBadge(r.evidence_kind),
        r.attempts !== undefined && r.attempts !== null ? badge('', r.attempts + (Number(r.attempts) === 1 ? ' attempt' : ' attempts')) : null),
      el('div', {class: 'cell-meta'}, staleBadge(r.stale),
        isOn(r.judgement_pending) ? badge('s-warn', 'judgement pending') : null,
        r.mode && r.mode !== 'live' ? badge('', r.mode) : null,
        r.superseded_by ? badge('', 'superseded', 'superseded by ' + r.superseded_by) : null,
        vc && vc !== 'web' ? badge('', 'vc ' + vc, 'APK versionCode') : null),
      staleWhy(r.stale),
      thumbStrip(r.thumbs, 4, false),
      el('div', {class: 'small'}, runButton(r.run, open)),
      rows.length > 1 ? el('div', {class: 'cell-older'}, el('span', {class: 'muted'}, 'Other runs: '),
        rows.slice(1).map(function (o, i) {
          return [i ? ', ' : '', runButton(o.run, function () { openRun(o.run, ctx); }), ' ', verdictChip(o.verdict)];
        })) : null);
  }

  function differentialBlock(diffs, ctx) {
    const wrap = el('div', {class: 'card flat'}, el('h3', {}, 'Differential'));
    if (!diffs.length) {
      wrap.appendChild(el('p', {class: 'small muted'}, 'No differential yet for this tablet-and-web case.'));
      return wrap;
    }
    wrap.appendChild(el('div', {class: 'table-wrap'}, el('table', {},
      el('thead', {}, el('tr', {}, ['Differential', 'Result', 'Runs', 'Builds', 'Compared', 'Created'].map(function (h) {
        return el('th', {}, h);
      }))),
      el('tbody', {}, diffs.slice().sort(byCreatedDesc).map(function (d) {
        return el('tr', {},
          el('td', {class: 'code nowrap'}, d.id || ''),
          el('td', {}, chip(diffClass(d.result), d.result || 'unknown')),
          el('td', {}, diffRuns(d.runs, ctx)),
          el('td', {class: 'small'}, mapLines(d.builds)),
          el('td', {class: 'small'}, list(d.compared).join(', ')),
          el('td', {}, timeEl(d.created)));
      })))));
    return wrap;
  }

  function diffClass(result) {
    // The records also hold results outside SCHEMA.md section 6 (platforms_differ, undecidable_missing_run).
    const r = String(result || '');
    if (r === 'consistent') return 's-ok';
    if (r.indexOf('undecidable') === 0) return 's-warn';
    if (/only|differ/.test(r)) return 's-bad';
    return 's-muted';
  }

  function runLabel(id) {
    const m = String(id).match(/^R-\d{8}-([A-Za-z0-9]+)-[A-Z]+-\d{3}-(\d{2}(?:c\d+)?)$/);
    return m ? m[1] + ' run ' + m[2] : String(id);
  }

  function diffRuns(runs, ctx) {
    if (!runs || typeof runs !== 'object') return compact(runs);
    const out = [];
    for (const k of Object.keys(runs)) {
      const ids = Array.isArray(runs[k]) ? runs[k] : [runs[k]];
      out.push(el('div', {}, el('span', {class: 'small muted'}, k + ': '), ids.filter(Boolean).map(function (id, i) {
        return [i ? ', ' : '', el('button', {type: 'button', class: 'btn-link nowrap', title: String(id),
          onclick: function () { openRun(String(id), ctx); }}, runLabel(id))];
      })));
    }
    return el('div', {class: 'diff-runs'}, out);
  }

  function mapLines(obj) {
    if (!obj || typeof obj !== 'object') return compact(obj);
    const keys = Object.keys(obj);
    const values = keys.map(function (k) { return compact(obj[k], 80); });
    if (keys.length > 1 && values.every(function (v) { return v === values[0]; })) return values[0] + ' (all)';
    return keys.map(function (k, i) {
      return el('div', {class: 'nowrap', title: k}, el('span', {class: 'muted'}, runLabel(k) + ': '), values[i]);
    });
  }

  async function showHistory(slot, caseId, tag, ctx) {
    put(slot, el('div', {class: 'card flat'}, loading('Loading history of ' + caseId + ' on ' + tag + '…')));
    let d;
    try {
      d = await getJSON('/api/history' + qs({case: caseId, target: tag}));
    } catch (e) {
      put(slot, el('div', {class: 'card flat'}, errorBox(e, 'History did not load')));
      return;
    }
    const rows = list(d.rows);
    const close = el('button', {type: 'button', class: 'btn btn-secondary btn-small', onclick: function () { clear(slot); }},
      'Close');
    put(slot, el('div', {class: 'card flat'},
      el('div', {class: 'card-head'}, el('h3', {}, 'History: ' + caseId + ' on ' + tag), el('span', {class: 'spacer'}), close),
      rows.length ? el('div', {class: 'table-wrap'}, el('table', {},
        el('thead', {}, el('tr', {}, ['Created', 'Build', 'Verdict', 'Evidence', 'Attempts', 'Stale', 'Run'].map(function (h) {
          return el('th', {}, h);
        }))),
        el('tbody', {}, rows.map(function (r) {
          const b = rowBuild(r);
          return el('tr', {},
            el('td', {}, timeEl(r.created)),
            el('td', {}, [b.semver, b.env].filter(Boolean).join(' '), b.hash7 ? el('div', {class: 'code muted'}, b.hash7) : null),
            el('td', {}, verdictChip(r.verdict, function () { openRun(r.run, ctx); })),
            el('td', {}, evidenceBadge(r.evidence_kind)),
            el('td', {}, r.attempts === undefined || r.attempts === null ? '' : String(r.attempts)),
            el('td', {}, staleBadge(r.stale), staleWhy(r.stale)),
            el('td', {}, runButton(r.run, function () { openRun(r.run, ctx); })));
        })))) : el('p', {class: 'small muted'}, 'No runs of this case on ' + tag + '.')));
  }

  async function openRun(runId, ctx) {
    if (!runId) return;
    history.replaceState(null, '', routeHash('results', {
      view: 'detail', case: ctx.caseId, task: ctx.taskId, harness: ctx.harness ? '1' : '', run: runId,
    }));
    const panel = el('section', {class: 'card run-detail'}, loading('Loading ' + runId + '…'));
    put(ctx.detail, panel);
    panel.scrollIntoView({block: 'start', behavior: 'smooth'});
    let r;
    try {
      r = await getJSON('/api/runs/' + encodeURIComponent(runId));
    } catch (e) {
      put(panel, el('div', {class: 'card-head'}, el('h2', {class: 'code'}, runId), el('span', {class: 'spacer'}),
        closeRunButton(ctx)), errorBox(e, 'Run did not load'));
      return;
    }
    if (ctx.detail.firstChild === panel) put(panel, runDetail(r, ctx));
  }

  function closeRunButton(ctx) {
    return el('button', {type: 'button', class: 'btn btn-secondary btn-small', onclick: function () {
      clear(ctx.detail);
      history.replaceState(null, '', routeHash('results', {view: 'detail', case: ctx.caseId, task: ctx.taskId,
        harness: ctx.harness ? '1' : ''}));
    }}, 'Close');
  }

  function runDetail(r, ctx) {
    const v = r.verdict && typeof r.verdict === 'object' ? r.verdict : {v: r.verdict};
    const b = r.build || {};
    const t = r.target || {};
    const link = r.link || null;
    const pred = r.prediction || null;
    return [
      el('div', {class: 'card-head'}, el('h2', {class: 'code'}, r.id || ''), verdictChip(v),
        evidenceBadge(v.evidence_kind), r.mode ? badge('', r.mode) : null, el('span', {class: 'spacer'}), closeRunButton(ctx)),
      verdictOf(v) === 'ERROR' ? msgBox('info', 'ERROR: ' + ERROR_TIP + '. It is never turned into PASS or FAIL.') : null,
      kv([
        ['Case', (r.case || '') + (r.case_title ? ' · ' + r.case_title : '')],
        ['Target', targetText(t)],
        ['About label', val(b.about_label)],
        ['Build', [val(b.semver), val(b.env), val(b.hash7)].filter(Boolean).join(' · ')],
        ['Version code', val(b.version_code)],
        ['Git hash', val(b.git_hash) ? el('span', {class: 'code'}, String(val(b.git_hash))) : null],
        ['Link', link ? [val(link.kind), link.simulated ? 'simulated' : null,
          link.vehicle_id !== undefined && link.vehicle_id !== null ? 'vehicle ' + val(link.vehicle_id) : null]
          .filter(Boolean).join(' · ') : null],
        ['Account', val(r.account)],
        ['Created', timeEl(r.created)],
        ['Attempts', list(r.attempts).length || null],
        ['Evidence folder', r.evidence_dir],
        ['Record', r.file ? fileLink(r.file) : null],
        ['Stale', r.stale && r.stale.v !== false ? [staleBadge(r.stale), staleWhy(r.stale)] : null],
        ['Supersedes', r.supersedes],
        ['Superseded by', r.superseded_by],
      ].concat(verdictPairs(v))),
      subResults(v),
      el('h3', {class: 'section-title'}, 'Prediction'),
      pred && (pred.id || pred.path) ? kv([
        ['Id', el('span', {class: 'code'}, pred.id || '')],
        ['File', pred.path ? fileLink(pred.path) : null],
        ['Ledger', pred.ledger ? (pred.ledger.day || '') + ' #' + (pred.ledger.seq === undefined ? '?' : pred.ledger.seq) : null],
      ]) : el('p', {class: 'small muted'}, 'No prediction recorded.'),
      differentialSummary(r.differential || r.differential_id, ctx),
      isOn(r.judgement_pending) ? msgBox('warn', 'Judgement pending: ' + compact(r.judgement_pending, 300)) : null,
      el('h3', {class: 'section-title'}, 'Steps'),
      stepsTable(list(r.steps)),
      el('h3', {class: 'section-title'}, 'Evidence'),
      evidenceList(list(r.evidence)),
    ];
  }

  function targetText(t) {
    if (!t || typeof t !== 'object') return t ? String(t) : null;
    return [t.tag, t.class, t.serial || t.host, t.browser].map(val)
      .filter(function (x) { return typeof x === 'string' || typeof x === 'number'; }).join(' · ');
  }

  function verdictPairs(v) {
    const items = [];
    if (v.by) {
      // SCHEMA.md 4 sets by only for BLOCKED; older back-test records use it for who or what ran the test
      items.push([verdictOf(v) === 'BLOCKED' ? 'Blocked by' : 'By',
        typeof v.by === 'object' ? [v.by.kind, v.by.ref].filter(Boolean).join(' · ') : String(v.by)]);
    }
    if (v.why) items.push(['Why', typeof v.why === 'object' ? [v.why.reason, v.why.text].filter(Boolean).join(': ') : String(v.why)]);
    if (v.harness) {
      items.push(['Harness', typeof v.harness === 'object' ?
        [v.harness.component, v.harness.detail].filter(Boolean).join(': ') : String(v.harness)]);
    }
    if (v.handback) items.push(['Handback', compact(v.handback, 400)]);
    if (v.stand_in) items.push(['Stand-in', String(v.stand_in)]);
    if (v.judged) items.push(['Judged by', String(v.judged)]);
    if (v.decided) items.push(['Decided', timeEl(v.decided)]);
    return items;
  }

  function subResults(v) {
    const sub = list(v.sub);
    if (!sub.length) return null;
    return el('div', {}, el('div', {class: 'field-label'}, 'Sub-results'), el('ul', {class: 'list-plain'},
      sub.map(function (s) {
        return el('li', {class: 'row top'}, verdictChip(s.v), el('span', {class: 'code'}, s.id || ''), el('span', {}, s.text || ''));
      })));
  }

  function differentialSummary(d, ctx) {
    if (!d) return null;
    if (typeof d !== 'object') return kv([['Differential', String(d)]]);
    return [el('h3', {class: 'section-title'}, 'Differential'), kv([
      ['Id', el('span', {class: 'code'}, d.id || '')],
      ['Result', chip(diffClass(d.result), d.result || 'unknown')],
      ['Compared', list(d.compared).join(', ')],
      ['Runs', diffRuns(d.runs, ctx)],
      ['Builds', d.builds ? mapLines(d.builds) : null],
    ])];
  }

  function stepsTable(steps) {
    if (!steps.length) return el('p', {class: 'small muted'}, 'No steps recorded.');
    return el('div', {class: 'table-wrap'}, el('table', {},
      el('thead', {}, el('tr', {}, ['#', 'Step', 'Readback', 'Assert', 'Actions'].map(function (h) { return el('th', {}, h); }))),
      el('tbody', {}, steps.map(function (s) {
        return el('tr', {class: s.skipped ? 'step-skipped' : null},
          el('td', {class: 'min'}, String(s.n === undefined ? '' : s.n)),
          el('td', {}, s.text || '', s.skipped ? [' ', badge('', 'skipped')] : null,
            s.error ? el('div', {class: 'inline-error break'}, compact(s.error, 300)) : null),
          el('td', {}, readbackCell(s.readback)),
          el('td', {}, assertCell(s.assert)),
          el('td', {class: 'small'}, actionsCell(s.actions)));
      }))));
  }

  function readbackCell(rb) {
    if (rb === null || rb === undefined) return null;
    if (typeof rb !== 'object') return el('div', {class: 'pre'}, compact(rb));
    return [
      'ok' in rb ? okChip(rb.ok) : null,
      rb.expected !== undefined ? el('div', {class: 'pre'}, 'expected: ' + compact(rb.expected)) : null,
      rb.got !== undefined ? el('div', {class: 'pre'}, 'got: ' + (rb.got === null ? '(nothing)' : compact(rb.got))) : null,
    ];
  }

  function assertCell(a) {
    if (a === null || a === undefined) return null;
    if (typeof a !== 'object') return el('div', {class: 'pre'}, compact(a));
    const result = a.result;
    let resultNode = null;
    if (result === true) resultNode = chip('s-ok', 'true');
    else if (result === false) resultNode = chip('s-bad', 'false');
    else if (result !== undefined && result !== null) resultNode = el('span', {class: 'small'}, compact(result, 120));
    return [
      el('div', {class: 'row'}, a.kind ? badge('', a.kind) : null, resultNode, a.by ? el('span', {class: 'small muted'}, 'by ' + a.by) : null),
      a.expr ? el('div', {class: 'pre'}, a.expr) : null,
      a.question ? el('div', {class: 'pre'}, a.question) : null,
    ];
  }

  function actionsCell(actions) {
    if (!Array.isArray(actions)) return actions ? compact(actions, 120) : '';
    if (!actions.length) return '';
    const refs = actions.map(function (x) {
      return x && typeof x === 'object' ? (x.day || '') + ' #' + (x.seq === undefined ? '?' : x.seq) : String(x);
    });
    return el('span', {title: refs.join(', ')}, actions.length + (actions.length === 1 ? ' action' : ' actions'));
  }

  function evidenceList(ev) {
    if (!ev.length) return el('p', {class: 'small muted'}, 'No evidence listed.');
    const images = [];
    for (const e of ev) {
      const u = e.url || null;
      if (u && safeSrc(u) && IMAGE_RE.test(String(e.path || u))) images.push(u);
    }
    return [
      images.length ? thumbStrip(images, 0, true) : null,
      el('ul', {class: 'list-plain small'}, ev.map(function (e) {
        return el('li', {class: 'row'}, badge('', e.kind || 'file'),
          e.url ? fileLink(e.path, e.url) : el('span', {class: 'break'}, e.path || ''),
          e.url ? null : el('span', {class: 'muted'}, '(not served)'),
          e.captured ? timeEl(e.captured) : null);
      })),
    ];
  }

  function backtestCard(bt) {
    if (!bt || typeof bt !== 'object') return null;
    const verdicts = bt.verdicts || {};
    const claims = bt.claims || {};
    const keys = VERDICTS.filter(function (k) { return k in verdicts; })
      .concat(Object.keys(verdicts).filter(function (k) { return VERDICTS.indexOf(k) < 0; }));
    return el('section', {class: 'card'},
      el('div', {class: 'card-head'}, el('h2', {}, 'Back-test summary'),
        el('span', {class: 'small muted'}, (bt.runs === undefined ? '?' : bt.runs) + ' back-test runs' +
          (bt.superseded ? ' (' + bt.superseded + ' corrected by a later record; verdicts count the ' +
            bt.current + ' current ones)' : ''))),
      keys.length ? el('div', {class: 'row'}, keys.map(function (k) {
        return el('span', {class: 'row'}, verdictChip(k), el('span', {class: 'strong'}, String(verdicts[k])));
      })) : el('p', {class: 'small muted'}, 'No back-test verdicts.'),
      el('h3', {class: 'section-title'}, 'Claims checked against the records'),
      kv([
        ['Agree', claims.agree], ['Disagree', claims.disagree], ['Not in records', claims.not_in_records],
        ['Unverifiable', claims.unverifiable],
      ]) || el('p', {class: 'small muted'}, 'No claims scored.'),
      bt.disagreements_md ? el('p', {}, fileLink(bt.disagreements_md, null, 'Disagreement list')) : null);
  }

  // ---- Tickets ----

  // One tab since 2026-10-22: results on top, the drafts and comment drafts below.
  async function viewResultsTickets(root, params, alive) {
    const green = el('div', {});
    const parts = [el('section', {}), el('section', {class: 'section-split'})];
    add(root, [green].concat(parts));
    const got = await Promise.allSettled([viewResults(parts[0], params, alive), viewTickets(parts[1], params, alive),
      greenlightSection(green, alive)]);
    got.slice(0, 2).forEach(function (g, i) {
      if (g.status === 'rejected' && alive()) add(parts[i], [errorBox(g.reason, i ? 'Tickets failed' : 'Results failed')]);
    });
    // the greenlight card goes under the Results heading
    if (alive() && green.firstChild && parts[0].firstChild) parts[0].insertBefore(green, parts[0].firstChild.nextSibling);
  }

  async function viewTickets(root, params, alive) {
    const body = el('div', {}, loading('Loading drafts…'));
    add(root, [viewHead('Tickets', 'Drafts, triage results and comment drafts the engine wrote after your greenlight.'),
      el('div', {class: 'msg msg-warn banner'}, 'Nothing posts to ClickUp from here: copy, then post it yourself.'),
      tokenWarning(), body]);
    await loadTickets(body, alive, null);
  }

  async function loadTickets(body, alive, focusId) {
    let d;
    try {
      d = await getJSON('/api/tickets');
    } catch (e) {
      if (alive()) put(body, errorBox(e, 'Tickets did not load'));
      return;
    }
    if (!alive()) return;
    const reload = function (id) { return loadTickets(body, alive, id); };
    const drafts = list(d.drafts);
    const triage = list(d.triage);
    const legacy = list(d.legacy);
    put(body,
      el('h2', {class: 'section-title'}, 'Drafts (' + drafts.length + ')'),
      drafts.length ? drafts.map(function (x) { return draftCard(x, reload); }) :
        el('p', {class: 'muted'}, 'No ticket drafts. Drafts are written only after your greenlight.'),
      el('h2', {class: 'section-title'}, 'Triage (' + triage.length + ')'),
      triage.length ? triage.map(triageCard) : el('p', {class: 'muted'}, 'No triage results.'),
      legacy.length ? el('details', {class: 'card'}, el('summary', {}, 'Legacy tickets (' + legacy.length + ')'),
        legacy.map(legacyBlock)) : null);
    if (focusId) {
      const n = document.getElementById(focusId);
      if (n) n.scrollIntoView({block: 'start'});
    }
  }

  function draftId(x) {
    return 'draft-' + String(x.slug || '') + '-' + String(x.n === undefined ? '' : x.n);
  }

  function draftCard(x, reload) {
    const titleLen = typeof x.title_len === 'number' ? x.title_len : jsLen(x.title);
    const descLen = typeof x.description_len === 'number' ? x.description_len : jsLen(x.description);
    const checks = list(x.checks);
    const failing = checks.filter(function (c) { return !c.ok; }).length;
    return el('article', {class: 'card', id: draftId(x)},
      el('div', {class: 'card-head'}, el('h3', {}, (x.slug || '') + ' · ticket ' + (x.n === undefined ? '?' : x.n)),
        x.filed && x.filed.cl ? chip('s-ok', 'filed as ' + x.filed.cl) : chip('s-muted', 'not filed'),
        x.submitted ? chip('s-info', 'submitted', compact(x.submitted, 200)) : null,
        checks.length ? chip(failing ? 's-bad' : 's-ok', failing ? failing + ' check(s) failing' : 'checks pass') : null),
      kv([
        ['Folder', x.dir ? el('span', {class: 'code'}, x.dir) : null],
        ['Run record', x.run_record ? runRecordLink(x.run_record) : null],
        ['draft.json', x.draft_json && x.dir ? fileLink(x.dir + '/draft.json') : null],
      ]),
      el('div', {class: 'row'}, el('span', {class: 'field-label'}, 'Title'), counterEl(titleLen, 100), copyButton(x.title || '', 'Copy title')),
      el('div', {class: 'prewrap'}, x.title || ''),
      el('div', {class: 'row'}, el('span', {class: 'field-label'}, 'Description'), counterEl(descLen, 1000),
        copyButton(x.description || '', 'Copy description')),
      el('div', {class: 'prewrap'}, x.description || ''),
      checks.length ? el('div', {}, el('div', {class: 'field-label'}, 'Checks'), el('ul', {class: 'list-plain small'},
        checks.map(function (c) {
          return el('li', {class: 'row top'}, okChip(Boolean(c.ok), 'ok', 'fails'), el('span', {class: 'strong'}, c.name || ''),
            c.detail ? el('span', {class: 'break'}, compact(c.detail, 300)) : null);
        }))) : null,
      packList(list(x.pack)),
      commentDrafts(list(x.comments)),
      filedForm(x, reload));
  }

  function runRecordLink(rr) {
    const s = String(rr);
    const m = s.match(/R-\d{8}-[A-Za-z0-9]+-[A-Z]+-\d{3}-\d{2}(c\d+)?/);
    if (m) {
      return el('a', {href: routeHash('results', {run: m[0]}), class: 'code break', onclick: function (e) {
        e.preventDefault();
        go('results', {run: m[0]});
      }}, s);
    }
    return el('span', {class: 'code break'}, s);
  }

  function packList(pack) {
    if (!pack.length) return el('p', {class: 'small muted'}, 'No evidence pack.');
    const images = pack.filter(function (p) { return p.url && safeSrc(p.url) && IMAGE_RE.test(String(p.rel || p.url)); })
      .map(function (p) { return p.url; });
    return el('div', {}, el('div', {class: 'field-label'}, 'Evidence pack (' + pack.length + ')'),
      images.length ? thumbStrip(images, 0, false) : null,
      el('ul', {class: 'list-plain small'}, pack.map(function (p) {
        return el('li', {class: 'row'}, p.url ? fileLink(p.rel, p.url, p.rel) : el('span', {class: 'break'}, p.rel || ''),
          el('span', {class: 'muted'}, fmtBytes(p.size)), p.url ? null : el('span', {class: 'muted'}, '(not served)'));
      })));
  }

  function commentDrafts(comments) {
    if (!comments.length) return null;
    return el('div', {}, el('div', {class: 'field-label'}, 'Comment drafts'), comments.map(function (c) {
      const len = typeof c.len === 'number' ? c.len : jsLen(c.text);
      return el('div', {}, el('div', {class: 'row'}, el('span', {class: 'strong'}, 'Comment for ' + (c.cl || '?')),
        el('span', {class: 'counter'}, len + ' characters'), copyButton(c.text || '', 'Copy comment'),
        c.rel ? el('span', {class: 'small muted code'}, c.rel) : null),
      el('div', {class: 'prewrap'}, c.text || ''));
    }));
  }

  function filedForm(x, reload) {
    const prev = x.filed && x.filed.cl ? String(x.filed.cl) : '';
    const input = el('input', {type: 'text', class: 'input-cl', placeholder: 'CL-12345', spellcheck: 'false',
      'aria-label': 'ClickUp id this draft was filed as'});
    const btn = el('button', {type: 'submit', class: 'btn btn-small'}, prev ? 'Change' : 'Save');
    const msg = el('span', {});
    const form = el('form', {class: 'row', novalidate: true}, el('span', {class: 'field-label'}, 'Filed as CL-…'), input, btn, msg);
    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      const cl = input.value.trim().toUpperCase();
      if (!CL_RE.test(cl)) {
        put(msg, el('span', {class: 'inline-error'}, 'Use the form CL-12345.'));
        return;
      }
      if (prev === cl) {
        put(msg, el('span', {class: 'small muted'}, 'Already marked as ' + cl + '.'));
        return;
      }
      if (prev && !window.confirm('This draft is marked as filed as ' + prev + '. Replace it with ' + cl + '?')) return;
      btn.disabled = true;
      clear(msg);
      try {
        await sendJSON('POST', '/api/tickets/filed', {dir: x.dir, cl: cl, replace: Boolean(prev)});
        await reload(draftId(x));
      } catch (err) {
        put(msg, el('span', {class: 'inline-error'}, errText(err)));
        btn.disabled = false;
      }
    });
    const f = x.filed;
    return el('div', {class: 'task-actions'},
      f ? el('div', {class: 'small'}, 'Marked filed as ' + (f.cl || '?') + ' by ' + (f.by || '?') +
        (f.via ? ' via ' + f.via : '') + ' ', timeEl(f.at),
      list(f.history).length ? ' · earlier: ' + list(f.history).map(function (h) {
        return h && typeof h === 'object' ? (h.cl || '?') : String(h);
      }).join(', ') : null) : null,
      form);
  }

  function triageCard(t) {
    const dups = list(t.duplicates);
    const related = list(t.related);
    const fix = t.fix_in_build;
    return el('article', {class: 'card'},
      el('div', {class: 'card-head'}, el('h3', {}, 'Triage of ' + (t.input || '?')),
        chip(t.gate === 'passed' ? 's-ok' : 's-warn', 'gate ' + (t.gate || '?')),
        t.classification ? badge('', t.classification) : null,
        el('span', {class: 'spacer'}), t.decision ? chip('s-info', 'decided: ' + t.decision) : chip('s-muted', 'no decision yet')),
      t.gate_reason ? msgBox('warn', t.gate_reason) : null,
      kv([
        ['Recommendation', t.recommendation ? el('span', {class: 'strong'}, t.recommendation) : null],
        ['Reason', t.reason],
        ['Account', t.account],
        ['Platform scope', t.platform_scope ? t.platform_scope + (t.scope_basis ? ' (' + t.scope_basis + ')' : '') : null],
        ['Fix task', t.fix_task],
        ['Release tag', t.release_tag],
        ['Build hash', t.build_hash ? el('span', {class: 'code'}, short(t.build_hash, 12)) : null],
        ['Fix in build', fix === true ? chip('s-ok', 'yes') : (fix === false ? chip('s-bad', 'no') : chip('s-muted', 'unknown'))],
        ['Ancestry check', t.ancestry && t.ancestry.command ?
          [el('span', {class: 'code break'}, t.ancestry.command), ' → exit ' + compact(t.ancestry.exit_code)] : null],
        ['Decided by', t.decided_by],
        ['Folder', t.dir ? el('span', {class: 'code'}, t.dir) : null],
      ]),
      dups.length ? el('div', {}, el('div', {class: 'field-label'}, 'Duplicates'), el('div', {class: 'table-wrap'},
        el('table', {}, el('thead', {}, el('tr', {}, ['Id', 'Title', 'Status', 'Score', 'Why'].map(function (h) {
          return el('th', {}, h);
        }))), el('tbody', {}, dups.map(function (x) {
          return el('tr', {}, el('td', {class: 'code nowrap'}, x.id || ''), el('td', {}, x.title || ''),
            el('td', {}, x.status || ''), el('td', {}, x.score === undefined ? '' : String(x.score)),
            el('td', {class: 'small'}, x.why || ''));
        }))))) : el('p', {class: 'small muted'}, 'No duplicates found.'),
      related.length ? el('div', {}, el('div', {class: 'field-label'}, 'Related'), el('ul', {class: 'list-plain small'},
        related.map(function (x) {
          return el('li', {}, el('span', {class: 'code strong'}, x.id || ''), ' ', x.why || '');
        }))) : null);
  }

  function legacyBlock(x) {
    const titleLen = typeof x.title_len === 'number' ? x.title_len : jsLen(x.title);
    const descLen = typeof x.description_len === 'number' ? x.description_len : jsLen(x.description);
    return el('div', {class: 'card flat'},
      el('div', {class: 'card-head'}, el('h3', {}, 'Ticket ' + (x.k === undefined ? '?' : x.k)),
        x.dir ? el('span', {class: 'small muted code'}, x.dir) : null),
      el('div', {class: 'row'}, el('span', {class: 'field-label'}, 'Title'), counterEl(titleLen, 100), copyButton(x.title || '', 'Copy title')),
      el('div', {class: 'prewrap'}, x.title || ''),
      el('div', {class: 'row'}, el('span', {class: 'field-label'}, 'Description'), counterEl(descLen, 1000),
        copyButton(x.description || '', 'Copy description')),
      el('div', {class: 'prewrap'}, x.description || ''));
  }

  // ---- Tests ----

  async function viewTests(root, params, alive) {
    const st = {
      cases: [], q: '', target: '', area: '', verdict: '', stale: '', status: '', harness: false,
      open: new Set(params.get('case') ? [params.get('case')] : []),
    };
    const formSlot = el('div', {});
    const toolbar = el('section', {class: 'card'});
    const tableSlot = el('section', {class: 'card'}, loading('Loading cases…'));
    add(root, [viewHead('Cases', 'The case library. A new test is saved as a draft, and an edit saves a new version.'),
      tokenWarning(), formSlot, toolbar, tableSlot]);

    const reload = async function () {
      try {
        const d = await getJSON('/api/cases');
        if (!alive()) return;
        st.cases = list(d.cases);
        put(toolbar, testsToolbar(st, draw, formSlot, reload));
        draw();
      } catch (e) {
        if (alive()) put(tableSlot, errorBox(e, 'Cases did not load'));
      }
    };
    const draw = function () { put(tableSlot, casesTable(st, formSlot, reload)); };
    await reload();
    const first = params.get('case');
    const row = first ? document.getElementById('case-row-' + first) : null;
    if (row && alive()) row.scrollIntoView({block: 'center'});
  }

  function testsToolbar(st, draw, formSlot, reload) {
    const areas = new Set(list(app.meta && app.meta.areas));
    for (const c of st.cases) if (c.area) areas.add(c.area);
    const q = el('input', {type: 'search', placeholder: 'Search id, title, area, source', value: st.q});
    q.addEventListener('input', function () { st.q = q.value; draw(); });
    const select = function (key, label, options) {
      const s = el('select', {}, options.map(function (o) { return el('option', {value: o[0]}, o[1]); }));
      s.value = st[key];
      s.addEventListener('change', function () { st[key] = s.value; draw(); });
      return el('label', {class: 'field'}, el('span', {}, label), s);
    };
    const harness = el('input', {type: 'checkbox', checked: st.harness});
    harness.addEventListener('change', function () { st.harness = harness.checked; draw(); });
    const addBtn = el('button', {type: 'button', class: 'btn', onclick: function () {
      openCaseForm(formSlot, null, reload);
    }}, 'Add test');
    return el('div', {class: 'filters'},
      el('label', {class: 'field grow'}, el('span', {}, 'Search'), q),
      select('target', 'Target', [['', 'any']].concat(CASE_TARGETS.map(function (t) { return [t, t]; }))),
      select('area', 'Area', [['', 'any']].concat(Array.from(areas).sort().map(function (a) { return [a, a]; }))),
      select('verdict', 'Last verdict', [['', 'any']].concat(VERDICTS.map(function (v) { return [v, v]; }),
        [['none', 'never run']])),
      select('stale', 'Stale', [['', 'any'], ['stale', 'stale'], ['fresh', 'not stale'], ['unknown', 'unknown']]),
      select('status', 'Status', [['', 'any'], ['active', 'active'], ['draft', 'draft'], ['retired', 'retired']]),
      el('label', {class: 'checks'}, harness, 'Show harness cases'),
      el('span', {class: 'spacer'}), addBtn);
  }

  function lastVerdicts(c) {
    const last = c.last && typeof c.last === 'object' ? c.last : {};
    return Object.keys(last).sort().map(function (tag) { return {tag: tag, x: last[tag] || {}}; });
  }

  function caseMatches(c, st) {
    if (c.harness && !st.harness && !st.open.has(c.id)) return false;
    if (st.target && c.targets !== st.target) return false;
    if (st.area && c.area !== st.area) return false;
    if (st.status && (c.status || 'active') !== st.status) return false;
    if (st.stale) {
      const v = c.stale ? c.stale.v : null;
      if (st.stale === 'stale' && v !== true) return false;
      if (st.stale === 'fresh' && v !== false) return false;
      if (st.stale === 'unknown' && !(v === null || v === undefined)) return false;
    }
    if (st.verdict) {
      const vs = lastVerdicts(c).map(function (l) { return verdictOf(l.x.verdict); });
      if (st.verdict === 'none' ? vs.length > 0 : vs.indexOf(st.verdict) < 0) return false;
    }
    const q = st.q.trim().toLowerCase();
    if (q) {
      const hay = [c.id, c.title, c.area, compact(c.requirement_source, 1000)].join(' ').toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    return true;
  }

  function casesTable(st, formSlot, reload) {
    const shown = st.cases.filter(function (c) { return caseMatches(c, st); });
    const hiddenHarness = st.harness ? 0 : st.cases.filter(function (c) { return c.harness && !st.open.has(c.id); }).length;
    const head = el('tr', {}, ['', 'Id', 'Title', 'Area', 'Targets', 'Status', 'Last verdicts', 'Stale', 'Versions', '']
      .map(function (h) { return el('th', {}, h); }));
    const body = el('tbody', {});
    for (const c of shown) add(body, caseRows(c, st, formSlot, reload));
    return [
      el('div', {class: 'row between'}, el('h2', {}, 'Cases (' + shown.length + ' of ' + st.cases.length + ')'),
        hiddenHarness ? el('span', {class: 'small muted'}, hiddenHarness + ' harness case(s) hidden') : null),
      shown.length ? el('div', {class: 'table-wrap'}, el('table', {}, el('thead', {}, head), body)) :
        el('p', {class: 'muted'}, 'No case matches these filters.'),
    ];
  }

  function statusChip(status) {
    const s = status || 'active';
    return chip(s === 'active' ? 's-ok' : (s === 'draft' ? 's-warn' : 's-muted'), s);
  }

  function versionCount(v) {
    if (Array.isArray(v)) return v.length;
    return v === undefined || v === null ? '' : String(v);
  }

  function caseRows(c, st, formSlot, reload) {
    const open = st.open.has(c.id);
    const detail = el('tr', {class: 'detail-row', hidden: !open}, el('td', {colspan: 10}));
    const arrow = el('span', {'aria-hidden': 'true'}, open ? '▾' : '▸');
    const row = el('tr', {class: 'clickable' + (open ? ' expanded' : ''), id: 'case-row-' + c.id, tabindex: 0,
      'aria-expanded': open ? 'true' : 'false'},
      el('td', {class: 'min'}, arrow),
      el('td', {class: 'code nowrap'}, c.id),
      el('td', {}, c.title || ''),
      el('td', {}, c.area || ''),
      el('td', {}, c.targets || ''),
      el('td', {}, statusChip(c.status), c.harness ? [' ', badge('badge-harness', 'harness')] : null),
      el('td', {}, el('div', {class: 'last-verdicts'}, lastVerdicts(c).map(function (l) {
        const tip = [l.x.run, l.x.hash7, fmtTime(l.x.created)].filter(Boolean).join(' · ');
        return [el('span', {class: 'small', title: tip || null}, l.tag), verdictChip(l.x.verdict, function (e) {
          e.stopPropagation();
          go('results', {case: c.id, run: l.x.run});
        })];
      }))),
      el('td', {}, staleBadge(c.stale)),
      el('td', {}, String(versionCount(c.versions))),
      el('td', {class: 'min'}, c.harness ? null : el('button', {type: 'button', class: 'btn btn-secondary btn-small',
        onclick: function (e) {
          e.stopPropagation();
          editCase(c.id, formSlot, reload);
        }}, 'Edit')));
    const toggle = function () {
      const now = !st.open.has(c.id);
      if (now) st.open.add(c.id);
      else st.open.delete(c.id);
      row.classList.toggle('expanded', now);
      row.setAttribute('aria-expanded', now ? 'true' : 'false');
      arrow.textContent = now ? '▾' : '▸';
      detail.hidden = !now;
      if (now) loadCaseDetail(detail.firstChild, c, formSlot, reload);
    };
    row.addEventListener('click', toggle);
    row.addEventListener('keydown', function (e) {
      if (e.target === row && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault();
        toggle();
      }
    });
    if (open) loadCaseDetail(detail.firstChild, c, formSlot, reload);
    return [row, detail];
  }

  async function loadCaseDetail(td, summary, formSlot, reload) {
    put(td, loading('Loading ' + summary.id + '…'));
    let d;
    try {
      d = await getJSON('/api/cases/' + encodeURIComponent(summary.id));
    } catch (e) {
      put(td, errorBox(e, 'Case did not load'));
      return;
    }
    put(td, caseDetail(d, summary, formSlot, reload));
  }

  // A precondition the test takes from the person (2026-11-10): its check is "person" (also "the person", or with
  // more after the word: "person: answered yes"), or its text says something was done by them ("set by the person",
  // "set by you"). The run relies on the person for it, so the Plan screen marks it.
  function fromPerson(p) {
    const o = p && typeof p === 'object' ? p : {text: p};
    return (typeof o.check === 'string' && /^\s*(?:the\s+)?person\b/i.test(o.check)) ||
      (typeof o.text === 'string' && /\bby\s+(?:the\s+person|you)\b/i.test(o.text));
  }

  // One precondition of a case as a list item, on the case page and on the Plan screen: its id, its text and the kind
  // of device it applies to. cls and after are the Plan screen's mark on a line from the person.
  function preItem(p, cls, after) {
    if (!p || typeof p !== 'object') return el('li', {class: cls}, String(p), after);
    return el('li', {class: cls}, p.id ? el('span', {class: 'code strong'}, p.id + ' ') : null, p.text || '',
      p.applies_to && p.applies_to !== 'both' ? [' ', badge('', p.applies_to)] : null, after);
  }

  function caseDetail(d, summary, formSlot, reload) {
    const c = d.case || {};
    const req = c.requirement || {};
    const src = req.source || {};
    const needs = c.needs || {};
    const versions = list(d.versions);
    return [
      el('div', {class: 'row'}, el('h3', {}, c.id + ' · ' + (c.title || '')), statusChip(c.status),
        el('span', {class: 'spacer'}),
        el('a', {href: routeHash('results', {case: c.id}), class: 'btn btn-secondary btn-small'}, 'Results'),
        summary.harness ? null : el('button', {type: 'button', class: 'btn btn-secondary btn-small', onclick: function () {
          openCaseForm(formSlot, c, reload, d.latest_file);
        }}, 'Edit')),
      el('div', {class: 'field-label'}, 'Requirement'),
      el('div', {class: 'prewrap'}, req.text || ''),
      kv([
        ['Source', [src.type, src.ref, src.custom_id].filter(Boolean).join(' · ')],
        ['Locator', src.locator],
        ['Requester', src.requester],
        ['Requirement sha256', req.sha256 ? el('span', {class: 'code'}, short(req.sha256, 16)) : null],
        ['Targets', c.targets],
        ['Needs', [needs.link ? 'link ' + needs.link : null, list(needs.flags).length ? 'flags ' + list(needs.flags).join(', ') : null,
          needs.usb_port ? 'USB port' : null, needs.drone_hw ? 'drone HW ' + needs.drone_hw : null].filter(Boolean).join(' · ')],
        ['Author', c.author],
        ['Latest file', summary.latest_file ? el('span', {class: 'code'}, summary.latest_file) : null],
      ]),
      el('div', {class: 'field-label'}, 'Preconditions'),
      list(c.preconditions).length ? el('ul', {}, list(c.preconditions).map(function (p) { return preItem(p); })) :
        el('p', {class: 'small muted'}, 'None.'),
      el('div', {class: 'field-label'}, 'Steps'),
      list(c.steps).length ? el('ol', {}, list(c.steps).map(function (s) {
        if (typeof s !== 'object') return el('li', {}, String(s));
        const a = s.assert;
        return el('li', {}, s.text || '', s.on && s.on !== 'both' ? [' ', badge('', s.on)] : null,
          list(s.gates).map(function (g) { return [' ', badge('s-warn', g)]; }),
          a && typeof a === 'object' ? el('div', {class: 'small muted break'},
            'assert (' + (a.kind || '?') + '): ' + compact(a.expr || a.question || '', 200)) : null);
      })) : el('p', {class: 'small muted'}, 'No steps yet.'),
      c.steps_note ? el('p', {class: 'small muted'}, c.steps_note) : null,
      el('div', {class: 'field-label'}, 'Expected'),
      c.expected ? el('div', {class: 'prewrap'}, typeof c.expected === 'string' ? c.expected : compact(c.expected, 2000)) :
        el('p', {class: 'small muted'}, 'Not set in this version (see the step asserts).'),
      el('div', {class: 'field-label'}, 'Covered paths'),
      list(c.covered_paths).length ? el('ul', {}, list(c.covered_paths).map(function (g) {
        return el('li', {}, el('code', {}, String(g)));
      })) : el('p', {class: 'small muted'}, 'None, so staleness by code change is not checked.'),
      el('div', {class: 'field-label'}, 'Versions'),
      versions.length ? el('div', {class: 'table-wrap'}, el('table', {},
        el('thead', {}, el('tr', {}, ['File', 'sha256', 'Created', 'Supersedes'].map(function (h) { return el('th', {}, h); }))),
        el('tbody', {}, versions.map(function (v) {
          return el('tr', {}, el('td', {class: 'code'}, v.file || ''), el('td', {class: 'code'}, short(v.sha256, 12)),
            el('td', {}, timeEl(v.created)), el('td', {class: 'small break'},
              v.supersedes && typeof v.supersedes === 'object' ?
                [v.supersedes.file || v.supersedes.id, v.supersedes.why].filter(Boolean).join(': ') : compact(v.supersedes, 200)));
        })))) : el('p', {class: 'small muted'}, 'One version.'),
    ];
  }

  function splitFlags(s) {
    return String(s || '').split(',').map(function (x) { return x.trim(); }).filter(Boolean);
  }

  async function editCase(id, formSlot, reload) {
    put(formSlot, el('section', {class: 'card'}, loading('Loading ' + id + '…')));
    try {
      const d = await getJSON('/api/cases/' + encodeURIComponent(id));
      openCaseForm(formSlot, d.case || {}, reload, d.latest_file);
    } catch (e) {
      put(formSlot, el('section', {class: 'card'}, errorBox(e, 'Case did not load')));
    }
  }

  // base: the latest_file the edit was opened on; the server refuses the save (409) once a newer version exists.
  function openCaseForm(slot, existing, reload, base) {
    const isEdit = Boolean(existing && existing.id);
    const c = existing || {};
    const req = c.requirement || {};
    const src = req.source || {};
    const needs = c.needs || {};
    const areas = list(app.meta && app.meta.areas).slice();
    if (c.area && areas.indexOf(c.area) < 0) areas.push(c.area);

    const area = el('select', {disabled: isEdit}, el('option', {value: ''}, 'pick an area'),
      areas.map(function (a) { return el('option', {value: a}, a); }));
    area.value = c.area || '';
    const title = el('input', {type: 'text', value: c.title || ''});
    const titleCount = el('span', {class: 'counter'});
    bindCounter(title, titleCount, CASE_TITLE_MAX);
    const reqText = el('textarea', {rows: 4, placeholder: 'The requirement, verbatim'});
    reqText.value = req.text || '';
    const srcType = el('select', {}, SOURCE_TYPES.map(function (t) { return el('option', {value: t}, t); }));
    srcType.value = SOURCE_TYPES.indexOf(src.type) >= 0 ? src.type : 'user_chat';
    const srcRef = el('input', {type: 'text', value: src.ref || '', placeholder: 'ClickUp id, doc or chat reference'});
    const locator = el('input', {type: 'text', value: src.locator || '', placeholder: 'e.g. description bullet 7'});
    const requester = el('input', {type: 'text', value: src.requester || ''});
    const targetRadios = CASE_TARGETS.map(function (t) {
      return el('label', {}, el('input', {type: 'radio', name: 'case-targets', value: t, checked: c.targets === t}), t);
    });
    const textLines = function (items) {
      return list(items).map(function (x) { return x && typeof x === 'object' ? (x.text || '') : String(x); })
        .filter(Boolean).join('\n');
    };
    const pre = el('textarea', {rows: 3, placeholder: 'One per line'});
    pre.value = textLines(c.preconditions);
    const steps = el('textarea', {rows: 6, placeholder: 'One step per line'});
    steps.value = textLines(c.steps);
    const expected = el('textarea', {rows: 3});
    expected.value = typeof c.expected === 'string' ? c.expected : '';
    const paths = el('textarea', {rows: 3, placeholder: 'apps/src/pages/Flying/**'});
    paths.value = list(c.covered_paths).join('\n');
    const link = el('select', {}, el('option', {value: ''}, 'not set'), LINK_KINDS.map(function (k) {
      return el('option', {value: k}, k);
    }));
    link.value = LINK_KINDS.indexOf(needs.link) >= 0 ? needs.link : '';
    const initialLink = link.value;
    const flags = el('input', {type: 'text', value: list(needs.flags).join(', '), placeholder: 'Unleash flags, comma-separated'});
    const initialFlags = splitFlags(flags.value);
    const status = isEdit ? el('select', {}, el('option', {value: ''}, 'keep ' + (c.status || 'current')),
      el('option', {value: 'draft'}, 'draft'), el('option', {value: 'retired'}, 'retired')) : null;
    const activeNote = isEdit && (c.status || 'active') === 'active' ? el('p', {class: 'small muted'},
      'This case is active. It stays active only when the requirement, targets, steps, expected result and needs ' +
      'are unchanged. To change any of them pick status draft: Claude then reviews the case and adds helpers ' +
      'before it runs again.') : null;
    const msg = el('div', {});
    const submit = el('button', {type: 'submit', class: 'btn'}, isEdit ? 'Save new version' : 'Save test');
    const cancel = el('button', {type: 'button', class: 'btn btn-secondary', onclick: function () { clear(slot); }}, 'Cancel');

    const form = el('form', {class: 'card', novalidate: true},
      el('div', {class: 'card-head'}, el('h2', {}, isEdit ? 'Edit ' + c.id : 'Add test')),
      isEdit ? msgBox('info', 'This saves a new version; the old one stays.') :
        el('p', {class: 'small muted'}, 'Saved with status draft. Claude routes it and adds helpers before it runs.'),
      el('div', {class: 'form-grid'},
        el('label', {class: 'field'}, el('span', {}, 'Area'), area),
        el('label', {class: 'field wide'}, el('span', {}, 'Title'), title, titleCount),
        el('label', {class: 'field wide'}, el('span', {}, 'Requirement'), reqText),
        el('label', {class: 'field'}, el('span', {}, 'Source type'), srcType),
        el('label', {class: 'field'}, el('span', {}, 'Source ref'), srcRef),
        el('label', {class: 'field'}, el('span', {}, 'Locator (optional)'), locator),
        el('label', {class: 'field'}, el('span', {}, 'Requester (optional)'), requester),
        el('div', {class: 'field wide'}, el('span', {class: 'field-label'}, 'Targets'), el('div', {class: 'checks'}, targetRadios)),
        el('label', {class: 'field wide'}, el('span', {}, 'Preconditions'), pre),
        el('label', {class: 'field wide'}, el('span', {}, 'Steps'), steps),
        el('label', {class: 'field wide'}, el('span', {}, 'Expected'), expected),
        el('label', {class: 'field wide'}, el('span', {}, 'Covered paths (globs, one per line)'), paths),
        el('label', {class: 'field'}, el('span', {}, 'Needs: link (optional)'), link),
        el('label', {class: 'field'}, el('span', {}, 'Needs: flags (optional)'), flags),
        status ? el('label', {class: 'field'}, el('span', {}, 'Status'), status) : null,
        activeNote ? el('div', {class: 'field wide'}, activeNote) : null),
      el('div', {class: 'form-actions'}, submit, cancel),
      msg);

    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      const picked = form.querySelector('input[name=case-targets]:checked');
      const problems = [];
      if (!area.value) problems.push('Pick an area.');
      const t = title.value.trim();
      if (!t) problems.push('Give a title.');
      if (jsLen(t) > CASE_TITLE_MAX) problems.push('The title is longer than ' + CASE_TITLE_MAX + ' characters.');
      if (!reqText.value.trim()) problems.push('Write the requirement.');
      if (!srcRef.value.trim()) problems.push('Give the source ref.');
      if (!picked) problems.push('Pick the targets.');
      if (!lines(steps.value).length) problems.push('Write at least one step.');
      if (!expected.value.trim()) problems.push('Write what is expected.');
      if (problems.length) {
        put(msg, msgBox('error', problems.join(' ')));
        return;
      }
      const source = {type: srcType.value, ref: srcRef.value.trim()};
      if (locator.value.trim()) source.locator = locator.value.trim();
      if (requester.value.trim()) source.requester = requester.value.trim();
      const body = {
        area: area.value, title: t, requirement: {text: reqText.value.trim(), source: source},
        targets: picked.value, preconditions: lines(pre.value), steps: lines(steps.value),
        expected: expected.value.trim(), covered_paths: lines(paths.value),
      };
      const flagList = splitFlags(flags.value);
      // An edit sends needs only when link or flags changed, and then keeps the other needs keys (usb_port, ...).
      const changed = link.value !== initialLink || flagList.join(',') !== initialFlags.join(',');
      if (isEdit ? changed : (link.value || flagList.length)) {
        body.needs = Object.assign({}, isEdit ? needs : {}, {link: link.value || 'none', flags: flagList});
      }
      if (status && status.value) body.status = status.value;
      if (isEdit) body.base = base || '';
      submit.disabled = true;
      try {
        const res = isEdit ? await sendJSON('PUT', '/api/cases/' + encodeURIComponent(c.id), body) :
          await sendJSON('POST', '/api/cases', body);
        put(slot, el('div', {class: 'msg msg-ok'}, (isEdit ? 'Saved a new version of ' : 'Saved ') + (res.id || '') +
          (res.file ? ' as ' + res.file : '') + '.'));
        await reload();
      } catch (err) {
        if (isEdit && err.status === 409 && /changed since/.test(err.message)) {
          put(msg, el('div', {}, msgBox('error', 'Not saved: ' + c.id + ' has a newer version than the one this form ' +
            'was opened on (Claude may have completed it). Reload the case and make the edit again.'),
            el('button', {type: 'button', class: 'btn btn-secondary btn-small', onclick: function () {
              editCase(c.id, slot, reload);
            }}, 'Reload the case')));
          return;
        }
        put(msg, errorBox(err, 'Not saved'));
        submit.disabled = false;
      }
    });
    put(slot, form);
    form.scrollIntoView({block: 'start', behavior: 'smooth'});
    (isEdit ? title : area).focus({preventScroll: true});
  }

  // ---- boot ----

  // ---- Today: Colleague's office tests from ClickUp (read-only; DESIGN.md section 8) ----

  const PRIORITY_CLASS = {urgent: 's-bad', high: 's-warn', normal: 's-info', low: 's-muted'};
  const TAG_ORDER = ['TA5', 'ET65', 'W044', 'TA3'];

  const COLUMNS = [['up next', 'Up next'], ['ongoing', 'Ongoing']];
  // ClickUp is read when the page opens, on Refresh, and again on a new day. Moving between Today's screens
  // re-reads only the app's own files: the saved list, the pre-test notes and the engine cases.
  const today = {out: null, cases: [], casesError: null, readDay: null, inflight: null, worker: null};

  function zurichDay() {
    return new Intl.DateTimeFormat('en-CA', {timeZone: 'Europe/Zurich', year: 'numeric', month: '2-digit',
      day: '2-digit'}).format(new Date());
  }

  async function planFallback(e) {
    // A 403 here is the per-start token: the server was restarted after this page was opened.
    const message = e && e.status === 403 ? 'The app server restarted after this page was opened. Reload the page.' :
      errText(e);
    const out = {ok: false, kind: 'app', message: message, snapshot: null};
    try {
      const p = await getJSON('/api/plan');
      out.snapshot = p.snapshot;
      out.today = p.today;
      out.prep = p.prep;
      out.prep_status = p.prep_status;
    } catch (e2) { /* nothing saved yet */ }
    return out;
  }

  async function readCases() {
    try {
      today.cases = list((await getJSON('/api/cases')).cases);
      today.casesError = null;
    } catch (e) {
      today.cases = [];
      today.casesError = errText(e);
    }
  }

  function todayRefresh() {
    if (today.inflight) return today.inflight;
    today.inflight = Promise.all([sendJSON('POST', '/api/plan/refresh', {}).catch(planFallback), readCases()])
      .then(function (got) {
        today.out = got[0] || {};
        today.readDay = zurichDay();
      }).finally(function () { today.inflight = null; });
    return today.inflight;
  }

  // The newest saved list and notes, keeping what the last ClickUp read said (ok, or its error).
  async function todayReload() {
    const got = await Promise.allSettled([getJSON('/api/plan'), readCases()]);
    if (got[0].status !== 'fulfilled') return;
    const p = got[0].value;
    today.out = Object.assign({}, today.out,
      {snapshot: p.snapshot, today: p.today, prep: p.prep, prep_status: p.prep_status});
  }

  async function viewToday(root, params, alive) {
    if (today.inflight || !today.out || today.readDay !== zurichDay()) {
      add(root, [viewHead('Today'), loading('Refreshing from ClickUp…')]);
      await Promise.all([todayRefresh(), readWorker()]);
    } else {
      await Promise.all([todayReload(), readWorker()]);
    }
    if (!alive()) return;
    clear(root);
    // an old #/today link still opens Today: it points to New test, which replaced it (DESIGN.md 8.14)
    add(root, [msgBox('warn', ['Today was replaced by ', el('a', {href: routeHash('new')}, 'New test'),
      ' on 29 Oct: start tests there.'])]);
    const out = today.out;
    const snap = out.snapshot && typeof out.snapshot === 'object' ? out.snapshot : null;
    const taskId = params.get('task');
    const t = snap && taskId ? list(snap.tests).find(function (x) { return x.task_id === taskId; }) : null;
    if (!t) {
      add(root, todayBoard(out, snap, Boolean(taskId && snap)));
      return;
    }
    const show = params.get('show');
    if ((show === 'check' || show === 'progress') && params.get('req')) {
      add(root, [todayStatus(out, snap, true)]);
      await (show === 'check' ? checkScreen : progressScreen)(root, t, params.get('req'), alive);
      return;
    }
    const notes = prepFor(out, snap, t);
    const parts = show === 'details' ? taskDetails(t, notes, out) : show === 'run' ? runOverview(t, notes, out, params) :
      taskChoice(t);
    parts.splice(1, 0, todayStatus(out, snap, true));
    add(root, parts);
  }

  function todayBoard(out, snap, gone) {
    const refreshBtn = el('button', {type: 'button', class: 'btn btn-secondary btn-small'}, 'Refresh');
    refreshBtn.addEventListener('click', async function () {
      refreshBtn.disabled = true;
      refreshBtn.textContent = 'Refreshing…';
      await todayRefresh();
      if (app.route === 'today') render();
    });
    const tests = list(snap && snap.tests);
    const cols = COLUMNS.map(function (c) {
      const items = tests.filter(function (t) { return t.status === c[0]; });
      return el('section', {class: 'board-col', 'aria-label': c[1]},
        el('h2', {class: 'board-col-title'}, c[1], el('span', {class: 'board-count'}, String(items.length))),
        items.length ? el('ul', {class: 'board-list'}, items.map(function (t) {
          return el('li', {}, el('a', {class: 'board-item', href: routeHash('today', {task: t.task_id})},
            el('span', {class: 'break'}, String(t.title || t.task_id)), boardChip(out, snap, t)));
        })) : el('p', {class: 'muted small'}, 'Nothing ' + c[1].toLowerCase() + '.'));
    });
    return [
      el('div', {class: 'view-head'}, el('h1', {text: 'Today'}), refreshBtn),
      todayStatus(out, snap, false),
      gone ? msgBox('warn', 'That test is no longer in the list.') : null,
      snap ? el('div', {class: 'board'}, cols) :
        el('p', {class: 'muted'}, 'No list yet. It appears after the first successful refresh.'),
      hiddenLine(snap),
    ];
  }

  // Banners for a failed ClickUp read, an old list and the pre-test notes' progress; on the board also one line
  // saying when the list and the notes were made.
  function todayStatus(out, snap, bannersOnly) {
    const parts = [];
    if (!out.ok) {
      parts.push(msgBox(out.kind === 'offline' || out.kind === 'app' ? 'error' : 'warn',
        out.message || 'ClickUp could not be read.'));
      if (snap) parts.push(msgBox('info', 'Showing the last list, read at ' + fmtTime(snap.fetched) + '.'));
    }
    const day = zurichDay();
    if (snap && snap.day !== day) parts.push(msgBox('warn', 'This list is from ' + snap.day + ', not today (' + day + ').'));
    const st = out.prep_status;
    const prep = out.prep && typeof out.prep === 'object' ? out.prep : null;
    const failed = st && st.state === 'failed' && (!prep || st.at > prep.created);
    if (st && st.state === 'running') {
      parts.push(msgBox('info', 'Pre-test notes are being prepared (started ' + fmtTime(st.at) + ').'));
    } else if (failed) {
      parts.push(msgBox('warn', 'Preparing the pre-test notes failed at ' + fmtTime(st.at) + (st.note ? ': ' + st.note : '.')));
    }
    if (bannersOnly) return parts;
    const bits = [];
    if (out.ok && snap) bits.push('read from ClickUp at ' + fmtTime(snap.fetched));
    const ww = workerWord(today.worker);
    if (prep && snap && prep.day === snap.day) {
      bits.push('pre-test notes from ' + fmtTime(prep.created));
      const changed = list(snap.tests).filter(function (t) { return prepFor(out, snap, t).why; }).length;
      if (changed) bits.push(changed + (changed === 1 ? ' test is' : ' tests are') + ' new or changed since then');
    }
    if (ww) bits.push(ww);
    if (bits.length) {
      const s = bits.join(' · ') + '.';
      parts.push(el('p', {class: 'small muted'}, s.charAt(0).toUpperCase() + s.slice(1)));
    }
    return parts;
  }

  function hiddenLine(snap) {
    const h = (snap && snap.hidden) || {};
    const hidden = (Number(h.location_unset) || 0) + (Number(h.not_office) || 0);
    if (!hidden) return null;
    const why = [];
    if (h.location_unset) why.push(h.location_unset + ' with LOCATION not set');
    if (h.not_office) why.push(h.not_office + ' not marked OFFICE');
    return el('p', {class: 'small muted'}, hidden + (hidden === 1 ? ' test is' : ' tests are') + ' not shown: ' +
      why.join(', ') + '. Ask Colleague to set LOCATION, priority and SW ENVIRONMENT.');
  }

  // The notes for t, or why there are none. Notes are paired with cases by number, so they are only used while the
  // test's cases are still the ones they were written for (the server's fingerprint, else the case count).
  function prepFor(out, snap, t) {
    const prep = out.prep && typeof out.prep === 'object' ? out.prep : null;
    if (!prep || !snap || prep.day !== snap.day) return {pt: null, why: null};
    const pt = list(prep.tests).find(function (x) { return x.task_id === t.task_id; });
    if (!pt) return {pt: null, why: 'new', at: prep.created};
    const changed = pt.cases_sig && t.cases_sig ? pt.cases_sig !== t.cases_sig :
      list(pt.cases).length !== list(t.cases).length;
    return changed ? {pt: null, why: 'changed', at: prep.created} : {pt: pt, why: null};
  }

  // The engine's hands-off verdict (qa/runner/autonomy.py, through /api/cases) for a ready note's engine cases.
  function handsOff(pc) {
    const ids = list(pc && pc.engine_cases);
    if (!ids.length) return {ok: false, why: 'no engine case yet'};
    for (const id of ids) {
      const c = today.cases.find(function (x) { return x.id === id; });
      const v = c && c.hands_off;
      if (!v) return {ok: false, why: id + ': not checked'};
      if (!v.ok) {
        const b = list(v.blockers)[0] || {};
        return {ok: false, why: id + ': ' + (b.text || 'not hands-off') + (b.fix ? ' (needs ' + b.fix + ')' : '')};
      }
    }
    return {ok: true, why: null};
  }

  // Per test on the board: 'Hands-off' when at least one ready case can run with nobody touching a device.
  function boardChip(out, snap, t) {
    const pt = prepFor(out, snap, t).pt;
    const ready = list(pt && pt.cases).filter(function (pc) { return pc.status === 'ready'; });
    if (!ready.length) return null;
    return ready.some(function (pc) { return handsOff(pc).ok; }) ? chip('s-ok', 'Hands-off ✓') :
      chip('s-muted', 'Needs you');
  }

  function notesMissing(notes, out) {
    if (notes.why === 'new') {
      return 'This test came into the list after the pre-test notes (' + fmtTime(notes.at) + '). The next prep run covers it.';
    }
    if (notes.why === 'changed') {
      return 'This test changed in ClickUp after the pre-test notes (' + fmtTime(notes.at) + '), so they are not shown. ' +
        'The next prep run covers it.';
    }
    const st = out.prep_status;
    if (st && st.state === 'running') return 'The pre-test notes are being prepared.';
    if (st && st.state === 'failed') return 'Preparing the pre-test notes failed, so there are none for this test.';
    return 'There are no pre-test notes for today\'s list yet.';
  }

  function backLink(href, text) {
    return el('a', {class: 'back-link', href: href}, '← ' + text);
  }

  function taskMeta(t) {
    return el('div', {class: 'row'},
      t.priority ? chip(PRIORITY_CLASS[t.priority] || 's-muted', t.priority) : chip('s-muted', 'no priority'),
      chip(t.status === 'ongoing' ? 's-info' : 's-muted', t.status || 'unknown'),
      t.sw_env ? badge('', t.sw_env) : badge('badge-unknown', 'SW env not set'));
  }

  function taskChoice(t) {
    return [
      backLink(routeHash('today'), 'All tests'),
      el('section', {class: 'card task-screen'},
        el('h1', {class: 'task-title break'}, String(t.title || t.task_id)),
        taskMeta(t),
        el('div', {class: 'choice'},
          el('a', {class: 'btn choice-btn', href: routeHash('today', {task: t.task_id, show: 'run'})}, 'Run'),
          el('a', {class: 'btn btn-secondary choice-btn', href: routeHash('today', {task: t.task_id, show: 'details'})},
            'Details')),
        activityLine(t)),
    ];
  }

  function taskDetails(t, notes, out) {
    const pt = notes.pt;
    const linked = today.cases.filter(function (c) {
      const s = c.requirement_source || {};
      return s.ref !== t.task_id && s.custom_id && list(t.dev_tickets).indexOf(s.custom_id) >= 0;
    });
    const prepCases = {};
    for (const pc of list(pt && pt.cases)) prepCases[pc.n] = pc;
    const cases = list(t.cases);
    return [
      backLink(routeHash('today', {task: t.task_id}), String(t.title || t.task_id)),
      el('section', {class: 'card'},
        el('div', {class: 'card-head'}, el('h1', {class: 'task-title break'}, String(t.title || t.task_id))),
        el('div', {class: 'row'}, taskMeta(t), clickupLink({url: t.url, task_id: 'Open in ClickUp'})),
        list(t.intro).length ? el('p', {class: 'small muted break'}, list(t.intro).join(' · ')) : null,
        pt ? (pt.summary ? el('p', {class: 'break'}, pt.summary) : null) :
          el('p', {class: 'small muted'}, notesMissing(notes, out)),
        today.casesError ? msgBox('warn', 'The engine cases could not be read: ' + today.casesError) : null,
        pt && list(pt.dev_tickets).length ? el('ul', {class: 'small'}, list(pt.dev_tickets).map(devTicketLine)) :
          list(t.dev_tickets).length ? el('p', {class: 'small'}, 'Dev tickets: ' + list(t.dev_tickets).join(', ')) : null,
        cases.length ? el('div', {class: 'case-list'}, cases.map(function (c) {
          return caseDetails(t, c, prepCases[c.n] || null);
        })) : el('p', {class: 'muted'}, 'No test cases found in the description.'),
        list(t.matrix).length ? el('p', {class: 'small'}, el('span', {class: 'strong'}, 'Devices asked: '),
          list(t.matrix).join(', ')) : null,
        linked.length ? el('p', {class: 'small'}, el('span', {class: 'strong'}, 'Also linked through the dev ticket: '),
          linked.map(function (c, i) { return [i ? ', ' : '', engineCaseLink(c)]; })) : null),
    ];
  }

  function runOverview(t, notes, out, params) {
    const title = String(t.title || t.task_id);
    const head = [backLink(routeHash('today', {task: t.task_id}), title),
      el('div', {class: 'view-head'}, el('h1', {class: 'break'}, 'Run: ' + title))];
    const cases = list(t.cases);
    if (!cases.length) return head.concat([msgBox('info', 'No test cases were found in the ClickUp description.')]);
    if (!notes.pt) return head.concat([msgBox('info', notesMissing(notes, out) + ' Without them the app cannot say what would run.')]);
    const byN = {};
    for (const pc of list(notes.pt.cases)) byN[pc.n] = pc;
    const isReady = function (c) { return byN[c.n] && byN[c.n].status === 'ready'; };
    const will = cases.filter(function (c) { return isReady(c) && handsOff(byN[c.n]).ok; });
    const wont = cases.filter(function (c) { return !(isReady(c) && handsOff(byN[c.n]).ok); });
    // cases that need input: attached test files may complete them (the worker decides on Prepare)
    const isPending = function (c) {
      return byN[c.n] && byN[c.n].status === 'needs_input' && list(byN[c.n].engine_cases).length > 0;
    };
    const pending = wont.filter(isPending);
    const devices = [];
    let minutes = 0;
    let unknown = 0;
    for (const c of will) {
      for (const tag of list(byN[c.n].targets)) if (devices.indexOf(tag) < 0) devices.push(tag);
      if (byN[c.n].estimate_min) minutes += Number(byN[c.n].estimate_min) || 0;
      else unknown += 1;
    }
    devices.sort(function (a, b) { return (TAG_ORDER.indexOf(a) + 99) % 99 - (TAG_ORDER.indexOf(b) + 99) % 99; });
    const withFiles = devices.slice();
    for (const c of pending) for (const tag of list(byN[c.n].targets)) if (withFiles.indexOf(tag) < 0) withFiles.push(tag);
    const pendingRows = [];
    let total = minutes ? ' · about ' + minutes + ' min in total' : '';
    if (unknown) total += (minutes ? ', plus ' : ' · ') + unknown + (unknown === 1 ? ' case' : ' cases') + ' without an estimate';
    return head.concat([
      el('section', {class: 'card'},
        el('h2', {}, 'Will be tested, hands-off (' + will.length + ')'),
        will.length ? el('ul', {class: 'list-plain'}, will.map(function (c) {
          const pc = byN[c.n];
          return el('li', {},
            el('div', {class: 'row top'}, el('span', {class: 'strong nowrap'}, c.n + '.'), el('span', {class: 'break'}, String(c.text || ''))),
            el('div', {class: 'small muted'}, [list(pc.engine_cases).join(', '), 'on ' + list(pc.targets).join(', '),
              pc.estimate_min ? 'about ' + pc.estimate_min + ' min' : 'no estimate'].filter(Boolean).join(' · ')),
            list(pc.side_effects).length ? el('div', {class: 'small break'}, el('span', {class: 'strong'}, 'Creates: '),
              sentences(pc.side_effects)) : null);
        })) : el('p', {class: 'muted'}, 'Nothing in this test can run in the office today.'),
        will.length ? el('p', {class: 'small'}, el('span', {class: 'strong'}, 'Devices: '), devices.join(', ') + total) :
          el('p', {class: 'small muted'}, 'The engine runs only cases it can finish with nobody touching a device.')),
      wont.length ? el('section', {class: 'card flat'},
        el('h2', {}, 'Won\'t run today (' + wont.length + ')'),
        el('ul', {class: 'list-plain'}, wont.map(function (c) {
          const pc = byN[c.n];
          const held = isReady(c) ? handsOff(pc) : null;
          const chipEl = held ? chip('s-muted', 'Needs you') :
            pc ? chip(PREP_CLASS[pc.status] || 's-muted', PREP_LABEL[pc.status] || pc.status) : chip('s-muted', 'no note');
          const whyEl = held ? el('div', {class: 'small muted break'}, 'Not hands-off: ' + held.why) :
            pc && list(pc.why).length ? el('div', {class: 'small muted break'}, list(pc.why)[0]) : null;
          if (isPending(c)) pendingRows.push({chip: chipEl, why: whyEl, label: chipEl.textContent,
            cls: chipEl.className, text: whyEl ? whyEl.textContent : ''});
          return el('li', {},
            el('div', {class: 'row top'}, el('span', {class: 'strong nowrap'}, c.n + '.'),
              el('span', {class: 'break'}, String(c.text || '')), chipEl),
            whyEl);
        }))) : null,
      runActions(t, will, devices, params, {pending: pending, pendingRows: pendingRows, withFiles: withFiles}),
    ]);
  }

  // ---- Today: device check, start and progress through the QA worker (DESIGN.md 8.3) ----

  const REQ_POLL_MS = 2000;
  const RUN_POLL_MS = 3000;
  const CHECK_FRESH_MS = 30 * 60 * 1000;
  const CASE_FILE_RE = /^qa\/cases\/[A-Za-z0-9][A-Za-z0-9._-]*\.json$/;  // a plan case's pinned file (plan_cases[].file)
  const DEVICE_POLL_MS = 5000;  // New test's live device status (/api/devices; the server reads at most every 8 s)
  const GUIDANCE_MAX = 1000;  // writes.GUIDANCE_MAX
  const WORKER_START_HINT = 'Start it in the Claude app: sidebar > Scheduled > Hover > Run now.';
  const REQ_STATE = {new: 'waiting for Hover', claimed: 'in progress', done: 'done', failed: 'stopped',
    cancelled: 'cancelled'};
  const REQ_CLASS = {new: 's-muted', claimed: 's-info', done: 's-ok', failed: 's-bad', cancelled: 's-muted'};

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  // A 403 on a write is the per-start token: the server restarted after this page was opened.
  function failText(e) {
    return e && e.status === 403 ? 'The app server restarted after this page was opened. Reload the page.' : errText(e);
  }

  async function readWorker() {
    try {
      today.worker = await getJSON('/api/worker');
    } catch (e) {
      today.worker = null;
    }
    return today.worker;
  }

  function isOpen(r) {
    return r && (r.state === 'new' || r.state === 'claimed');
  }

  function testRequests(wv, taskId) {
    return list(wv && wv.requests).filter(function (r) {
      return (r.type === 'check' || r.type === 'start') && (r.clickup || {}).task_id === taskId;
    });
  }

  function workerLink(w) {
    return w && w.link ? el('a', {href: w.link}, 'Open Hover\'s session') : null;
  }

  function workerLine(wv) {
    const w = wv && wv.worker;
    if (!w) return el('p', {class: 'small muted'}, 'Hover\'s state could not be read.');
    if (w.state === 'ready') return el('p', {class: 'small row'}, chip('s-ok', 'Hover standing by'), 'It picks up a request within seconds.');
    if (w.state === 'busy') {
      return el('p', {class: 'small row'}, chip('s-info', 'Hover busy'),
        w.request ? ['with ' + w.request, w.since ? [' since ', timeEl(w.since)] : null] : 'finishing a request',
        workerLink(w));
    }
    if (w.state === 'stuck') {
      return el('p', {class: 'small row'}, chip('s-warn', 'Hover went quiet'),
        ['No sign of work on ' + w.request + ' since ', timeEl(w.last_activity), '. It may have stopped; you can ' +
          'release the request on its screen, then start Hover again.'], workerLink(w));
    }
    return el('p', {class: 'small row'}, chip('s-warn', 'Hover off duty'), WORKER_START_HINT);
  }

  function workerWord(wv) {
    const s = wv && wv.worker && wv.worker.state;
    return s === 'ready' ? 'Hover standing by' : s === 'busy' ? 'Hover busy' : s === 'stuck' ? 'Hover went quiet' :
      s ? 'Hover off duty' : null;
  }

  function notesList(r) {
    const notes = list(r && r.notes);
    if (!notes.length) return null;
    return el('ol', {class: 'notes-list small'}, notes.map(function (n) {
      return el('li', {}, el('span', {class: 'muted nowrap'}, fmtTime(n.at)), ' ', el('span', {class: 'break'}, n.text || ''));
    }));
  }

  function lastNote(r) {
    const notes = list(r && r.notes);
    return notes.length ? notes[notes.length - 1].text : '';
  }

  function mark(v) {
    if (v === true) return el('span', {class: 'ok-mark', title: 'yes'}, '✓');
    if (v === false) return el('span', {class: 'bad-mark', title: 'no'}, '✗');
    return el('span', {class: 'muted', title: 'not checked'}, '–');
  }

  // What the test's Run screens say about requests already made today.
  function activityLine(t) {
    const reqs = testRequests(today.worker, t.task_id);
    const open = reqs.find(isOpen);
    // a finished check still under 30 minutes old can still be started from
    const fresh = reqs.find(function (x) {
      return x.type === 'check' && x.state === 'done' && x.result &&
        Date.now() - new Date(x.result.checked_at || 0).getTime() < CHECK_FRESH_MS;
    });
    // else the newest of that check and the starts that ran (the list is newest first, in the order they were created):
    // a run started after the check leads to the run, not back to Run; a start cancelled before the worker took it
    // ran nothing
    const ran = function (x) { return x.type === 'start' && x.state !== 'cancelled'; };
    const r = open || reqs.find(function (x) { return x === fresh || ran(x); }) ||
      reqs.find(function (x) { return x.type === 'start'; });
    if (!r) return null;
    const show = r.type === 'check' ? 'check' : 'progress';
    const what = r.type === 'check' ? 'Device check ' : 'Run ';
    return el('p', {class: 'small row'}, chip(REQ_CLASS[r.state] || 's-muted', what + (REQ_STATE[r.state] || r.state)),
      el('a', {href: routeHash('today', {task: t.task_id, show: show, req: r.id})}, open ? 'See it' : 'See the last one'),
      el('span', {class: 'muted'}, r.id));
  }

  const FILE_EXT = ['.tif', '.tiff', '.las', '.laz', '.zip'];  // uploads.EXTENSIONS

  // The Run form's "Test files" (qa/app/uploads.py): the attached files and the uploads in progress, one file at a time
  // in pieces. files.attached and files.uploading are shared with the form; onChange runs after every change.
  // words: New test's label and button name (default the Run form's). files.stop removes the box before a route change;
  // files.add attaches picked files (the Plan screen's "Attach a file").
  function testFilesBox(t, files, onChange, words) {
    const w = words || {};
    const base = '/api/files/' + encodeURIComponent(t.task_id);
    const input = el('input', {type: 'file', id: 'test-files', class: 'sr-only', multiple: 'multiple',
      accept: FILE_EXT.join(',')});
    const drop = el('label', {class: 'drop-zone', for: 'test-files'},
      el('span', {class: 'strong'}, 'Drop files here or choose files'),
      el('span', {class: 'small muted'}, FILE_EXT.join(', ') + ' · up to 2 GB each · kept on this Mac, copied only to ' +
        'the test devices'), input);
    const listEl = el('div', {class: 'file-list'});
    const err = el('div', {class: 'inline-error', role: 'alert'});
    const short = function (sha) { return sha ? String(sha).slice(0, 4) + '…' + String(sha).slice(-4) : '?'; };
    const draw = function () {
      const rows = files.attached.map(function (f) {
        const rm = el('button', {type: 'button', class: 'btn-link'}, 'Remove');
        rm.addEventListener('click', async function () {
          rm.disabled = true;
          err.textContent = '';
          try {
            await sendJSON('POST', base + '/remove', {name: f.name});
          } catch (e) {
            err.textContent = failText(e);
          }
          await reload();
        });
        return el('div', {class: 'file-row'}, el('span', {class: 'strong break'}, f.name),
          el('span', {class: 'small muted'}, fmtBytes(f.size) + ' · added ' + fmtTime(f.added) + ' · checksum ' + short(f.sha256)),
          el('span', {class: 'ok-mark'}, 'Ready'), w.noRemove ? null : rm);
      }).concat(files.uploading.map(function (u) {
        const cancel = el('button', {type: 'button', class: 'btn-link'}, 'Cancel');
        cancel.addEventListener('click', function () { stopUpload(u); });
        return el('div', {class: 'file-row'}, el('span', {class: 'strong break'}, u.name),
          el('span', {class: 'small muted'}, fmtBytes(u.size) + (u.waiting ? ' · waiting' :
            ' · uploading ' + Math.floor(100 * u.sent / u.size) + '%')),
          el('progress', {max: u.size, value: u.sent, 'aria-label': u.name + ' upload'}), cancel);
      }));
      put(listEl, rows.length ? rows : null);
      onChange();
    };
    const reload = async function () {
      try {
        const r = await getJSON(base);
        files.info = r;
        files.attached = list(r.files);
      } catch (e) {
        err.textContent = failText(e);
      }
      draw();
    };
    // the server takes plain names only: anything else becomes _ (shown as the attached name)
    const safeName = function (name) {
      return String(name).replace(/[^A-Za-z0-9._-]+/g, '_').slice(-150).replace(/^[^A-Za-z0-9]+/, '');
    };
    // Cancel, and leaving the form, stop an upload: the piece in flight is aborted and what arrived is removed
    const stopUpload = function (u) {
      u.cancelled = true;
      if (u.ctrl) u.ctrl.abort();
    };
    let running = false;
    const pump = async function () {
      if (running) return;
      running = true;
      while (files.uploading.length) {
        const u = files.uploading[0];
        const piece = (files.info && files.info.piece_bytes) || 8 * 1024 * 1024;
        u.waiting = false;
        draw();
        try {
          while (u.sent < u.size) {
            if (u.cancelled) throw new Error('cancelled');
            const chunk = u.file.slice(u.sent, Math.min(u.size, u.sent + piece));
            u.ctrl = new AbortController();
            await sendPiece(base + '/' + encodeURIComponent(u.name) + '?offset=' + u.sent + '&total=' + u.size, chunk,
              u.ctrl.signal);
            u.sent += chunk.size;
            draw();
          }
          if (u.cancelled) throw new Error('cancelled');  // cancelled while the last piece was on its way
        } catch (e) {
          if (u.sent > 0 || u.cancelled) {
            try { await sendJSON('POST', base + '/remove', {name: u.name}); } catch (e2) { /* nothing was kept */ }
          }
          if (!u.cancelled) err.textContent = u.name + ': ' + failText(e);
        }
        files.uploading.shift();
        await reload();
      }
      running = false;
    };
    const addFiles = function (picked) {
      err.textContent = '';
      const max = (files.info && files.info.max_bytes) || 2 * 1024 * 1024 * 1024;
      for (const f of Array.from(picked || [])) {
        const name = safeName(f.name);
        const lower = name.toLowerCase();
        if (!FILE_EXT.some(function (x) { return lower.endsWith(x); })) {
          err.textContent = f.name + ': only ' + FILE_EXT.join(', ') + ' files.';
        } else if (!f.size || f.size > max) {
          err.textContent = f.name + ': a file of 1 byte to 2 GB.';
        } else if (files.attached.concat(files.uploading).some(function (x) { return x.name === name; })) {
          err.textContent = name + ' is already attached.';
        } else {
          files.uploading.push({file: f, name: name, size: f.size, sent: 0, waiting: true, cancelled: false});
        }
      }
      draw();
      pump();
    };
    input.addEventListener('change', function () {
      addFiles(input.files);
      input.value = '';
    });
    drop.addEventListener('dragover', function (e) {
      e.preventDefault();
      drop.classList.add('over');
    });
    drop.addEventListener('dragleave', function () { drop.classList.remove('over'); });
    // a file dropped anywhere on the form is attached, never opened in the tab (which would leave the app)
    const onDragOver = function (e) { e.preventDefault(); };
    const onDrop = function (e) {
      e.preventDefault();
      drop.classList.remove('over');
      addFiles(e.dataTransfer && e.dataTransfer.files);
    };
    const onLeave = function (e) {
      if (files.uploading.length) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    // any route change replaces this form: stop its uploads and its page-wide handlers
    const onRoute = function () {
      for (const u of files.uploading) stopUpload(u);
      window.removeEventListener('dragover', onDragOver);
      window.removeEventListener('drop', onDrop);
      window.removeEventListener('beforeunload', onLeave);
      window.removeEventListener('hashchange', onRoute);
    };
    window.addEventListener('dragover', onDragOver);
    window.addEventListener('drop', onDrop);
    window.addEventListener('beforeunload', onLeave);
    window.addEventListener('hashchange', onRoute);
    files.stop = onRoute;
    files.add = addFiles;
    reload();
    return el('div', {class: 'test-files'}, el('div', {class: w.label && !w.fieldLabel ? 'step-label' : 'field-label'},
      w.fieldLabel || w.label || 'Test files (optional)'), drop, listEl,
      err, el('p', {class: 'small muted'}, (w.noRemove ? 'Files stay attached to this test. ' :
        'Files stay attached to this test until you remove them. ') +
        (w.button || 'Prepare summary') + ' waits until every upload has finished.'));
  }

  // The Run form: the person's guidance and the devices to run on. Prepare asks the worker to check the devices and
  // write a summary; nothing runs before Run on the next screen. ?edit=<W-id> fills it from an earlier Prepare.
  function runActions(t, will, devices, params, extra) {
    const open = testRequests(today.worker, t.task_id).find(isOpen);
    if (open) {
      const show = open.type === 'check' ? 'check' : 'progress';
      return el('div', {class: 'form-actions'},
        el('a', {class: 'btn', href: routeHash('today', {task: t.task_id, show: show, req: open.id})},
          open.type === 'check' ? 'See the summary' : 'See the run'),
        el('span', {class: 'hint'}, open.id + ' is ' + (REQ_STATE[open.state] || open.state) + '.'));
    }
    const pending = list(extra && extra.pending);
    if (!will.length && !pending.length) return el('div', {}, workerLine(today.worker));
    const editId = params && params.get('edit');
    const prev = editId ? testRequests(today.worker, t.task_id).find(function (r) {
      return r.id === editId && r.type === 'check';
    }) : null;
    const registered = list(app.meta && app.meta.targets).map(function (x) { return x && x.tag; });
    const guidance = el('textarea', {id: 'run-guidance', rows: 5, value: prev && prev.guidance ? String(prev.guidance) : '',
      placeholder: 'Optional: what to focus on or report, in your own words. What counts as a pass stays the case\'s own.'});
    const counter = counterEl(0, GUIDANCE_MAX);
    const files = {attached: [], uploading: [], info: null};
    let boxes = {};
    const grid = el('div', {class: 'device-grid'});
    // the devices the cases run on: with files attached, the pending cases' devices too; ticks survive a redraw
    const drawDevices = function () {
      const routedTags = files.attached.length && extra ? list(extra.withFiles) : devices;
      const was = {};
      for (const tag of Object.keys(boxes)) was[tag] = boxes[tag].checked;
      boxes = {};
      put(grid, TAG_ORDER.map(function (tag) {
        const known = !registered.length || registered.indexOf(tag) >= 0;
        const routed = routedTags.indexOf(tag) >= 0;
        const usable = known && routed;
        const cb = el('input', {type: 'checkbox', disabled: !usable, checked: usable && (tag in was ? was[tag] :
          prev ? list(prev.targets).indexOf(tag) >= 0 : true)});
        if (usable) {
          boxes[tag] = cb;
          cb.addEventListener('change', update);
        }
        return el('label', {class: 'device-choice' + (usable ? '' : ' off')}, cb, el('span', {class: 'strong'}, tag),
          el('span', {class: 'small muted'}, !known ? 'not set up' : !routed ? 'nothing to run' :
            tag === 'W044' ? 'web' : 'tablet'));
      }));
      webOnly.disabled = !boxes.W044;
    };
    const ticked = function () { return TAG_ORDER.filter(function (tag) { return boxes[tag] && boxes[tag].checked; }); };
    const btn = el('button', {type: 'button', class: 'btn'}, 'Prepare summary');
    const err = el('span', {class: 'inline-error', role: 'alert'});
    const update = function () {
      btn.disabled = !ticked().length || jsLen(guidance.value) > GUIDANCE_MAX || files.uploading.length > 0 ||
        (!will.length && !files.attached.length);
    };
    bindCounter(guidance, counter, GUIDANCE_MAX);
    guidance.addEventListener('input', update);
    const pick = function (only) {
      for (const tag of Object.keys(boxes)) boxes[tag].checked = !only || tag === only;
      update();
    };
    const webOnly = el('button', {type: 'button', class: 'btn-link'}, 'Web only');
    const all = el('button', {type: 'button', class: 'btn-link'}, 'All');
    const filesBox = testFilesBox(t, files, function () {
      drawDevices();
      for (const row of list(extra && extra.pendingRows)) {
        const has = files.attached.length > 0;
        row.chip.className = has ? 'chip s-info' : row.cls;
        row.chip.textContent = has ? 'Files attached' : row.label;
        if (row.why) row.why.textContent = has ? 'Files are attached below: Hover decides on Prepare summary whether ' +
          'they complete this case.' : row.text;
      }
      update();
    });
    drawDevices();
    webOnly.addEventListener('click', function () { pick('W044'); });
    all.addEventListener('click', function () { pick(null); });
    btn.addEventListener('click', async function () {
      btn.disabled = true;
      err.textContent = '';
      try {
        const r = await sendJSON('POST', '/api/worker/check',
          {task_id: t.task_id, targets: ticked(), guidance: guidance.value, prepare: true});
        go('today', {task: t.task_id, show: 'check', req: r.id});
      } catch (e) {
        err.textContent = failText(e);
        update();
      }
    });
    update();
    return el('section', {class: 'card'},
      el('label', {class: 'field', for: 'run-guidance'}, el('span', {}, 'Your guidance (optional)')),
      guidance,
      el('p', {class: 'small muted row'}, 'What to focus on and report. Hover reads it with the ClickUp task and the ' +
        'pre-test notes; it never changes what counts as a pass.', counter),
      filesBox,
      el('fieldset', {class: 'device-choices'},
        el('legend', {class: 'row'}, el('span', {class: 'strong'}, 'Devices'), webOnly, all),
        grid,
        el('p', {class: 'small muted'}, 'A device the test cannot run on is greyed out.')),
      workerLine(today.worker),
      el('div', {class: 'form-actions'}, btn,
        el('a', {class: 'btn btn-secondary', href: routeHash('today', {task: t.task_id})}, 'Cancel'),
        el('span', {class: 'hint'}, 'Nothing runs yet. Next you see a summary to approve.'), err));
  }

  // Polls the worker until request rid is no longer open or the screen is left; draw(request, view) each time.
  async function pollRequest(rid, alive, ms, draw, more) {
    for (;;) {
      const wv = await readWorker();
      if (!alive()) return;
      const r = wv ? list(wv.requests).find(function (x) { return x.id === rid; }) || null : undefined;
      const extra = more ? await more(r) : null;
      if (!alive()) return;
      draw(r, wv, extra);
      if (r === null || (r && !isOpen(r) && !(extra && extra.keepPolling))) return;
      await sleep(ms);
      if (!alive()) return;
    }
  }

  // h1: the title's text, or its node when the screen changes it later
  function requestHead(t, back, backText, h1, sub) {
    const title = h1 instanceof Node ? h1 : el('h1', {class: 'break'}, h1);
    return [backLink(back, backText), el('div', {class: 'view-head'}, sub ? el('div', {class: 'head-titles'}, title, sub) :
      title)];
  }

  // under a New test plan's title: its ClickUp test, in grey (the title is the plan's own since 2026-11-04)
  function clickupLine(cu) {
    const name = cu && (cu.title || cu.custom_id || cu.task_id);
    if (!name) return null;
    const text = 'ClickUp: ' + name;
    return el('p', {class: 'head-sub'}, cu.url && CLICKUP_RE.test(cu.url) ?
      el('a', {href: cu.url, target: '_blank', rel: 'noopener noreferrer'}, text + ' ↗') : text);
  }

  // A request's screen: New test's plan or run screen for a test opened there (t.intake), else the test's Today screen.
  function reqHash(t, show, rid) {
    return t.intake ? routeHash('new', {show: show === 'check' ? 'plan' : show, req: rid}) :
      routeHash('today', {task: t.task_id, show: show, req: rid});
  }

  // Cancel (not yet taken) or Release (taken, but no sign of work for 20 min). The error goes to errSlot, which
  // lives outside the polled body, so the next redraw does not wipe it.
  function cancelButton(r, errSlot, label) {
    const b = el('button', {type: 'button', class: 'btn btn-secondary btn-small'}, label || 'Cancel');
    b.addEventListener('click', async function () {
      b.disabled = true;
      put(errSlot);
      try {
        await sendJSON('POST', '/api/worker/cancel', {id: r.id});
        render();
      } catch (e) {
        put(errSlot, msgBox('error', failText(e)));
        b.disabled = false;
      }
    });
    return b;
  }

  function stuckRelease(r, wv, errSlot) {
    const w = wv && wv.worker;
    if (!w || w.state !== 'stuck' || w.request !== r.id) return null;
    return el('div', {class: 'msg msg-warn'}, el('p', {}, 'No sign of work since ', timeEl(w.last_activity),
      '. Hover may have stopped. Release the request, start Hover again, and try once more.'),
      cancelButton(r, errSlot, 'Release it'));
  }

  // Asks again with the same devices and guidance as the earlier request r (when there is one).
  function checkAgainButton(t, label, prev) {
    const b = el('button', {type: 'button', class: 'btn btn-secondary'}, label || 'Check again');
    const err = el('span', {class: 'inline-error', role: 'alert'});
    b.addEventListener('click', async function () {
      b.disabled = true;
      err.textContent = '';
      const body = {task_id: t.task_id};
      if (prev && prev.prepare === true) {
        body.prepare = true;
        body.guidance = prev.guidance || '';
        if (list(prev.targets).length) body.targets = list(prev.targets);
      }
      if (prev && prev.intake === true) body.intake = true;
      // an answered plan's answers go to the new plan too, so the worker does not ask them again
      if (prev && prev.intake === true && prev.follows && list(prev.answers).length) body.answers_from = prev.id;
      try {
        const r = await sendJSON('POST', '/api/worker/check', body);
        goTo(reqHash(t, 'check', r.id));
      } catch (e) {
        err.textContent = failText(e);
        b.disabled = false;
      }
    });
    return [b, err];
  }

  async function checkScreen(root, t, rid, alive) {
    const title = String(t.title || t.task_id);
    const body = el('div', {}, loading(t.intake ? 'Loading the plan…' : 'Loading the summary…'));
    const errSlot = el('div', {});
    // a new screen starts with no answers; the last screen's files box stops (it adds page-wide handlers)
    if (planPage && planPage.files) planPage.files.stop();
    planPage = {rid: rid, errSlot: errSlot, answers: new Map(), files: null, box: null, onFiles: null};
    // a New test plan shows its title on top, so Run approves it with the plan; the server's title (GET /api/worker)
    // is the description cut until Hover's plan brings its own
    const h1 = el('h1', {class: 'break'}, title);
    add(root, t.intake ? requestHead(t, routeHash('new'), 'Back to New test', h1, clickupLine(t.clickup)) :
      requestHead(t, routeHash('today', {task: t.task_id, show: 'run', edit: rid}), 'Back to the Run form', 'Run: ' + title));
    add(root, [errSlot, body]);
    await pollRequest(rid, alive, REQ_POLL_MS, function (r, wv) {
      if (t.intake && r && r.title && h1.textContent !== String(r.title)) h1.textContent = String(r.title);
      put(body, checkBody(t, r, wv, errSlot));
    });
  }

  function checkBody(t, r, wv, errSlot) {
    if (r === undefined) return msgBox('warn', 'Hover\'s state could not be read. Trying again…');
    if (r === null) {
      return [msgBox('warn', 'This request is not in Hover\'s list.'), t.intake ?
        el('a', {class: 'btn btn-secondary', href: routeHash('new')}, 'Back to New test') : checkAgainButton(t)];
    }
    const head = el('p', {class: 'row'}, chip(REQ_CLASS[r.state] || 's-muted', REQ_STATE[r.state] || r.state),
      el('span', {class: 'small muted'}, r.id + ' · devices ' + list(r.targets).join(', ')));
    if (r.state === 'new') return [head, workerLine(wv), el('div', {class: 'form-actions'}, cancelButton(r, errSlot))];
    if (r.state === 'claimed') {
      return [head, stuckRelease(r, wv, errSlot), el('p', {}, r.intake ? 'Checking the devices and writing the plan…' :
        r.prepare ? 'Checking the devices and writing the summary…' : 'Checking the devices…'), notesList(r)];
    }
    if (r.state !== 'done') {
      return [head, msgBox(r.state === 'failed' ? 'error' : 'info', lastNote(r) || 'The check did not finish.'),
        el('div', {class: 'form-actions'}, checkAgainButton(t, r.intake ? 'Prepare again' : null, r), editButton(t, r))];
    }
    if (r.intake) {
      return [el('p', {class: 'muted'}, 'Prepared ', timeEl((r.result || {}).checked_at || r.done_at),
        (r.follows ? ' · from the ClickUp task, your description and your answers · ' :
          ' · from the ClickUp task and your description · ') + r.id), checkResult(t, r, wv)];
    }
    return [head, checkResult(t, r, wv)];
  }

  function editButton(t, r) {
    if (!r || !r.prepare) return null;
    return el('a', {class: 'btn btn-secondary', href: t.intake ? routeHash('new', {edit: r.id}) :
      routeHash('today', {task: t.task_id, show: 'run', edit: r.id})}, 'Edit');
  }

  // What the worker will do, from its summary and the request: the person reads this before pressing Run.
  function planCard(r, wv) {
    const res = r.result || {};
    // with attached files: the pending cases the worker said the files complete run too
    const done = list(res.complete);
    const cases = list(r.cases).concat(list(r.pending).map(function (c) {
      return Object.assign({}, c, {engine_cases: list(c.engine_cases).filter(function (e) { return done.indexOf(e) >= 0; })});
    }).filter(function (c) { return c.engine_cases.length > 0; }));
    // a plan from New test names its own cases (an existing engine case or one written from the description)
    const planned = list(res.plan_cases);
    const byName = {};
    for (const f of list(r.files)) byName[f.name] = f;
    const caseTargets = {};
    for (const c of cases) for (const e of list(c.engine_cases)) caseTargets[e] = list(c.targets);
    for (const pc of planned) caseTargets[pc.id] = list(pc.targets);
    const used = list(res.plan_files).map(function (x) { return x.name; });
    const unused = list(r.files).filter(function (f) { return used.indexOf(f.name) < 0; }).map(function (f) { return f.name; });
    const fileRows = list(res.plan_files).map(function (x) {
      const f = byName[x.name] || {};
      return el('div', {class: 'file-row'}, el('span', {class: 'strong'}, x.case),
        el('span', {class: 'break'}, x.name + (f.size ? ' · ' + fmtBytes(f.size) : '')),
        el('span', {class: 'small muted'}, '→ ' + list(caseTargets[x.case]).join(', ')),
        el('span', {class: 'small muted'}, 'checksum ' + (f.sha256 ? f.sha256.slice(0, 4) + '…' + f.sha256.slice(-4) : '?')));
    });
    // a New test plan's tiles name only the devices one of its cases runs on
    const onPlan = function (tag) {
      return !r.intake || planned.some(function (pc) { return list(pc.targets).indexOf(tag) >= 0; });
    };
    const ready = list(res.targets).filter(function (x) { return x.ok && onPlan(x.tag); }).map(function (x) { return x.tag; });
    let minutes = 0;
    let unknown = 0;
    const creates = [];
    for (const c of cases) {
      if (Number(c.estimate_min)) minutes += Number(c.estimate_min);
      else unknown += 1;
      for (const s of list(c.side_effects)) if (creates.indexOf(s) < 0) creates.push(String(s));
    }
    // an empty side_effects list in the notes means the cases create nothing; a missing one is not stated
    const stated = cases.length > 0 && cases.every(function (c) { return Array.isArray(c.side_effects); });
    const timeSub = [ready.length > 1 ? 'Devices run in parallel' : null,
      minutes && unknown ? 'plus ' + unknown + (unknown === 1 ? ' case' : ' cases') + ' without an estimate' : null]
      .filter(Boolean).join('; ');
    const notReady = list(res.targets).filter(function (x) { return !x.ok && onPlan(x.tag); }).map(function (x) { return x.tag; });
    const tile = function (label, value, sub) {
      return el('div', {class: 'plan-tile'}, el('div', {class: 'small muted'}, label), el('div', {class: 'strong'}, value),
        sub ? el('div', {class: 'small muted'}, sub) : null);
    };
    return el('section', {class: 'card'},
      el('h2', {}, 'What I will do'),
      res.summary ? el('div', {class: 'prewrap'}, String(res.summary)) :
        msgBox('warn', 'Hover gave no summary. Press Edit and prepare again.'),
      r.intake ? planCases(planned, planNeeds(r).length > 0) : null,
      r.guidance ? el('div', {class: 'plan-guidance'}, el('div', {class: 'field-label'},
        r.intake ? 'Your description' : 'Your guidance'),
        el('p', {class: 'plan-text muted'}, '"' + String(r.guidance) + '"')) : null,
      r.intake ? yourAnswers(r, wv) : null,
      fileRows.length || unused.length ? el('div', {class: 'plan-guidance'}, el('div', {class: 'field-label'}, 'Test files'),
        fileRows.length ? el('div', {class: 'file-list'}, fileRows) : null,
        unused.length ? el('p', {class: 'small muted'}, 'Attached but not used in this run: ' + unused.join(', ') + '.') : null,
        fileRows.length ? el('p', {class: 'small muted'}, 'The engine copies each file to the devices at the start of ' +
          'the run and checks its checksum there.') : null) : null,
      el('div', {class: 'plan-tiles'},
        tile('Devices', ready.length ? ready.join(', ') : 'none ready', notReady.length ? notReady.join(', ') +
          ' not ready (below)' : null),
        // a New test plan has no estimates: the tile then only says the devices run side by side
        tile('Time', minutes ? 'About ' + minutes + ' min' : r.intake ? 'Devices run in parallel' : 'No estimate',
          r.intake && !minutes ? null : timeSub || null),
        // a New test plan has no pre-test notes: what it creates is in its summary
        r.intake ? null : tile('Creates', creates.length ? sentences(creates) : stated ? 'Nothing' : 'Not stated',
          creates.length || stated ? null : 'in the pre-test notes')));
  }

  // The cases a New test plan runs: the engine case, whether it was written or changed while planning (the server
  // decides), what it checks, and its steps and expected result.
  function planCases(planned, asks) {
    if (!planned.length) {
      return el('p', {class: 'small muted'}, asks ?
        'No test case in this plan yet. Answer the questions below and Hover writes a new plan.' :
        'No test case in this plan, so Run stays off. The summary says what is missing.');
    }
    const isNew = planned.some(function (pc) { return pc.new; });
    const changed = planned.some(function (pc) { return pc.changed_in_plan; });
    return el('div', {class: 'plan-guidance'},
      el('ul', {class: 'list-plain'}, planned.map(function (pc) {
        return el('li', {},
          el('div', {class: 'row'}, el('span', {class: 'strong'}, 'Test case ' + pc.id),
            pc.new ? chip('s-warn', 'New, written from your description') : null,
            pc.changed_in_plan ? chip('s-warn', 'Changed while planning') : null,
            el('span', {class: 'small muted'}, 'on ' + list(pc.targets).join(', '))),
          pc.text ? el('div', {class: 'break'}, String(pc.text)) : null,
          caseSteps(pc));
      })),
      isNew && !changed ? el('p', {class: 'small muted'}, 'A new case has not passed yet, ' +
        'so a FAIL in this run stays "suspected" until the case has passed once.') :
        changed ? el('p', {class: 'small muted'}, (isNew ? 'A new or changed case' : 'A case changed while planning') +
          ' has not passed in this version yet, so a FAIL in this run stays "suspected" until it has passed once.') : null);
  }

  // A plan case in the version the plan pinned (plan_cases[].file and sha256; Run refuses a case changed since): the
  // newest record from /api/cases/<id>, or, when a newer version came after the plan, the pinned file itself (/files),
  // with a line saying so. Its parts (2026-11-04, the case rule: the test as a person would do it, each with the task's
  // words it carries out, the checks marked); a case written before the rule shows its check steps (the steps with an
  // assert). Under them, what the case takes as given before the run (its preconditions, 2026-11-10; nothing when it
  // has none): Run approves these too, so a line from the person (fromPerson) is a warning box with "Set by you".
  // Every engine step and the expected result fold away under "All engine steps (n)". When the record cannot be read,
  // the id links to the case's history.
  function caseSteps(pc) {
    const slot = el('div', {class: 'small'}, loading('Loading the steps…'));
    getJSON('/api/cases/' + encodeURIComponent(pc.id)).then(function (d) {
      const newest = list(d && d.versions).find(function (v) { return v && v.file === d.latest_file; });
      const pinned = typeof pc.sha256 === 'string' && pc.sha256 && newest && typeof newest.sha256 === 'string' &&
        newest.sha256 !== pc.sha256 && typeof pc.file === 'string' && CASE_FILE_RE.test(pc.file);
      if (!pinned) return {c: (d && d.case) || {}, older: false};
      return getJSON(fileUrl(pc.file)).then(function (c) { return {c: c || {}, older: true}; });
    }).then(function (got) {
      const c = got.c && typeof got.c === 'object' ? got.c : {};
      const steps = list(c.steps);
      const stepText = function (s) { return s && typeof s === 'object' ? String(s.text || '') : String(s); };
      const nOf = function (x) { return x && typeof x === 'object' && Number.isInteger(x.n) ? x.n : null; };
      const parts = list(c.parts).filter(function (p) { return p && typeof p === 'object' && p.text; });
      const checks = steps.filter(function (s) { return s && typeof s === 'object' && s.assert && !s.always; });
      const pre = list(c.preconditions).filter(function (p) { return p && (typeof p !== 'object' || p.text); });
      put(slot,
        got.older ? el('p', {class: 'muted'}, 'This plan runs an earlier version of this case; a newer one exists.') : null,
        parts.length ? el('ol', {class: 'plan-parts'}, parts.map(function (p) {
          const quotes = (Array.isArray(p.quote) ? p.quote : [p.quote]).filter(function (q) {
            return typeof q === 'string' && q;
          });
          return el('li', {value: nOf(p)}, String(p.text), p.check === true ? chip('s-info', 'Check') : null,
            quotes.map(function (q) { return el('span', {class: 'plan-quote'}, '"' + q + '"'); }));
        })) : checks.length ? [el('div', {class: 'field-label'}, 'Checks'),
          el('ol', {class: 'plan-parts'}, checks.map(function (s) { return el('li', {value: nOf(s)}, stepText(s)); }))] :
          el('p', {class: 'muted'}, 'No checks in this version: the engine steps say what it does.'),
        pre.length ? el('div', {class: 'plan-pre'},
          el('div', {class: 'field-label'}, 'Before the run, this test takes as given'),
          el('ul', {}, pre.map(function (p) {
            const mine = fromPerson(p);
            return preItem(p, mine ? 'msg msg-warn' : null, mine ? [' ', chip('s-warn', 'Set by you')] : null);
          }))) : null,
        steps.length ? el('details', {class: 'plan-steps'}, el('summary', {}, 'All engine steps (' + steps.length + ')'),
          el('ol', {}, steps.map(function (s) { return el('li', {class: 'break', value: nOf(s)}, stepText(s)); })),
          el('div', {class: 'break'}, el('span', {class: 'strong'}, 'Expected: '),
            c.expected ? compact(c.expected, 2000) : 'not set in this version (see the step asserts)')) :
          el('p', {class: 'muted'}, 'No steps in this version.'));
    }, function () {
      put(slot, el('p', {class: 'muted'}, 'The steps did not load: see ', engineCaseLink(pc), '.'));
    });
    return slot;
  }

  function checkResult(t, r, wv) {
    const res = r.result || {};
    const rows = list(res.targets);
    const stale = !res.checked_at || Date.now() - new Date(res.checked_at).getTime() > CHECK_FRESH_MS;
    // a check runs once: after a start from it (not one cancelled before the worker took it, or failed before any
    // run), Run needs a new check (writes.start_run refuses a second start on the same devices)
    const started = list(wv && wv.requests).find(function (x) {
      return x.type === 'start' && x.check === r.id && x.state !== 'cancelled' && x.state !== 'failed';
    });
    // the newest Run from this check, when it stopped: its last note stays above Run
    const last = list(wv && wv.requests).find(function (x) {
      return x.type === 'start' && x.check === r.id && x.state !== 'cancelled';
    });
    const stopped = last && last.state === 'failed' ? last : null;
    const boxes = {};
    // a New test plan without a case: the devices are fine, the plan is what is missing
    const emptyPlan = Boolean(r.intake) && r.state === 'done' && !list(res.plan_cases).length;
    // in a New test plan only the devices a plan case runs on can be ticked
    const planned = function (tag) {
      return !r.intake || list(res.plan_cases).some(function (c) { return list(c.targets).indexOf(tag) >= 0; });
    };
    const tbody = el('tbody', {}, rows.map(function (row) {
      const usable = row.ok && !stale && planned(row.tag);
      const cb = el('input', {type: 'checkbox', checked: usable, disabled: !usable, 'aria-label': 'Run on ' + row.tag});
      boxes[row.tag] = cb;
      return el('tr', {},
        el('td', {class: 'min'}, el('label', {class: 'check-device'}, cb, el('span', {class: 'strong'}, row.tag))),
        el('td', {class: 'min'}, mark(row.connected)), el('td', {class: 'min'}, mark(row.account)),
        el('td', {}, el('span', {class: 'row'}, mark(row.app_ok), row.app ? el('span', {class: 'small code break'}, row.app) : null)),
        el('td', {class: 'break'}, !row.ok ? row.needs || '' : planned(row.tag) || emptyPlan ?
          el('span', {class: 'ok-mark'}, 'Ready') : el('span', {class: 'muted'}, 'Ready · not used by this plan')));
    }));
    const total = Math.max(3, rows.length);
    const noPlan = r.prepare === true && !res.summary;
    const start = el('button', {type: 'button', class: 'btn'}, 'Run');
    const err = el('span', {class: 'inline-error', role: 'alert'});
    const picked = function () { return rows.filter(function (x) { return boxes[x.tag].checked; }).map(function (x) { return x.tag; }); };
    // something must run on the ticked devices: a ready case, or a pending case the attached files complete; in a
    // New test plan, one of its plan cases
    const done = list(res.complete);
    const runs = function (tags) {
      const on = function (c) { return list(c.targets).some(function (x) { return tags.indexOf(x) >= 0; }); };
      if (r.intake) return list(res.plan_cases).some(on);
      return list(r.cases).some(on) || list(r.pending).some(function (c) {
        return on(c) && list(c.engine_cases).some(function (e) { return done.indexOf(e) >= 0; });
      });
    };
    // a New test plan's parts of the description it does not test (the worker's not_covered): listed above Run, which
    // then says "Run anyway"; a plan without a case lists them too (review 2026-11-03) and keeps "Nothing to run"
    const notCovered = r.intake ?
      list(res.not_covered).filter(function (x) { return typeof x === 'string' && x; }) : [];
    const runWord = notCovered.length ? 'Run anyway' : 'Run';
    // a blocking question of the worker's keeps Run off until a new plan with the answers replaces this one
    const blocked = runBlocked(r);
    const update = function () {
      const tags = picked();
      start.disabled = Boolean(started) || stale || noPlan || blocked || !tags.length || !runs(tags);
      start.textContent = started ? 'Already run' : blocked ?
        (followUps(r, wv).live ? 'Answered: use the newer plan' : 'Answer the questions first') :
        emptyPlan ? 'Nothing to run' : !tags.length ? runWord :
        !runs(tags) ? 'Nothing runs on these devices' :
        runWord + ' on ' + tags.join(', ') + ' (' + tags.length + ' of ' + total + ' devices)';
    };
    for (const tag of Object.keys(boxes)) boxes[tag].addEventListener('change', update);
    update();
    start.addEventListener('click', async function () {
      start.disabled = true;
      err.textContent = '';
      try {
        const s = await sendJSON('POST', '/api/worker/start', {check: r.id, targets: picked()});
        // the Results page shows the run live, then its result (2026-11-03); the progress screen stays for old links
        goTo(s.task ? routeHash('results', {task: s.task}) : reqHash(t, 'progress', s.id));
      } catch (e) {
        err.textContent = failText(e);
        update();
      }
    });
    const ready = rows.filter(function (x) { return x.ok; }).length;
    const again = r.intake ? 'prepare again' : 'check the devices again';
    return el('div', {},
      r.prepare ? planCard(r, wv) : null,
      needsCard(t, r, wv),
      el('section', {class: 'card'},
        el('h2', {}, 'Devices'),
        el('p', {}, ready + ' of ' + rows.length + (rows.length === 1 ? ' device is' : ' devices are') + ' ready' +
          (res.checked_at ? ' (checked ' + fmtTime(res.checked_at) + ').' : '.')),
        stale ? msgBox('warn', 'This check is older than 30 minutes. ' + again.charAt(0).toUpperCase() + again.slice(1) +
          ' before running.') : null,
        started ? msgBox('info', ['Already run on ' + list(started.targets).join(', ') + ' as ' + started.id +
          '; ' + again + ' to run again. ', el('a', {href: reqHash(t, 'progress', started.id)}, 'See the run')]) : null,
        el('div', {class: 'table-wrap'}, el('table', {},
          el('thead', {}, el('tr', {}, el('th', {}, 'Device'), el('th', {}, 'Connected'), el('th', {}, 'Account'),
            el('th', {}, 'App on the device'), el('th', {}, 'Needed'))), tbody)),
        res.note ? el('p', {class: 'small muted break'}, res.note) : null,
        el('p', {class: 'small muted'}, r.intake ? 'Run is your approval: Hover runs exactly this plan on the ticked ' +
          'devices and does not ask again. Steps that need your hands stop as BLOCKED.' :
          r.prepare ? 'Run is your approval: Hover runs exactly this summary on the ' +
          'ticked devices and does not ask again. Steps that need your hands or your OK stop as BLOCKED.' :
          'Run is your approval: Hover runs the cases from the run overview on the ticked devices and does not ' +
          'ask again. Steps that need your hands or your OK stop as BLOCKED.'),
        stopped ? msgBox('error', ['The last Run stopped: ' + (lastNote(stopped) || 'no reason was given') + ' ',
          el('a', {href: reqHash(t, 'progress', stopped.id)}, 'See the run')]) : null,
        emptyPlan ? msgBox('warn', 'Nothing to run: the plan has no test case. ' + (planNeeds(r).length ?
          'Hover needs your input above.' : 'The devices are ready; "What I will do" above says what the test ' +
          'needs.')) : null,
        notCovered.length ? msgBox('warn', [el('div', {class: 'msg-head'}, 'Not covered by this run:'),
          el('ul', {class: 'msg-lines'}, notCovered.map(function (x) { return el('li', {class: 'break'}, x); }))]) : null,
        el('div', {class: 'form-actions'}, start, editButton(t, r),
          checkAgainButton(t, r.intake ? 'Prepare again' : 'Check devices again', r), err)));
  }

  // ---- New test plan: the worker's questions and the person's answers (DESIGN.md 8.14) ----

  const ANSWER_TEXT_MAX = 1000;  // worker.py: the text of one answer
  const ANSWER_LINE_MAX = 80;  // "Your answers" cuts the question and the answer to this

  // The Plan screen's state outside its redrawn body, set by checkScreen: the answers (need id -> {choice}, {text} or
  // {file: {name, sha256}}), the error slot and the screen's one files box (testFilesBox adds page-wide handlers).
  let planPage = null;

  // The worker's questions on a plan (result.needs).
  function planNeeds(r) {
    return list(r && r.result && r.result.needs).filter(function (n) { return n && typeof n === 'object'; });
  }

  // Run stays off while a New test plan has a blocking question (the server refuses Start too). Answers reach the
  // worker as a new plan, so a question stays open on this one.
  function runBlocked(r) {
    return Boolean(r && r.intake) && planNeeds(r).some(function (n) { return n.blocking === true; });
  }

  // The body of POST /api/worker/answer: one entry per answered question, in the plan's order. An answer that does not
  // fit its question (an unknown option, a file or text for another kind, a blank text) is left out.
  function answersBody(r, answers) {
    const out = [];
    for (const n of planNeeds(r)) {
      const a = answers.get(n.id) || {};
      if (a.choice && list(n.options).some(function (o) { return o && o.id === a.choice; })) {
        out.push({need: n.id, choice: a.choice});
      } else if (a.file && n.kind === 'file') {
        out.push({need: n.id, file: {name: a.file.name, sha256: a.file.sha256}});
      } else if (typeof a.text === 'string' && a.text.trim() && n.kind === 'text') {
        out.push({need: n.id, text: a.text.trim()});
      }
    }
    return {check: r.id, answers: out};
  }

  // A follow-up plan's answers, one line each: the question and the option, file name or text, both cut to 80
  // characters. prev is the answered plan; when it is not in the worker's list, the question shows as its id.
  function answerLines(r, prev) {
    const needs = planNeeds(prev);
    const line = function (s) { return compact(String(s || '').replace(/\s+/g, ' ').trim(), ANSWER_LINE_MAX); };
    return list(r && r.answers).filter(function (a) { return a && typeof a === 'object'; }).map(function (a) {
      const n = needs.find(function (x) { return x.id === a.need; }) || {};
      const opt = list(n.options).find(function (o) { return o && o.id === a.choice; }) || {};
      return {need: line(n.text || a.need), answer: line(a.file ? a.file.name : a.choice ? opt.label || a.choice : a.text)};
    });
  }

  function yourAnswers(r, wv) {
    const rows = answerLines(r, list(wv && wv.requests).find(function (x) { return x.id === r.follows; }));
    if (!rows.length) return null;
    return el('div', {class: 'plan-guidance'}, el('div', {class: 'field-label'}, 'Your answers'),
      el('ul', {class: 'small'}, rows.map(function (x) {
        return el('li', {class: 'break'}, el('span', {class: 'muted'}, x.need), ' → ', x.answer);
      })));
  }

  // The requests that follow a plan: the live one (cancelled and failed ones do not count, as on the server) and the
  // newest dead one, whose answers can be sent again.
  function followUps(r, wv) {
    const all = list(wv && wv.requests).filter(function (x) { return x && x.follows === r.id; });
    const dead = function (x) { return x.state === 'cancelled' || x.state === 'failed'; };
    const byNew = function (a, b) { return String(b.created || '').localeCompare(String(a.created || '')); };
    return {live: all.find(function (x) { return !dead(x); }) || null, dead: all.filter(dead).sort(byNew)[0] || null};
  }

  // A newer plan of the same test that does not follow this one (Prepare again or Edit made it): this one is old.
  function newerPlan(r, wv) {
    const tid = (r.clickup || {}).task_id;
    return list(wv && wv.requests).find(function (x) {
      return x && x.id !== r.id && x.type === 'check' && x.intake === true && (x.clickup || {}).task_id === tid &&
        String(x.created || '') > String(r.created || '') && x.follows !== r.id &&
        x.state !== 'cancelled' && x.state !== 'failed';
    }) || null;
  }

  // "a, b or c"
  function orList(items) {
    const xs = list(items).map(String);
    return xs.length < 2 ? xs.join('') : xs.slice(0, -1).join(', ') + ' or ' + xs[xs.length - 1];
  }

  // "Needs your input", right after "What I will do": a row per question with its option buttons, an attached file or a
  // text, and Send answers, which asks the worker for a new plan with the answers and opens it.
  function needsCard(t, r, wv) {
    const needs = planNeeds(r);
    const page = planPage;
    if (!r.intake || !needs.length || !page || page.rid !== r.id) return null;
    const answers = page.answers;
    const now = function (n) { return answers.get(n.id) || {}; };
    const blocking = needs.filter(function (n) { return n.blocking === true; }).length;
    // answered already (the newer plan holds the answers), or replaced by a newer plan of this test
    const follow = followUps(r, wv);
    const newer = follow.live || newerPlan(r, wv);
    // answers that did not reach a plan (the follow-up was cancelled or failed) come back to be sent again
    if (!newer && follow.dead && !answers.size) {
      for (const a of list(follow.dead.answers)) {
        if (!a || typeof a.need !== 'string') continue;
        answers.set(a.need, a.file ? {file: a.file} : a.choice ? {choice: a.choice} : {text: String(a.text || '')});
      }
    }
    const send = el('button', {type: 'button', class: blocking ? 'btn' : 'btn btn-secondary'}, 'Send answers');
    const marks = [];  // each row's redraw from the answers
    let sending = false;
    const update = function () {
      for (const f of marks) f();
      const over = Array.from(answers.values()).some(function (a) { return jsLen(a.text) > ANSWER_TEXT_MAX; });
      send.disabled = sending || Boolean(newer) || over || Boolean(page.files && page.files.uploading.length) ||
        !answersBody(r, answers).answers.length;
    };
    const answer = function (n, a) {
      if (a) answers.set(n.id, a);
      else answers.delete(n.id);
      update();
    };
    // one files box per screen, kept across redraws: it shows the uploads and refreshes the file lists here
    const hasFile = needs.some(function (n) { return n.kind === 'file'; });
    if (hasFile && !page.files) {
      page.files = {attached: [], uploading: [], info: null};
      page.box = testFilesBox(t, page.files, function () { if (page.onFiles) page.onFiles(); },
        {button: 'Send answers', fieldLabel: 'Files attached to this test', noRemove: true});
    }
    if (hasFile) page.onFiles = update;
    // the attached files a file question takes: an extension from its accept list, and a checksum
    const fitting = function (n) {
      const exts = list(n.accept).map(function (x) { return String(x).toLowerCase(); });
      return list(page.files && page.files.attached).filter(function (f) {
        const name = String(f.name).toLowerCase();
        return typeof f.sha256 === 'string' && exts.some(function (x) { return name.endsWith(x); });
      });
    };
    const fileField = function (n) {
      const sel = el('select', {'aria-label': 'Attached file'});
      // the chooser itself stays out of sight and out of the tab order; the button opens it
      const pick = el('input', {type: 'file', class: 'sr-only', tabindex: '-1', 'aria-hidden': 'true',
        accept: list(n.accept).join(',')});
      const attach = el('button', {type: 'button', class: 'btn btn-secondary btn-small'}, 'Attach a file');
      attach.addEventListener('click', function () { pick.click(); });
      // the file uploads in pieces in the files box below; once it is attached, the list here shows it
      pick.addEventListener('change', function () {
        page.files.add(pick.files);
        pick.value = '';
      });
      // choosing a file clears a pressed option, and an option clears the file
      sel.addEventListener('change', function () {
        const f = fitting(n).find(function (x) { return x.name === sel.value; });
        answer(n, f ? {file: {name: f.name, sha256: f.sha256}} : null);
      });
      marks.push(function () {
        const files = fitting(n);
        const a = now(n);
        // a chosen file that was removed or replaced is no longer an answer
        if (a.file && !files.some(function (f) { return f.name === a.file.name && f.sha256 === a.file.sha256; })) {
          answers.delete(n.id);
        }
        put(sel, el('option', {value: ''}, files.length ? 'Choose an attached file' :
          'No ' + orList(n.accept) + ' file attached yet'),
        files.map(function (f) { return el('option', {value: f.name}, f.name); }));
        sel.disabled = !files.length;
        sel.value = now(n).file ? now(n).file.name : '';
      });
      return el('div', {class: 'need-file row'}, sel, attach, pick);
    };
    const textField = function (n) {
      const box = el('textarea', {rows: 3, 'aria-label': 'Your answer', value: now(n).text || ''});
      const counter = counterEl(0, ANSWER_TEXT_MAX);
      const count = bindCounter(box, counter, ANSWER_TEXT_MAX);
      // typing replaces a pressed option; pressing an option empties the box
      box.addEventListener('input', function () {
        if (box.value.trim()) answer(n, {text: box.value});
        else if (!now(n).choice) answer(n, null);
      });
      marks.push(function () {
        if (now(n).choice && box.value) {
          box.value = '';
          count();
        }
      });
      return [box, el('p', {class: 'small muted row'}, 'Up to ' + ANSWER_TEXT_MAX + ' characters.', counter)];
    };
    const rows = needs.map(function (n) {
      const buttons = list(n.options).filter(function (o) { return o && typeof o.id === 'string'; }).map(function (o) {
        const b = el('button', {type: 'button', class: 'btn btn-secondary btn-small need-opt'},
          String(o.label || o.id));
        // one option at a time; pressing the pressed one again clears it
        b.addEventListener('click', function () { answer(n, now(n).choice === o.id ? null : {choice: o.id}); });
        marks.push(function () { b.setAttribute('aria-pressed', now(n).choice === o.id ? 'true' : 'false'); });
        return b;
      });
      return el('li', {class: 'need-row'},
        el('div', {class: 'break'}, String(n.text || ''),
          n.blocking === true ? null : [' ', chip('s-muted', 'Optional')]),
        n.kind === 'file' ? fileField(n) : n.kind === 'text' ? textField(n) : null,
        buttons.length ? el('div', {class: 'need-options'}, buttons) : null);
    });
    send.addEventListener('click', async function () {
      sending = true;
      update();
      put(page.errSlot);
      try {
        const next = await sendJSON('POST', '/api/worker/answer', answersBody(r, answers));
        goTo(reqHash(t, 'check', next.id));
      } catch (e) {
        sending = false;
        put(page.errSlot, msgBox('error', failText(e)));
        page.errSlot.scrollIntoView({block: 'nearest'});
        update();
      }
    });
    update();
    return el('section', {class: 'card'},
      el('div', {class: 'card-head'}, el('h2', {}, 'Needs your input'),
        blocking ? chip('s-warn', blocking + ' required') : null),
      el('ul', {class: 'list-plain'}, rows),
      hasFile ? page.box : null,
      el('div', {class: 'form-actions'}, send, follow.live ?
        el('span', {class: 'hint'}, 'Already answered: ', el('a', {href: reqHash(t, 'check', newer.id)},
          'open the newer plan'), '.') : newer ?
        el('span', {class: 'hint'}, 'A newer plan of this test exists: ', el('a', {href: reqHash(t, 'check', newer.id)},
          'open it'), '.') : follow.dead ?
        el('span', {class: 'hint'}, 'Your last answers did not reach a plan. Send them again.') :
        el('span', {class: 'hint'}, 'Hover writes a new plan with your answers. Nothing runs yet.')));
  }

  async function progressScreen(root, t, rid, alive) {
    const title = String(t.title || t.task_id);
    const body = el('div', {}, loading('Loading the run…'));
    const errSlot = el('div', {});
    add(root, t.intake ? requestHead(t, routeHash('new'), 'New test', 'Run: ' + title) :
      requestHead(t, routeHash('today', {task: t.task_id}), title, 'Run: ' + title));
    add(root, [errSlot, body]);
    await pollRequest(rid, alive, RUN_POLL_MS,
      function (r, wv, extra) { put(body, progressBody(t, r, wv, extra, errSlot)); },
      async function (r) {
        if (!r || !r.task) return null;
        const got = await Promise.allSettled([getJSON('/api/tasks/' + encodeURIComponent(r.task)), getJSON('/api/live')]);
        const task = got[0].status === 'fulfilled' ? got[0].value : null;
        return {task: task, live: got[1].status === 'fulfilled' ? got[1].value : null,
          keepPolling: Boolean(task && task.state === 'running')};
      });
  }

  function progressBody(t, r, wv, extra, errSlot) {
    if (r === undefined) return msgBox('warn', 'Hover\'s state could not be read. Trying again…');
    if (r === null) return msgBox('warn', 'This run is not in Hover\'s list.');
    const task = extra && extra.task;
    const live = extra && extra.live;
    const cases = list(task && task.cases);
    // this run's devices only, between the claim and the end of the request
    const since = r.claimed_at || r.created || '';
    const until = r.done_at || '9999';
    const progress = list(live && live.progress).filter(function (p) {
      const st = String(p.started || '');
      return cases.indexOf(p.case) >= 0 && list(r.targets).indexOf(p.target) >= 0 && st >= since && st <= until;
    });
    const runIds = list(task && task.run_ids);
    const handbacks = list(live && live.handbacks).filter(function (h) { return runIds.indexOf(h.run) >= 0; });
    const head = el('p', {class: 'row'}, chip(REQ_CLASS[r.state] || 's-muted', 'Run ' + (REQ_STATE[r.state] || r.state)),
      el('span', {class: 'small muted'}, r.id + (r.task ? ' · task ' + r.task : '') + ' · ' + list(r.engine_cases).join(', ') +
        ' on ' + list(r.targets).join(', ')));
    const parts = [head];
    if (r.plan_summary || r.guidance) {
      const lines = String(r.plan_summary || '').split('\n');
      parts.push(el('section', {class: 'card flat'},
        el('p', {class: 'strong'}, 'The plan you approved' + (r.created ? ' at ' + fmtTime(r.created) : '')),
        r.plan_summary ? el('div', {class: 'plan-text'}, lines.slice(0, 2).join('\n')) : null,
        r.guidance ? el('p', {class: 'small muted plan-text'}, (r.intake ? 'Your description' : 'Your guidance') + ': "' +
          String(r.guidance) + '"') : null,
        list(r.plan_files).length ? el('p', {class: 'small muted'}, 'Test files: ' + list(r.plan_files).map(function (x) {
          return x.name + ' (' + x.case + ')';
        }).join(', ')) : null,
        lines.length > 2 ? el('details', {}, el('summary', {}, 'See the full summary'),
          el('div', {class: 'prewrap'}, String(r.plan_summary))) : null));
    }
    if (r.state === 'new') parts.push(workerLine(wv), el('div', {class: 'form-actions'}, cancelButton(r, errSlot)));
    if (r.state === 'claimed') parts.push(stuckRelease(r, wv, errSlot));
    if (isOpen(r)) {
      parts.push(el('p', {class: 'small muted'}, 'You can leave this page. A "results ready" notification appears on ' +
        'this Mac when the run ends.'));
    }
    if (r.state === 'failed') parts.push(msgBox('error', lastNote(r) || 'The run stopped.'));
    if (task && task.state === 'results_ready' || (task && ['greenlit', 'drafted'].indexOf(task.state) >= 0)) {
      parts.push(el('div', {class: 'msg msg-ok row'},
        el('span', {class: 'strong'}, 'Results ready (' + list(r.targets).length + ' of 3 devices)'),
        el('a', {class: 'btn', href: routeHash('results', {task: task.id})}, 'Open in Results & Tickets')));
    }
    if (handbacks.length) {
      parts.push(el('section', {class: 'card flat'}, el('h2', {}, 'Needs your OK'),
        el('p', {class: 'small muted'}, 'The engine refuses these steps inside a run and hands them back. Hover ' +
          'never runs them; approve them in a Claude session in this folder.'),
        el('ul', {class: 'list-plain'}, handbacks.map(function (h) {
          return el('li', {}, el('div', {class: 'row'}, badge('', h.target || '?'), resultsLink(h.case, h.run),
            h.step !== undefined && h.step !== null ? el('span', {class: 'small'}, 'step ' + compact(h.step, 80)) : null),
          h.reason ? el('div', {class: 'small break'}, h.reason) : null);
        }))));
    }
    if (progress.length) {
      parts.push(el('div', {class: 'grid'}, progress.map(function (p) {
        return el('section', {class: 'card flat device-card'}, el('div', {class: 'card-head'},
          el('h3', {}, p.target || p.tag || p.lock || 'device')), progressBlock(p));
      })));
    } else if (r.state === 'claimed') {
      parts.push(el('p', {class: 'small muted'}, r.intake ? 'No device has started yet. Hover writes its ' +
        'predictions first.' : 'No device has started yet. Before a run Hover completes draft cases from source ' +
        'and writes its predictions, which can take a while.'));
    }
    const done = list(task && task.runs).filter(function (x) { return x && x.run; });
    if (done.length) {
      // devices from the device check that this run left out, with what they needed
      const check = list(wv && wv.requests).find(function (x) { return x.id === r.check; });
      const left = list(check && check.result && check.result.targets).filter(function (x) {
        return list(r.targets).indexOf(x.tag) < 0;
      });
      // in a New test run, a device none of the plan's cases runs on was never there to choose
      const inPlan = function (tag) {
        return list(check && check.result && check.result.plan_cases).some(function (pc) {
          return list(pc.targets).indexOf(tag) >= 0;
        });
      };
      parts.push(el('div', {}, el('h2', {}, 'Verdicts'), el('ul', {class: 'list-plain'}, done.map(function (x) {
        // a corrected run shows its correction (the newest record), as the greenlight card does
        return el('li', {class: 'row'}, verdictChip(x.verdict), badge('', x.target || '?'), resultsLink(x.case, x.run, task.id),
          x.corrects ? el('span', {class: 'small muted'}, 'corrects ' + x.corrects) : null);
      }).concat(left.map(function (x) {
        return el('li', {class: 'row'}, chip('s-muted', 'NOT RUN'), badge('', x.tag),
          el('span', {class: 'small muted'}, r.intake && !inPlan(x.tag) ? 'no case in this plan runs here' :
            x.ok ? 'not chosen' : String(x.needs || 'not ready')));
      })))));
    }
    parts.push(el('h2', {}, 'Hover\'s notes'), notesList(r) || el('p', {class: 'small muted'}, 'No notes yet.'),
      workerLink(wv && wv.worker) ? el('p', {class: 'small'}, workerLink(wv.worker)) : null);
    return parts;
  }

  // ---- New test: the person's ClickUp link, description, files and devices; the worker answers with a plan ----

  // #/new is the form and "Your plans"; ?edit=<W-id> fills the form from an earlier plan. ?show=plan|progress&req=<W-id>
  // are the plan and run screens, shared with Today's (checkScreen, progressScreen).
  async function viewNew(root, params, alive) {
    const show = params.get('show');
    const rid = params.get('req');
    if ((show === 'plan' || show === 'progress') && rid) {
      if (show === 'plan') root.classList.add('narrow');
      add(root, [loading()]);
      const wv = await readWorker();
      if (!alive()) return;
      clear(root);
      const req = list(wv && wv.requests).find(function (x) { return x.id === rid; }) || {};
      const cu = req.clickup || {};
      // the plan's title, which Run approves with the plan (the server's "title", 2026-11-04); the ClickUp name in grey
      const t = {task_id: cu.task_id || '', title: req.title || cu.title || cu.task_id || rid, clickup: cu, intake: true};
      await (show === 'plan' ? checkScreen : progressScreen)(root, t, rid, alive);
      return;
    }
    root.classList.add('narrow');
    add(root, [viewHead('New test'), loading()]);
    const got = await Promise.allSettled([readWorker(), getJSON('/api/results/tests'), app.metaPromise]);
    if (!alive()) return;
    clear(root);
    const wv = today.worker;
    const editId = params.get('edit');
    const prev = editId ? list(wv && wv.requests).find(function (r) { return r.id === editId && r.type === 'check'; }) : null;
    add(root, [viewHead('New test'), tokenWarning(),
      editId && !prev ? msgBox('warn', (wv ? 'That plan is no longer in Hover\'s list' : 'Hover\'s list ' +
        'could not be read') + ', so the form starts empty.') : null,
      newForm(prev, alive),
      yourPlans(wv, got[1].status === 'fulfilled' ? got[1].value : null)]);
  }

  // S1: 1. the ClickUp test (dropped or pasted, read through /api/intake/resolve), 2. what to test, 3. test files once
  // the task is known, 4. devices. Prepare plan asks the worker for a plan; nothing runs before Run on the plan screen.
  function newForm(prev, alive) {
    const cu = (prev && prev.clickup) || {};
    const registered = list(app.meta && app.meta.targets).map(function (x) { return x && x.tag; });
    let intake = null;  // the task the link names, as /api/intake/resolve read it
    let asked = '';  // the link last sent to be read
    let seq = 0;
    let files = null;  // the files box's state, for intake's task
    const input = el('input', {type: 'url', id: 'new-link', autocomplete: 'off', value: cu.url || cu.custom_id || cu.task_id || '',
      placeholder: 'https://app.clickup.com/t/… or CL-12345'});
    const found = el('div', {class: 'link-found'});
    const what = el('textarea', {id: 'new-what', rows: 5, value: prev && prev.guidance ? String(prev.guidance) : ''});
    const counter = counterEl(0, GUIDANCE_MAX);
    const filesSlot = el('div', {});
    const btn = el('button', {type: 'button', class: 'btn'}, 'Prepare plan');
    const err = el('span', {class: 'inline-error', role: 'alert'});
    const boxes = {};
    const status = {};  // the live status line under each device (/api/devices)
    const touched = {};  // devices the person ticked or unticked themselves
    const wanted = function (tag) { return prev ? list(prev.targets).indexOf(tag) >= 0 : true; };
    const grid = el('div', {class: 'device-grid'}, TAG_ORDER.map(function (tag) {
      const known = !registered.length || registered.indexOf(tag) >= 0;
      const cb = el('input', {type: 'checkbox', disabled: !known, checked: known && wanted(tag)});
      status[tag] = el('span', {class: 'device-status small muted'}, known ? 'Checking…' : 'Not set up on this Mac');
      if (known) {
        boxes[tag] = cb;
        cb.addEventListener('change', function () { touched[tag] = true; update(); });
      }
      return el('label', {class: 'device-choice' + (known ? '' : ' off')}, cb, el('span', {class: 'strong'}, tag),
        el('span', {class: 'small muted'}, tag === 'W044' ? 'web' : 'tablet'), status[tag]);
    }));
    // each device's live state from the engine while this page shows: a device that is not ready cannot be ticked;
    // one that becomes ready is ticked again unless the person changed it. An unreadable status leaves the boxes.
    const showDevices = function (d) {
      for (const row of list(d && d.rows)) {
        const cb = boxes[row.tag];
        if (!cb) continue;
        status[row.tag].textContent = row.text;
        status[row.tag].className = 'device-status small ' + (row.ready ? 'ok' : row.state === 'unknown' ? 'muted' : 'warn');
        if (row.state === 'unknown') continue;
        if (!row.ready) {
          cb.checked = false;
          cb.disabled = true;
        } else if (cb.disabled) {
          cb.disabled = false;
          if (!touched[row.tag]) cb.checked = wanted(row.tag);
        }
      }
      update();
    };
    let shown = false;
    let pollTimer = null;
    const poll = async function () {
      const inPage = document.body.contains(grid);
      if (!alive() || (shown && !inPage)) return;  // the page was left: stop
      shown = shown || inPage;
      if (inPage) {
        try {
          showDevices(await getJSON('/api/devices'));
        } catch (e) {
          for (const tag of Object.keys(boxes)) {
            status[tag].textContent = 'Could not read the devices just now';
            status[tag].className = 'device-status small muted';
          }
        }
      }
      pollTimer = setTimeout(poll, inPage ? DEVICE_POLL_MS : 200);
    };
    setTimeout(poll, 0);
    const ticked = function () { return TAG_ORDER.filter(function (tag) { return boxes[tag] && boxes[tag].checked; }); };
    const uploading = function () { return Boolean(files && files.uploading.length); };
    // the link cannot change while a file uploads to its test; a blank description is none (writes.guidance_text)
    const update = function () {
      input.disabled = uploading();
      const n = jsLen(what.value);
      btn.disabled = !intake || input.value.trim() !== asked || !what.value.trim() || n > GUIDANCE_MAX || !ticked().length ||
        uploading();
    };
    const filesOff = el('div', {class: 'test-files'}, el('div', {class: 'step-label'}, '3. Test files (optional)'),
      el('div', {class: 'drop-zone off'}, el('span', {class: 'strong'}, 'Drop files here or choose files'),
        el('span', {class: 'small muted'}, 'Open the ClickUp test first: the files are kept with that test.')));
    // the files box follows the task: a new task gets its own box, and the old one's uploads and handlers stop
    const setIntake = function (x) {
      intake = x;
      const id = x ? x.task_id : null;
      if (!files || files.task !== id) {
        if (files && files.stop) files.stop();
        files = null;
        if (id) {
          const mine = {attached: [], uploading: [], info: null, task: id};
          files = mine;
          put(filesSlot, testFilesBox({task_id: id}, mine, function () { if (files === mine) update(); },
            {label: '3. Test files (optional)', button: 'Prepare plan'}));
        } else {
          put(filesSlot, filesOff);
        }
      }
      update();
    };
    const resolve = async function (force) {
      const url = input.value.trim();
      if (url === asked && !force) return;
      asked = url;
      const mine = ++seq;
      if (!url) {
        put(found);
        setIntake(null);
        return;
      }
      intake = null;
      put(found, loading('Reading the task from ClickUp…'));
      update();
      try {
        const got = await sendJSON('POST', '/api/intake/resolve', {url: url});
        if (mine !== seq || !alive()) return;
        setIntake(got);
        const n = list(got.cases).length;
        const facts = [got.status, got.priority, n + (n === 1 ? ' case' : ' cases') + ' in the task'].filter(Boolean).join(' · ');
        put(found, el('div', {class: 'found-row'}, el('span', {class: 'ok-mark'}, '✓'),
          el('span', {class: 'strong break'}, String(got.title || got.custom_id || got.task_id)),
          el('span', {class: 'small muted'}, facts.charAt(0).toUpperCase() + facts.slice(1)),
          el('span', {class: 'push-end'}, clickupLink({url: got.url, task_id: 'Open in ClickUp'}))));
      } catch (e) {
        if (mine !== seq || !alive()) return;
        asked = '';  // a failed read is read again when the link is pasted or changed
        setIntake(null);
        put(found, el('p', {class: 'inline-error', role: 'alert'}, failText(e)));
      }
    };
    const zone = el('div', {class: 'link-zone'}, el('span', {class: 'strong'}, 'Drop the test\'s link here, or paste it'),
      input);
    zone.addEventListener('dragover', function (e) {
      e.preventDefault();
      zone.classList.add('over');
    });
    zone.addEventListener('dragleave', function () { zone.classList.remove('over'); });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      e.stopPropagation();  // not a test file: the files box's page-wide drop must not take it
      zone.classList.remove('over');
      if (input.disabled) return;
      const dt = e.dataTransfer;
      const uri = dt ? lines(dt.getData('text/uri-list')).filter(function (x) { return x.charAt(0) !== '#'; })[0] : '';
      const text = uri || (dt ? String(dt.getData('text/plain') || '').trim() : '');
      if (!text) {
        put(found, el('p', {class: 'inline-error', role: 'alert'}, 'Drop the test\'s ClickUp link here; files go under ' +
          'Test files.'));
        return;
      }
      input.value = text;
      resolve(true);
    });
    input.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      resolve(true);
    });
    input.addEventListener('change', function () { resolve(false); });
    input.addEventListener('paste', function () { setTimeout(function () { resolve(false); }, 0); });
    input.addEventListener('input', update);
    bindCounter(what, counter, GUIDANCE_MAX);
    what.addEventListener('input', update);
    // until the files box is there, a link or file dropped beside the zones must not open in the tab (leaving the app)
    const onDragOver = function (e) { e.preventDefault(); };
    const onRoute = function () {
      clearTimeout(pollTimer);
      window.removeEventListener('dragover', onDragOver);
      window.removeEventListener('drop', onDragOver);
      window.removeEventListener('hashchange', onRoute);
    };
    window.addEventListener('dragover', onDragOver);
    window.addEventListener('drop', onDragOver);
    window.addEventListener('hashchange', onRoute);
    btn.addEventListener('click', async function () {
      btn.disabled = true;
      err.textContent = '';
      try {
        const r = await sendJSON('POST', '/api/worker/check',
          {intake: true, task_id: intake.task_id, guidance: what.value, targets: ticked(), prepare: true});
        go('new', {show: 'plan', req: r.id});
      } catch (e) {
        err.textContent = failText(e);
        update();
      }
    });
    setIntake(null);
    if (input.value.trim()) resolve(true);
    const wl = workerLine(today.worker);
    wl.classList.add('push-end');
    return el('section', {class: 'card new-form'},
      el('div', {class: 'new-step'},
        el('label', {class: 'step-label', for: 'new-link'}, '1. ClickUp test'),
        zone, found,
        el('p', {class: 'small muted'}, 'Hover reads the task (read only). Any test, from any list.')),
      el('div', {class: 'new-step'},
        el('label', {class: 'step-label', for: 'new-what'}, '2. What to test'),
        what,
        el('p', {class: 'small muted row'}, 'Your words decide what is checked and what counts as a pass. Hover uses an ' +
          'existing test case if one fits, or writes one from this. Up to 1000 characters.', counter)),
      filesSlot,
      el('fieldset', {class: 'device-choices'}, el('legend', {class: 'step-label'}, '4. Devices'), grid,
        el('p', {class: 'small muted'}, 'Live from the devices, refreshed every few seconds: a device becomes tickable as ' +
          'soon as it is ready. The engine account and the app build are checked when you press Prepare plan.')),
      el('div', {class: 'form-actions'}, btn,
        el('span', {class: 'hint'}, 'Nothing runs yet. Next you see the plan to approve.'), err, wl));
  }

  // "Your plans" (2026-11-04; it was "Your tests"): the newest ten plans, from the worker's requests (two days, and
  // every open one). A plan (the newest of its ClickUp task) shows until a run starts from it, a run until it ends; then
  // the test is on Results and in History's "Your tests", which the line under the list counts. A plan or Run that
  // stopped, a plan with nothing to run and an expired plan stay listed as such. Each row is titled by the server
  // (GET /api/worker "title": the plan's title, else the description cut), the ClickUp name in grey.
  function yourPlans(wv, tests) {
    const reqs = list(wv && wv.requests);
    const rows = [];
    const item = function (cls, label, r, devices, at, href, note) {
      const cu = r.clickup || {};
      const title = String(r.title || cu.title || cu.task_id || r.id);
      const sub = [cu.title ? String(cu.title) : '', devices].filter(Boolean).join(' · ');
      rows.push({at: new Date(at || 0).getTime() || 0, node: el('li', {class: 'test-row'}, chip(cls, label),
        el('span', {class: 'strong break'}, title),
        el('a', {class: 'push-end', href: href}, 'Open ›'),
        el('span', {class: 'small muted plan-sub'}, sub ? sub + ' · ' : '', timeEl(at)),
        note ? el('span', {class: 'small break plan-sub'}, note) : null)});
    };
    const seen = {};
    for (const r of reqs) {
      const cu = r.clickup || {};
      const devices = list(r.targets).join(', ');
      if (r.type === 'check' && r.intake === true) {
        // a check cancelled before Hover took it made no plan: it hides no older plan of its task (worker.newer_plan
        // skips it too, so that plan can still run)
        if (r.state === 'cancelled') continue;
        // the requests come newest first: only the newest check of a ClickUp task shows
        if (seen[cu.task_id]) continue;
        seen[cu.task_id] = true;
        // its newest start (one cancelled before the worker took it ran nothing): open, it is the Running row below;
        // done, the test is on Results and in History; failed (perhaps before any run, so the run screen with its
        // notes, not Results), the plan can run again
        const s = reqs.find(function (x) { return x.type === 'start' && x.check === r.id && x.state !== 'cancelled'; });
        const plan = routeHash('new', {show: 'plan', req: r.id});
        const at = (r.result || {}).checked_at || r.done_at;
        if (isOpen(r)) item('s-warn', 'Preparing plan', r, devices, r.created, plan);
        else if (r.state === 'failed') {
          item('s-bad', 'Plan stopped', r, devices, r.done_at || r.created, plan, lastNote(r));
        } else if (r.state === 'done' && s && s.state === 'failed') {
          item('s-bad', 'Run stopped', r, list(s.targets).join(', '), s.done_at || s.created,
            routeHash('new', {show: 'progress', req: s.id}), lastNote(s));
        } else if (r.state === 'done' && !s) {
          if (runBlocked(r) || (!list((r.result || {}).plan_cases).length && planNeeds(r).length)) {
            item('s-warn', followUps(r, wv).live ? 'Answered' : 'Needs your input', r, devices, at, plan);
          } else if (!list((r.result || {}).plan_cases).length) item('s-muted', 'Nothing to run', r, devices, at, plan);
          else if (Date.now() - new Date(at || 0).getTime() > CHECK_FRESH_MS) {
            item('s-muted', 'Plan expired', r, devices, at, plan);
          } else item('s-ok', 'Plan ready', r, devices, at, plan);
        }
      } else if (r.type === 'start' && isOpen(r)) {
        // a Run on its way: the test shows live on Results
        item('s-info', 'Running', r, devices, r.created, r.task ? routeHash('results', {task: r.task}) :
          routeHash('new', {show: 'progress', req: r.id}));
      }
    }
    rows.sort(function (a, b) { return b.at - a.at; });
    // the tests that ran wait in History's "Your tests" (GET /api/results/tests, its Tested group)
    const waiting = tests && tests.tested && typeof tests.tested.count === 'number' ? tests.tested.count : null;
    const history = el('a', {href: routeHash('history')}, waiting === null ? 'see History ›' : waiting ?
      (waiting === 1 ? '1 waits' : waiting + ' wait') + ' for you in History ›' : 'none wait for you. History ›');
    return el('section', {class: 'card'}, el('h2', {}, 'Your plans'),
      wv ? null : msgBox('warn', 'Hover\'s requests could not be read.'),
      rows.length ? el('ul', {class: 'list-plain'}, rows.slice(0, 10).map(function (x) { return x.node; })) :
        el('p', {class: 'small muted'}, 'Nothing yet. A plan shows here once you prepare one.'),
      el('p', {class: 'tests-line'}, 'Tests you ran: ', history));
  }

  // ---- Results & Tickets: the greenlight ----

  async function greenlightSection(sec, alive) {
    const got = await Promise.allSettled([getJSON('/api/tasks'), readWorker()]);
    if (!alive()) return;
    const all = got[0].status === 'fulfilled' ? list(got[0].value.tasks) : [];
    const wv = got[1].status === 'fulfilled' ? got[1].value : null;
    const recent = Date.now() - 7 * 24 * 3600 * 1000;
    const lastAt = function (t) { const h = list(t.history); return h.length ? h[h.length - 1].at : t.created; };
    const waiting = all.filter(function (t) { return t.state === 'results_ready'; });
    const writing = all.filter(function (t) { return t.state === 'greenlit'; });
    const drafted = all.filter(function (t) {
      return t.state === 'drafted' && new Date(lastAt(t) || 0).getTime() >= recent;
    });
    if (got[0].status === 'rejected') {
      put(sec, errorBox(got[0].reason, 'Tasks did not load'));
      return;
    }
    if (!waiting.length && !writing.length && !drafted.length) return;
    const reload = function () { greenlightSection(sec, alive); };
    put(sec, el('section', {class: 'card'},
      el('div', {class: 'card-head'}, el('h2', {}, 'Your greenlight'),
        waiting.length ? chip('s-warn', waiting.length + ' waiting') : null),
      el('p', {class: 'small muted'}, 'Results first: look at the verdicts, then greenlight. Hover then writes the ' +
        'drafts you pick. Nothing is posted to ClickUp.'),
      el('ul', {class: 'list-plain'},
        waiting.map(function (t) { return greenlightItem(t, reload); }),
        writing.map(function (t) { return writingItem(t, wv, reload); }),
        drafted.map(draftedItem))));
  }

  function taskLine(t) {
    return el('div', {class: 'row top'}, el('span', {class: 'code nowrap'}, t.id),
      el('span', {class: 'break'}, compact(t.text || '', 220)));
  }

  function runChips(t) {
    const runs = list(t.run_summary);
    if (!runs.length) return el('p', {class: 'small muted'}, 'No runs recorded.');
    return el('div', {class: 'row'}, runs.map(function (x) {
      return el('span', {class: 'row'}, verdictChip(x.verdict), badge('', x.target || '?'), resultsLink(x.case, x.run, t.id),
        x.corrects ? el('span', {class: 'small muted'}, 'corrects ' + x.corrects) : null);
    }));
  }

  // The outputs picker and its button: Greenlight on results_ready, Write drafts on a greenlit task.
  function draftsControls(t, reload, label) {
    const sum = el('input', {type: 'checkbox', checked: true});
    const com = el('input', {type: 'checkbox', checked: Boolean(t.clickup)});
    const btn = el('button', {type: 'button', class: 'btn'}, label);
    const err = el('span', {class: 'inline-error', role: 'alert'});
    const update = function () { btn.disabled = !sum.checked && !com.checked; };
    sum.addEventListener('change', update);
    com.addEventListener('change', update);
    btn.addEventListener('click', async function () {
      const outputs = [];
      if (sum.checked) outputs.push('summary');
      if (com.checked) outputs.push('comment');
      btn.disabled = true;
      err.textContent = '';
      try {
        await sendJSON('POST', '/api/worker/greenlight', {task: t.id, outputs: outputs});
        reload();
      } catch (e) {
        err.textContent = failText(e);
        update();
      }
    });
    return el('div', {class: 'form-actions'},
      el('label', {class: 'row small'}, sum, 'Summary'),
      el('label', {class: 'row small'}, com, 'Results comment for ClickUp'),
      btn, el('a', {class: 'small', href: routeHash('results', {task: t.id})}, 'See only these results'), err);
  }

  function greenlightItem(t, reload) {
    return el('li', {}, taskLine(t), runChips(t), draftsControls(t, reload, 'Greenlight'));
  }

  function writingItem(t, wv, reload) {
    const reqs = list(wv && wv.requests).filter(function (r) { return r.type === 'write' && r.task === t.id; });
    const req = reqs[0];  // newest first
    const open = req && isOpen(req);
    return el('li', {}, taskLine(t), el('div', {class: 'row small'}, chip('s-info', 'Greenlit'),
      req ? [chip(REQ_CLASS[req.state] || 's-muted', 'Drafts ' + (REQ_STATE[req.state] || req.state)),
        req.state === 'failed' ? el('span', {class: 'inline-error'}, lastNote(req)) : el('span', {class: 'muted'}, lastNote(req))] :
        el('span', {class: 'muted'}, 'No drafts asked for in the app yet.')),
      open ? null : draftsControls(t, reload, 'Write drafts'));
  }

  function draftedItem(t) {
    const out = t.outputs || {};
    const files = [];
    if (out.summary) files.push(el('span', {class: 'row'}, 'Summary:', fileLink(out.summary)));
    for (const rel of list(out.comments)) {
      const copy = el('button', {type: 'button', class: 'btn btn-secondary btn-small'}, 'Copy comment');
      copy.addEventListener('click', async function () {
        copy.disabled = true;
        try {
          const r = await reach(fileUrl(rel), {cache: 'no-store', credentials: 'same-origin'});
          if (!r.ok) throw new Error('HTTP ' + r.status);
          copy.textContent = (await copyText(await r.text())) ? 'Copied' : 'Copy failed: open the file and copy it';
        } catch (e) {
          copy.textContent = 'Copy failed';
        }
        setTimeout(function () { copy.textContent = 'Copy comment'; copy.disabled = false; }, 2000);
      });
      files.push(el('span', {class: 'row'}, 'Comment:', fileLink(rel), copy));
    }
    return el('li', {}, taskLine(t), el('div', {class: 'row small'}, chip('s-ok', 'Drafts written'), files));
  }

  function devicesStrip(liveData) {
    const st = liveData && liveData.status && typeof liveData.status === 'object' ? liveData.status : null;
    if (!st) return el('p', {class: 'small muted'}, 'Device status not available yet.');
    const items = [];
    for (const t of list(st.tablets)) {
      items.push(el('span', {class: 'row'}, el('span', {class: 'strong'}, t.tag || t.serial || 'tablet'),
        deviceStateChip(t.state), t.why ? el('span', {class: 'small muted'}, t.why) : null));
    }
    const w = st.webpc044;
    if (w) items.push(el('span', {class: 'row'}, el('span', {class: 'strong'}, 'W044'), deviceStateChip(w.state)));
    const age = liveData.status_age_s;
    return el('div', {class: 'card flat'}, el('div', {class: 'card-head'}, el('h3', {}, 'Devices'),
      el('span', {class: 'small muted'}, 'status from ' + fmtAge(age) + ' ago')), el('div', {class: 'row'}, items));
  }

  const PREP_LABEL = {ready: 'Ready', needs_input: 'Needs input', blocked: 'Blocked', not_office: 'Not office',
    covered: 'Already covered'};
  const PREP_CLASS = {ready: 's-ok', needs_input: 's-warn', blocked: 's-bad', not_office: 's-muted', covered: 's-info'};

  const BRANCH_LABEL = [['sky', 'develop'], ['sky-staging', 'staging'], ['sky-preproduction', 'preproduction'],
    ['sky-production', 'production']];

  function sentences(items) {
    return list(items).map(function (s) { s = String(s).trim(); return /[.!?]$/.test(s) ? s : s + '.'; }).join(' ');
  }

  function devTicketLine(d) {
    const bits = [d.cl];
    if (d.status) bits.push(d.status);
    const fix = d.fix;
    if (fix && fix.in) {
      const inb = BRANCH_LABEL.filter(function (b) { return b[0] in fix.in; }).map(function (b) {
        return b[1] + (fix.in[b[0]] ? ' ✓' : ' ✗');
      });
      bits.push('fix ' + list(fix.commits).map(function (c) { return String(c).slice(0, 9); }).join(', ') +
        (fix.pr ? ' (' + fix.pr + ')' : '') + ' in: ' + inb.join(', '));
    }
    if (d.note) bits.push(d.note);
    return el('li', {class: 'break'}, bits.join(' · '));
  }

  function engineCasesFor(t, c) {
    return today.cases.filter(function (k) {
      const s = k.requirement_source || {};
      if (s.ref !== t.task_id) return false;
      const b = /bullet\s+(\d+)/i.exec(String(s.locator || ''));
      return b ? Number(b[1]) === c.n : c.n === 1;
    });
  }

  function caseDetails(t, c, pc) {
    const m = c.markers || {};
    const engine = engineCasesFor(t, c);
    const notes = list(c.notes);
    return el('details', {class: 'case-row'},
      el('summary', {},
        el('span', {class: 'nowrap'}, c.n + '. '), el('span', {class: 'break case-text'}, String(c.text || '')),
        pc ? chip(PREP_CLASS[pc.status] || 's-muted', PREP_LABEL[pc.status] || pc.status) : null,
        m.green || (pc && pc.green) ? badge('ev-device', 'you marked passed') : null,
        m.field ? badge('badge-unknown', 'field test') : null,
        m.drone ? badge('', 'needs a drone') : null),
      el('div', {class: 'case-body stack-tight'},
        pc ? prepDetails(pc) : el('p', {class: 'small muted'}, 'No pre-test note for this case.'),
        notes.length ? el('div', {class: 'small'}, el('span', {class: 'strong'}, 'From ClickUp:'),
          el('ul', {}, notes.map(function (n) { return el('li', {class: 'break'}, n); }))) : null,
        el('div', {class: 'row small'}, engine.length ? engine.map(engineCaseBlock) :
          el('span', {class: 'muted'}, today.casesError ? 'Engine cases not read.' : 'No engine case yet.'))));
  }

  function prepDetails(pc) {
    const devices = list(pc.targets).map(function (tag) { return badge('', tag); });
    const missing = list(pc.missing_devices).map(function (d) { return d.tag + ' (' + d.why + ')'; });
    const req = pc.required || {};
    return el('div', {class: 'small stack-tight'},
      list(pc.why).length ? el('div', {class: 'break'}, sentences(pc.why)) : null,
      pc.note ? el('div', {class: 'break muted'}, pc.note) : null,
      list(pc.needs).length ? el('div', {class: 'break'}, el('span', {class: 'strong'}, 'Needs: '), sentences(pc.needs)) : null,
      devices.length || missing.length ? el('div', {class: 'row'}, el('span', {class: 'strong'}, 'Devices:'), devices,
        missing.length ? el('span', {class: 'muted'}, 'missing: ' + missing.join(', ')) : null) : null,
      req.env || req.build_note ? el('div', {class: 'break'}, el('span', {class: 'strong'}, 'Build: '),
        [req.env, req.build_note].filter(Boolean).join(' · ')) : null,
      list(pc.side_effects).length ? el('div', {class: 'break'}, el('span', {class: 'strong'}, 'Creates or changes: '),
        sentences(pc.side_effects)) : null,
      pc.proposal ? el('div', {class: 'break'}, el('span', {class: 'strong'}, 'Proposed case: '), pc.proposal) : null,
      pc.estimate_min ? el('div', {class: 'muted'}, 'About ' + pc.estimate_min + ' min') : null);
  }

  function engineCaseLink(c) {
    return el('a', {href: routeHash('history', {case: c.id}), class: 'code'}, c.id);
  }

  function engineCaseBlock(c) {
    const last = c.last || {};
    const tags = Object.keys(last).sort(function (a, b) {
      return (TAG_ORDER.indexOf(a) + 99) % 99 - (TAG_ORDER.indexOf(b) + 99) % 99;
    });
    return el('span', {class: 'row'}, engineCaseLink(c),
      c.status === 'draft' ? badge('badge-unknown', 'draft') : null,
      tags.length ? tags.map(function (tag) {
        const v = last[tag] || {};
        return el('span', {class: 'row', title: (v.hash7 ? 'build ' + v.hash7 : '') + (v.created ? ' · ' + fmtTime(v.created) : '')},
          el('span', {class: 'small'}, tag), verdictChip(v.verdict));
      }) : el('span', {class: 'muted'}, 'not run yet'));
  }

  // ---- History: what was tested, how it went, the bugs found (DESIGN.md 8.4) ----

  const HIST_DAYS = 30;
  // Stack order bottom to top; colours in app.css (.hv-*), checked for colour-blind separation on 2026-10-23.
  const HIST_VERDICTS = ['PASS', 'PARTIAL', 'BLOCKED', 'FAIL', 'ERROR'];
  const HIST_LABEL = {PASS: 'PASS', PARTIAL: 'PARTIAL', BLOCKED: 'BLOCKED', FAIL: 'FAIL', ERROR: 'Harness error'};
  const HIST_DEVICES = ['TA5', 'ET65', 'W044', 'TA3'];
  const BUG_REGISTER = '/files/qa/history/bugs.json';
  const CLICKUP_TEAM_URL = 'https://app.clickup.com/t/1919191/';
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function svg(tag, attrs) {
    const n = document.createElementNS(SVG_NS, tag);
    for (const k of Object.keys(attrs || {})) {
      if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, String(attrs[k]));
    }
    for (const c of Array.prototype.slice.call(arguments, 2)) {
      if (c !== null && c !== undefined && c !== false) n.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return n;
  }

  function localDay(iso) {
    const d = new Date(iso || '');
    if (isNaN(d.getTime())) return '';
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function shortDay(day) {
    const p = String(day).split('-');
    return p.length === 3 ? Number(p[2]) + ' ' + MONTHS[Number(p[1]) - 1] : String(day);
  }

  function lastDays(n) {
    const out = [];
    const d = new Date();
    d.setHours(12, 0, 0, 0);
    for (let i = n - 1; i >= 0; i--) {
      const x = new Date(d.getTime() - i * 86400000);
      out.push(x.getFullYear() + '-' + pad(x.getMonth() + 1) + '-' + pad(x.getDate()));
    }
    return out;
  }

  async function readBugRegister() {
    const r = await reach(BUG_REGISTER, {cache: 'no-store', credentials: 'same-origin'});
    if (r.status === 404) return {bugs: [], missing: true};
    const d = await readBody(r);
    return {bugs: list(d.bugs).filter(function (b) { return b && typeof b === 'object'; }), updated: d.updated || null};
  }

  // One row per run record that stands (corrections replace the run they supersede); harness self-tests left out.
  function historyRuns(results) {
    const out = [];
    for (const c of list(results && results.cases)) {
      if (c.harness) continue;
      for (const r of list(c.rows)) {
        if (r.harness || r.superseded_by) continue;
        out.push({day: localDay(r.started || r.created), case: r.case || c.case, title: c.title, target: r.target,
          verdict: verdictOf(r.verdict) || 'PENDING', campaign: r.campaign || '', run: r.run});
      }
    }
    return out;
  }

  async function viewHistory(root, params, alive) {
    if (params.get('case') || params.get('show') === 'cases') {
      add(root, [backLink(routeHash('history'), 'History')]);
      return viewTests(root, params, alive);
    }
    const body = el('div', {}, loading('Loading history…'));
    add(root, [viewHead('History', 'What was tested, how it went, and the bugs found, over the last ' + HIST_DAYS + ' days.'), body]);
    const got = await Promise.allSettled([getJSON('/api/results'), getJSON('/api/live'), getJSON('/api/tickets'),
      readBugRegister()]);
    if (!alive()) return;
    if (got[0].status === 'rejected') {
      put(body, errorBox(got[0].reason, 'Results did not load'));
      return;
    }
    const val = function (i) { return got[i].status === 'fulfilled' ? got[i].value : null; };
    const errs = [];
    if (got[1].status === 'rejected') errs.push(errorBox(got[1].reason, 'Steps waiting for your OK did not load'));
    if (got[2].status === 'rejected') errs.push(errorBox(got[2].reason, 'Ticket drafts did not load'));
    if (got[3].status === 'rejected') errs.push(errorBox(got[3].reason, 'The bug list did not load'));
    put(body, historyBoard(historyRuns(val(0)), val(1), val(2), val(3), errs, list(val(0).cases).filter(function (c) {
      return !c.harness;
    }).length));
  }

  function historyBoard(runs, live, tickets, register, errs, caseCount) {
    const days = lastDays(HIST_DAYS);
    const win = runs.filter(function (r) { return r.day >= days[0]; });
    const counted = win.filter(function (r) { return HIST_VERDICTS.indexOf(r.verdict) >= 0; });
    const decided = counted.filter(function (r) { return r.verdict !== 'ERROR'; });
    const passed = decided.filter(function (r) { return r.verdict === 'PASS'; }).length;
    const notTested = win.filter(function (r) { return r.verdict === 'NOT_TESTED'; }).length;
    const runDays = new Set(counted.map(function (r) { return r.day; })).size;
    const waiting = list(live && live.handbacks).length;
    const bugs = bugRows(register, tickets);
    const filed = bugs.filter(function (b) { return b.status === 'filed by us'; }).length;
    const drafted = bugs.filter(function (b) { return b.status === 'drafted, not filed'; }).length;
    return [
      errs,
      el('div', {class: 'tiles'},
        tile('Runs', String(counted.length), counted.length ? 'on ' + runDays + (runDays === 1 ? ' day' : ' days') +
          (notTested ? ' · ' + notTested + ' device slots not tested' : '') : 'none in the last ' + HIST_DAYS + ' days'),
        tile('Passed', decided.length ? Math.round(100 * passed / decided.length) + '%' : '–',
          decided.length ? passed + ' of ' + decided.length + ' runs with a verdict (harness errors left out)' : 'no verdicts yet'),
        tile('Waiting for your OK', live ? String(waiting) : '–', 'handed-back steps of the last 7 days'),
        tile('Bugs filed', register || tickets ? String(filed) : '–', drafted ? drafted + ' more drafted, not filed' : 'tickets from testing')),
      el('section', {class: 'card'},
        el('div', {class: 'card-head'}, el('h2', {}, 'Runs per day')),
        historyLegend(),
        counted.length ? dayChart(days, counted) : el('p', {class: 'muted'}, 'No runs in the last ' + HIST_DAYS + ' days.')),
      el('section', {class: 'card'},
        el('div', {class: 'card-head'}, el('h2', {}, 'Results per device')),
        historyLegend(),
        deviceChart(counted)),
      el('section', {class: 'card'},
        el('div', {class: 'card-head'}, el('h2', {}, 'Bugs'), chip('s-muted', String(bugs.length))),
        bugTable(bugs, register)),
      el('section', {class: 'card'},
        el('div', {class: 'card-head'}, el('h2', {}, 'What was done')),
        doneList(win)),
      el('p', {class: 'small'}, el('a', {href: routeHash('history', {show: 'cases'})},
        'Case library (' + caseCount + ' cases) →')),
    ];
  }

  function tile(label, value, sub) {
    return el('div', {class: 'tile'}, el('div', {class: 'tile-label'}, label), el('div', {class: 'tile-value'}, value),
      sub ? el('div', {class: 'tile-sub'}, sub) : null);
  }

  function historyLegend() {
    return el('div', {class: 'hlegend', 'aria-hidden': 'true'}, HIST_VERDICTS.map(function (v) {
      return el('span', {class: 'hlegend-item'}, el('span', {class: 'hswatch hv-' + v}), HIST_LABEL[v]);
    }));
  }

  function countBy(rows, key) {
    const out = {};
    for (const r of rows) {
      const k = r[key];
      out[k] = out[k] || {total: 0};
      out[k][r.verdict] = (out[k][r.verdict] || 0) + 1;
      out[k].total += 1;
    }
    return out;
  }

  function niceMax(n) {
    if (n <= 5) return Math.max(1, n);
    const step = Math.pow(10, Math.floor(Math.log10(n)));
    for (const m of [1, 2, 2.5, 5, 10]) if (n <= m * step) return m * step;
    return 10 * step;
  }

  function breakdown(c) {
    return HIST_VERDICTS.filter(function (v) { return c[v]; }).map(function (v) { return HIST_LABEL[v] + ' ' + c[v]; }).join(', ');
  }

  // Columns per day, stacked by verdict; a 2px white gap between segments (stroke); one tooltip per segment and day.
  function dayChart(days, rows) {
    const W = 720, H = 220, L = 34, R = 6, T = 10, B = 26;
    const per = countBy(rows, 'day');
    const max = niceMax(Math.max.apply(null, days.map(function (d) { return (per[d] || {total: 0}).total; })));
    const slot = (W - L - R) / days.length;
    const bw = Math.max(4, Math.min(18, slot * 0.62));
    const ph = H - T - B;
    const g = svg('g', {});
    for (const f of [0, 0.5, 1]) {
      const y = T + ph - f * ph;
      g.appendChild(svg('line', {x1: L, x2: W - R, y1: y, y2: y, class: 'hgrid'}));
      g.appendChild(svg('text', {x: L - 6, y: y + 4, class: 'haxis', 'text-anchor': 'end'}, String(Math.round(f * max))));
    }
    days.forEach(function (d, i) {
      const c = per[d];
      const x = L + i * slot + (slot - bw) / 2;
      if (c) {
        let y = T + ph;
        const col = svg('g', {class: 'hcol'}, svg('title', {}, shortDay(d) + ': ' + c.total + ' runs (' + breakdown(c) + ')'));
        for (const v of HIST_VERDICTS) {
          if (!c[v]) continue;
          const h = c[v] / max * ph;
          y -= h;
          col.appendChild(svg('rect', {x: x, y: y, width: bw, height: h, class: 'hseg hv-' + v},
            svg('title', {}, shortDay(d) + ': ' + HIST_LABEL[v] + ' ' + c[v])));
        }
        g.appendChild(col);
      }
      if (i % 5 === 4 || i === days.length - 1) {
        g.appendChild(svg('text', {x: x + bw / 2, y: H - 8, class: 'haxis', 'text-anchor': 'middle'}, shortDay(d)));
      }
    });
    const total = rows.length;
    return el('div', {class: 'hchart'}, svg('svg', {viewBox: '0 0 ' + W + ' ' + H, role: 'img', class: 'hsvg',
      'aria-label': total + ' runs over the last ' + days.length + ' days'}, g));
  }

  // One bar per device, stacked by verdict, the total at the bar's end.
  function deviceChart(rows) {
    const per = countBy(rows, 'target');
    const devices = HIST_DEVICES.concat(Object.keys(per).filter(function (t) { return HIST_DEVICES.indexOf(t) < 0 && t !== 'undefined'; }));
    const W = 720, L = 56, R = 70, RH = 30, BH = 16;
    const H = devices.length * RH + 6;
    const max = niceMax(Math.max(1, Math.max.apply(null, devices.map(function (t) { return (per[t] || {total: 0}).total; }))));
    const pw = W - L - R;
    const g = svg('g', {});
    devices.forEach(function (t, i) {
      const c = per[t];
      const y = 6 + i * RH;
      g.appendChild(svg('text', {x: 0, y: y + BH - 3, class: 'hlabel'}, t));
      if (!c) {
        g.appendChild(svg('text', {x: L, y: y + BH - 3, class: 'haxis'}, 'no runs'));
        return;
      }
      let x = L;
      const bar = svg('g', {class: 'hcol'}, svg('title', {}, t + ': ' + c.total + ' runs (' + breakdown(c) + ')'));
      for (const v of HIST_VERDICTS) {
        if (!c[v]) continue;
        const w = c[v] / max * pw;
        bar.appendChild(svg('rect', {x: x, y: y, width: w, height: BH, class: 'hseg hv-' + v},
          svg('title', {}, t + ': ' + HIST_LABEL[v] + ' ' + c[v])));
        x += w;
      }
      g.appendChild(bar);
      g.appendChild(svg('text', {x: x + 6, y: y + BH - 3, class: 'hlabel'}, c.total + ' runs'));
    });
    return el('div', {class: 'hchart'}, svg('svg', {viewBox: '0 0 ' + W + ' ' + H, role: 'img', class: 'hsvg',
      'aria-label': 'Runs per device: ' + devices.map(function (t) { return t + ' ' + ((per[t] || {}).total || 0); }).join(', ')}, g));
  }

  // The bug list (qa/history/bugs.json, kept by hand) plus the ticket drafts the engine wrote, one row per bug.
  function bugRows(register, tickets) {
    const rows = list(register && register.bugs).map(function (b) {
      return {cl: String(b.cl || ''), title: String(b.title || ''), found: String(b.found || ''),
        filed: String(b.filed || ''), status: String(b.status || ''), test: String(b.test || ''),
        devices: list(b.devices).map(String)};
    });
    const seen = new Set(rows.map(function (b) { return b.cl; }).filter(Boolean));
    for (const d of list(tickets && tickets.drafts)) {
      const cl = d.filed && typeof d.filed.cl === 'string' ? d.filed.cl : '';
      if (cl && seen.has(cl)) continue;
      rows.push({cl: cl, title: String(d.title || ''), found: String(d.slug || '').slice(0, 10),
        filed: d.filed ? String(d.filed.at || '').slice(0, 10) : '', status: d.filed ? 'filed by us' : 'drafted, not filed',
        test: String(d.slug || ''), devices: []});
    }
    rows.sort(function (a, b) { return b.found.localeCompare(a.found) || a.cl.localeCompare(b.cl); });
    return rows;
  }

  function bugTable(bugs, register) {
    if (!bugs.length) {
      return el('p', {class: 'muted'}, register && register.missing ? 'No bugs recorded yet. Earlier bugs are added from ' +
        'the test notes once you have checked the list.' : 'No bugs recorded.');
    }
    const statusClass = {'filed by us': 's-ok', 'existing ticket we linked': 's-info', 'drafted, not filed': 's-warn',
      'found, not filed': 's-warn', 'fixed before a ticket': 's-muted'};
    return el('div', {class: 'table-wrap'}, el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, 'Found'), el('th', {}, 'Bug'), el('th', {}, 'Ticket'), el('th', {}, 'Status'),
        el('th', {}, 'Devices'), el('th', {}, 'Test'))),
      el('tbody', {}, bugs.map(function (b) {
        return el('tr', {},
          el('td', {class: 'min nowrap'}, b.found ? shortDay(b.found) : '–'),
          el('td', {class: 'break'}, b.title),
          el('td', {class: 'min nowrap'}, /^CL-\d{4,6}$/.test(b.cl) ?
            el('a', {href: CLICKUP_TEAM_URL + b.cl, target: '_blank', rel: 'noopener noreferrer'}, b.cl) : (b.cl || '–')),
          el('td', {class: 'min'}, chip(statusClass[b.status] || 's-muted', b.status || 'unknown'),
            b.filed ? el('div', {class: 'small muted nowrap'}, 'filed ' + shortDay(b.filed)) : null),
          el('td', {class: 'small'}, b.devices.join(', ') || '–'),
          el('td', {class: 'small break'}, b.test || '–'));
      }))));
  }

  function campaignName(slug) {
    const s = String(slug || '').replace(/^\d{4}-\d{2}-\d{2}-/, '').replace(/-/g, ' ');
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : 'No campaign';
  }

  // One line per day and campaign: the cases, the devices and the verdict counts; cases link to their results.
  function doneList(rows) {
    if (!rows.length) return el('p', {class: 'muted'}, 'Nothing was run in the last ' + HIST_DAYS + ' days.');
    const byDay = {};
    for (const r of rows) {
      byDay[r.day] = byDay[r.day] || {};
      const c = byDay[r.day][r.campaign] = byDay[r.day][r.campaign] || {cases: {}, devices: new Set(), counts: {}};
      c.cases[r.case] = r.title;
      if (r.target) c.devices.add(r.target);
      c.counts[r.verdict] = (c.counts[r.verdict] || 0) + 1;
    }
    return el('ul', {class: 'list-plain'}, Object.keys(byDay).sort().reverse().map(function (day) {
      return Object.keys(byDay[day]).sort().map(function (camp) {
        const c = byDay[day][camp];
        const ids = Object.keys(c.cases).sort();
        return el('li', {class: 'done-row'},
          el('div', {class: 'row'}, el('span', {class: 'strong nowrap'}, shortDay(day)),
            el('span', {class: 'break'}, campaignName(camp)),
            HIST_VERDICTS.concat(['NOT_TESTED']).filter(function (v) { return c.counts[v]; }).map(function (v) {
              return el('span', {class: 'row'}, verdictChip(v), el('span', {class: 'small'}, '× ' + c.counts[v]));
            })),
          el('div', {class: 'small muted break'}, ids.length + (ids.length === 1 ? ' case' : ' cases') + ' on ' +
            Array.from(c.devices).sort().join(', ') + ': ',
          ids.map(function (id, i) {
            return [i ? ', ' : '', el('a', {href: routeHash('results', {case: id}), class: 'code', title: c.cases[id] || ''}, id)];
          })));
      });
    }));
  }

  // The Results and History pages of 2026-11-02 live in results.js and history.js and plug in here; the older
  // detailed pages stay behind ?view=detail (Results) and ?view=old (History; the case library keeps its routes).
  window.QA = {getJSON: getJSON, sendJSON: sendJSON, el: el, add: add, put: put, routeHash: routeHash, qs: qs,
    loading: loading, errorBox: errorBox, viewHead: viewHead, copyButton: copyButton, chip: chip, list: list,
    bugRows: bugRows, bugTable: bugTable, readBugRegister: readBugRegister, doneList: doneList,
    historyRuns: historyRuns};
  function viewResultsRoute(root, params, alive) {
    // the old page's own links name a run (resultsLink): they stay on the old page
    if (window.QAResults && params.get('view') !== 'detail' && !params.get('run')) {
      return window.QAResults.render(root, params, alive, window.QA);
    }
    return viewResultsTickets(root, params, alive);
  }
  function viewHistoryRoute(root, params, alive) {
    if (window.QAHistory && params.get('view') !== 'old' && !params.get('case') && !params.get('show')) {
      return window.QAHistory.render(root, params, alive, window.QA);
    }
    return viewHistory(root, params, alive);
  }
  const VIEWS = {new: viewNew, today: viewToday, results: viewResultsRoute, history: viewHistoryRoute};

  function boot() {
    app.metaPromise = loadMeta();
    window.addEventListener('hashchange', render);
    document.addEventListener('visibilitychange', onVisibility);
    render();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
