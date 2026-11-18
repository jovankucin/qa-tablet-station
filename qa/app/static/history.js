// history.js: the History page (2026-11-02). Defines window.QAHistory = {render(root, params, alive, QA)}.
/* Since 2026-11-04 a first card, "Your tests" (Ongoing · Tested · Done, GET /api/results/tests), lists every test the
person ran; Results shows one test at a time. The ERROR verdict reads "Test error" (the test itself stopped).
Two KPI groups and five charts over GET /api/stats?days=N (views.quality_stats: engine runs only, a correction
counts instead of the run it corrects, NOT_TESTED slots and self-tests left out), then the old page's bug table and
"What was done" list (app.js bugRows, bugTable, doneList over /api/results, /api/tickets and the bug register).

Charts are inline SVG drawn here at the card's own width (redrawn when it changes), each with a table twin. Rules from
the dataviz method: one y-axis, thin bars with a 4px rounded outer end, a 2px gap between stacked segments, hairline
grid, a legend for two or more series, totals at bar ends only, a tooltip per mark on a hit area larger than the mark.
Verdicts are status colours with an icon and a label, in the fixed order PASS, PARTIAL, FAIL, BLOCKED, ERROR; a test
error (ERROR) is hatched so it does not read as a result. CSP: no inline style attribute anywhere; colours come
from classes in history.css, the meter and the tooltip are placed with el.style from JS.
*/
(function () {
  'use strict';

  const PERIODS = [7, 30, 90];
  const DAYS_DEFAULT = 30;
  const DAYS_MAX = 120;  // views.DAYS_MAX: the server refuses more
  const ORDER = ['PASS', 'PARTIAL', 'FAIL', 'BLOCKED', 'ERROR'];
  const LABEL = {PASS: 'Passed', PARTIAL: 'Partly passed', FAIL: 'Failed', BLOCKED: 'Blocked', ERROR: 'Test error'};
  const LEGEND_LABEL = {ERROR: 'Test error (the test itself stopped)'};
  // Fills a white count label cannot sit on (contrast), so their labels are near-black.
  const DARK_TEXT = {PASS: true, PARTIAL: true, ERROR: true};
  const DEVICES = ['TA5', 'ET65', 'W044', 'TA3'];
  const CAUSES = {
    'test case': {label: 'Test case', means: 'the test case was wrong or out of date',
      most: 'were stopped by a wrong test case'},
    engine: {label: 'Engine', means: 'the test runner itself broke', most: 'broke in the engine'},
    'device not ready': {label: 'Device not ready', means: 'a tablet was locked, lost or not connected',
      most: 'found a device that was not ready'},
    'W044 browser': {label: 'W044 browser', means: 'Chrome on W044 (sign-in, tab or time-out)',
      most: 'broke in the W044 browser'},
    other: {label: 'Other', means: 'none of the causes above', most: 'had another cause'},
  };
  const DEF_NAMES = [['pass_rate', 'Pass rate'], ['fails', 'Failures'], ['coverage', 'Coverage'],
    ['decided_rate', 'Runs that decided'], ['false_fails', 'False failures corrected'], ['hands_off', 'Hands-off runs'],
    ['times', 'Time to result']];
  const BUG_DEF = 'Bugs in the bug list found in this period: filed means we wrote the ticket, linked means an ' +
    'existing ticket already covered it, fixed means it was fixed before a ticket.';
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const GAP = 2;       // surface gap between stacked segments and between adjacent bars
  const RADIUS = 4;    // rounded data end, away from the baseline
  const BAR = 18;      // thickness of horizontal bars (thin: at most 24)
  const COL_MAX = 24;  // widest column

  let Q = null;  // the app.js helpers (window.QA), handed in by render
  let uid = 0;   // ids of the hatch patterns: one per chart, unique in the document

  function el() {
    return Q.el.apply(null, arguments);
  }

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

  // ---- numbers and words ----

  function num(x) {
    const n = Number(x);
    return x !== null && x !== undefined && isFinite(n) ? n : 0;
  }

  function has(x) {
    return x !== null && x !== undefined && isFinite(Number(x));
  }

  function pct(v) {
    if (!has(v)) return '–';
    const n = Number(v);
    return (Number.isInteger(n) ? String(n) : n.toFixed(1)) + '%';
  }

  function plural(n, one, many) {
    return n === 1 ? one : many;
  }

  function runs(n) {
    return n + plural(n, ' run', ' runs');
  }

  // "42 s", "2 min 49 s"
  function dur(s) {
    if (!has(s)) return '–';
    const t = Math.round(Number(s));
    if (t < 60) return t + ' s';
    return Math.floor(t / 60) + ' min' + (t % 60 ? ' ' + (t % 60) + ' s' : '');
  }

  // "3.5 min" for a tile or a bar end
  function shortDur(s) {
    if (!has(s)) return '–';
    const t = Number(s);
    if (t < 60) return Math.round(t) + ' s';
    if (t < 3600) return (t / 60).toFixed(1).replace(/\.0$/, '') + ' min';
    return (t / 3600).toFixed(1).replace(/\.0$/, '') + ' h';
  }

  function dateOf(day) {
    const p = String(day).split('-').map(Number);
    return p.length === 3 && p.every(isFinite) ? new Date(p[0], p[1] - 1, p[2], 12) : null;
  }

  function dayText(day, withYear) {
    const d = dateOf(day);
    if (!d) return String(day || '');
    return d.getDate() + ' ' + MONTHS[d.getMonth()] + (withYear ? ' ' + d.getFullYear() : '');
  }

  function weekdayText(day) {
    const d = dateOf(day);
    return d ? WEEKDAYS[d.getDay()] + ' ' + dayText(day) : String(day || '');
  }

  function isoDay(d) {
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  function capital(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  }

  function readDays(raw) {
    const n = Number(raw);
    return raw && Number.isInteger(n) && n >= 1 && n <= DAYS_MAX ? n : DAYS_DEFAULT;
  }

  // The counts of one series row in the fixed order, with their total.
  function counts(row) {
    const c = {total: 0};
    for (const v of ORDER) {
      c[v] = num(row && row[v]);
      c.total += c[v];
    }
    return c;
  }

  function breakdown(c) {
    return ORDER.filter(function (v) { return c[v]; }).map(function (v) { return LABEL[v].toLowerCase() + ' ' + c[v]; })
      .join(', ');
  }

  // Clean axis steps: at most five intervals; whole numbers for counts.
  function scaleFor(top, whole) {
    const steps = whole ? [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000] : [0.5, 1, 2, 5, 10, 20, 50, 100, 200];
    const n = Math.max(0, Number(top) || 0);
    if (!n) return {max: 1, step: 1};
    for (const s of steps) if (Math.ceil(n / s) <= 5) return {max: Math.ceil(n / s) * s, step: s};
    const s = steps[steps.length - 1];
    return {max: Math.ceil(n / s) * s, step: Math.ceil(n / s / 5) * s};
  }

  function ticksOf(sc) {
    const out = [];
    for (let v = 0; v <= sc.max + 1e-9; v += sc.step) out.push(Math.round(v * 100) / 100);
    return out;
  }

  // ---- keys, icons and the tooltip ----

  // The glyph of a verdict on its colour key: check, half, cross, no entry, question mark.
  function glyph(v) {
    const s = svg('svg', {viewBox: '0 0 14 14', 'aria-hidden': 'true', focusable: 'false'});
    if (v === 'PASS') s.appendChild(svg('path', {d: 'M3.6 7.3 6 9.6 10.4 4.7'}));
    else if (v === 'PARTIAL') {
      s.appendChild(svg('circle', {cx: 7, cy: 7, r: 3.6}));
      s.appendChild(svg('path', {d: 'M7 3.4a3.6 3.6 0 0 1 0 7.2z', class: 'hx-gfill'}));
    } else if (v === 'FAIL') s.appendChild(svg('path', {d: 'M4.6 4.6 9.4 9.4M9.4 4.6 4.6 9.4'}));
    else if (v === 'BLOCKED') {
      s.appendChild(svg('circle', {cx: 7, cy: 7, r: 3.8}));
      s.appendChild(svg('path', {d: 'M4.4 9.6 9.6 4.4'}));
    } else if (v === 'ERROR') s.appendChild(svg('path', {d: 'M5.2 5.3a1.9 1.9 0 1 1 2.7 1.7c-.6.3-.9.7-.9 1.3v.3M7 10.5v.1'}));
    return s;
  }

  // A key mirrors its mark: a filled square (verdicts carry their icon), a dashed line for the target.
  function keyNode(k) {
    return el('span', {class: 'hx-key hx-k-' + k, 'aria-hidden': 'true'}, ORDER.indexOf(k) >= 0 ? glyph(k) : null);
  }

  function legendNode(items) {
    return el('ul', {class: 'hx-legend'}, items.map(function (it) {
      return el('li', {}, keyNode(it.key), it.label);
    }));
  }

  function verdictLegend() {
    return ORDER.map(function (v) { return {key: v, label: LEGEND_LABEL[v] || LABEL[v]}; });
  }

  function statusIcon(good) {
    const s = svg('svg', {viewBox: '0 0 16 16', class: 'hx-status-icon', 'aria-hidden': 'true', focusable: 'false'});
    if (good) {
      s.appendChild(svg('circle', {cx: 8, cy: 8, r: 7, class: 'hx-st-good'}));
      s.appendChild(svg('path', {d: 'M4.8 8.3 7 10.4 11.2 5.8', class: 'hx-st-glyph'}));
    } else {
      s.appendChild(svg('path', {d: 'M8 2 14.6 13.6H1.4Z', class: 'hx-st-warn'}));
      s.appendChild(svg('path', {d: 'M8 6.2v3.6M8 11.8v.1', class: 'hx-st-glyph'}));
    }
    return s;
  }

  // One tooltip per page, placed next to the pointer (or the focused mark) with el.style; values lead, labels follow.
  function tooltip() {
    const node = el('div', {class: 'hx-tip', role: 'tooltip', hidden: true});
    function move(x, y) {
      const pad = 8;
      const vw = document.documentElement.clientWidth || window.innerWidth;
      const vh = window.innerHeight;
      const r = node.getBoundingClientRect();
      let left = x + 14;
      let top = y + 14;
      if (left + r.width > vw - pad) left = Math.max(pad, x - 14 - r.width);
      if (top + r.height > vh - pad) top = Math.max(pad, y - 14 - r.height);
      node.style.left = Math.round(left) + 'px';
      node.style.top = Math.round(top) + 'px';
    }
    return {
      node: node,
      show: function (content, x, y) { Q.put(node, content); node.hidden = false; move(x, y); },
      move: function (x, y) { if (!node.hidden) move(x, y); },
      hide: function () { node.hidden = true; },
    };
  }

  function tipBody(title, rows, notes) {
    return el('div', {},
      el('div', {class: 'hx-tip-title'}, title),
      rows.map(function (r) {
        return el('div', {class: 'hx-tip-row'}, r.key ? keyNode(r.key) : null, el('span', {class: 'hx-tip-val'}, r.value),
          el('span', {}, r.label));
      }),
      (notes || []).filter(Boolean).map(function (n) { return el('div', {class: 'hx-tip-note'}, n); }));
  }

  function verdictRows(c) {
    return ORDER.filter(function (v) { return c[v]; }).map(function (v) {
      return {key: v, value: String(c[v]), label: LABEL[v] + (c.total ? ' (' + Math.round(100 * c[v] / c.total) + '%)' : '')};
    });
  }

  // One mark (a column, a bar or a row): the group listens, its transparent hit area is larger than the painted mark;
  // keyboard focus shows the same tooltip as hover.
  function hoverable(g, tip, content, label) {
    g.setAttribute('class', 'hx-item');
    g.setAttribute('tabindex', '0');
    g.setAttribute('role', 'img');
    g.setAttribute('aria-label', label);
    g.addEventListener('pointerenter', function (e) { g.classList.add('on'); tip.show(content(), e.clientX, e.clientY); });
    g.addEventListener('pointermove', function (e) { tip.move(e.clientX, e.clientY); });
    g.addEventListener('pointerleave', function (e) {
      g.classList.remove('on');
      if (e.pointerType !== 'touch') tip.hide();  // a tap keeps its tooltip until the next tap
    });
    g.addEventListener('focus', function () {
      g.classList.add('on');
      const r = g.getBoundingClientRect();
      tip.show(content(), r.left + r.width / 2, r.top);
    });
    g.addEventListener('blur', function () { g.classList.remove('on'); tip.hide(); });
    return g;
  }

  // ---- chart pieces ----

  function f2(n) {
    return Math.round(n * 100) / 100;
  }

  // A bar with the 4px rounded end away from the baseline: 'up' for columns, 'right' for bars.
  function barPath(x, y, w, h, dir, round) {
    const r = round ? Math.max(0, Math.min(RADIUS, dir === 'up' ? w / 2 : h / 2, dir === 'up' ? h : w)) : 0;
    const a = function (ex, ey) { return r ? 'A' + f2(r) + ' ' + f2(r) + ' 0 0 1 ' + f2(ex) + ' ' + f2(ey) : ''; };
    if (dir === 'up') {
      return 'M' + f2(x) + ' ' + f2(y + h) + 'V' + f2(y + r) + a(x + r, y) + 'H' + f2(x + w - r) + a(x + w, y + r) +
        'V' + f2(y + h) + 'Z';
    }
    return 'M' + f2(x) + ' ' + f2(y) + 'H' + f2(x + w - r) + a(x + w, y + r) + 'V' + f2(y + h - r) + a(x + w - r, y + h) +
      'H' + f2(x) + 'Z';
  }

  // Verdict marks wear a class colour; a test error (ERROR) and the causes of test errors wear the chart's hatch.
  function mark(kind, d, hatch) {
    const hatched = kind === 'ERROR' || kind === 'cause';
    return svg('path', {d: d, class: 'hx-mark' + (hatched ? '' : ' hx-m-' + kind), fill: hatched ? 'url(#' + hatch + ')' : null});
  }

  function hatchDefs(id) {
    return svg('defs', {}, svg('pattern', {id: id, width: 5, height: 5, patternUnits: 'userSpaceOnUse',
      patternTransform: 'rotate(45)'},
    svg('rect', {width: 5, height: 5, class: 'hx-hatch-bg'}), svg('rect', {width: 3, height: 5, class: 'hx-hatch-ink'})));
  }

  function chartSvg(w, h, label) {
    return svg('svg', {viewBox: '0 0 ' + w + ' ' + h, width: '100%', class: 'hx-svg', role: 'group', 'aria-label': label});
  }

  function text(x, y, s, cls, anchor) {
    return svg('text', {x: f2(x), y: f2(y), class: cls, 'text-anchor': anchor || null}, s);
  }

  // Label anchored so it stays inside the chart near the edges.
  function edgeText(cx, y, s, cls, w) {
    const half = String(s).length * 3.4;
    if (cx - half < 0) return text(0, y, s, cls, 'start');
    if (cx + half > w) return text(w, y, s, cls, 'end');
    return text(cx, y, s, cls, 'middle');
  }

  // Horizontal gridlines with their values at the left (vertical charts).
  function yGrid(s, sc, x0, x1, y, fmt) {
    for (const v of ticksOf(sc)) {
      if (v) s.appendChild(svg('line', {x1: x0, x2: x1, y1: f2(y(v)), y2: f2(y(v)), class: 'hx-grid'}));
      s.appendChild(text(x0 - 6, y(v) + 4, fmt(v), 'hx-tick', 'end'));
    }
  }

  // Vertical gridlines with their values under the plot (horizontal charts).
  function xGrid(s, sc, x, y0, y1, fmt) {
    for (const v of ticksOf(sc)) {
      s.appendChild(svg('line', {x1: f2(x(v)), x2: f2(x(v)), y1: y0, y2: y1, class: v ? 'hx-grid' : 'hx-axis'}));
      s.appendChild(text(x(v), y1 + 16, fmt(v), 'hx-tick', v ? 'middle' : 'start'));
    }
  }

  // A table twin of a chart: same numbers, readable without the picture. Columns after the first are numbers
  // (right-aligned) unless listed in words.
  function table(heads, rows, note, words) {
    const cls = function (i) { return i && (words || []).indexOf(i) < 0 ? 'num' : null; };
    return [
      el('div', {class: 'table-wrap'}, el('table', {class: 'hx-numbers'},
        el('thead', {}, el('tr', {}, heads.map(function (h, i) { return el('th', {class: cls(i), scope: 'col'}, h); }))),
        el('tbody', {}, rows.map(function (r) {
          return el('tr', {class: r.foot ? 'hx-foot' : null}, r.cells.map(function (c, i) {
            return i ? el('td', {class: cls(i)}, c) : el('th', {scope: 'row'}, c);
          }));
        })))),
      note ? el('p', {class: 'small muted'}, note) : null,
    ];
  }

  function verdictTable(name, rows, note) {
    const all = counts(null);
    for (const r of rows) for (const v of ORDER.concat(['total'])) all[v] += r.c[v];
    const line = function (label, c, foot) {
      return {foot: foot, cells: [label].concat(ORDER.map(function (v) { return String(c[v]); }), [String(c.total)])};
    };
    return table([name].concat(ORDER.map(function (v) { return LABEL[v]; }), ['All runs']),
      rows.map(function (r) { return line(r.name, r.c); }).concat(rows.length > 1 ? [line('All', all, true)] : []), note);
  }

  // ---- the chart card ----

  // A card: a kicker naming the chart, a title stating its takeaway, the legend, the plot and its table twin.
  function chartCard(charts, spec, wide) {
    const plot = el('div', {class: 'hx-plot'});
    const twin = el('div', {class: 'hx-twin', hidden: true});
    const legend = spec.legend && !spec.empty ? legendNode(spec.legend) : null;
    let asTable = false;
    const btn = spec.empty ? null : el('button', {type: 'button', class: 'btn btn-secondary btn-small hx-toggle',
      onclick: function () {
        asTable = !asTable;
        if (asTable && !twin.firstChild) Q.put(twin, spec.table());
        twin.hidden = !asTable;
        plot.hidden = asTable;
        if (legend) legend.hidden = asTable;
        btn.textContent = asTable ? 'Show as chart' : 'Show as table';
      }}, 'Show as table');
    if (spec.empty) Q.put(plot, el('p', {class: 'muted hx-empty'}, spec.empty));
    else charts.push({host: plot, draw: spec.draw, w: 0});
    return el('section', {class: ['card', 'hx-card', wide ? 'hx-wide' : null]},
      el('div', {class: 'hx-card-head'},
        el('div', {class: 'hx-card-titles'}, el('p', {class: 'hx-kicker'}, spec.kicker), el('h2', {class: 'hx-title'}, spec.title)),
        btn),
      legend, plot, twin);
  }

  // Draw each chart at its card's width, and again when that width changes (window resize, scrollbar).
  function mountCharts(charts, alive) {
    function draw(c) {
      const w = Math.floor(c.host.clientWidth);
      if (!w || w === c.w) return;
      c.w = w;
      Q.put(c.host, c.draw(Math.max(w, 260)));
    }
    charts.forEach(draw);
    if (typeof ResizeObserver !== 'function') return;
    let pending = false;
    const ro = new ResizeObserver(function () {
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () {
        pending = false;
        if (!alive()) { ro.disconnect(); return; }
        charts.forEach(draw);
      });
    });
    charts.forEach(function (c) { ro.observe(c.host); });
  }

  // ---- 1. runs per day ----

  function daySpec(rows, tip) {
    const days = rows.map(function (r) { return {day: String(r.day || ''), c: counts(r)}; });
    const total = days.reduce(function (a, d) { return a + d.c.total; }, 0);
    const active = days.filter(function (d) { return d.c.total; });
    const busiest = active.reduce(function (b, d) { return !b || d.c.total > b.c.total ? d : b; }, null);
    const errors = days.reduce(function (a, d) { return a + d.c.ERROR; }, 0);
    let title = 'No engine runs in this period';
    if (total) {
      title = runs(total) + ' on ' + active.length + plural(active.length, ' day', ' days');
      if (active.length > 1) title += '; the busiest was ' + dayText(busiest.day) + ' with ' + busiest.c.total;
      if (errors) title += '; ' + errors + plural(errors, ' test error', ' test errors');
    }
    return {
      kicker: 'Runs per day',
      title: title,
      legend: verdictLegend(),
      empty: total ? null : 'No engine runs in the last ' + days.length + plural(days.length, ' day.', ' days.'),
      draw: function (w) { return dayChart(days, w, tip); },
      table: function () {
        return verdictTable('Day', active.map(function (d) { return {name: dayText(d.day, true), c: d.c}; }),
          'Days without runs are left out.');
      },
    };
  }

  function dayChart(days, W, tip) {
    const H = 236, L = 36, R = 8, T = 22, B = 30;
    const pw = W - L - R, ph = H - T - B;
    const sc = scaleFor(Math.max.apply(null, days.map(function (d) { return d.c.total; })), true);
    const y = function (v) { return T + ph - v / sc.max * ph; };
    const slot = pw / days.length;
    const bw = Math.max(1, Math.min(COL_MAX, slot * 0.6, slot - GAP));
    const hatch = 'hx-hatch-' + (++uid);
    const total = days.reduce(function (a, d) { return a + d.c.total; }, 0);
    const s = chartSvg(W, H, 'Runs per day, stacked by result: ' + runs(total) + ' in ' + days.length + ' days');
    s.appendChild(hatchDefs(hatch));
    yGrid(s, sc, L, W - R, y, String);
    const every = Math.max(1, Math.ceil(52 / slot));
    days.forEach(function (d, i) {
      const x = L + i * slot + (slot - bw) / 2;
      if (d.c.total) {
        const g = svg('g', {}, svg('rect', {x: f2(L + i * slot), y: T - 4, width: f2(slot), height: ph + 4, class: 'hx-hit'}));
        const parts = ORDER.filter(function (v) { return d.c[v]; });
        let cum = 0;
        parts.forEach(function (v, j) {
          const y0 = y(cum) - (j ? GAP : 0);
          cum += d.c[v];
          const h = Math.max(1, y0 - y(cum));
          g.appendChild(mark(v, barPath(x, y0 - h, bw, h, 'up', j === parts.length - 1), hatch));
        });
        if (slot >= 18) g.appendChild(text(x + bw / 2, y(d.c.total) - 6, String(d.c.total), 'hx-total', 'middle'));
        s.appendChild(hoverable(g, tip, function () {
          return tipBody(weekdayText(d.day) + ' · ' + runs(d.c.total), verdictRows(d.c));
        }, dayText(d.day) + ': ' + runs(d.c.total) + ', ' + breakdown(d.c)));
      }
      if ((days.length - 1 - i) % every === 0) s.appendChild(edgeText(x + bw / 2, H - 8, dayText(d.day), 'hx-tick', W));
    });
    s.appendChild(svg('line', {x1: L, x2: W - R, y1: T + ph, y2: T + ph, class: 'hx-axis'}));
    return s;
  }

  // ---- 2. runs that decided, per week ----

  function weekSpec(rows, target, tip) {
    const weeks = rows.map(function (r) {
      return {week: String(r.week || ''), from: String(r.from || ''), decided: num(r.decided), runs: num(r.runs),
        rate: has(r.rate) ? Number(r.rate) : null};
    });
    const used = weeks.filter(function (w) { return w.runs; });
    const met = used.filter(function (w) { return w.rate >= target; });
    let title = 'No engine runs in this period';
    if (used.length === 1) {
      title = 'The one week with runs ' + (met.length ? 'reached' : 'stayed below') + ' the ' + target + '% target';
    } else if (used.length) {
      title = (met.length ? met.length + ' of ' + used.length : 'None of the ' + used.length) + ' weeks with runs reached the ' +
        target + '% target';
    }
    return {
      kicker: 'Runs that decided, per week',
      title: title,
      legend: [{key: 'rate', label: 'Share of runs that ended with a result'}, {key: 'target', label: 'Target ' + target + '%'}],
      empty: used.length ? null : 'No engine runs in this period.',
      draw: function (w) { return weekChart(weeks, target, w, tip); },
      table: function () {
        return table(['Week from', 'Runs', 'Ended with a result', 'Test errors', 'Share decided'], weeks.map(function (w) {
          return {cells: [dayText(w.from, true), String(w.runs), String(w.decided), String(w.runs - w.decided),
            w.runs ? pct(w.rate) : 'no runs']};
        }), 'A week starts on Monday; the first week can start later, with the period.');
      },
    };
  }

  function weekChart(weeks, target, W, tip) {
    const H = 252, L = 44, R = 8, T = 22, B = 46;
    const pw = W - L - R, ph = H - T - B;
    const y = function (v) { return T + ph - v / 100 * ph; };
    const slot = pw / weeks.length;
    const bw = Math.max(4, Math.min(COL_MAX, slot * 0.5));
    const s = chartSvg(W, H, 'Share of runs that ended with a result, per week, against the ' + target + '% target');
    yGrid(s, {max: 100, step: 50}, L, W - R, y, function (v) { return v + '%'; });
    const every = Math.max(1, Math.ceil(44 / slot));
    weeks.forEach(function (wk, i) {
      const cx = L + (i + 0.5) * slot;
      if (wk.runs) {
        const h = ph * Math.max(0, Math.min(100, num(wk.rate))) / 100;
        const g = svg('g', {}, svg('rect', {x: f2(L + i * slot), y: T - 4, width: f2(slot), height: ph + 4, class: 'hx-hit'}));
        if (h > 0) g.appendChild(mark('rate', barPath(cx - bw / 2, T + ph - Math.max(1, h), bw, Math.max(1, h), 'up', true)));
        // "100%" needs about 30px: narrower slots (90 days on a phone) leave the value to the tooltip and the table
        if (slot >= 30) g.appendChild(text(cx, T + ph - h - 6, Math.round(num(wk.rate)) + '%', 'hx-total', 'middle'));
        s.appendChild(hoverable(g, tip, function () {
          return tipBody('Week from ' + dayText(wk.from), [
            {key: 'rate', value: pct(wk.rate), label: 'ended with a result'},
            {key: null, value: String(wk.decided), label: 'of ' + runs(wk.runs) + ' decided'},
            {key: 'ERROR', value: String(wk.runs - wk.decided), label: plural(wk.runs - wk.decided, 'test error',
              'test errors')},
          ], [wk.rate >= target ? 'At or above the ' + target + '% target' : 'Below the ' + target + '% target']);
        }, 'Week from ' + dayText(wk.from) + ': ' + pct(wk.rate) + ', ' + wk.decided + ' of ' + runs(wk.runs) + ' decided'));
      }
      if ((weeks.length - 1 - i) % every === 0) {
        s.appendChild(edgeText(cx, H - 24, dayText(wk.from), 'hx-tick', W));
        const n = wk.runs ? (slot >= 64 ? wk.decided + ' of ' + wk.runs : wk.decided + '/' + wk.runs) : 'no runs';
        s.appendChild(edgeText(cx, H - 8, n, 'hx-tick hx-tick-sub', W));
      }
    });
    s.appendChild(svg('line', {x1: L, x2: W - R, y1: T + ph, y2: T + ph, class: 'hx-axis'}));
    s.appendChild(svg('line', {x1: L, x2: W - R, y1: f2(y(target)), y2: f2(y(target)), class: 'hx-target'}));
    s.appendChild(text(L - 6, y(target) + 4, target + '%', 'hx-target-label', 'end'));
    return s;
  }

  // ---- 3. results per device ----

  function deviceSpec(rows, tip) {
    const devs = rows.map(function (r) { return {tag: String(r.tag || ''), c: counts(r)}; });
    const total = devs.reduce(function (a, d) { return a + d.c.total; }, 0);
    const failing = devs.filter(function (d) { return d.c.FAIL; });
    const fails = failing.reduce(function (a, d) { return a + d.c.FAIL; }, 0);
    const idle = devs.filter(function (d) { return !d.c.total; }).map(function (d) { return d.tag; });
    const busiest = devs.reduce(function (b, d) { return d.c.total && (!b || d.c.total > b.c.total) ? d : b; }, null);
    const parts = [];
    if (fails && failing.length === 1) {
      parts.push(fails === 1 ? 'the one failure was on ' + failing[0].tag : 'all ' + fails + ' failures were on ' + failing[0].tag);
    } else if (busiest) parts.push(busiest.tag + ' ran the most (' + runs(busiest.c.total) + ')');
    if (idle.length && total) parts.push(idle.join(' and ') + plural(idle.length, ' has', ' have') + ' no runs');
    return {
      kicker: 'Results per device',
      title: total ? capital(parts.join('; ')) : 'No engine runs in this period',
      legend: verdictLegend(),
      empty: total ? null : 'No engine runs in this period.',
      draw: function (w) { return deviceChart(devs, w, tip); },
      table: function () {
        return verdictTable('Device', devs.map(function (d) { return {name: d.tag, c: d.c}; }),
          idle.indexOf('TA3') >= 0 ? 'TA3 is not set up for the engine yet.' : null);
      },
    };
  }

  function deviceChart(devs, W, tip) {
    const L = 56, R = 70, RH = 36, top = 2;
    const H = top + devs.length * RH + 22;
    const pw = W - L - R;
    const hatch = 'hx-hatch-' + (++uid);
    const s = chartSvg(W, H, 'Results per device as shares of each device\'s runs: ' + devs.map(function (d) {
      return d.tag + ' ' + runs(d.c.total);
    }).join(', '));
    s.appendChild(hatchDefs(hatch));
    xGrid(s, {max: 100, step: 50}, function (v) { return L + v / 100 * pw; }, top, top + devs.length * RH,
      function (v) { return v + '%'; });
    devs.forEach(function (d, i) {
      const yb = top + i * RH + (RH - BAR) / 2;
      const mid = yb + BAR / 2 + 4;
      s.appendChild(text(0, mid, d.tag, 'hx-label'));
      if (!d.c.total) {
        s.appendChild(text(L + 6, mid, d.tag === 'TA3' ? 'not set up, no runs' : 'no runs', 'hx-tick'));
        return;
      }
      const g = svg('g', {}, svg('rect', {x: 0, y: top + i * RH, width: W, height: RH, class: 'hx-hit'}));
      const parts = ORDER.filter(function (v) { return d.c[v]; });
      let cum = 0;
      parts.forEach(function (v, j) {
        const x0 = L + cum / d.c.total * pw + (j ? GAP : 0);
        cum += d.c[v];
        const w = Math.max(1, L + cum / d.c.total * pw - x0);
        g.appendChild(mark(v, barPath(x0, yb, w, BAR, 'right', j === parts.length - 1), hatch));
        const label = String(d.c[v]);
        if (w >= label.length * 7 + 10) {
          g.appendChild(text(x0 + w / 2, mid, label, 'hx-in ' + (DARK_TEXT[v] ? 'hx-in-dark' : 'hx-in-light') +
            (v === 'ERROR' ? ' hx-in-halo' : ''), 'middle'));
        }
      });
      g.appendChild(text(L + pw + 8, mid, runs(d.c.total), 'hx-total'));
      s.appendChild(hoverable(g, tip, function () {
        return tipBody(d.tag + ' · ' + runs(d.c.total), verdictRows(d.c));
      }, d.tag + ': ' + runs(d.c.total) + ', ' + breakdown(d.c)));
    });
    return s;
  }

  // ---- 4. why runs ended in a test error ----

  function causeOf(c) {
    return CAUSES[c] || {label: capital(String(c)), means: '', most: 'had the cause "' + c + '"'};
  }

  function causeSpec(rows, tip) {
    const causes = rows.map(function (r) { return {cause: String(r.cause || ''), n: num(r.n)}; })
      .sort(function (a, b) { return b.n - a.n; });
    const total = causes.reduce(function (a, c) { return a + c.n; }, 0);
    let title = 'Every run in this period ended with a result';
    if (total) {
      const a = causes[0], b = causes[1];
      if (b && b.n === a.n) {
        // "Engine and test case", but "Engine and W044 browser": a device name keeps its capitals
        const second = causeOf(b.cause).label;
        title = causeOf(a.cause).label + ' and ' + (/^[A-Z][a-z]/.test(second) ? second.charAt(0).toLowerCase() +
          second.slice(1) : second) + ' stopped the most runs (' + a.n + ' each, of ' + total +
          plural(total, ' test error)', ' test errors)');
      } else {
        title = (a.n * 2 > total ? 'Most runs that ended in a test error ' :
          'Most often, runs that ended in a test error ') +
          causeOf(a.cause).most + ' (' + a.n + ' of ' + total + ')';
      }
    }
    return {
      kicker: 'Why runs ended in a test error',
      title: title,
      legend: null,
      empty: total ? null : 'No run ended in a test error in this period.',
      draw: function (w) { return causeChart(causes, total, w, tip); },
      table: function () {
        return table(['Cause', 'Runs', 'What it means'], causes.map(function (c) {
          return {cells: [causeOf(c.cause).label, String(c.n), causeOf(c.cause).means]};
        }).concat([{foot: true, cells: ['All test errors', String(total), '']}]), null, [2]);
      },
    };
  }

  function causeChart(causes, total, W, tip) {
    const L = W < 380 ? 112 : 132, R = 36, RH = 32, top = 2;
    const H = top + causes.length * RH + 22;
    const pw = W - L - R;
    const sc = scaleFor(Math.max.apply(null, causes.map(function (c) { return c.n; })), true);
    const x = function (v) { return L + v / sc.max * pw; };
    const hatch = 'hx-hatch-' + (++uid);
    const s = chartSvg(W, H, 'Test errors by cause: ' + causes.map(function (c) {
      return causeOf(c.cause).label + ' ' + c.n;
    }).join(', '));
    s.appendChild(hatchDefs(hatch));
    xGrid(s, sc, x, top, top + causes.length * RH, String);
    causes.forEach(function (c, i) {
      const info = causeOf(c.cause);
      const yb = top + i * RH + (RH - BAR) / 2;
      const mid = yb + BAR / 2 + 4;
      s.appendChild(text(0, mid, info.label, 'hx-label'));
      if (!c.n) {
        s.appendChild(text(L + 6, mid, '0', 'hx-total'));
        return;
      }
      const w = Math.max(1, x(c.n) - L);
      const g = svg('g', {}, svg('rect', {x: 0, y: top + i * RH, width: W, height: RH, class: 'hx-hit'}),
        mark('cause', barPath(L, yb, w, BAR, 'right', true), hatch),
        text(L + w + 6, mid, String(c.n), 'hx-total'));
      s.appendChild(hoverable(g, tip, function () {
        return tipBody(info.label, [{key: 'ERROR', value: String(c.n), label: 'of ' + total +
          plural(total, ' test error', ' test errors')}],
          [info.means ? capital(info.means) + '.' : null]);
      }, info.label + ': ' + c.n + ' of ' + total + plural(total, ' test error', ' test errors')));
    });
    return s;
  }

  // ---- 5. time to result per test ----

  function timeSpec(rows, times, tip) {
    const tasks = rows.filter(function (r) { return has(r.prepare_s) && has(r.run_s); }).slice(-15).reverse()
      .map(function (r) {
        return {task: String(r.task || ''), case: String(r.case || ''), day: String(r.day || ''), prepare: num(r.prepare_s),
          run: num(r.run_s), result: has(r.result_s) ? Number(r.result_s) : null};
      });
    let title = 'No timed tests in this period';
    if (tasks.length && has(times.run_median_s) && has(times.prepare_median_s)) {
      const runLonger = Number(times.run_median_s) >= Number(times.prepare_median_s);
      title = runLonger ? 'Running takes most of the time: typically ' + dur(times.run_median_s) + ', against ' +
        dur(times.prepare_median_s) + ' to prepare' : 'Preparing takes most of the time: typically ' +
        dur(times.prepare_median_s) + ', against ' + dur(times.run_median_s) + ' to run';
    } else if (tasks.length) title = 'Time from asking for a plan to the results, per test';
    return {
      kicker: 'Time to result per test',
      title: title,
      legend: [{key: 'prepare', label: 'Prepare: asking for a plan until it is ready'}, {key: 'run', label: 'Run: Start until the results'}],
      empty: tasks.length ? null : 'No test from this app has both a prepare and a run time in this period.',
      draw: function (w) { return timeChart(tasks, w, tip); },
      table: function () {
        return table(['Test', 'Case', 'Day', 'Prepare', 'Run', 'Prepare and run', 'Asked to results'], tasks.map(function (t) {
          return {cells: [t.task, t.case, dayText(t.day, true), dur(t.prepare), dur(t.run), dur(t.prepare + t.run),
            t.result === null ? '–' : dur(t.result)]};
        }), '"Asked to results" also holds the wait between the plan and pressing Start. Newest test first.', [1, 2]);
      },
    };
  }

  function timeChart(tasks, W, tip) {
    const wide = W >= 360;
    const L = wide ? 132 : 76, R = 64, RH = 30, top = 2;
    const H = top + tasks.length * RH + 22;
    const pw = W - L - R;
    const sc = scaleFor(Math.max.apply(null, tasks.map(function (t) { return (t.prepare + t.run) / 60; })), false);
    const x = function (m) { return L + m / sc.max * pw; };
    const s = chartSvg(W, H, 'Minutes to prepare and to run, per test, newest first');
    const last = ticksOf(sc).slice(-1)[0];
    xGrid(s, sc, x, top, top + tasks.length * RH, function (v) { return v + (v === last ? ' min' : ''); });
    tasks.forEach(function (t, i) {
      const yb = top + i * RH + (RH - BAR) / 2;
      const mid = yb + BAR / 2 + 4;
      // a test with several cases ("PLAN-001, PLAN-002") is cut to the label column (about 7px a character at 12px);
      // the tooltip and the table keep the whole name
      const name = wide ? t.case + ' · ' + dayText(t.day) : t.case;
      const fit = Math.floor((L - 8) / 7);
      s.appendChild(text(0, mid, name.length > fit ? name.slice(0, fit - 1).trimEnd() + '…' : name, 'hx-label'));
      const g = svg('g', {}, svg('rect', {x: 0, y: top + i * RH, width: W, height: RH, class: 'hx-hit'}));
      const xp = x(t.prepare / 60);
      if (t.prepare) g.appendChild(mark('prepare', barPath(L, yb, Math.max(1, xp - L), BAR, 'right', !t.run)));
      if (t.run) {
        const x0 = t.prepare ? xp + GAP : L;
        g.appendChild(mark('run', barPath(x0, yb, Math.max(1, x((t.prepare + t.run) / 60) - x0), BAR, 'right', true)));
      }
      g.appendChild(text(x((t.prepare + t.run) / 60) + 6, mid, shortDur(t.prepare + t.run), 'hx-total'));
      s.appendChild(hoverable(g, tip, function () {
        return tipBody(t.case + ' · ' + weekdayText(t.day), [
          {key: 'prepare', value: dur(t.prepare), label: 'prepare'},
          {key: 'run', value: dur(t.run), label: 'run'},
        ], ['Prepare and run: ' + dur(t.prepare + t.run),
          t.result === null ? null : 'Asked to results: ' + dur(t.result) + ' (with the wait before Start)', 'Test ' + t.task]);
      }, t.case + ' on ' + dayText(t.day) + ': prepare ' + dur(t.prepare) + ', run ' + dur(t.run)));
    });
    return s;
  }

  // ---- KPI tiles ----

  function tile(o) {
    return el('div', {class: 'hx-tile', title: o.def || null},
      el('div', {class: 'hx-tile-label'}, o.label),
      el('div', {class: 'hx-tile-value'}, o.value, o.unit ? el('span', {class: 'hx-tile-unit'}, ' ' + o.unit) : null),
      o.extra || null,
      o.sub ? el('div', {class: 'hx-tile-sub'}, o.sub) : null);
  }

  function group(title, sub, tiles) {
    return el('section', {class: 'hx-group'},
      el('div', {class: 'hx-group-head'}, el('h2', {}, title), el('p', {}, sub)),
      el('div', {class: 'hx-tiles'}, tiles));
  }

  function appTiles(a, defs) {
    const pr = a.pass_rate || {}, f = a.fails || {}, b = a.bugs || {}, cov = a.coverage || {};
    const withResult = num(pr.passed) + num(pr.failed) + num(pr.partly);
    const failed = num(f.confirmed) + num(f.suspected);
    const devs = cov.devices || {};
    const tags = DEVICES.concat(Object.keys(devs).filter(function (t) { return DEVICES.indexOf(t) < 0; }));
    return [
      tile({label: 'Pass rate', value: pct(pr.value), def: defs.pass_rate,
        sub: withResult ? num(pr.passed) + ' of ' + runs(withResult) + ' with a result about the app' +
          (num(pr.partly) ? '; ' + num(pr.partly) + ' partly passed' : '') : 'No run ended with a result about the app'}),
      tile({label: 'Failures (confirmed / suspected)', value: num(f.confirmed) + ' / ' + num(f.suspected), def: defs.fails,
        sub: failed ? failed + ' failed ' + plural(failed, 'run', 'runs') + '; a failure stays suspected until it repeats'
          : 'No failed runs'}),
      tile({label: 'Bugs found', value: String(num(b.found)), def: BUG_DEF,
        sub: [num(b.filed) + ' filed', num(b.linked) ? num(b.linked) + ' linked to an existing ticket' : null,
          num(b.fixed) + ' fixed'].filter(Boolean).join(', ')}),
      tile({label: 'Coverage', value: String(num(cov.cases_passed)),
        unit: 'of ' + num(cov.cases_active) + ' active cases passed', def: defs.coverage,
        sub: 'Runs with a result: ' + tags.map(function (t) {
          return t + ' ' + (num(devs[t]) || (t === 'TA3' ? 'not set up' : '0'));
        }).join(' · ')}),
    ];
  }

  function decidedTile(d, def) {
    const target = has(d.target) ? Number(d.target) : 90;
    const v = has(d.value) ? Number(d.value) : null;
    const good = v !== null && v >= target;
    let extra = null;
    if (v !== null) {
      const fill = el('span', {class: 'hx-meter-fill'});
      const tick = el('span', {class: 'hx-meter-target'});
      fill.style.width = Math.max(0, Math.min(100, v)) + '%';
      tick.style.left = Math.max(0, Math.min(100, target)) + '%';
      extra = [
        el('div', {class: ['hx-meter', good ? 'good' : 'warn'], role: 'img',
          'aria-label': pct(v) + ' of runs ended with a result; the target is ' + target + '%'}, fill, tick),
        el('div', {class: 'hx-status'}, statusIcon(good),
          good ? 'On target (' + target + '% or more)' : 'Below the ' + target + '% target'),
      ];
    }
    return tile({label: 'Runs that decided', value: pct(v), def: def, extra: extra,
      sub: num(d.runs) ? num(d.decided) + ' of ' + runs(num(d.runs)) + ' ended with a result about the app'
        : 'No engine runs in this period'});
  }

  function engineTiles(g, defs) {
    const ff = g.false_fails || {}, ho = g.hands_off || {}, t = g.times || {};
    return [
      decidedTile(g.decided_rate || {}, defs.decided_rate),
      tile({label: 'False failures corrected', value: String(num(ff.corrected)),
        unit: 'of ' + num(ff.fails_before_correction) + plural(num(ff.fails_before_correction), ' failure', ' failures'),
        def: defs.false_fails,
        sub: num(ff.fails_before_correction) ? 'The test case was wrong, not the app' + (has(ff.value) ? ' (' + pct(ff.value) + ')' : '')
          : 'No failures in this period'}),
      tile({label: 'Hands-off runs', value: String(num(ho.runs_from_app)), unit: 'of ' + runs(num(ho.runs)),
        def: defs.hands_off, sub: 'Started from a test in this app' + (has(ho.value) ? ' (' + pct(ho.value) + ')' : '')}),
      tile({label: 'Median time to result', value: shortDur(t.result_median_s), def: defs.times,
        sub: num(t.samples) ? 'Prepare ' + dur(t.prepare_median_s) + ', run ' + dur(t.run_median_s) + '; middle values over ' +
          num(t.samples) + plural(num(t.samples), ' test', ' tests') : 'No timed tests in this period'}),
    ];
  }

  function definitions(defs) {
    const items = DEF_NAMES.filter(function (n) { return typeof defs[n[0]] === 'string'; });
    return el('details', {class: 'hx-defs'}, el('summary', {}, 'How these numbers are counted'),
      el('p', {class: 'small'}, 'Only runs of the test engine count. When a run was corrected, the correction counts ' +
        'instead. Self-tests of the test system and device slots that were not tested are left out.'),
      el('dl', {class: 'kv'}, items.map(function (n) { return [el('dt', {}, n[1]), el('dd', {}, defs[n[0]])]; }),
        el('dt', {}, 'Bugs found'), el('dd', {}, BUG_DEF)));
  }

  // ---- Your tests (2026-11-04): every test the person ran, Ongoing · Tested · Done ----

  /* GET /api/results/tests (views.tests; tests/2026-11-04-results-redesign/API.md section 4). Not tied to the period
  links. A test is Done when its comment is posted, the person pressed Mark done, or a newer run of the same ClickUp
  test replaced it (a failure only when it failed again there); nothing moves by age. Each row opens #/results?task=. */
  const TESTS_SHOWN = 5;  // views.tests "shown": the rows a group shows before "n more"
  const CLICKUP_RE = /^https:\/\/app\.clickup\.com\/t\/\S+$/;
  // the verdict icons of the Results page (results.js), on a 16 x 16 grid: a wrench for a test error, a clock for a test
  // that runs; each with its words for a screen reader
  const X_MARK = [['path', {d: 'M4.5 4.5l7 7M11.5 4.5l-7 7'}]];
  const TEST_ICONS = {
    passed: [['path', {d: 'M3.5 8.5l3 3 6-7'}]],
    failed: X_MARK,
    failed_suspected: X_MARK,
    not_decided: [['path', {d: 'M10.6 2.3a3.3 3.3 0 0 0-3.8 4.4l-4.2 4.2a1.4 1.4 0 0 0 2 2l4.2-4.2a3.3 3.3 0 0 0 ' +
      '4.4-3.8l-2 2-1.8-.5-.5-1.8z'}]],
    partly: [['circle', {cx: 8, cy: 8, r: 5.5}], ['path', {d: 'M8 2.5a5.5 5.5 0 0 1 0 11z', fill: 'currentColor'}]],
    blocked: [['circle', {cx: 8, cy: 8, r: 5.5}], ['path', {d: 'M4.1 11.9l7.8-7.8'}]],
    not_tested: [['path', {d: 'M4.5 8h7'}]],
    running: [['circle', {cx: 8, cy: 8, r: 5.5}], ['path', {d: 'M8 5v3.2l2.2 1.4'}]],
  };

  function testIcon(code, label) {
    const k = TEST_ICONS[code] ? code : 'not_decided';
    const s = svg('svg', {viewBox: '0 0 16 16', class: 'hx-ti hx-tv-' + k, role: 'img', 'aria-label': label,
      focusable: 'false'}, svg('title', {}, label));
    for (const p of TEST_ICONS[k]) s.appendChild(svg(p[0], p[1]));
    return s;
  }

  // "today 15:25", else "11-03 13:44" (with the year when it is not this year); bare leaves out "today"
  function stampOf(iso, bare) {
    const d = new Date(iso || '');
    if (!iso || isNaN(d.getTime())) return '';
    const p = function (n) { return String(n).padStart(2, '0'); };
    const now = new Date();
    const hm = p(d.getHours()) + ':' + p(d.getMinutes());
    if (d.toDateString() === now.toDateString()) return (bare ? '' : 'today ') + hm;
    return (d.getFullYear() !== now.getFullYear() ? d.getFullYear() + '-' : '') + p(d.getMonth() + 1) + '-' +
      p(d.getDate()) + ' ' + hm;
  }

  function dots(parts) {
    const kids = [];
    for (const x of parts) {
      if (!x) continue;
      if (kids.length) kids.push(' · ');
      kids.push(x);
    }
    return kids;
  }

  // an ongoing test: "TA5 waits for your OK at step 4" for a device handed back to the person, else "Running: TA5 step 3
  // of 6" for its first running device, else "Running" while Hover works (between two cases), else who it waits for
  function ongoingWords(run) {
    const r = run && typeof run === 'object' ? run : {};
    const held = Q.list(r.devices).find(function (d) { return d && num(d.waits_at) > 0; });
    if (held) return held.tag + ' waits for your OK at step ' + num(held.waits_at);
    const dev = Q.list(r.devices).find(function (d) { return d && d.state === 'running'; });
    if (dev) {
      const part = dev.part && typeof dev.part === 'object' ? dev.part : null;
      const n = num(part ? part.n : dev.step);
      const of = num(part ? part.of : dev.of);
      return 'Running: ' + dev.tag + (n ? ' step ' + n + (of ? ' of ' + of : '') : '');
    }
    if (r.wingmate === 'working') return 'Running';
    return r.wingmate === 'off' ? 'Hover is off' : 'Waiting for Hover';
  }

  // how a test in Done ended
  function doneWords(h) {
    if (!h || typeof h !== 'object') return null;
    if (h.how === 'posted') {
      const when = stampOf(h.at);
      const said = 'Posted' + (when ? ' ' + when : '');
      return CLICKUP_RE.test(String(h.url || '')) ? el('a', {href: h.url, target: '_blank', rel: 'noopener noreferrer'},
        said + ' ↗') : said;
    }
    if (h.how === 'marked') return 'Marked done';
    if (h.how !== 'replaced' && h.how !== 'failed_again') return null;
    const said = (h.how === 'replaced' ? 'Replaced by the ' : 'Failed again in the ') + (stampOf(h.at, true) || 'newer') +
      ' run ›';
    return h.by_task ? el('a', {href: Q.routeHash('results', {task: String(h.by_task)})}, said) : said;
  }

  function testRow(r, group) {
    const href = Q.routeHash('results', {task: String(r.task_id || '')});
    const ck = r.clickup && typeof r.clickup === 'object' ? r.clickup : {};
    const devs = Q.list(r.devices).join(', ');
    let right = null;
    let sub;
    if (group === 'ongoing') {
      right = el('span', {class: 'hx-test-state'}, ongoingWords(r.running));
      sub = [ck.title, devs, r.created ? 'Run ' + stampOf(r.created) : null];
    } else if (group === 'tested') {
      right = r.next && r.next.label ? el('a', {class: 'hx-test-next', href: href}, 'Next: ' + r.next.label + ' ›') : null;
      sub = [ck.title, devs, stampOf(r.last_change), r.results, r.stopped_early ? 'Stopped early' : null];
    } else {
      sub = [devs, stampOf(r.last_change), r.stopped_early ? 'Stopped early' : null, doneWords(r.done_how)];
    }
    return el('li', {class: 'hx-test-row'},
      group === 'ongoing' ? testIcon('running', 'Running') : testIcon(r.verdict, r.verdict_label || 'Test error'),
      el('div', {class: 'hx-test-main'}, el('a', {class: 'hx-test-title', href: href}, String(r.title || r.task_id || '')),
        el('div', {class: 'hx-test-sub'}, dots(sub))),
      right);
  }

  // one group: its first rows, then the rest behind "n more" (Tested) or "Show all" (Done); Ongoing shows all
  function testGroup(name, g, group, shown, moreLabel) {
    const rows = Q.list(g && g.rows).filter(function (r) { return r && typeof r === 'object'; });
    const n = num(g && g.count);
    const head = el('h3', {class: 'hx-group-title'}, group === 'ongoing' && !n ? name : name + ' (' + n + ')',
      group === 'ongoing' && !n ? el('span', {class: 'hx-group-note'}, 'nothing running') : null);
    if (!rows.length) {
      return el('div', {class: 'hx-test-group'}, head, group === 'ongoing' ? null :
        el('p', {class: 'hx-group-note'}, group === 'tested' ? 'No test waits for you.' : 'No test is done yet.'));
    }
    const first = group === 'ongoing' ? rows : rows.slice(0, shown);
    const rest = group === 'ongoing' ? [] : rows.slice(shown);
    const kids = [head, el('ul', {class: 'hx-test-list'}, first.map(function (r) { return testRow(r, group); }))];
    if (rest.length) {
      const more = el('ul', {class: 'hx-test-list', hidden: true}, rest.map(function (r) { return testRow(r, group); }));
      const btn = el('button', {type: 'button', class: 'btn-link hx-more', 'aria-expanded': 'false'}, moreLabel);
      btn.addEventListener('click', function () {
        const open = more.hidden;
        more.hidden = !open;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        btn.textContent = open ? 'Show fewer ▴' : moreLabel;
      });
      kids.push(more, btn);
    }
    return el('div', {class: 'hx-test-group'}, kids);
  }

  function yourTests(d) {
    const shown = num(d && d.shown) > 0 ? num(d.shown) : TESTS_SHOWN;
    const tested = (d && d.tested) || {};
    const done = (d && d.done) || {};
    const unreported = num(tested.more_unreported);
    // the server sends at most rows_max rows a group: the labels count the rows sent ("Show 100 of 130"), never more
    const sent = function (g) { return Q.list(g.rows).length; };
    const more = Math.max(0, sent(tested) - shown);
    const moreAll = Math.max(0, num(tested.count) - shown);
    const doneAll = num(done.count) > sent(done) ? 'Show ' + sent(done) + ' of ' + num(done.count) + ' ▾' :
      'Show all ' + num(done.count) + ' ▾';
    return el('section', {class: 'card hx-tests', 'aria-label': 'Your tests'},
      el('h2', {}, 'Your tests'),
      testGroup('Ongoing', d && d.ongoing, 'ongoing', shown, null),
      testGroup('Tested', tested, 'tested', shown, (moreAll > more ? more + ' of ' + moreAll : more) + ' more ▾' +
        (unreported > 0 ? ' (' + unreported + ' of them failed and ' + plural(unreported, 'is', 'are') + ' not reported)' :
          '')),
      testGroup('Done', done, 'done', shown, doneAll));
  }

  // ---- the page ----

  function head(days) {
    const h = Q.viewHead('History', null);
    Q.add(h, [el('div', {class: 'hx-tools'},
      el('nav', {class: 'hx-period', 'aria-label': 'Period'}, PERIODS.map(function (d) {
        return el('a', {href: Q.routeHash('history', {days: String(d)}), 'aria-current': d === days ? 'page' : null},
          d + ' days');
      })),
      el('a', {href: Q.routeHash('history', {view: 'old'}), class: 'hx-old'}, 'Old view'))]);
    return h;
  }

  function windowOf(stats, days) {
    if (stats && stats.window && stats.window.from && stats.window.to) {
      return {from: String(stats.window.from), to: String(stats.window.to)};
    }
    const to = new Date();
    to.setHours(12, 0, 0, 0);
    return {from: isoDay(new Date(to.getTime() - (days - 1) * 86400000)), to: isoDay(to)};
  }

  function windowLine(stats, days) {
    const w = windowOf(stats, days);
    const bt = num(stats && stats.backtest && stats.backtest.records);
    return el('p', {class: 'hx-window'}, dayText(w.from) + ' to ' + dayText(w.to, true) + ': runs of the test engine only.',
      bt ? [' The ' + bt + ' hand and back-test records are not counted here; they are in the ',
        el('a', {href: Q.routeHash('history', {view: 'old'})}, 'old view'), '.'] : null);
  }

  function board(stats, charts, tip) {
    const a = stats.app || {}, g = stats.engine || {}, ser = stats.series || {}, defs = stats.definitions || {};
    const target = has(g.decided_rate && g.decided_rate.target) ? Number(g.decided_rate.target) : 90;
    return [
      group('The app (QA)', 'What the tests say about WingtraApp.', appTiles(a, defs)),
      group('The test system (reliability)', 'How well the test system itself works.', engineTiles(g, defs)),
      definitions(defs),
      el('div', {class: 'hx-charts'},
        chartCard(charts, daySpec(Q.list(ser.per_day), tip), true),
        chartCard(charts, weekSpec(Q.list(ser.decided_per_week), target, tip)),
        chartCard(charts, deviceSpec(Q.list(ser.per_device), tip)),
        chartCard(charts, causeSpec(Q.list(g.not_decided_causes), tip)),
        chartCard(charts, timeSpec(Q.list(ser.times), g.times || {}, tip))),
    ];
  }

  // The old page's "What was done" rows, kept to engine runs in the same period so they agree with the numbers above.
  function doneRows(results, w) {
    const live = {cases: Q.list(results.cases).map(function (c) {
      return Object.assign({}, c, {rows: Q.list(c.rows).filter(function (r) { return r && r.mode === 'live'; })});
    })};
    return Q.historyRuns(live).filter(function (r) {
      return r.day >= w.from && r.day <= w.to && !/^SELFTEST/i.test(String(r.case || ''));
    });
  }

  function lowerPart(got, stats, days) {
    const value = function (i) { return got[i].status === 'fulfilled' ? got[i].value : null; };
    const results = value(0), tickets = value(1), register = value(2);
    const errs = [];
    if (!results) errs.push(Q.errorBox(got[0].reason, 'The list of runs did not load'));
    if (!tickets) errs.push(Q.errorBox(got[1].reason, 'Ticket drafts did not load'));
    if (!register) errs.push(Q.errorBox(got[2].reason, 'The bug list did not load'));
    const bugs = Q.bugRows(register, tickets);
    const found = stats && stats.app && stats.app.bugs ? num(stats.app.bugs.found) : null;
    const done = results ? doneRows(results, windowOf(stats, days)) : [];
    const caseCount = results ? Q.list(results.cases).filter(function (c) { return !c.harness; }).length : null;
    return [
      errs,
      el('section', {class: 'card'},
        el('div', {class: 'card-head'}, el('h2', {}, 'Bugs'), Q.chip('s-muted', String(bugs.length))),
        el('p', {class: 'small muted'}, 'Every bug in the bug list, newest first' +
          (found === null ? '.' : '; ' + found + ' found in this period.')),
        Q.bugTable(bugs, register)),
      el('section', {class: 'card'},
        el('div', {class: 'card-head'}, el('h2', {}, 'What was done')),
        el('p', {class: 'small muted'}, 'Engine runs in this period, one line per day and campaign. Hand and back-test ' +
          'records are in the old view.'),
        results ? (done.length ? Q.doneList(done) : el('p', {class: 'muted'}, 'No engine runs in the last ' + days +
          plural(days, ' day.', ' days.'))) : null),
      el('p', {class: 'small'}, el('a', {href: Q.routeHash('history', {show: 'cases'})},
        'Case library' + (caseCount === null ? '' : ' (' + caseCount + plural(caseCount, ' case)', ' cases)')) + ' →')),
    ];
  }

  async function render(root, params, alive, QA) {
    Q = QA;
    const days = readDays(params.get('days'));
    const tip = tooltip();
    const tests = el('div', {}, Q.loading('Loading your tests…'));
    const top = el('div', {});
    const body = el('div', {}, Q.loading('Loading the numbers…'));
    const lower = el('div', {});
    Q.add(root, [head(days), tests, top, body, lower, tip.node]);
    // Your tests first, above the numbers; it loads on its own and is not tied to the period
    Q.getJSON('/api/results/tests').then(function (d) {
      if (alive()) Q.put(tests, yourTests(d));
    }, function (e) {
      if (alive()) Q.put(tests, Q.errorBox(e, 'Your tests did not load'));
    });
    // a tap outside a mark closes its tooltip; scrolling closes any
    root.addEventListener('pointerdown', function (e) {
      if (!(e.target && e.target.closest && e.target.closest('.hx-item'))) tip.hide();
    });
    const onScroll = function () {
      if (!alive()) window.removeEventListener('scroll', onScroll);
      else tip.hide();
    };
    window.addEventListener('scroll', onScroll, {passive: true});

    const statsP = Q.getJSON('/api/stats' + Q.qs({days: String(days)}));
    const restP = Promise.allSettled([Q.getJSON('/api/results'), Q.getJSON('/api/tickets'), Q.readBugRegister()]);
    let stats = null;
    try {
      stats = await statsP;
    } catch (e) {
      if (!alive()) return;
      Q.put(body, Q.errorBox(e, 'The numbers did not load'));
    }
    if (!alive()) return;
    Q.put(top, windowLine(stats, days));
    if (stats) {
      const charts = [];
      Q.put(body, board(stats, charts, tip));
      mountCharts(charts, alive);
    }
    Q.put(lower, Q.loading('Loading bugs and runs…'));
    const got = await restP;
    if (!alive()) return;
    Q.put(lower, lowerPart(got, stats, days));
  }

  window.QAHistory = {render: render};
})();
