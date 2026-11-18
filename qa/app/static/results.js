// results.js: the Results page (2026-11-02; one test per page since 2026-11-04). Defines
// window.QAResults = {render(root, params, alive, QA)}.
/* 2026-11-04, as approved ("Yes, build it"; tests/2026-11-04-results-redesign/SPEC.md screens 1-4, JSON in API.md):
Results shows one test, which is one Run press. GET /api/results/cards?task=<id>|current[&case=<id>] gives
{test, cards, running}. The page shows the test's header (its title, a grey line with the ClickUp task, the devices and
the missing ones, the build and the run window, and '▸ What you asked and the plan'), one box (the result in one
sentence, the fixed meaning of its word, Hover's done note and one Sun Orange button, the first that applies of File
ticket > Post comment > Report issue > Plan again > Add result; Mark done… asks first), then one row per case with a
chip per device and one line of why. A row opens in place: the parts or check steps per device, What happened, Why, Not
tested, the shots, each device's run time, the ticket part where a device failed or partly passed, and 'Run records ›'
(the older detailed page, #/results?view=detail). While the test runs the box is live (each device's step and newest
screenshot, the case rows done, running or waiting, a look every 4 s); when the test ends it becomes the result box in
place. Every other test is in History ('Your tests'); the top line links there. The texts come from the server; this
file adds only labels. Nodes are built with the app.js helpers in window.QA and createElementNS for the icons: no
innerHTML and no inline style (CSP).
2026-11-03, kept as it was and moved: Report issue (the worker's ticket draft, File ticket, Touch ID, the worker files
it) sits in the rows; Add result (the worker's results comment) and Post comment (Touch ID, then the app server posts
the text the person read, by its sha256) sit in the box, once per test. Nothing is filed or posted without the
person's press and Touch ID.
2026-11-05 (tests/2026-11-05-wingmate-debug/FINDINGS.md): File ticket only for a draft the server would file
(ticket.fileable), else its why; Plan again gives way to a newer plan of the test (plan_again.newer); Add result is off
while a write request of the test is open (test.open_write); a posted comment reads the test again; a later draft or
comment takes away an earlier refusal; a ticket the looks lost is no longer followed; '–' for a device that does not
run a live case; a case with no engine run points at its back-test records. */
(function () {
  'use strict';

  const SVG_NS = 'http://www.w3.org/2000/svg';
  const VERDICTS = ['passed', 'failed', 'failed_suspected', 'not_decided', 'partly', 'blocked', 'not_tested'];
  // views.LABELS (API.md section 6): the code not_decided, an engine ERROR, reads "Test error"
  const LABELS = {passed: 'Passed', failed: 'Failed', failed_suspected: 'Failed, suspected', not_decided: 'Test error',
    partly: 'Partly passed', blocked: 'Blocked', not_tested: 'Not tested'};
  // a step or part per device (views.steps_of): its words and the verdict whose icon and colour it borrows
  const OUTCOMES = {as_expected: ['as expected', 'passed'], not_as_expected: ['not as expected', 'failed'],
    stopped: ['the test stopped here', 'not_decided'], not_run: ['not reached', 'not_tested']};
  const OUTCOME_ORDER = ['as_expected', 'not_as_expected', 'stopped', 'not_run'];
  // a case row while the test runs (running.case_rows)
  const LIVE_WORDS = {done: 'done', running: 'running', waiting: 'waiting'};
  const SHOT_CAPTION = {result: 'Result', end: 'End of the run', other: 'Screenshot'};
  const SHOT_ORDER = ['result', 'end', 'other'];
  // icon shapes on a 16 x 16 grid, stroked in the text colour (results.css .rv-icon); never the only signal
  const X_MARK = [['path', {d: 'M4.5 4.5l7 7M11.5 4.5l-7 7'}]];
  const ICONS = {
    passed: [['path', {d: 'M3.5 8.5l3 3 6-7'}]],
    failed: X_MARK,
    failed_suspected: X_MARK,
    // a wrench: the test itself stopped (it took the place of the question mark of "Not decided")
    not_decided: [['path', {d: 'M10.6 2.3a3.3 3.3 0 0 0-3.8 4.4l-4.2 4.2a1.4 1.4 0 0 0 2 2l4.2-4.2a3.3 3.3 0 0 0 ' +
      '4.4-3.8l-2 2-1.8-.5-.5-1.8z'}]],
    partly: [['circle', {cx: 8, cy: 8, r: 5.5}], ['path', {d: 'M8 2.5a5.5 5.5 0 0 1 0 11z', fill: 'currentColor'}]],
    blocked: [['circle', {cx: 8, cy: 8, r: 5.5}], ['path', {d: 'M4.1 11.9l7.8-7.8'}]],
    not_tested: [['path', {d: 'M4.5 8h7'}]],
  };
  const RUN_ICON = [['circle', {cx: 8, cy: 8, r: 5.5}], ['path', {d: 'M8 5v3.2l2.2 1.4'}]];  // a clock: in progress
  const TAG_ORDER = ['TA5', 'ET65', 'W044', 'TA3'];
  const RUN_TAG = /-(TA5|ET65|W044|TA3)-/;
  const CLICKUP_URL = /^https:\/\/app\.clickup\.com\/t\/\S+$/;
  const COMMENT_MAX = 1000;  // views.py COMMENT_MAX: a comment text this long may be cut, so the whole file is read
  const STEP_HINT_MAX = 120;
  const SHOTS_MAX = 4;
  const COLS_MAX = 4;  // results.css has a grid for 1 to 4 device columns; more wrap
  const REQ_OPEN = ['new', 'claimed'];  // worker.py OPEN
  const REQ_WORDS = {new: 'Waiting for Hover.', claimed: 'Hover is on it.'};
  const POLL_MS = 5000;      // a request of this test is open (ticket draft, comment, file): the test again this often
  const RUN_POLL_MS = 4000;  // the test runs: the live box again this often
  const MISSES_MAX = 2;      // a draft request that shows neither as open nor as done in this many looks has ended
  const BAD = ['failed', 'failed_suspected', 'partly'];  // a device result that makes Report issue active
  const COMMENT_STATES = ['none', 'drafting', 'draft', 'posting', 'posted', 'failed'];
  const RESULT_STATES = ['results_ready', 'greenlit', 'drafted'];  // a task with its results in (tasks.py)
  const COMMENT_HEAD = {draft: 'Results comment, not posted yet', failed: 'Results comment, not posted',
    posting: 'Results comment, being posted'};
  const RELOAD = 'The app server restarted after this page was opened. Reload the page.';
  const NO_CLICKUP = 'This test has no ClickUp task to add the result to.';
  const NOT_POSTED = 'Not posted yet. Post comment asks for Touch ID on the Mac, then the app posts it to the ClickUp ' +
    'test task.';
  const MARK_DONE_ASK = 'It moves to Done in History; Add result is off after this.';
  const BUSY_WHY = 'Hover is still working on this test (a comment, a ticket draft or filing a ticket).';
  // a draft whose run no longer backs a ticket (the server's fileable false, 2026-11-05), when it sends no why
  const NOT_FILEABLE = 'Not filed: its run was corrected to a test error, so there is nothing to file.';
  const TICKET_LOST = 'This result is no longer in the list. Reload the page to see where the ticket stands.';

  let Q = null;  // window.QA, set by render
  let uidN = 0;

  function el() {
    return Q.el.apply(null, arguments);
  }

  function svgEl(tag, attrs) {
    const n = document.createElementNS(SVG_NS, tag);
    for (const k of Object.keys(attrs || {})) n.setAttribute(k, String(attrs[k]));
    return n;
  }

  function verdictKey(v) {
    return VERDICTS.indexOf(v) >= 0 ? v : 'not_decided';
  }

  function str(x) {
    return typeof x === 'string' ? x : '';
  }

  function obj(x) {
    return x && typeof x === 'object' && !Array.isArray(x) ? x : null;
  }

  function cut(s, n) {
    return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s;
  }

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  function count(x) {
    const n = typeof x === 'number' ? x : typeof x === 'string' && /^\d+$/.test(x) ? Number(x) : NaN;
    return isFinite(n) && n > 0 ? Math.floor(n) : 0;
  }

  function uid(prefix) {
    uidN += 1;
    return prefix + uidN;
  }

  function fileUrl(rel) {
    return '/files/' + String(rel).replace(/^\/+/, '').split('/').map(encodeURIComponent).join('/');
  }

  function isFileUrl(u) {
    return typeof u === 'string' && u.indexOf('/files/') === 0;
  }

  function runTag(id) {
    const m = RUN_TAG.exec(String(id || ''));
    return m ? m[1] : '';
  }

  function tagRank(tag) {
    const i = TAG_ORDER.indexOf(tag);
    return i < 0 ? TAG_ORDER.length : i;
  }

  // true when n or a parent up to top is hidden, or folded away in a closed <details> (its summary stays in view)
  function hiddenIn(n, top) {
    for (let x = n; x && x !== top; x = x.parentNode) {
      if (x.hidden) return true;
      const p = x.parentNode;
      if (p && p.tagName === 'DETAILS' && !p.open && x.tagName !== 'SUMMARY') return true;
    }
    return false;
  }

  function capital(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  }

  // ---- words for times and devices ----

  function dateOf(iso) {
    const d = new Date(iso || '');
    return iso && !isNaN(d.getTime()) ? d : null;
  }

  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  function hmOf(d) {
    return pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  // "Today 14:14", "Yesterday 09:30", else "2026.10.30", in the browser's time zone
  function whenText(iso) {
    const d = dateOf(iso);
    if (!d) return '';
    const now = new Date();
    const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    if (sameDay(d, now)) return 'Today ' + hmOf(d);
    if (sameDay(d, yesterday)) return 'Yesterday ' + hmOf(d);
    return d.getFullYear() + '.' + pad(d.getMonth() + 1) + '.' + pad(d.getDate());
  }

  // "14:36" on the day of near (today without it), else "11-03 13:44", with the year when it is not this year
  function stampText(iso, near) {
    const d = dateOf(iso);
    if (!d) return '';
    const ref = dateOf(near) || new Date();
    if (sameDay(d, ref) && (near || sameDay(d, new Date()))) return hmOf(d);
    const now = new Date();
    return (d.getFullYear() !== now.getFullYear() ? d.getFullYear() + '-' : '') + pad(d.getMonth() + 1) + '-' +
      pad(d.getDate()) + ' ' + hmOf(d);
  }

  function fullText(d) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + hmOf(d);
  }

  function timeTag(iso, text) {
    const d = dateOf(iso);
    return d && text ? el('time', {datetime: iso, title: fullText(d)}, text) : null;
  }

  function timeNode(iso) {
    return timeTag(iso, whenText(iso));
  }

  // "14:05:32"
  function clockText(iso) {
    const d = dateOf(iso);
    return d ? hmOf(d) + ':' + pad(d.getSeconds()) : '';
  }

  // a device's run time: "38 s", "11 min"
  function durText(s) {
    if (typeof s !== 'number' || !isFinite(s) || s < 0) return '';
    if (s < 60) return Math.max(1, Math.round(s)) + ' s';
    return Math.round(s / 60) + ' min';
  }

  function deviceName(tag) {
    return tag === 'W044' ? 'W044 (web)' : String(tag || '');
  }

  function tagsOf(xs) {
    return Q.list(xs).filter(function (x) { return typeof x === 'string' && x; });
  }

  function joinDots(parts) {
    const kids = [];
    for (const p of parts) {
      if (!p || (Array.isArray(p) && !p.length)) continue;
      if (kids.length) kids.push(' · ');
      kids.push(p);
    }
    return kids;
  }

  // ---- small parts ----

  function shapesIcon(shapes) {
    const s = svgEl('svg', {viewBox: '0 0 16 16', class: 'rv-icon', 'aria-hidden': 'true', focusable: 'false'});
    for (const part of shapes) s.appendChild(svgEl(part[0], part[1]));
    return s;
  }

  function icon(verdict) {
    return shapesIcon(ICONS[verdictKey(verdict)]);
  }

  // a chip with the verdict's icon and words; "Test error" is hatched (results.css), as History hatches it
  function verdictBadge(verdict, label) {
    const v = verdictKey(verdict);
    return el('span', {class: 'rv-badge rv-v-' + v}, icon(v), el('span', {}, label || LABELS[v]));
  }

  // the worker's explanation when it wrote one, else the text built from the records
  function texts(c) {
    const ex = obj(c.explain) || {};
    const auto = obj(c.auto) || {};
    return function (k) { return str(ex[k]) || str(auto[k]); };
  }

  function facts(pairs) {
    const kids = [];
    for (const p of pairs) {
      if (!p[1]) continue;
      kids.push(el('dt', {}, p[0]), el('dd', {}, p[1]));
    }
    return kids.length ? el('dl', {class: 'rv-facts'}, kids) : null;
  }

  function setWhy(p, text) {
    Q.put(p, text || null);
    p.hidden = !text;
  }

  // ---- proof and flight plan ----

  // the result shots first, then the end of the run, by device; other shots only when there is neither
  function proofShown(c) {
    const all = Q.list(c.proof).filter(function (p) { return p && isFileUrl(p.url); });
    const main = all.filter(function (p) { return p.kind === 'result' || p.kind === 'end'; });
    const shown = main.length ? main : all.slice(0, 2);
    shown.sort(function (a, b) {
      return (SHOT_ORDER.indexOf(a.kind) - SHOT_ORDER.indexOf(b.kind)) || (tagRank(runTag(a.run)) - tagRank(runTag(b.run)));
    });
    return shown.slice(0, SHOTS_MAX);
  }

  function shot(p) {
    const tag = runTag(p.run);
    const kind = SHOT_CAPTION[p.kind] ? p.kind : 'other';
    const caption = SHOT_CAPTION[kind] + (tag ? ', ' + tag : '');
    const alt = 'Screenshot' + (tag ? ' from ' + tag : '') +
      (kind === 'result' ? ' when the result showed' : kind === 'end' ? ' at the end of the run' : '');
    const img = el('img', {src: p.url, alt: alt, loading: 'lazy'});
    const fig = el('figure', {class: 'rv-shot'},
      el('a', {href: p.url, target: '_blank', rel: 'noopener noreferrer', title: 'Open the full image in a new tab'}, img),
      el('figcaption', {}, caption));
    img.addEventListener('error', function () {
      Q.put(fig, el('p', {class: 'small muted'}, caption + ': the image is not available.'));
    });
    return fig;
  }

  // the areas of the plan, north up: equirectangular (longitude scaled by cos(latitude)), fit into 64 x 64
  function outline(polygons, bad) {
    const rings = [];
    for (const r of Q.list(polygons)) {
      const pts = Q.list(r).filter(function (p) {
        return Array.isArray(p) && typeof p[0] === 'number' && typeof p[1] === 'number' && isFinite(p[0]) && isFinite(p[1]);
      });
      if (pts.length >= 3) rings.push(pts);
    }
    if (!rings.length) return null;
    let latMin = Infinity, latMax = -Infinity;
    for (const r of rings) for (const p of r) { latMin = Math.min(latMin, p[0]); latMax = Math.max(latMax, p[0]); }
    const k = Math.cos((latMin + latMax) / 2 * Math.PI / 180);
    const xy = rings.map(function (r) { return r.map(function (p) { return [p[1] * k, -p[0]]; }); });
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const r of xy) {
      for (const p of r) {
        x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]);
        y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]);
      }
    }
    const w = x1 - x0, h = y1 - y0;
    const s = 64 / (Math.max(w, h) || 1);
    const ox = (64 - w * s) / 2, oy = (64 - h * s) / 2;
    const label = rings.length > 1 ? 'Outline of the ' + rings.length + ' flight plan areas' :
      'Outline of the flight plan area';
    const svg = svgEl('svg', {viewBox: '-4 -4 72 72', class: 'rv-outline' + (bad ? ' rv-bad' : ''), role: 'img',
      'aria-label': label});
    const title = svgEl('title', {});
    title.textContent = label;
    svg.appendChild(title);
    for (const r of xy) {
      svg.appendChild(svgEl('polygon', {class: 'rv-area', points: r.map(function (p) {
        return ((p[0] - x0) * s + ox).toFixed(1) + ',' + ((p[1] - y0) * s + oy).toFixed(1);
      }).join(' ')}));
    }
    return svg;
  }

  function planBox(fp, bad) {
    const name = str(fp.name) || 'flight plan';
    return el('div', {class: 'rv-plan'}, outline(fp.polygons, bad),
      el('div', {class: 'rv-plan-text'}, el('div', {class: 'rv-plan-label'}, 'Flight plan'),
        isFileUrl(fp.url) ? el('a', {href: fp.url, download: name, title: 'Download the flight plan'}, name) :
          el('span', {}, name)));
  }

  // each device's run time from its own record: "TA5 11 min · ET65 11 min", the start and end in the hint
  function deviceTimes(c) {
    const bits = Q.list(c.devices).map(function (d) {
      const took = d ? durText(d.duration_s) : '';
      if (!took || !str(d.tag)) return null;
      const from = clockText(d.started);
      const to = clockText(d.ended);
      return el('span', {title: from && to ? d.tag + ' ran ' + from + ' to ' + to : null}, d.tag + ' ' + took);
    }).filter(Boolean);
    return bits.length ? el('p', {class: 'rv-times'}, joinDots(bits)) : null;
  }

  function proofBlock(c) {
    const v = verdictKey(c.verdict);
    const bad = v === 'failed' || v === 'failed_suspected';
    const shots = proofShown(c);
    const plans = Q.list(c.flight_plans).filter(function (fp) { return obj(fp); });
    const times = deviceTimes(c);
    if (!shots.length && !plans.length && !times) return null;
    return el('div', {class: 'rv-proof'}, shots.map(shot), plans.map(function (fp) { return planBox(fp, bad); }),
      times);
  }

  // ---- the step x device table of an opened row ----

  function outcomeCell(code, tag) {
    const o = OUTCOMES[code];
    if (!o) return el('td', {class: 'rv-step-cell'});
    return el('td', {class: 'rv-step-cell rv-o-' + code, title: tag + ': ' + o[0]}, icon(o[1]),
      el('span', {class: 'sr-only'}, o[0]));
  }

  function outcomeLegend() {
    return el('p', {class: 'rv-legend'}, OUTCOME_ORDER.map(function (k) {
      return el('span', {class: 'rv-o-' + k}, icon(OUTCOMES[k][1]), OUTCOMES[k][0]);
    }));
  }

  // one row per part of the case (case rule), else per check step (the steps with an assert, plus where a device
  // stopped); one column per device of the test
  function stepTable(c, tags) {
    const rows = Q.list(c.rows).filter(function (r) { return obj(r); });
    if (!rows.length || !tags.length) return null;
    const parts = c.rows_from === 'parts';
    return el('div', {class: 'rv-table'},
      el('div', {class: 'table-wrap'}, el('table', {class: 'rv-step-table'},
        el('thead', {}, el('tr', {}, el('th', {scope: 'col'}, parts ? 'Part' : 'Check'),
          tags.map(function (tag) { return el('th', {scope: 'col', class: 'rv-step-cell'}, deviceName(tag)); }))),
        el('tbody', {}, rows.map(function (r) {
          const cells = obj(r.cells) || {};
          const text = str(r.text);
          const label = parts && Number.isInteger(r.n) ? r.n + '. ' + text : text;
          return el('tr', {}, el('td', {class: 'rv-step-text'}, label),
            tags.map(function (tag) { return outcomeCell(cells[tag], tag); }));
        })))),
      outcomeLegend());
  }

  // ---- the server's word on a card, a ticket and the comment (2026-11-03) ----

  function sameCard(a, b) {
    if (!a || !b) return false;
    return str(a.key) && str(b.key) ? a.key === b.key : a.task === b.task && a.case === b.case;
  }

  // this card in a newer look at the test
  function findCard(data, c) {
    return Q.list(data && data.cards).find(function (x) { return sameCard(x, c); }) || null;
  }

  // the ticket drafts of the card; a draft request still open ("drafting"); one that ended without a draft
  function draftsOf(c) {
    return Q.list(c.tickets).filter(function (t) { return t && str(t.title) && t.state !== 'drafting'; });
  }

  function draftingOf(c) {
    return Q.list(c.tickets).find(function (t) { return t && t.state === 'drafting'; }) || null;
  }

  function draftFailed(c) {
    const t = Q.list(c.tickets).find(function (x) { return x && !str(x.title) && x.state === 'failed'; });
    return t ? str(t.error) || 'The ticket was not drafted.' : '';
  }

  // a draft the person already pressed File ticket for: filing, filed, or a try that failed
  function ticketBegun(t) {
    return ticketState(t) !== 'draft' || Boolean(ticketError(t));
  }

  // a draft File ticket cannot send (views.py fileable false: its run was corrected to a test error since, or ran on an
  // earlier engine account); a filed or filing ticket and a draft without a folder (no File ticket at all) are never
  // one, and a server without the field leaves it to the press
  function unfileable(t) {
    return t.fileable === false && Boolean(str(t.dir)) && ticketState(t) === 'draft';
  }

  // the drafts that still stand for Report issue: all but those File ticket cannot send (they show with their why)
  function standing(c) {
    return draftsOf(c).filter(function (t) { return !unfileable(t); });
  }

  // a draft in its card: its folder, else its title (a draft without a folder)
  function draftKey(t) {
    return str(t.dir) || str(t.title);
  }

  // Report issue: the server's word (views.py report); without it, a device that failed, failed suspected or partly
  // passed in a card with a task
  function reportOf(c) {
    const r = obj(c.report);
    if (r) return {enabled: r.enabled === true, why: str(r.why)};
    const bad = Q.list(c.devices).some(function (d) { return d && BAD.indexOf(d.verdict) >= 0; });
    return {enabled: bad && Boolean(str(c.task)), why: bad ? 'This result has no test task to report from.' :
      'Nothing went wrong in these runs.'};
  }

  // the results comment of the test (views.py comment_state); without it, the comment text of the drafts, if any
  function commentOf(c) {
    const m = obj(c.comment);
    if (m) return Object.assign({}, m, {state: COMMENT_STATES.indexOf(m.state) >= 0 ? m.state : 'none'});
    const text = str((c.outputs || {}).comment_text);
    return {state: text ? 'draft' : 'none', text: text || null, sha256: null, posted_at: null, error: null,
      clickup_url: null};
  }

  // the test's ClickUp test: a task id, or only a custom id (a CL link), which the server takes for Add result too
  function clickupOf(c) {
    const ck = obj(c.clickup);
    return ck && (str(ck.task_id) || str(ck.custom_id)) ? ck : null;
  }

  // a comment Post comment can send: views.py gives the sha256 only to a draft it would post (never to "failed")
  function postable(m) {
    return m.state === 'draft' && Boolean(str(m.sha256));
  }

  function clickupUrl(c, m) {
    const ck = obj(c.clickup) || {};
    return [m.clickup_url, ck.url].find(function (u) { return typeof u === 'string' && CLICKUP_URL.test(u); }) || null;
  }

  function staleToken(e) {
    return Boolean(e && e.status === 403 && /\btoken\b|\bOrigin\b|\bHost header\b|cross-site/.test(String(e.message)));
  }

  // The words for a press the server refused: a stale page token (security.py); the person's no at the Touch ID prompt
  // of File ticket or Post comment (approvals.NOT_APPROVED: cancelled, no answer in 90 s, Touch ID and password not
  // available), in the server's words when it sent them; its 409 words as sent (and Plan again's 400, as the plan
  // screen shows them); ClickUp's refusal of a comment (502); else what did not go through.
  function refusal(e, what) {
    const msg = e && e.message ? String(e.message) : String(e);
    if (staleToken(e)) return RELOAD;
    if (e && e.status === 403 && (what === 'File ticket' || what === 'Post comment')) {
      return /^Not approved: /.test(msg) ? msg : 'Not approved (Touch ID cancelled).';
    }
    if (e && (e.status === 409 || (e.status === 400 && what === 'Plan again'))) return msg;
    if (e && e.status === 502 && what === 'Post comment') return 'Not posted: ' + msg;
    return what + ' did not go through: ' + msg;
  }

  async function readText(rel) {
    const r = await fetch(fileUrl(rel), {cache: 'no-store', credentials: 'same-origin'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.text();
  }

  // ---- the page ----

  // The one Sun Orange button of the page (2026-11-04): the first that applies of File ticket (the first draft shown
  // and not filed, row by row), Post comment, Report issue (the first row where something went wrong and no ticket is
  // filed or filing), Plan again (unless a newer plan of the test exists), Add result (until the comment is written,
  // and not while a write request of the test is open). None while the test runs or once it is in Done. A button keeps
  // the look while its own request runs (it is disabled then); every other button is secondary.
  function markPrimary(page) {
    const root = page.view;
    for (const p of page.parts) if (p.sync) p.sync();
    const q = function (cls) { return Array.prototype.slice.call(root.querySelectorAll('.' + cls)); };
    const shown = function (b) { return Boolean(b) && b.isConnected && !hiddenIn(b, root); };
    const next = function (b) { return shown(b) && b.classList.contains('rv-next'); };
    const files = q('rv-file-btn');
    const posts = q('rv-post-btn');
    const reports = q('rv-report-btn');
    const plans = q('rv-plan-btn');
    const adds = q('rv-add-btn');
    const off = Boolean(page.running) || (page.test && page.test.group === 'done');
    const main = off ? null : files.find(shown) || posts.find(shown) || reports.find(next) || plans.find(next) ||
      adds.find(next) || null;
    for (const b of files.concat(posts, reports, plans, adds)) b.classList.toggle('btn-secondary', b !== main);
    if (page.markDone) page.markDone.draw();
  }

  // what a test in Done does not show: Plan again (or the newer plan's link) and Report issue; put away when a posted
  // comment moved the test there and no newer copy of it came
  function doneHides(page) {
    return ['rv-plan-btn', 'rv-newer', 'rv-report-btn'].reduce(function (out, cls) {
      return out.concat(Array.prototype.slice.call(page.view.querySelectorAll('.' + cls)));
    }, []);
  }

  // the test fields a newer look may change, taken by every part that looks (the comment and the tickets keep theirs)
  function took(page, data) {
    const f = data && obj(data.test);
    if (!f || !page.test || f.task !== page.test.task) return;
    for (const k of ['group', 'state', 'can_mark_done', 'mark_done_why', 'done_how', 'next', 'waiting', 'open_write']) {
      if (k in f) page.test[k] = f[k];
    }
  }

  function freshTest(page, data) {
    const f = data && obj(data.test);
    return f && page.test && f.task === page.test.task ? f : null;
  }

  function busy(page) {
    return page.parts.some(function (p) { return p.busy(); });
  }

  // "9 other tests wait for you · History ›" and "Also running: <title> ›"
  function topLine(t) {
    const also = Q.list(t.also_running).filter(function (r) { return r && str(r.task); }).map(function (r) {
      return el('a', {class: 'rv-also', href: Q.routeHash('results', {task: r.task})},
        'Also running: ' + (str(r.title) || r.task) + ' ›');
    });
    const n = count(t.waiting);
    const wait = n ? el('a', {class: 'rv-waiting', href: Q.routeHash('history')},
      n + (n === 1 ? ' other test waits' : ' other tests wait') + ' for you · History ›') : null;
    return also.length || wait ? el('div', {class: 'rv-top'}, also, wait) : null;
  }

  function clickupBit(ck) {
    const c = obj(ck);
    const name = c ? str(c.title) || str(c.custom_id) || str(c.task_id) : '';
    if (!name) return null;
    return CLICKUP_URL.test(str(c.url)) ? el('a', {href: c.url, target: '_blank', rel: 'noopener noreferrer'},
      'ClickUp: ' + name + ' ↗') : 'ClickUp: ' + name;
  }

  // "2 of 3 devices (W044: not in this test)"
  function missingBit(t) {
    const miss = Q.list(t.not_tested_devices).filter(function (x) { return x && str(x.tag); });
    if (!miss.length) return null;
    const groups = [];
    for (const m of miss) {
      const why = str(m.why) || 'not in this test';
      const g = groups.find(function (x) { return x.why === why; });
      if (g) g.tags.push(m.tag);
      else groups.push({why: why, tags: [m.tag]});
    }
    const n = tagsOf(t.devices).length;
    return n + ' of ' + (n + miss.length) + ' devices (' + groups.map(function (g) {
      return g.tags.join(', ') + ': ' + g.why;
    }).join('; ') + ')';
  }

  function timesBit(t, running) {
    const run = stampText(t.run_at);
    if (!run) return null;
    const res = running ? '' : stampText(t.results_at, t.run_at);
    return ['Run ', timeTag(t.run_at, run), res ? [' → results ', timeTag(t.results_at, res)] : null];
  }

  // '▸ What you asked and the plan': the description, the plan's summary and what it does not cover
  function askedFold(page) {
    const a = obj(page.test.asked);
    if (!a) return null;
    const g = str(a.guidance);
    const p = str(a.plan_summary);
    const nc = Q.list(a.not_covered).filter(function (x) { return typeof x === 'string' && x; });
    if (!g && !p && !nc.length) return null;
    const fold = el('details', {class: 'rv-asked', open: page.askedOpen},
      el('summary', {}, 'What you asked and the plan'),
      facts([['What you asked', g ? el('span', {class: 'rv-pre'}, g) : null],
        ['The plan', p ? el('span', {class: 'rv-pre'}, p) : null],
        ['Not covered by this run', nc.length ? el('ul', {class: 'rv-list'}, nc.map(function (x) {
          return el('li', {}, x);
        })) : null]]));
    fold.addEventListener('toggle', function () { page.askedOpen = fold.open; });
    return fold;
  }

  function headCard(page) {
    const t = page.test;
    const running = Boolean(page.running);
    const title = str(t.title) || str(t.task) || 'This test';
    const build = str(t.build) ? t.build + (str(t.native) ? ', native ' + t.native : '') : null;
    return el('section', {class: 'card rv-head-card'},
      el('h1', {class: 'rv-test-title', title: title}, title),
      el('p', {class: 'rv-meta'}, joinDots([clickupBit(t.clickup), tagsOf(t.devices).map(deviceName).join(', '),
        running ? null : missingBit(t), running ? null : build, timesBit(t, running)])),
      askedFold(page));
  }

  // How a test in Done ended; a posted comment says so in the comment part
  function doneLine(t) {
    const h = obj(t.done_how);
    if (!h || h.how === 'posted') return null;
    if (h.how === 'marked') return el('p', {class: 'rv-done-line', tabindex: '-1'}, icon('passed'), 'Marked done');
    if (h.how !== 'replaced' && h.how !== 'failed_again') return null;
    const words = (h.how === 'replaced' ? 'Replaced by the ' : 'Failed again in the ') +
      (stampText(h.at) || 'newer') + ' run ›';
    return el('p', {class: 'rv-done-line', tabindex: '-1'},
      str(h.by_task) ? el('a', {href: Q.routeHash('results', {task: h.by_task})}, words) : words);
  }

  // The box after the test: the worst result in one sentence, the fixed meaning of its word, Hover's done note,
  // then the comment, Plan again, Add result and Mark done…
  function resultBox(page) {
    const t = page.test;
    const res = obj(t.result);
    const v = verdictKey(res && res.verdict);
    const sec = el('section', {class: 'card rv-box', 'aria-label': 'Result'});
    Q.add(sec, [
      el('h2', {class: 'rv-verdict'}, el('span', {class: 'rv-verdict-icon rv-v-' + v}, icon(v)),
        el('span', {}, res ? str(res.sentence) || LABELS[v] : 'No result yet',
          t.stopped_early ? Q.chip('s-warn', 'Stopped early') : null)),
      res && str(res.meaning) ? el('p', {class: 'rv-meaning'}, res.meaning) : null,
      str(t.done_note) ? el('p', {class: 'rv-note'}, el('span', {class: 'strong'}, 'Hover: '), t.done_note) : null,
      doneLine(t),
      boxActions(page, sec)]);
    return sec;
  }

  // the buttons of the box: Post comment and Copy (the comment part), Plan again, Add result, Mark done…
  function boxActions(page, sec) {
    const t = page.test;
    const comment = str(t.task) ? commentPart(page, sec) : null;
    const plan = planAgainPart(page);
    page.markDone = markDonePart(page);
    page.comment = comment;
    if (comment) page.parts.push(comment);
    const md = page.markDone;
    if (!comment && !plan && !md) return null;
    const row = el('div', {class: 'rv-actions'}, comment ? [comment.post, comment.copy] : null, plan ? plan.btn : null,
      comment ? comment.add : null, md ? md.btn : null, comment ? comment.status : null);
    return [comment ? comment.fold : null, row, comment ? [comment.why, comment.err] : null, plan ? plan.err : null,
      md ? md.nodes : null];
  }

  // Plan again (a test where no case gave an app result): the plan screen's Prepare again body, unchanged, to
  // POST /api/worker/check, then the new plan's screen. Nothing runs before Run. Never on a test in Done.
  // A newer plan of the same ClickUp task (plan_again.newer {id, fresh}, 2026-11-05): while it can still run, the box
  // links it instead of Plan again; once it expired, Plan again shows as a secondary button.
  function planAgainPart(page) {
    const raw = obj(page.test.plan_again);
    if (!raw || page.test.group === 'done') return null;
    const newer = obj(raw.newer);
    const newerId = newer && str(newer.id) ? newer.id : '';
    if (newerId && newer.fresh === true) {
      return {btn: el('a', {class: 'rv-newer', href: Q.routeHash('new', {show: 'plan', req: newerId})},
        'A newer plan of this test exists ›'), err: null};
    }
    // the body is the Prepare again body; newer only says which plan came after it
    const body = Object.assign({}, raw);
    delete body.newer;
    const btn = el('button', {type: 'button', class: ['btn', 'rv-plan-btn', newerId ? null : 'rv-next']}, 'Plan again');
    const err = el('div', {class: 'rv-error'});
    let sending = false;
    btn.addEventListener('click', async function () {
      if (sending) return;
      sending = true;
      btn.disabled = true;
      Q.put(err);
      let r;
      try {
        r = await Q.sendJSON('POST', '/api/worker/check', body);
      } catch (e) {
        if (!page.ctx.alive()) return;
        sending = false;
        btn.disabled = false;
        Q.put(err, Q.errorBox(refusal(e, 'Plan again')));
        btn.focus();
        return;
      }
      if (!page.ctx.alive()) return;
      window.location.hash = Q.routeHash('new', str(r && r.id) ? {show: 'plan', req: r.id} : null);
    });
    return {btn: btn, err: err};
  }

  // Mark done… (a test in Tested): it asks first, then POST /api/tasks/<id>/state {to: closed} and the test again
  function markDonePart(page) {
    const t = page.test;
    if (!str(t.task) || t.group !== 'tested') return null;
    const btn = el('button', {type: 'button', class: 'btn btn-secondary rv-done-btn'}, 'Mark done…');
    const yes = el('button', {type: 'button', class: 'btn btn-secondary'}, 'Mark done');
    const no = el('button', {type: 'button', class: 'btn btn-secondary'}, 'Cancel');
    const ask = el('div', {class: 'rv-confirm', role: 'group', 'aria-label': 'Mark done', hidden: true},
      el('p', {}, MARK_DONE_ASK), el('div', {class: 'rv-actions'}, yes, no));
    const why = el('p', {class: 'rv-why', id: uid('rv-why-')});
    btn.setAttribute('aria-describedby', why.getAttribute('id'));
    const err = el('div', {class: 'rv-error'});
    const part = {btn: btn, nodes: [ask, why, err], sending: false};
    part.draw = function () {
      const on = t.group === 'tested';
      const off = !on ? '' : t.can_mark_done !== true ? str(t.mark_done_why) || 'It cannot be marked done now.' :
        busy(page) ? BUSY_WHY : '';
      btn.hidden = !on;
      btn.disabled = Boolean(off) || part.sending;
      setWhy(why, off);
      if (!on || off) ask.hidden = true;
    };
    btn.addEventListener('click', function () {
      if (btn.disabled) return;
      Q.put(err);
      ask.hidden = false;
      yes.focus();
    });
    no.addEventListener('click', function () {
      ask.hidden = true;
      btn.focus();
    });
    yes.addEventListener('click', async function () {
      if (part.sending) return;
      part.sending = true;
      yes.disabled = true;
      Q.put(err);
      part.draw();
      try {
        await Q.sendJSON('POST', '/api/tasks/' + encodeURIComponent(t.task) + '/state', {to: 'closed'});
      } catch (e) {
        if (!page.ctx.alive()) return;
        part.sending = false;
        yes.disabled = false;
        Q.put(err, Q.errorBox(refusal(e, 'Mark done')));
        part.draw();
        (ask.hidden ? btn : yes).focus();
        return;
      }
      if (!page.ctx.alive()) return;
      let data = null;
      try {
        data = await Q.getJSON(page.src);
      } catch (e) { /* drawn below as done */ }
      if (!page.ctx.alive()) return;
      if (freshTest(page, data)) drawPage(page, data);
      else {
        ask.hidden = true;
        part.sending = false;
        t.group = 'done';
        t.state = 'closed';
        t.can_mark_done = false;
        markPrimary(page);
        Q.put(err, el('p', {class: 'rv-status', role: 'status'}, 'Marked done. Reload the page to see the test as it ' +
          'is now.'));
      }
      const line = page.view.querySelector('.rv-done-line');
      if (line) line.focus();
    });
    part.draw();
    return part;
  }

  // ---- the results comment: Add result and Post comment (2026-11-03), once per test in the box ----

  // Add result asks Hover for the comment (a write request with the "comment" output); its text folds under
  // 'Results comment, not posted yet'; Post comment shows only for a draft with its sha256 and sends that sha256: the
  // server asks for Touch ID on the Mac, then posts the text it holds when it still has that sha256. A press while
  // the text is folded away opens it and sends nothing, so what is posted is what the person read.
  function commentPart(page, sec) {
    const t = page.test;
    const ctx = page.ctx;
    const start = commentOf(t);
    const ui = {commentOpen: false, adding: false, posting: false, watchComment: start.state === 'drafting',
      watchPost: start.state === 'posting', commentMisses: 0, commentErr: '', full: null, loading: null, timer: null};
    const add = el('button', {type: 'button', class: 'btn rv-add-btn'}, 'Add result');
    const why = el('p', {class: 'rv-why', id: uid('rv-why-')});
    add.setAttribute('aria-describedby', why.getAttribute('id'));
    const post = el('button', {type: 'button', class: 'btn rv-post-btn'}, 'Post comment');
    const copy = Q.copyButton(function () { return shownText(); }, 'Copy ClickUp comment');
    const head = el('summary', {});
    const text = el('pre', {class: 'rv-comment-text', tabindex: '-1'});
    const whole = el('p', {class: 'rv-comment-file'});
    const fold = el('details', {class: 'rv-comment'}, head, text, whole);
    const status = el('span', {class: 'rv-status', role: 'status', tabindex: '-1'});
    const err = el('div', {class: 'rv-error'});
    fold.addEventListener('toggle', function () { ui.commentOpen = fold.open; });

    // the text the person reads is the text Post comment sends: views.py never cuts a text it would post, so a draft
    // shows exactly the server's text (review 2026-11-03: never the file read again, whose spaces, line ends or newer
    // content can differ from what the sha256 stands for); only a "failed" text too long to post comes cut at
    // COMMENT_MAX, and that one is read whole from its file, to read only
    function shownText() {
      const m = commentOf(t);
      const s = str(m.text);
      return m.state === 'failed' && ui.full && ui.full.from === s ? ui.full.text : s;
    }

    function loadWhole() {
      const m = commentOf(t);
      const s = str(m.text);
      const file = str((t.outputs || {}).comment_file);
      if (m.state !== 'failed' || s.length < COMMENT_MAX || !file || (ui.full && ui.full.from === s) ||
        ui.loading === s) return;
      ui.loading = s;
      readText(file).then(function (all) {
        ui.full = {from: s, text: all};
      }, function () {
        ui.full = {from: s, text: s, file: file};  // read failed: the cut text, with a link to the whole file
      }).then(function () {
        ui.loading = null;
        if (ctx.alive()) draw();
      });
    }

    function draw() {
      const m = commentOf(t);
      const st = m.state;
      const shown = shownText();
      const readable = Boolean(shown) && (st === 'draft' || st === 'failed' || st === 'posting');
      const noTask = !clickupOf(t) && st === 'none' ? NO_CLICKUP : '';
      const link = clickupUrl(t, m);
      // Add result is the greenlight, once per test, until a comment is written; only where the server takes it
      // (results in, not closed: API.md 1.5) and not on a test in Done; off while a write request of the test is open
      // (open_write: the server refuses a second one with 409, even one without the comment)
      const writing = t.open_write === true;
      add.hidden = RESULT_STATES.indexOf(t.state) < 0 || t.group === 'done' ||
        ['none', 'failed', 'drafting'].indexOf(st) < 0;
      add.disabled = Boolean(noTask) || ui.adding || st === 'drafting' || writing;
      add.classList.toggle('rv-next', !noTask && !writing && (st === 'none' || st === 'failed'));
      setWhy(why, add.hidden || !noTask ? '' : 'Add result: ' + noTask);
      if (readable) loadWhole();
      Q.put(head, COMMENT_HEAD[st] || 'Results comment');
      fold.hidden = !readable;
      fold.open = readable && ui.commentOpen;
      Q.put(text, readable ? shown : null);
      Q.put(whole, st === 'failed' && ui.full && ui.full.file && ui.full.from === str(m.text) ? el('a', {
        href: fileUrl(ui.full.file), target: '_blank', rel: 'noopener noreferrer'}, 'Open the whole comment') : null);
      whole.hidden = !whole.firstChild;
      const loading = readable && ui.loading === str(m.text);
      // only a draft the server would post (a "failed" text goes back to Add result, as its error says)
      post.hidden = !readable || !postable(m);
      post.disabled = ui.posting;
      copy.hidden = !readable;
      let words = null;
      if (ui.posting) words = 'Confirm with Touch ID on the Mac…';
      else if (st === 'drafting') words = 'Writing the comment…';
      else if (st === 'posting') words = 'Posting the comment…';
      else if (st === 'posted') {
        const when = timeNode(m.posted_at);
        const said = ['Posted', when ? [' ', when] : null];
        words = [icon('passed'), link ? el('a', {href: link, target: '_blank', rel: 'noopener noreferrer',
          title: 'Open the ClickUp task'}, said, ' ↗') : said];
      } else if (loading) words = 'Loading the whole comment…';
      else if (readable && postable(m)) words = NOT_POSTED;
      Q.put(status, words);
      status.hidden = !words;
      status.classList.toggle('rv-comment-done', st === 'posted' && !ui.posting);
      const error = ui.commentErr || (st === 'failed' && !ui.posting ?
        str(m.error) || (shown ? 'The comment was not posted.' : 'The comment was not written.') : '');
      Q.put(err, error ? Q.errorBox(error) : null);
      markPrimary(page);
    }

    // the test in a newer look (null when it was not in it): the comment and its file; the watches end when what they
    // wait for came, or after MISSES_MAX looks that show neither it nor the request. A comment that came (written,
    // being posted or posted) takes away an earlier refusal, which no longer holds (a 409 of Add result, say).
    function take(fresh) {
      const was = commentOf(t).state;
      if (fresh) {
        t.outputs = fresh.outputs;
        if (!ui.posting) t.comment = fresh.comment;
      }
      const st = commentOf(t).state;
      if (st !== was && (st === 'draft' || st === 'posting' || st === 'posted')) ui.commentErr = '';
      if (ui.watchComment) {
        if (st === 'draft' || st === 'failed' || st === 'posted' || st === 'posting') {
          ui.watchComment = false;
          ui.watchPost = st === 'posting';
          ui.commentOpen = true;
        } else if (fresh && st === 'drafting') ui.commentMisses = 0;
        else if (++ui.commentMisses >= MISSES_MAX) {
          ui.watchComment = false;
          ui.commentErr = fresh ? 'The comment was not written. Press Add result to try again.' :
            'This result is no longer in the list. Reload the page to see where the comment stands.';
        }
      }
      if (ui.watchPost && (st !== 'posting' || !fresh)) ui.watchPost = false;
    }

    // also while a write request of the test is open (open_write): Add result comes back when it ends
    function waits() {
      return ui.watchComment || ui.watchPost || t.open_write === true;
    }

    // a newer look: the test fields every part takes, then the comment's own. A write request that ended takes away the
    // refusal it caused (Add result's 409 while it was open), so Add result comes back without it.
    function takeLook(data) {
      const writing = t.open_write === true;
      took(page, data);
      take(freshTest(page, data));
      if (writing && t.open_write !== true && ['none', 'failed'].indexOf(commentOf(t).state) >= 0) ui.commentErr = '';
    }

    // while a request is open: the test again every POLL_MS, until it is done or the page changes
    function poll() {
      if (ui.timer) return;
      ui.timer = setTimeout(async function tick() {
        ui.timer = null;
        if (!ctx.alive() || !sec.isConnected || !waits()) return;
        let data = null;
        try {
          data = await Q.getJSON(page.src);
        } catch (e) { /* the next look tries again */ }
        if (!ctx.alive() || !sec.isConnected) return;
        if (data) takeLook(data);
        draw();
        if (waits()) ui.timer = setTimeout(tick, POLL_MS);
      }, POLL_MS);
    }

    // one look right away, after a 409 (the comment may be there already, or its request open)
    async function look() {
      let data = null;
      try {
        data = await Q.getJSON(page.src);
      } catch (e) {
        return;
      }
      if (!ctx.alive() || !sec.isConnected) return;
      const fresh = freshTest(page, data);
      if (!fresh) return;
      if (commentOf(fresh).state === 'drafting') ui.watchComment = true;
      takeLook(data);
      draw();
      if (waits()) poll();
    }

    // Add result: the worker writes the comment (the write request's "comment" output). A comment the server cannot
    // post ("failed": too long, a token in it, ...) is written again, as its error says.
    add.addEventListener('click', async function () {
      if (ui.adding) return;
      const m = commentOf(t);
      ui.adding = true;
      ui.commentErr = '';
      draw();
      try {
        await Q.sendJSON('POST', '/api/results/comment', {task: t.task});
      } catch (e) {
        if (!ctx.alive()) return;
        ui.adding = false;
        ui.commentErr = refusal(e, 'Add result');
        draw();
        add.focus();
        if (e && e.status === 409) look();
        return;
      }
      if (!ctx.alive()) return;
      ui.adding = false;
      t.comment = Object.assign({}, m, {state: 'drafting', error: null});
      ui.watchComment = true;
      ui.commentMisses = 0;
      ui.commentOpen = true;
      draw();
      status.focus();
      poll();
    });

    // Post comment: the server asks for Touch ID on the Mac (it waits for the answer), then posts the text it holds
    // when it still has the sha256 the person read
    post.addEventListener('click', async function () {
      if (ui.posting) return;
      if (!ui.commentOpen) {
        ui.commentOpen = true;
        draw();
        post.focus();
        return;
      }
      const m = commentOf(t);
      ui.posting = true;
      ui.commentErr = '';
      draw();
      let res;
      try {
        res = await Q.sendJSON('POST', '/api/results/comment/post', {task: t.task, sha256: m.sha256});
      } catch (e) {
        if (!ctx.alive()) return;
        ui.posting = false;
        ui.commentErr = refusal(e, 'Post comment');
        draw();
        (post.hidden ? status : post).focus();
        if (e && e.status === 409) look();
        return;
      }
      if (!ctx.alive()) return;
      ui.posting = false;
      const at = str(res && res.posted_at) || new Date().toISOString();
      t.comment = Object.assign({}, m, {state: 'posted', posted_at: at,
        comment_id: res && res.comment_id ? res.comment_id : null, error: null});
      // a posted comment moves the test to Done (History); Mark done goes
      t.group = 'done';
      t.can_mark_done = false;
      t.done_how = {how: 'posted', at: at, by_task: null, url: clickupUrl(t, m)};
      draw();
      status.focus();
      // a test in Done shows no Plan again and no Report issue: the test again, as Mark done reads it (the server has
      // the posted marker before it answers), else they go here
      let data = null;
      try {
        data = await Q.getJSON(page.src);
      } catch (e) { /* put away below */ }
      if (!ctx.alive() || !sec.isConnected) return;
      const fresh = freshTest(page, data);
      if (fresh && commentOf(fresh).state === 'posted') {
        drawPage(page, data);
        if (page.comment) page.comment.status.focus();
      } else {
        for (const b of doneHides(page)) b.hidden = true;
      }
    });

    draw();
    if (waits()) poll();
    return {add: add, post: post, copy: copy, fold: fold, status: status, why: why, err: err,
      busy: function () { return ui.adding || ui.posting || waits(); }};
  }

  // ---- the ticket part of a row: Report issue and the drafts with File ticket (2026-11-03) ----

  // On a row where a device failed or partly passed: Report issue asks Hover for a Swiss Army Knife ticket draft
  // (bug-draft, in Owner's ClickUp style) and waits for it; the drafts show with File ticket. A test in Done offers no
  // Report issue: a failure it had failed again in a newer test, which its 'Failed again in …' link opens. A draft File
  // ticket cannot send shows with its why and does not stand for Report issue (no Report issue on a row it alone holds).
  function ticketPart(c, page, row) {
    const ctx = page.ctx;
    const ui = {pressing: false, watchDraft: Boolean(draftingOf(c)), draftMisses: 0,
      reportErr: draftingOf(c) ? '' : draftFailed(c), dirs: null, timer: null};
    const report = str(c.task) && page.test.group !== 'done' && (reportOf(c).enabled || standing(c).length > 0 ||
      Boolean(draftingOf(c)) || Boolean(draftFailed(c))) ?
      el('button', {type: 'button', class: 'btn rv-report-btn'}, 'Report issue') : null;
    const ticketStatus = el('p', {class: 'rv-status', role: 'status', tabindex: '-1'});
    const ticketSlot = el('div');
    const ticketErr = el('div', {class: 'rv-error'});
    const ticketPanel = el('div', {class: 'rv-ticket-panel'}, ticketStatus, ticketSlot, ticketErr);

    // Report issue is the next thing to do while something went wrong and no ticket of the row is filed, filing or set
    // aside (a draft File ticket cannot send: views.py's next skips a row with any drafted ticket too). A File ticket
    // press changes that without drawing the row again, so the page asks before it picks its button.
    function sync() {
      if (!report) return;
      const can = reportOf(c).enabled || standing(c).length > 0;
      report.disabled = !can || ui.watchDraft || Boolean(draftingOf(c)) || ui.pressing;
      report.classList.toggle('rv-next', can && !draftsOf(c).some(function (t) {
        return ticketBegun(t) || unfileable(t);
      }));
    }

    function draw() {
      const drafts = draftsOf(c);
      const drafting = ui.watchDraft || Boolean(draftingOf(c));
      sync();
      // the drafts are built again only when they changed (a new draft came in)
      const dirs = drafts.map(draftKey).join('\n');
      if (dirs !== ui.dirs) {
        ui.dirs = dirs;
        Q.put(ticketSlot, ticketBlock(c, page));
      }
      const req = draftingOf(c) && draftingOf(c).request;
      const words = obj(req) ? REQ_WORDS[req.state] : null;
      Q.put(ticketStatus, drafting ? 'Drafting the ticket…' + (words ? ' ' + words : '') : null);
      ticketStatus.hidden = !drafting;
      Q.put(ticketErr, ui.reportErr ? Q.errorBox(ui.reportErr) : null);
      ticketPanel.hidden = !(drafting || ui.reportErr || drafts.length);
      markPrimary(page);
    }

    // a newer copy of this card (null when it was not in the look): the ticket objects stay the same (ticketBlock
    // holds them) and take the new fields; the watch ends when the draft came, or after MISSES_MAX looks that show
    // neither it nor the request. The draft that came is one that was not in the card before this look, whether File
    // ticket can send it or not (one it cannot shows with its why); a draft set aside before the press never ends the
    // watch. It also takes away an earlier refusal, which no longer holds (a 409 while it was being written, say).
    function take(fresh) {
      const had = draftsOf(c).map(draftKey);
      if (fresh) {
        const old = Q.list(c.tickets);
        c.tickets = Q.list(fresh.tickets).map(function (ft) {
          const t = ft && str(ft.dir) ? old.find(function (x) { return x && x.dir === ft.dir; }) : null;
          if (!t) return ft;
          refresh(t, ft);
          return t;
        });
        c.report = fresh.report;
      }
      const came = draftsOf(c).some(function (t) { return had.indexOf(draftKey(t)) < 0; });
      if (came) ui.reportErr = '';
      if (ui.watchDraft) {
        if (fresh && came) ui.watchDraft = false;
        else if (fresh && draftingOf(c)) ui.draftMisses = 0;
        else if (draftFailed(c) || ++ui.draftMisses >= MISSES_MAX) {
          ui.watchDraft = false;
          ui.reportErr = draftFailed(c) || (fresh ? 'The ticket was not drafted. Press Report issue to try again.' :
            'This result is no longer in the list. Reload the page to see where the ticket stands.');
        }
      }
    }

    // while the draft request is open: the test again every POLL_MS, until the draft came or the page changes
    function poll() {
      if (ui.timer) return;
      ui.timer = setTimeout(async function tick() {
        ui.timer = null;
        if (!ctx.alive() || !row.isConnected || !ui.watchDraft) return;
        let data = null;
        try {
          data = await Q.getJSON(page.src);
        } catch (e) { /* the next look tries again */ }
        if (!ctx.alive() || !row.isConnected) return;
        if (data) {
          took(page, data);
          take(findCard(data, c));
        }
        draw();
        if (ui.watchDraft) ui.timer = setTimeout(tick, POLL_MS);
      }, POLL_MS);
    }

    // one look right away, after a 409 (the draft may be there already, or its request open)
    async function look() {
      let data = null;
      try {
        data = await Q.getJSON(page.src);
      } catch (e) {
        return;
      }
      if (!ctx.alive() || !row.isConnected) return;
      const fresh = findCard(data, c);
      if (!fresh) return;
      took(page, data);
      if (draftingOf(fresh)) ui.watchDraft = true;
      take(fresh);
      draw();
      if (ui.watchDraft) poll();
    }

    // Report issue: a card with drafts that stand points at them; else the worker drafts one
    if (report) {
      report.addEventListener('click', async function () {
        if (standing(c).length) {
          const file = Array.prototype.slice.call(ticketPanel.querySelectorAll('.rv-file-btn')).find(function (b) {
            return !b.hidden;
          });
          (file || ticketPanel.querySelector('.rv-status') || report).focus();
          return;
        }
        if (ui.pressing || ui.watchDraft) return;
        ui.pressing = true;
        ui.reportErr = '';
        draw();
        try {
          await Q.sendJSON('POST', '/api/tickets/draft', {task: c.task, case: c.case});
        } catch (e) {
          if (!ctx.alive()) return;
          ui.pressing = false;
          ui.reportErr = refusal(e, 'Report issue');
          draw();
          report.focus();
          if (e && e.status === 409) look();
          return;
        }
        if (!ctx.alive()) return;
        ui.pressing = false;
        ui.watchDraft = true;
        ui.draftMisses = 0;
        draw();
        ticketStatus.focus();
        poll();
      });
    }

    draw();
    if (ui.watchDraft) poll();
    return {nodes: el('div', {class: 'rv-ticket-part'}, report ? el('div', {class: 'rv-actions'}, report) : null,
      ticketPanel),
    sync: sync,
    busy: function () {
      return ui.pressing || ui.watchDraft || draftsOf(c).some(function (t) {
        return ticketState(t) === 'filing' || page.filing.has(t.dir);
      });
    }};
  }

  // ---- ticket drafts ----

  // The ticket fields the server adds (2026-11-03): state ('draft', 'waiting_online', 'approved', 'filing', 'filed',
  // 'failed'), offline (the test ran with the device offline), cl_id (once filed), request (the worker's "file" request:
  // an id or {id, state, notes}), error (why the last try failed) and target (the device tag).
  function ticketReq(t) {
    if (obj(t.request)) return t.request;
    return str(t.request) ? {id: t.request} : null;
  }

  // filed; filing while the worker's request is open; else a draft (with the error of a failed try, if any)
  function ticketState(t) {
    if (t.state === 'filed' || t.filed === true || str(t.cl_id)) return 'filed';
    const r = ticketReq(t);
    if (t.state === 'filing' || (r && REQ_OPEN.indexOf(r.state) >= 0)) return 'filing';
    return 'draft';
  }

  function ticketError(t) {
    if (str(t.error)) return t.error;
    const r = ticketReq(t);
    if (!r || (r.state !== 'failed' && r.state !== 'cancelled')) return '';
    const notes = Q.list(r.notes);
    const last = notes.length ? notes[notes.length - 1] : null;
    if (last && str(last.text)) return last.text;
    return r.state === 'cancelled' ? 'Filing was cancelled.' : 'Filing did not go through.';
  }

  function ticketTag(t, c) {
    const r = ticketReq(t);
    const tag = str(t.target) || str(t.tag) || str(r && r.target);
    if (tag) return tag;
    const devs = Q.list(c.devices);
    return devs.length === 1 && str(devs[0].tag) ? devs[0].tag : 'the device';
  }

  // POST /api/tickets/file answers 403 when the person did not approve (approvals.NOT_APPROVED: cancelled, no answer in
  // 90 s, Touch ID and password not available), and also for a stale page token (security.py)
  function fileError(e) {
    return refusal(e, 'File ticket');
  }

  // the server's newest copy of a ticket, found by its folder
  function findTicket(data, dir) {
    for (const c of Q.list(data && data.cards)) {
      const t = Q.list(c.tickets).find(function (x) { return x && x.dir === dir; });
      if (t) return t;
    }
    return null;
  }

  function refresh(t, fresh) {
    for (const k of Object.keys(t)) if (!(k in fresh)) delete t[k];
    Object.assign(t, fresh);
  }

  // A failed run's Swiss Army Knife ticket draft (2026-11-03): the worker writes it. File ticket asks for Touch ID on
  // the Mac (the server waits for it), then the worker files it with the Swiss Army Knife on the device; the person's
  // press plus the fingerprint is the OK for that one ClickUp write.
  function ticketBlock(c, page) {
    const drafts = draftsOf(c);
    if (!drafts.length) return null;
    return el('section', {class: 'rv-ticket', 'aria-label': drafts.length > 1 ? 'Ticket drafts' : 'Ticket draft'},
      drafts.map(function (t) { return ticketOne(t, c, page); }));
  }

  function ticketOne(t, c, page) {
    const ctx = page.ctx;
    const tag = ticketTag(t, c);
    const note = el('div');
    const status = el('span', {class: 'rv-status', role: 'status', tabindex: '-1'});
    const err = el('div', {class: 'rv-error'});
    const btn = str(t.dir) ? el('button', {type: 'button', class: 'btn rv-file-btn'}, 'File ticket') : null;
    let waiting = false;  // the server is waiting for Touch ID
    let failed = '';      // why the last press did not go through
    let timer = null;
    let misses = 0;       // looks in a row that did not have this ticket
    let lost = false;     // MISSES_MAX of them: where the ticket stands is not known any more

    function draw() {
      const state = ticketState(t);
      // File ticket only for a draft the server would file (its run still failed, on the engine account)
      const blocked = unfileable(t);
      const error = lost ? TICKET_LOST : state === 'draft' && !waiting && !blocked ? failed || ticketError(t) : '';
      if (btn) {
        btn.hidden = state !== 'draft' || blocked;
        btn.disabled = waiting;
      }
      Q.put(note, state === 'draft' && !blocked && t.offline === true && !error && !waiting ?
        el('p', {class: 'rv-ticket-note'}, 'This test ran with ' + tag + ' offline. Connect ' + tag +
          ' to the internet, then press File ticket.') : null);
      status.classList.toggle('rv-ticket-done', state === 'filed');
      if (waiting) Q.put(status, 'Confirm with Touch ID on the Mac…');
      else if (state === 'filed') Q.put(status, icon('passed'), str(t.cl_id) ? 'Filed: ' + t.cl_id : 'Filed');
      else if (lost) Q.put(status);
      else if (state === 'filing') {
        const r = ticketReq(t);
        Q.put(status, 'Filing on ' + tag + '…' + (r && REQ_WORDS[r.state] ? ' ' + REQ_WORDS[r.state] : ''));
      } else if (blocked) {
        Q.put(status, str(t.fileable_why) || NOT_FILEABLE);
      } else if (btn) {
        Q.put(status, 'Not filed yet. File ticket asks for Touch ID on the Mac, then the AI files it on ' + tag + '.');
      } else {
        Q.put(status, 'Not filed yet. To file it with the Swiss Army Knife, type /sak-submit ' + (t.n || '') +
          ' in Claude; it asks for your OK.');
      }
      Q.put(err, error ? Q.errorBox(error) : null);
      markPrimary(page);
    }

    // while the worker's request is open: the test again every POLL_MS, until filed or failed or the page changes; a
    // ticket that MISSES_MAX looks in a row do not have (the page asks ?case=X for a chat run, and that answer can move
    // on to another run of X) is no longer followed
    function poll() {
      if (timer) return;
      timer = setTimeout(async function tick() {
        timer = null;
        if (!ctx.alive() || !box.isConnected || ticketState(t) !== 'filing' || lost) return;
        try {
          const data = await Q.getJSON(page.src);
          if (!ctx.alive() || !box.isConnected) return;
          took(page, data);
          const fresh = findTicket(data, t.dir);
          if (fresh) {
            refresh(t, fresh);
            misses = 0;
          } else if (++misses >= MISSES_MAX) lost = true;
        } catch (e) { /* the next look tries again */ }
        draw();
        if (ticketState(t) === 'filing' && !lost) timer = setTimeout(tick, POLL_MS);
      }, POLL_MS);
    }

    async function file() {
      if (waiting) return;
      waiting = true;
      page.filing.add(t.dir);
      failed = '';
      draw();
      let res;
      try {
        res = await Q.sendJSON('POST', '/api/tickets/file', {dir: t.dir});
      } catch (e) {
        if (!ctx.alive()) return;
        waiting = false;
        page.filing.delete(t.dir);
        failed = fileError(e);
        draw();
        btn.focus();
        return;
      }
      if (!ctx.alive()) return;
      waiting = false;
      page.filing.delete(t.dir);
      if (obj(res.ticket)) refresh(t, res.ticket);
      else {
        t.state = 'filing';
        t.error = null;
        if (res.request) t.request = res.request;
        else if (str(res.id)) t.request = {id: res.id, state: 'new'};
      }
      draw();
      status.focus();
      poll();
    }

    if (btn) btn.addEventListener('click', file);
    const box = el('div', {class: 'rv-ticket-one'},
      el('div', {class: 'rv-ticket-head'}, el('span', {class: 'rv-ticket-label'}, 'Ticket draft'),
        el('span', {class: 'rv-ticket-title'}, str(t.title))),
      note,
      el('details', {class: 'rv-ticket-desc'}, el('summary', {}, 'Show the description'),
        el('pre', {}, str(t.description))),
      el('div', {class: 'rv-actions'}, btn,
        Q.copyButton(function () { return str(t.title); }, 'Copy title'),
        Q.copyButton(function () { return str(t.description); }, 'Copy description'),
        status),
      err);
    draw();
    if (ticketState(t) === 'filing') poll();
    return box;
  }

  // ---- the case rows ----

  function colsClass(n) {
    return n > COLS_MAX ? 'rv-cols-many' : 'rv-cols-' + Math.max(1, n);
  }

  function rowsHead(tags) {
    return el('div', {class: 'rv-case-line rv-case-head', 'aria-hidden': 'true'},
      el('span', {class: 'rv-case-title'}, 'Case'),
      tags.map(function (tag) { return el('span', {class: 'rv-cell'}, deviceName(tag)); }));
  }

  // one chip per device, so a device that passed is never hidden behind the worst result; the device's name shows on
  // a phone, where the columns stack, and is read out everywhere
  function deviceCell(c, tag) {
    const d = Q.list(c.devices).find(function (x) { return x && x.tag === tag; });
    const name = el('span', {class: 'rv-cell-tag'}, deviceName(tag) + ': ');
    if (!d) {
      return el('span', {class: 'rv-cell rv-cell-none'}, name, el('span', {'aria-hidden': 'true'}, '–'),
        el('span', {class: 'sr-only'}, 'not run'));
    }
    return el('span', {class: 'rv-cell'}, name, verdictBadge(d.verdict, str(d.verdict_label) || null));
  }

  function caseBody(c, page, tags, row) {
    const pick = texts(c);
    const ex = obj(c.explain) || {};
    // the ticket part only where a device failed or partly passed, not in Done (and on a drafted ticket, also of a
    // run from a chat)
    const tp = (reportOf(c).enabled && str(c.task) && page.test.group !== 'done') || draftsOf(c).length ||
      draftingOf(c) || draftFailed(c) ? ticketPart(c, page, row) : null;
    if (tp) page.parts.push(tp);
    // a failure the server takes no ticket for (views.py report.why: it ran on an earlier engine account, or on a
    // device tickets are not filed on) has no Report issue; its row says why instead (review 2026-11-09)
    const rep = reportOf(c);
    const noReport = !tp && str(c.task) && page.test.group !== 'done' && !rep.enabled && rep.why &&
      Q.list(c.devices).some(function (d) { return d && BAD.indexOf(d.verdict) >= 0; }) ?
      el('p', {class: 'rv-why rv-report-why'}, 'Report issue: ' + rep.why + (/[.!?]$/.test(rep.why) ? '' : '.')) :
      null;
    return el('div', {class: 'rv-case-body'},
      str(c.what_it_checks) ? el('p', {class: 'rv-checks'}, el('span', {class: 'strong'}, 'What it checks: '),
        c.what_it_checks) : null,
      stepTable(c, tags),
      facts([['What happened', pick('happened')], ['Why', pick('why')], ['Not tested', str(ex.not_tested)]]),
      proofBlock(c),
      tp ? tp.nodes : noReport,
      el('p', {class: 'rv-records'}, el('a', {class: 'rv-quiet', href: Q.routeHash('results', {view: 'detail',
        task: str(c.task), case: str(c.case), run: Q.list(c.run_ids)[0] || ''})}, 'Run records ›')));
  }

  // A case: its title, a chip per device and, when it did not pass, one line of Hover's why; it opens in place.
  // A row where a device failed or partly passed (the server's open), or the case the link names, is open.
  function caseRow(c, page, tags) {
    const target = Boolean(page.target) && c.case === page.target;
    const row = el('details', {class: ['rv-case', target ? 'rv-target' : null], open: c.open === true || target});
    const why = str(c.why_line) ? el('span', {class: 'rv-why-line', title: c.why_line}, c.why_line) : null;
    // the grid sits inside the summary: some browsers do not lay out a summary as a grid
    Q.add(row, [el('summary', {}, el('div', {class: 'rv-case-line'},
      el('span', {class: 'rv-case-title'}, el('span', {class: 'rv-case-name'}, str(c.title) || str(c.case)), why),
      tags.map(function (tag) { return deviceCell(c, tag); }))),
    caseBody(c, page, tags, row)]);
    row.addEventListener('toggle', function () { markPrimary(page); });
    return row;
  }

  function caseRows(page) {
    const tags = tagsOf(page.test.devices);
    const cards = page.cards.filter(function (c) { return obj(c); });
    return el('section', {class: ['card', 'rv-cases', colsClass(tags.length)], 'aria-label': 'Cases'},
      cards.length ? [rowsHead(tags), cards.map(function (c) { return caseRow(c, page, tags); })] :
        el('p', {class: 'muted'}, 'No case of this test has a result yet.'));
  }

  // ---- the live box: the test while it runs (2026-11-03 Running now, in the box since 2026-11-04) ----

  // where one device of the running test stands: "Step 3 of 6: Maps import, 29 m DSM: accepted" (a case's part),
  // else "Step 57 of 109: <the engine step>"
  function liveStep(d) {
    const wait = count(d.waits_at);
    if (wait) return 'waits for your OK at step ' + wait;
    if (d.state === 'finished') return 'Finished';
    const p = obj(d.part);
    if (p && count(p.n)) return 'Step ' + p.n + (count(p.of) ? ' of ' + p.of : '') + (str(p.text) ? ': ' + p.text : '');
    const n = count(d.step);
    const of = count(d.of);
    const text = str(d.text);
    if (n) return 'Step ' + n + (of ? ' of ' + of : '') + (text ? ': ' + text : '');
    return d.state === 'running' ? 'Starting' : 'Waiting to start';
  }

  function liveHead(r, begun) {
    if (r.wingmate === 'off') return 'Hover is off';
    if (r.wingmate === 'waiting') return 'Waiting for Hover';
    const cc = obj(r.current_case);
    if (cc && count(cc.n) && count(cc.of)) return 'Running · case ' + cc.n + ' of ' + cc.of;
    return begun ? 'Running' : 'Waiting to start';
  }

  // the newest screenshot of a device, opened in a new tab; the same node from look to look while the picture stays
  // the same, so it is not fetched again every 4 s
  function runShot(url, tag, node) {
    node.used.add(url);
    let a = node.shots.get(url);
    if (a) return a;
    const img = el('img', {src: url, alt: 'Newest screenshot from ' + tag, loading: 'lazy'});
    a = el('a', {class: 'rv-run-shot', href: url, target: '_blank', rel: 'noopener noreferrer',
      title: 'Open the screenshot in a new tab'}, img);
    img.addEventListener('error', function () { a.hidden = true; });
    node.shots.set(url, a);
    return a;
  }

  function runDevice(d, node) {
    const tag = String(d.tag);
    const step = liveStep(d);
    const url = isFileUrl(d.shot_url) ? d.shot_url : null;
    return el('li', {class: ['rv-run-dev', url ? null : 'rv-no-shot']},
      el('div', {class: 'rv-run-text'}, el('span', {class: 'strong'}, tag), ' ',
        el('span', {class: 'rv-run-step', title: step.length > STEP_HINT_MAX ? step : null}, cut(step, STEP_HINT_MAX))),
      url ? runShot(url, tag, node) : null);
  }

  function liveNode() {
    const node = {shots: new Map(), used: new Set(), head: el('h2', {class: 'rv-verdict'}), devs: el('div'),
      note: el('p', {class: 'rv-run-note', role: 'status'}), rows: el('div')};
    node.sec = el('section', {class: 'card rv-box rv-live', 'aria-label': 'Running'}, node.head, node.devs, node.note);
    return node;
  }

  function drawLive(page) {
    const r = page.running;
    const node = page.live;
    const devs = Q.list(r.devices).filter(function (d) { return obj(d) && str(d.tag); });
    devs.sort(function (a, b) { return tagRank(a.tag) - tagRank(b.tag); });
    const begun = r.state !== 'queued' && devs.some(function (d) {
      return d.state === 'running' || d.state === 'finished' || count(d.step) > 0;
    });
    Q.put(node.head, el('span', {class: 'rv-verdict-icon rv-v-running'}, shapesIcon(RUN_ICON)),
      el('span', {}, liveHead(r, begun)));
    node.used = new Set();
    Q.put(node.devs, devs.length ?
      el('ul', {class: 'rv-run-devs'}, devs.map(function (d) { return runDevice(d, node); })) : null);
    for (const url of Array.from(node.shots.keys())) if (!node.used.has(url)) node.shots.delete(url);
    const newest = devs.map(function (d) { return str(d.updated); }).filter(dateOf).sort().pop();
    Q.put(node.note, newest ? ['Updated ', timeTag(newest, stampText(newest)),
      ' · the results show here when the test ends'] : 'The results show here when the test ends.');
    // the case rows say done, running or waiting, and '–' for a device the plan does not run that case on (null);
    // their verdicts come when the test ends
    const tags = devs.map(function (d) { return d.tag; });
    const rows = Q.list(r.case_rows).filter(function (x) { return obj(x); });
    Q.put(node.rows, rows.length ? el('section', {class: ['card', 'rv-cases', colsClass(tags.length)],
      'aria-label': 'Cases'}, rowsHead(tags), rows.map(function (cr) {
      const states = obj(cr.devices) || {};
      return el('div', {class: 'rv-case-line rv-live-row'},
        el('span', {class: 'rv-case-title'}, el('span', {class: 'rv-case-name'}, str(cr.title) || str(cr.case))),
        tags.map(function (tag) {
          const name = el('span', {class: 'rv-cell-tag'}, deviceName(tag) + ': ');
          if (states[tag] === null || states[tag] === undefined) {
            return el('span', {class: 'rv-cell rv-cell-none'}, name, el('span', {'aria-hidden': 'true'}, '–'),
              el('span', {class: 'sr-only'}, 'does not run this case'));
          }
          const s = LIVE_WORDS[states[tag]] ? states[tag] : 'waiting';
          return el('span', {class: 'rv-cell rv-live-' + s}, name, LIVE_WORDS[s]);
        }));
    })) : null);
  }

  // the test again every RUN_POLL_MS while it runs; when it ends, the box becomes the result box in place. A 404 means
  // the test ended without results (its start was cancelled or stopped before any run).
  function runPoll(page) {
    if (page.timer) return;
    page.timer = setTimeout(async function tick() {
      page.timer = null;
      if (!page.ctx.alive() || !page.view.isConnected || !page.running) return;
      let data = null;
      let gone = false;
      try {
        data = await Q.getJSON(page.src);
      } catch (e) {
        gone = Boolean(e && e.status === 404);
      }
      if (!page.ctx.alive() || !page.view.isConnected) return;
      if (gone) {
        page.running = null;
        page.live.note.hidden = true;
        Q.put(page.live.rows);
        Q.put(page.live.head, el('span', {class: 'rv-verdict-icon rv-v-not_tested'}, icon('not_tested')),
          el('span', {}, 'Ended without results'));
        Q.put(page.live.devs, el('p', {class: 'rv-run-note'}, 'This test is no longer running, and no result has ' +
          'come in: its start was cancelled or stopped before any run. ', el('a', {href: Q.routeHash('new')},
          'New test'), ' · ', el('a', {href: Q.routeHash('history')}, 'History')));
        return;
      }
      const fresh = freshTest(page, data);
      if (fresh && !obj(data.running)) {
        drawPage(page, data);
        return;
      }
      if (fresh) {
        page.test = fresh;
        page.running = data.running;
        drawLive(page);
      }
      page.timer = setTimeout(tick, RUN_POLL_MS);
    }, RUN_POLL_MS);
  }

  // ---- drawing the page ----

  function drawPage(page, data) {
    page.data = data;
    page.test = data.test;
    page.cards = Q.list(data.cards);
    page.running = obj(data.running);
    page.parts = [];
    page.markDone = null;
    page.comment = null;
    page.live = null;
    const t = page.test;
    if (page.running) {
      page.live = liveNode();
      drawLive(page);
      Q.put(page.view, topLine(t), headCard(page), page.live.sec, page.live.rows);
      runPoll(page);
      return;
    }
    Q.put(page.view, topLine(t), headCard(page), resultBox(page), caseRows(page));
    markPrimary(page);
    if (page.target && !page.scrolled) {
      page.scrolled = true;
      const hit = page.view.querySelector('.rv-target');
      if (hit) hit.scrollIntoView({block: 'start'});
    }
  }

  function linksLine(lead) {
    return el('p', {}, lead || null, lead ? ' ' : null, 'Start a test on ', el('a', {href: Q.routeHash('new')}, 'New test'),
      ', or see your tests in ', el('a', {href: Q.routeHash('history')}, 'History'), '.');
  }

  // ?case=X that no engine run has (404 "no run of X"): its back-test and hand records, which this page leaves out, are
  // on the detailed page (the case library shows their verdicts)
  function noEngineRun(cs) {
    return [el('p', {}, 'No engine run of ' + cs + '. Its back-test records, if any, are in ',
      el('a', {href: Q.routeHash('results', {view: 'detail', case: cs})}, 'Run records ›')), linksLine('')];
  }

  // #/results shows the newest test by Run press (?task=current), #/results?task=T that test, #/results?case=X the
  // newest test that ran X with X open (a run from a chat: that case alone); then the page asks for that test only
  async function render(root, params, alive, qa) {
    Q = qa;
    const task = params.get('task');
    const cs = params.get('case');
    const page = {ctx: {alive: alive}, view: el('div', {class: 'rv'}, Q.loading('Loading results…')), src: null,
      test: null, cards: [], running: null, parts: [], markDone: null, comment: null, live: null, timer: null,
      target: cs || null, scrolled: false, askedOpen: false, filing: new Set()};
    Q.add(root, [page.view]);
    let data;
    try {
      data = await Q.getJSON('/api/results/cards' + Q.qs({task: task || (cs ? '' : 'current'), case: cs || ''}));
    } catch (e) {
      if (!alive()) return;
      Q.put(page.view, Q.viewHead('Results'), e && e.status === 404 ?
        el('div', {class: 'card rv-empty'}, cs && !task ? noEngineRun(cs) : linksLine(capital(str(e.message)) + '.')) :
        Q.errorBox(e, 'The results did not load'));
      return;
    }
    if (!alive()) return;
    if (!obj(data) || !obj(data.test)) {
      Q.put(page.view, Q.viewHead('Results'), el('div', {class: 'card rv-empty'}, linksLine('No results yet.')));
      return;
    }
    const chat = !str(data.test.task);
    page.src = '/api/results/cards' + (chat ? Q.qs({case: cs || str((Q.list(data.cards)[0] || {}).case)}) :
      Q.qs({task: data.test.task}));
    drawPage(page, data);
  }

  window.QAResults = {render: render};
})();
