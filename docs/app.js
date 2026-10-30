/* רדאר מניות – phone app. Plain JavaScript, no build step. Reads the JSON files that the daily cloud
   run writes to ./data and can start cloud runs through the GitHub API with the user's own token. */
'use strict';
(function () {
  const EMBED = window.__RADAR_EMBED__ || null;      // single-file preview: data is embedded, no network
  const PREVIEW = !!EMBED;
  const WORKFLOW = window.RADAR_WORKFLOW || '';
  const LRM = '‎';
  const app = document.getElementById('app');
  document.documentElement.setAttribute('dir', 'rtl');
  document.documentElement.setAttribute('lang', 'he');

  // ------------------------------------------------------------------ storage (best effort)
  const store = {
    get(k, d = null) { try { const v = localStorage.getItem('radar.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('radar.' + k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
    del(k) { try { localStorage.removeItem('radar.' + k); } catch (e) { /* ignore */ } },
  };

  // ------------------------------------------------------------------ formatting
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isNum = (v) => typeof v === 'number' && isFinite(v);
  function pct(v, d = 0, sign = false) {
    if (!isNum(v)) return '–';
    const s = (sign && v > 0 ? '+' : '') + (v * 100).toFixed(d) + '%';
    return (s[0] === '+' || s[0] === '-') ? LRM + s : s;
  }
  function price(v) {
    if (!isNum(v)) return '–';
    const s = v >= 1000 ? v.toLocaleString('en-US', { maximumFractionDigits: 0 }) : v >= 100 ? v.toFixed(1) : v.toFixed(2);
    return s + '$';
  }
  function money(v) {
    if (!isNum(v)) return '–';
    const a = Math.abs(v);
    let s;
    if (a >= 1e9) s = (a / 1e9).toFixed(1) + ' מיליארד $';
    else if (a >= 1e6) s = (a / 1e6).toFixed(1) + ' מיליון $';
    else if (a >= 1e5) s = Math.round(a / 1e3) + ' אלף $';
    else s = Math.round(a).toLocaleString('en-US') + ' $';
    return (v < 0 ? LRM + '-' : '') + s;
  }
  function dmy(iso) {
    if (!iso) return '–';
    const m = String(iso).match(/(\d{4})-(\d{2})-(\d{2})/);
    return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso);
  }
  const dm = (iso) => { const m = String(iso).match(/\d{4}-(\d{2})-(\d{2})/); return m ? `${m[2]}/${m[1]}` : String(iso); };
  const daysSince = (iso) => (Date.now() - new Date(iso + (String(iso).length === 10 ? 'T12:00:00Z' : '')).getTime()) / 864e5;
  function ago(iso) {
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 6e4);
    if (!isFinite(mins)) return '';
    if (mins < 1) return 'עכשיו';
    if (mins < 60) return `לפני ${mins} דק׳`;
    const h = Math.round(mins / 60);
    if (h < 24) return h === 1 ? 'לפני שעה' : `לפני ${h} שעות`;
    const d = Math.round(h / 24);
    return d === 1 ? 'אתמול' : `לפני ${d} ימים`;
  }
  const riskLevel = (r) => (r < 35 ? ['good', 'נמוך'] : r < 60 ? ['warn', 'בינוני'] : ['bad', 'גבוה']);
  const riskColor = (r) => `var(--${riskLevel(r)[0]})`;
  const GROUP_HE = {
    defense: 'ביטחון', oil_gas: 'נפט וגז', gold: 'זהב', shipping: 'ספנות', airlines: 'תעופה', travel: 'תיירות',
    fertilizer: 'דשנים', semis: 'שבבים', cyber: 'סייבר', steel: 'פלדה', importers: 'קמעונאות מיובאת', autos: 'רכב',
  };

  // ------------------------------------------------------------------ icons
  const ICON = {
    star: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3.5l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/></svg>',
    saved: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3.5l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/></svg>',
    starOn: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3.5l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/></svg>',
    today: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6-6"/><circle cx="15.5" cy="9" r="1.2" fill="currentColor" stroke="none"/></svg>',
    market: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.6 3.8 5.6 3.8 9s-1.2 6.4-3.8 9c-2.6-2.6-3.8-5.6-3.8-9S9.4 5.6 12 3z"/></svg>',
    perf: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 20h18"/><path d="M4 16l5-5 4 3 7-8"/><path d="M15 6h5v5"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
    back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
    cal: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/></svg>',
    results: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 20h18"/><path d="M4 16l5-5 4 3 7-8"/><path d="M15 6h5v5"/></svg>',
    learn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 18h5M10.5 21h3"/><path d="M12 3a6 6 0 0 0-3.7 10.7c.7.6 1.2 1.4 1.2 2.3h5c0-.9.5-1.7 1.2-2.3A6 6 0 0 0 12 3z"/><path d="M12 7.5v3l1.8 1.2"/></svg>',
    help: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.9.8c0 1.6-2.5 2.2-2.5 3.7"/><circle cx="12" cy="17.2" r="1" fill="currentColor" stroke="none"/></svg>',
  };
  const APP_VERSION = 8;  // shown once: what is new in this version
  const RADAR_MARK = '<svg class="radar-mark" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="14" style="fill:var(--accent-soft)"/><circle cx="16" cy="16" r="14" fill="none" style="stroke:var(--accent)" stroke-width="1.5"/><circle cx="16" cy="16" r="8.5" fill="none" style="stroke:var(--accent)" stroke-width="1" opacity=".55"/><path d="M16 16L16 2A14 14 0 0 1 28.1 9z" style="fill:var(--accent)" opacity=".35"/><path d="M16 16L28.1 9" style="stroke:var(--accent)" stroke-width="1.6" stroke-linecap="round"/><circle cx="21.5" cy="11.5" r="2" style="fill:var(--good)"/></svg>';

  // ------------------------------------------------------------------ state
  const state = {
    tab: 'today', data: null, demo: false, status: null, index: null, viewing: null, loading: true,
    backtest: undefined, model: undefined, review: undefined, pmAll: false, pmShow: 'all', tickers: undefined,
    view: store.get('view', 'simple'),   // simple = only the picks, the four things that matter and radar vs. the S&P 500 search: '', searchMsg: null, job: store.get('job'),
    installEvt: null, sheet: null, savedInfo: {}, unsaveAsk: null,
  };

  async function getJSON(path) {
    if (PREVIEW) return EMBED[path] ? JSON.parse(JSON.stringify(EMBED[path])) : null;
    try {
      const r = await fetch(`data/${path}?v=${Date.now()}`, { cache: 'no-store' });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  async function loadAll() {
    state.loading = true;
    const [status, latest] = await Promise.all([getJSON('status.json'), getJSON('latest.json')]);
    state.status = status;
    if (latest) { state.data = latest; state.demo = false; state.index = await getJSON('index.json'); }
    else if (PREVIEW) { state.data = await getJSON('demo/latest.json'); state.demo = !!state.data; state.index = null; }
    else { state.data = null; state.demo = false; state.index = null; }  // the real app never shows demo data
    state.viewing = null;
    state.backtest = undefined;
    state.model = undefined;
    state.review = undefined;
    state.tickers = undefined;
    state.tg = undefined;
    state.savedInfo = {};
    state.loading = false;
    render();
    reconcileSaved();
  }

  // ------------------------------------------------------------------ small components
  function ring(v, color, cap, big) {
    const r = 24, c = 2 * Math.PI * r, f = Math.max(0, Math.min(100, v || 0)) / 100;
    return `<div class="ring${big ? ' big' : ''}" role="img" aria-label="${esc(cap)} ${Math.round(v)} מתוך 100">
      <svg viewBox="0 0 58 58"><circle cx="29" cy="29" r="${r}" fill="none" style="stroke:var(--surface-2)" stroke-width="6"/>
      <circle cx="29" cy="29" r="${r}" fill="none" style="stroke:${color}" stroke-width="6" stroke-linecap="round" stroke-dasharray="${(c * f).toFixed(1)} ${c.toFixed(1)}"/></svg>
      <span class="val">${Math.round(v)}</span>${cap ? `<span class="cap">${esc(cap)}</span>` : ''}</div>`;
  }
  function divRow(label, v, valText, note) {
    const w = Math.min(50, Math.abs(v) * 50);
    const pos = v >= 0;
    const style = pos ? `inset-inline-start:50%;width:${w}%;background:var(--good)` : `inset-inline-end:50%;width:${w}%;background:var(--bad)`;
    return `<div class="div-row"><span>${esc(label)}${note ? `<span class="note">${esc(note)}</span>` : ''}</span>
      <div class="div-track" role="img" aria-label="${esc(label)} ${esc(valText)}"><i style="${style}"></i></div><span class="v">${esc(valText)}</span></div>`;
  }
  const divHead = (neg, posTxt) => `<div class="div-head"><span>${neg}</span><span>${posTxt}</span></div>`;
  function banner(kind, title, text, extra = '') {
    return `<div class="banner ${kind}"><span class="dot${kind === 'info' && /רצה/.test(title) ? ' pulse' : ''}"></span><div class="grow"><b>${esc(title)}</b>${text ? `<span>${esc(text)}</span>` : ''}${extra}</div></div>`;
  }
  const changeOver = (closes, n) => {
    const v = (closes || []).filter(isNum);
    if (v.length < 2) return null;
    const a = v[Math.max(0, v.length - 1 - n)], b = v[v.length - 1];
    return a ? b / a - 1 : null;
  };

  // ------------------------------------------------------------------ charts (SVG, drawn after layout)
  let chartSeq = 0;
  const charts = new Map();
  function chartSlot(cls, cfg) {
    const id = 'c' + (++chartSeq);
    charts.set(id, cfg);
    return `<div class="${cls}" data-chart="${id}"></div>`;
  }
  function niceTicks(lo, hi, count) {
    const span = hi - lo;
    if (!(span > 0)) return [lo];
    const raw = span / count, mag = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / mag;
    const step = (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
    const out = [];
    for (let t = Math.ceil(lo / step) * step; t <= hi + 1e-9; t += step) out.push(+t.toFixed(10));
    return out;
  }
  function drawLine(el, cfg) {
    const W = Math.max(140, Math.floor(el.clientWidth)), H = cfg.height || 200, axes = cfg.axes !== false;
    const padL = axes ? 4 : 3, padR = axes ? 48 : 5, padT = 8, padB = axes ? 22 : 4;
    const n = cfg.series[0].v.length;
    const vals = [];
    cfg.series.forEach((s) => s.v.forEach((x) => { if (isNum(x)) vals.push(x); }));
    (cfg.refs || []).forEach((r) => { if (isNum(r.y)) vals.push(r.y); });
    if (vals.length < 2 || n < 2) { el.innerHTML = '<div class="muted small">אין מספיק נתונים לגרף</div>'; return; }
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (lo === hi) { lo -= Math.abs(lo) * 0.02 || 1; hi += Math.abs(hi) * 0.02 || 1; }
    const pad = (hi - lo) * 0.07; lo -= pad; hi += pad;
    if (cfg.floor != null) lo = Math.max(lo, cfg.floor);
    const X = (i) => padL + (i * (W - padL - padR)) / (n - 1);
    const Y = (v) => padT + ((hi - v) * (H - padT - padB)) / (hi - lo);
    let s = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(cfg.label || 'גרף')}">`;
    if (axes) {
      niceTicks(lo, hi, cfg.yTicks || 3).forEach((t) => {
        const y = Y(t).toFixed(1);
        s += `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}" style="stroke:var(--line)" stroke-width="1"/>`;
        s += `<text x="${W - padR + 6}" y="${(+y + 4).toFixed(1)}" font-size="11" style="fill:var(--muted)" font-family="IBM Plex Mono, ui-monospace, monospace">${esc(cfg.fmtY ? cfg.fmtY(t) : t)}</text>`;
      });
      const idx = [0, Math.round((n - 1) / 2), n - 1];
      idx.forEach((i, k) => {
        const anchor = k === 0 ? 'start' : k === 2 ? 'end' : 'middle';
        s += `<text x="${X(i).toFixed(1)}" y="${H - 6}" font-size="11" text-anchor="${anchor}" style="fill:var(--muted)" font-family="IBM Plex Mono, ui-monospace, monospace">${esc(cfg.fmtX ? cfg.fmtX(i) : i)}</text>`;
      });
    }
    (cfg.refs || []).forEach((r) => {
      if (!isNum(r.y) || r.y < lo || r.y > hi) return;
      const y = Y(r.y).toFixed(1);
      s += `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}" style="stroke:${r.color}" stroke-width="1.5" stroke-dasharray="5 4"/>`;
      if (r.label) s += `<text x="${padL + 4}" y="${(+y - 5).toFixed(1)}" font-size="11.5" style="fill:${r.color}" font-weight="700">${esc(r.label)}</text>`;
    });
    cfg.series.forEach((ser) => {
      let d = '', started = false, first = null, last = null;
      ser.v.forEach((v, i) => {
        if (!isNum(v)) { started = false; return; }
        d += `${started ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
        started = true;
        if (first == null) first = i;
        last = i;
      });
      if (!d) return;
      if (ser.area) {
        s += `<path d="${d}L${X(last).toFixed(1)},${H - padB}L${X(first).toFixed(1)},${H - padB}Z" style="fill:var(--area)" stroke="none"/>`;
      }
      s += `<path d="${d}" fill="none" style="stroke:${ser.color}" stroke-width="${ser.width || 2}" stroke-linejoin="round" stroke-linecap="round"${ser.dash ? ' stroke-dasharray="5 4"' : ''}/>`;
      if (ser.dot !== false) {
        s += `<circle cx="${X(last).toFixed(1)}" cy="${Y(ser.v[last]).toFixed(1)}" r="${axes ? 4 : 3}" style="fill:${ser.color};stroke:var(--surface)" stroke-width="2"/>`;
      }
    });
    if (cfg.tip) {
      s += `<g class="cross" style="display:none"><line y1="${padT}" y2="${H - padB}" style="stroke:var(--muted)" stroke-width="1"/>`;
      cfg.series.forEach((ser) => { s += `<circle r="4.5" style="fill:${ser.color};stroke:var(--surface)" stroke-width="2"/>`; });
      s += '</g>';
    }
    s += '</svg>';
    el.innerHTML = s + (cfg.tip ? '<div class="tip" hidden></div>' : '');
    if (!cfg.tip) return;
    const svg = el.querySelector('svg'), cross = svg.querySelector('.cross'), tip = el.querySelector('.tip');
    const line = cross.querySelector('line'), dots = cross.querySelectorAll('circle');
    const show = (ev) => {
      const rect = svg.getBoundingClientRect();
      let i = Math.round(((ev.clientX - rect.left - padL) / (W - padL - padR)) * (n - 1));
      i = Math.max(0, Math.min(n - 1, i));
      const x = X(i);
      line.setAttribute('x1', x); line.setAttribute('x2', x);
      const parts = [];
      cfg.series.forEach((ser, k) => {
        const v = ser.v[i];
        if (isNum(v)) { dots[k].setAttribute('cx', x); dots[k].setAttribute('cy', Y(v)); dots[k].style.display = ''; parts.push(`${ser.name ? ser.name + ': ' : ''}<b>${esc(cfg.fmtTip ? cfg.fmtTip(v) : v)}</b>`); }
        else dots[k].style.display = 'none';
      });
      cross.style.display = '';
      tip.innerHTML = `<span>${esc(cfg.fmtX ? cfg.fmtX(i, true) : i)}</span> · ${parts.join(' · ')}`;
      tip.hidden = false;
      const tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(W - tw, x - tw / 2)) + 'px';
    };
    const hide = () => { cross.style.display = 'none'; tip.hidden = true; };
    svg.addEventListener('pointermove', show);
    svg.addEventListener('pointerdown', show);
    svg.addEventListener('pointerleave', hide);
    svg.addEventListener('pointercancel', hide);
  }
  function drawCharts(root) {
    (root || document).querySelectorAll('[data-chart]').forEach((el) => {
      const cfg = charts.get(el.dataset.chart);
      if (cfg) drawLine(el, cfg);
    });
  }
  let resizeT;
  window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(() => drawCharts(), 150); });

  // the scan runs Tuesday to Saturday mornings (UTC, after each US trading day); later than 11:00 UTC
  // (14:00 in Israel) the scan of that morning should exist, including GitHub's usual delays and backup times
  function expectedScanDate() {
    const now = new Date();
    let d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    if (now.getUTCHours() < 11) d = new Date(d.getTime() - 864e5);
    for (let i = 0; i < 7 && !(d.getUTCDay() >= 2 && d.getUTCDay() <= 6); i++) d = new Date(d.getTime() - 864e5);
    return d.toISOString().slice(0, 10);
  }
  const isStale = () => !!(state.data && !state.viewing && !state.demo && !PREVIEW && String(state.data.date) < expectedScanDate());
  // with a cloud connection, a skipped morning scan is started automatically (at most once every 6 hours)
  function maybeAutoScan() {
    if (!isStale() || !cloudReady() || state.job) return;
    if (Date.now() - (store.get('autoScan', 0) || 0) < 6 * 3600e3) return;
    store.set('autoScan', Date.now());
    startRun('scan');
  }

  // ------------------------------------------------------------------ views: today
  function statusBanners() {
    const out = [];
    const job = state.job;
    if (job) {
      const what = job.mode === 'ticker' ? `ניתוח ${job.symbols}` : job.mode === 'backtest' ? 'בדיקה לאחור' : job.mode === 'test-alert' ? 'הודעת בדיקה' : job.mode === 'train' ? 'אימון מודל הסיכוי' : job.mode === 'telegram' ? 'משיכה מהטלגרם' : 'סריקה';
      const st = job.state === 'queued' ? 'ממתינה בתור' : 'רצה עכשיו';
      out.push(banner('info', `${what} ${st} בענן`, `התחילה ${ago(new Date(job.since).toISOString())}. האפליקציה תתעדכן לבד כשתסתיים.`));
    }
    const st = state.status;
    if (st && st.ok === false) {
      out.push(banner('bad', 'הריצה האחרונה בענן נכשלה', `${st.message || ''} (${ago(st.finished)})`));
    } else if (st && st.ok && st.mode === 'setup' && (!state.data || daysSince(st.finished) < 0.2)) {
      out.push(banner('good', state.data ? 'העדכון הותקן' : 'ההתקנה הצליחה', st.message));
    } else if (st && st.ok && st.mode === 'train' && daysSince(st.finished) < 1) {
      out.push(banner('good', 'מודל הסיכוי אומן ונבדק', st.message));
    }
    const deg = (state.data && state.data.degraded) || [];
    if (deg.length && !state.demo) {
      out.push(banner('warn', 'הסריקה חלקית', `${deg.join('. ')}. הבחירות מוצגות, אבל לא נפתחו עסקאות חדשות בתיק הנייר, והמערכת תנסה שוב בריצת הגיבוי של הבוקר.`));
    }
    if (state.viewing) {
      out.push(banner('warn', `מוצגת הסריקה מ־${dmy(state.viewing)}`, '', '<div style="margin-top:6px"><button class="btn small ghost" data-act="today-latest">חזרה לסריקה האחרונה</button></div>'));
    }
    if (state.demo) {
      out.push(banner('info', 'נתוני דמו', PREVIEW
        ? 'זו תצוגה מקדימה עם מניות בדויות (DMO). אחרי ההתקנה יופיעו כאן מניות אמיתיות מהסריקה היומית.'
        : 'המניות כאן בדויות. הנתונים האמיתיים יופיעו אחרי שהסריקה הראשונה בענן תסתיים.'));
    } else if (isStale() && !job) {
      out.push(banner('warn', 'הסריקה של הבוקר לא רצה', `הסריקה האחרונה מ־${dmy(state.data.date)}. GitHub לפעמים מדלג על הרצות מתוזמנות. ${cloudReady() ? 'אפשר להפעיל אותה עכשיו.' : 'אפשר להפעיל אותה ידנית מההגדרות, אחרי חיבור לענן.'}`,
        cloudReady() ? '<div style="margin-top:6px"><button class="btn small ghost" data-run="scan">הרץ סריקה עכשיו</button></div>' : ''));
    }
    return out.join('');
  }

  // the model's rule: 'r' = target is N x the distance to the stop, 'pct' = a fixed % rise (scans from version 3)
  const isPctRule = (r) => !r.prob_rule || r.prob_rule.mode === 'pct';
  const rulePct = (r) => (r.prob_rule && r.prob_rule.pct) || 0.3;
  function probTitle(r) {
    if (isPctRule(r)) return `סיכוי לעלות ${pct(rulePct(r))} לפני הסטופ`;
    return 'סיכוי להגיע ליעד לפני הסטופ';
  }
  const bandFrac = () => { const d = state.data || {}; return d.band_frac || (d.model && d.model.band_frac) || null; };
  // the model's expected result per trade (what stocks at the same place in its ranking did in the test,
  // corrected by what really happened to my own recommendations since), against all stocks
  function expLine(r) {
    const h = r.prob_hist, lv = r.prob_live || {};
    const exp = isNum(lv.ret_adj) ? lv.ret_adj : (h && isNum(h.ret) ? h.ret : null);
    if (!isNum(exp) || !isNum(r.exp_base)) return '';
    const diff = exp - r.exp_base;
    const tone = diff >= 0.005 ? 'good' : diff <= -0.005 ? 'bad' : '';
    const mx = Math.max(0.01, Math.abs(exp), Math.abs(r.exp_base)) * 1.15;
    const w = (Math.max(0, exp) / mx) * 100, bw = (Math.max(0, r.exp_base) / mx) * 100;
    const bf = bandFrac();
    const where = h && h.band && bf ? `בין ${pct(bf, 1)} המובילות של המודל · ` : isNum(r.model_pct) ? `במקום ה־${pct(1 - r.model_pct, 0)} העליונים בדירוג · ` : '';
    return `<div class="prob ${tone}" role="img" aria-label="תוצאה צפויה לעסקה ${pct(exp, 1, true)} לעומת ${pct(r.exp_base, 1, true)} לכל המניות">
      <div class="prob-top"><span>תוצאה צפויה לעסקה</span><b class="num">${pct(exp, 1, true)}</b></div>
      <div class="prob-track"><i style="width:${w.toFixed(1)}%"></i><span class="base" style="inset-inline-start:${bw.toFixed(1)}%"></span></div>
      <div class="prob-sub">${where}כל המניות ${pct(r.exp_base, 1, true)}${isNum(r.prob) && r.plan && r.plan.target ? ` · הגיעו ליעד ${pct(r.prob)}` : ''}</div></div>`;
  }
  // chance (from the tested model) that the stock reaches its target before its stop is hit
  function probLine(r) {
    const e = expLine(r);
    if (e) return e;
    if (!isNum(r.prob) || !isNum(r.prob_base) || !r.prob_base) return '';
    const ratio = r.prob / r.prob_base;
    const tone = ratio >= 1.3 ? 'good' : ratio <= 0.7 ? 'bad' : '';
    const scale = Math.max(0.5, r.prob * 1.2);
    const w = Math.min(100, (r.prob / scale) * 100), bw = Math.min(100, (r.prob_base / scale) * 100);
    const plan = r.plan || {};
    const levels = !isPctRule(r) && isNum(plan.target_pct) && isNum(plan.stop_pct) ? `יעד ${pct(plan.target_pct, 0, true)} · סטופ ${pct(plan.stop_pct)} · ` : '';
    return `<div class="prob ${tone}" role="img" aria-label="${esc(probTitle(r))} ${pct(r.prob)} לעומת ממוצע ${pct(r.prob_base)}">
      <div class="prob-top"><span>${esc(probTitle(r))}</span><b>${pct(r.prob)}</b></div>
      <div class="prob-track"><i style="width:${w.toFixed(1)}%"></i><span class="base" style="inset-inline-start:${bw.toFixed(1)}%"></span></div>
      <div class="prob-sub">${levels}ממוצע ${pct(r.prob_base)}${ratio >= 1.15 ? ` · פי ${ratio.toFixed(1)}` : ratio <= 0.85 ? ' · נמוך מהממוצע' : ' · בערך כמו הממוצע'}</div></div>`;
  }

  // ------------------------------------------------------------------ can the list be trusted? (shown first)
  // proven   = the model passed its out-of-sample test AND the paper portfolio beats the S&P 500 after 20+ closed trades
  // tested   = the model passed, but the paper record is still short
  // unproven = no tested edge yet: the list is for checking and paper trading only
  const MIN_CLOSED = 20;
  const N = (x) => `<span class="num">${esc(x)}</span>`;   // isolated number / Latin name inside Hebrew text
  const SPX = N('S&P 500');
  // proven = the model passed its test AND, a month after each recommendation, at least 20 of its picks did clearly
  // better than the stocks it checked and did not pick, and better than the S&P 500 over the same days
  function trustState(d) {
    const m = (d && d.model) || null;
    const rv = (d && d.review) || {};
    if (!m || !m.passed || rv.drift === 'bad') return 'unproven';
    const beatsSpy = isNum(rv.pick_ret) && isNum(rv.pick_spy) && rv.pick_ret > rv.pick_spy;
    return (rv.graded || 0) >= MIN_CLOSED && rv.verdict === 'good' && beatsSpy ? 'proven' : 'tested';
  }
  function modelSpy(m) {
    if (m && isNum(m.spy_ret)) return m.spy_ret;
    const v = state.model && state.model.validation;
    return v && v.money && v.money.model && isNum(v.money.model.spy) ? v.money.model.spy : null;
  }
  // these return HTML: fixed Hebrew text plus isolated numbers
  function modelLine(m) {
    if (!m) return 'מודל הסיכוי עוד לא אומן ונבדק, ולכן אין הוכחה שהבחירות טובות מהממוצע.';
    if (!isNum(m.top_ret) || !isNum(m.all_ret)) {
      return m.passed ? 'מודל הסיכוי עבר את הבדיקה על שנים שלא ראה.' : 'מודל הסיכוי לא עבר את הבדיקה על שנים שלא ראה.';
    }
    const spy = modelSpy(m);
    return `בבדיקה הסופית, על תקופה שלא שימשה לשום החלטה: המובילות של המודל ${N(pct(m.top_ret, 1, true))} לעסקה בממוצע, כל המניות ${N(pct(m.all_ret, 1, true))}${isNum(spy) ? `, ${SPX} ${N(pct(spy, 1, true))}` : ''}.`;
  }
  function paperLine(p) {
    if (!p || !p.n) return 'תיק הנייר עוד ריק. הוא מתחיל לעקוב אחרי הבחירות מהסריקה הבאה.';
    const closedTxt = `נסגרו ${N(p.closed || 0)} עסקאות, ${N(p.open || 0)} פתוחות.`;
    return `תיק הנייר: ${N(pct(p.return_on_invested, 1, true))} מאז ${N(dm(p.since))}${isNum(p.spy_ret) ? `, ${SPX} באותה תקופה ${N(pct(p.spy_ret, 1, true))}` : ''}. ${closedTxt}`;
  }
  function reviewLine(rb) {
    if (!rb) return '';
    if (!rb.graded) {
      return `ביקורת עצמית: כל המלצה נשמרת ונבדקת אחרי כחודש מול מה שקרה באמת${rb.next_check ? `. הבדיקה הראשונה בסביבות ${N(dm(rb.next_check))}` : ''}.`;
    }
    const edge = isNum(rb.edge) ? `, ${N(pct(rb.edge, 1, true))} לבחירה לעומת המניות שלא בחרתי` : '';
    const drift = rb.drift === 'bad' ? '. המודל מצליח בפועל פחות מבבדיקה, ולכן המשקל שלו הוקטן' : '';
    return `ביקורת עצמית: ${N(rb.graded)} בחירות כבר נבדקו אחרי חודש${edge}${rb.lessons ? `. ${N(rb.lessons)} לקחים פעילים` : ''}${drift}.`;
  }
  // gaps in today's data that change how far the list can be trusted
  function dataIssues(d) {
    const out = [];
    const res = d.results || [];
    const h = d.health;
    const n = h ? h.n : res.length;
    const news = h ? h.news : res.filter((r) => (r.news || []).length).length;
    if (n >= 10 && news === 0) out.push('לא התקבלו חדשות לאף מניה בסריקה הזו, ולכן אותות החדשות לא פעלו. לבדוק חדשות ידנית.');
    if (res.length && !res.some((r) => 'filings_checked' in r)) out.push('הסריקה הזו מלפני העדכון: בדיקת עסקאות הרכישה תרוץ מהסריקה הבאה.');
    else if (h && n >= 10 && h.filings === 0) out.push('דיווחי SEC לא התקבלו בסריקה הזו, ולכן בדיקת עסקאות הרכישה וההנפקות לא רצה.');
    if (d.telegram && d.telegram.per) {
      const t = d.telegram;
      const miss = Object.keys(t.per).filter((c) => (t.reach && t.reach[c] ? !t.reach[c].ok : t.per[c] === 0));
      if (miss.length && miss.length === Object.keys(t.per).length) out.push('ערוצי הטלגרם לא נקראו בסריקה הזו, ולכן האזכורים שם לא מוצגים.');
      else if (miss.length) out.push(`ערוץ הטלגרם ${miss.map((c) => tgLabel(c)).join(', ')} לא נקרא בסריקה הזו, ולכן האזכורים שם לא מוצגים.`);
    } else if (d.telegram && d.telegram.posts === 0) out.push('ערוצי הטלגרם לא נקראו בסריקה הזו, ולכן האזכורים שם לא מוצגים.');
    return out;
  }
  function trustCard(d) {
    if (state.demo) return '';
    const st = trustState(d);
    const m = d.model || null;
    if (m && !isNum(m.spy_ret) && state.model === undefined) loadModel();
    const rvb = d.review || {};
    const head = st === 'proven' ? 'יש יתרון בבדיקה ההיסטורית וגם בהמלצות בפועל'
      : st === 'tested' ? 'המודל עבר בדיקה, ההמלצות בפועל עוד נבדקות'
        : rvb.drift === 'bad' ? 'המודל מצליח בפועל פחות מבבדיקה' : 'המערכת עוד לא הוכיחה יתרון על המדד';
    const bottom = st === 'proven' ? 'גם יתרון מוכח לא מבטיח את העסקה הבאה: כל כניסה עם סטופ וכמות לפי החישוב.'
      : st === 'tested' ? `עדיף לעקוב בתיק הנייר עד ש־${MIN_CLOSED} בחירות לפחות ייבדקו חודש אחרי ההמלצה${rvb.next_check && !rvb.graded ? ` (הבדיקה הראשונה בסביבות ${dm(rvb.next_check)})` : ''}, ורק אז לסמוך על הרשימה.`
        : 'לכן: רשימה לבדיקה ולתיק נייר, לא לקנייה בכסף אמיתי.';
    const issues = dataIssues(d);
    return `<section class="card trust ${st === 'proven' ? 'good' : 'warn'}" aria-label="האם לסמוך על הרשימה">
      <div class="trust-head"><span class="dot"></span><h3>${esc(head)}</h3></div>
      <ul class="trust-list"><li>${modelLine(m)}</li><li>${paperLine(d.paper)}</li>${d.review ? `<li>${reviewLine(d.review)}</li>` : ''}${issues.map((x) => `<li class="issue">${esc(x)}</li>`).join('')}</ul>
      <p class="trust-bottom">${esc(bottom)}</p>
      <div class="trust-actions"><button class="btn small" data-act="guide">איך קוראים את המסך</button><button class="btn small ghost" data-tab="perf">הבדיקה המלאה</button><button class="btn small ghost" data-tab="learn">מה למדתי</button></div>
    </section>`;
  }
  function whatsNew() {
    if (state.demo || PREVIEW || !state.data || store.get('seen', 0) >= APP_VERSION) return '';
    return banner('info', 'חדש: תצוגה פשוטה', 'במסך רק המניות של היום, ובכל אחת ארבעה דברים: פוטנציאל, סיכון, הסיבות והסטופ. למעלה – האם הרדאר מנצח את S&P 500. מאחורי הקלעים המודל שעבר את הבדיקה בוחר את המניות מכל השוק. כל המסכים הקודמים נמצאים ב"תצוגה מלאה" ובהגדרות.',
      '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap"><button class="btn small" data-act="guide">איך קוראים את המסך</button><button class="btn small ghost" data-act="seen">הבנתי</button></div>');
  }

  // ------------------------------------------------------------------ pre-entry checklist
  // Built only from what the scan already knows. Each item: good / bad / unk (not known). Reading the news stays manual.
  function daysUntil(iso, fromIso) {
    if (!iso) return null;
    const from = String(fromIso || new Date().toISOString()).slice(0, 10);
    const a = new Date(from + 'T12:00:00Z').getTime(), b = new Date(String(iso).slice(0, 10) + 'T12:00:00Z').getTime();
    return isFinite(a) && isFinite(b) ? Math.round((b - a) / 864e5) : null;
  }
  const hasCon = (r, re) => (r.cons || []).some((c) => re.test(c));
  const sigScore = (r, key) => { const x = (r.signals || []).find((s) => s.key === key); return x ? x.score : null; };
  const FLAG_TEXT = {
    offering: 'דיווח הנפקה ל־SEC בשבועות האחרונים: ייתכן דילול ולחץ מכירות',
    late: 'החברה הודיעה על איחור בדוח הכספי',
    bankruptcy: 'דיווח על פשיטת רגל או כינוס נכסים',
    restatement: 'החברה הודיעה שאין להסתמך על דוחות כספיים קודמים',
    listing: 'הודעה על אי עמידה בכללי הבורסה',
  };
  const DEAL_CHECK = {
    target: 'בתהליך רכישה: הרווח מוגבל למחיר העסקה', completed: 'עסקת רכישה בשלבי סגירה',
    suspect: 'חשד להצעת רכישה: לבדוק בחדשות', involved: 'מעורבת בעסקת מיזוג: לבדוק את התנאים',
    terminated: 'ייתכן שעסקת מיזוג בוטלה: לבדוק',
  };
  function checklist(r, scanDate) {
    const items = [];
    const add = (st, html) => items.push({ st, html });   // html: Hebrew text, numbers wrapped with N()
    // 1. takeover
    if (r.deal) add('bad', esc(DEAL_CHECK[r.deal.status] || r.deal.short || 'עסקת מיזוג'));
    else if (r.exclude) add('bad', esc(r.exclude));
    else if (r.filings_checked) add('good', 'אין סימן לעסקת רכישה בדיווחי SEC, בכותרות או בתנועת המחיר');
    else if ('filings_checked' in r) add('unk', 'דיווחי SEC לא זמינים למניה הזו: לבדוק בחדשות שאין הצעת רכישה');
    else add('unk', 'עסקת רכישה: לא נבדק בסריקה הזו (נבדק מהסריקה הבאה)');
    // 2. quarterly report
    const dd = daysUntil(r.earnings_date, scanDate);
    if (dd == null || dd < 0) add('unk', 'תאריך הדוח הרבעוני הבא לא ידוע');
    else if (dd <= 10) add('bad', dd === 0 ? 'דוח רבעוני היום: המחיר יכול לקפוץ או לצנוח' : `דוח רבעוני בעוד ${N(dd)} ימים: ביום הדוח המחיר יכול לקפוץ או לצנוח`);
    else add('good', `הדוח הרבעוני הבא רחוק (${N(dmy(r.earnings_date))})`);
    // 3. analysts' price target
    if (isNum(r.target_mean) && isNum(r.price) && r.price > 0) {
      const gap = r.target_mean / r.price - 1;
      if (isNum(r.n_analysts) && r.n_analysts < 3) add('unk', 'מעט מאוד אנליסטים מכסים את המניה');
      else if (gap < 0) add('bad', `מחיר היעד הממוצע של האנליסטים, ${N(price(r.target_mean))}, נמוך מהמחיר ב־${N(pct(-gap))}`);
      else add('good', `מחיר היעד הממוצע של האנליסטים, ${N(price(r.target_mean))}, גבוה מהמחיר ב־${N(pct(gap))}`);
    } else {
      const sc = sigScore(r, 'price_target');
      if (sc == null) add('unk', 'אין נתוני מחיר יעד של אנליסטים');
      else if (sc < -0.05 || hasCon(r, /מחיר היעד הממוצע[^(]*\(\u200e?-\d/)) add('bad', 'מחיר היעד הממוצע של האנליסטים נמוך מהמחיר');
      else add('good', 'מחיר היעד של האנליסטים לא נמוך מהמחיר');
    }
    // 4. stretched after a fast rise
    const hot = isNum(r.rsi) && r.rsi > 75, far = isNum(r.ext50) && r.ext50 > 0.3;
    if (hot || far) add('bad', [hot ? `קנויה־יתר (${N('RSI ' + Math.round(r.rsi))})` : '', far ? `${N(pct(r.ext50))} מעל הממוצע של 50 ימים` : ''].filter(Boolean).join(', ') + ': לרוב עדיף לחכות לתיקון');
    else if (!isNum(r.rsi) && !isNum(r.ext50) && hasCon(r, /קנויה־יתר|מתוחה/)) add('bad', 'המחיר מתוח אחרי עלייה מהירה: לרוב עדיף לחכות לתיקון');
    else add('good', 'המחיר לא מתוח מדי בטווח הקצר');
    // 5. company events from SEC filings (offering, late report, bankruptcy...)
    const flags = (r.flags || []).filter((f) => FLAG_TEXT[f]);
    if (flags.length) flags.forEach((f) => add('bad', FLAG_TEXT[f]));
    else if (hasCon(r, /הנפקת מניות|מצוקה פיננסית/)) add('bad', 'בחדשות: הנפקת מניות או מצוקה פיננסית');
    else if (r.filings_checked) add('good', 'אין בדיווחי SEC הנפקה, איחור בדוחות או פשיטת רגל');
    else add('unk', 'הנפקות ואירועי חברה: לא נבדק');
    // 6. overall risk score
    if (isNum(r.risk) && r.risk >= 60) add('bad', `ציון סיכון גבוה (${N(Math.round(r.risk))})`);
    else if (isNum(r.risk)) add('good', `ציון הסיכון סביר (${N(Math.round(r.risk))})`);
    return items;
  }
  const nBad = (n) => (n === 1 ? 'בעיה אחת' : `${n} בעיות`);
  function checkChip(r, scanDate) {
    const bad = checklist(r, scanDate).filter((x) => x.st === 'bad').length;
    return bad ? `<span class="chip bad">בדיקה לפני כניסה: ${nBad(bad)}</span>` : '<span class="chip good">בדיקה לפני כניסה: תקין</span>';
  }
  function checklistCard(r, ctx) {
    const items = checklist(r, ctx.date);
    const bad = items.filter((x) => x.st === 'bad').length;
    const unproven = !ctx.demo && trustState(state.data) === 'unproven';
    let verdict, tone;
    if (r.exclude) {
      verdict = `לא לקנייה: ${r.deal ? r.deal.short : r.exclude}.`; tone = 'bad';
    } else if (unproven) {
      verdict = `לא לכסף אמיתי כרגע: המערכת עוד לא הוכיחה יתרון על המדד. אפשר לעקוב אחרי המניה בתיק הנייר.${bad ? ` ובמניה עצמה: ${nBad(bad)}.` : ''}`; tone = 'warn';
    } else if (bad) {
      verdict = `יש ${nBad(bad)} שצריך לבדוק לפני כל החלטה.`; tone = 'warn';
    } else {
      verdict = 'עברה את הבדיקה האוטומטית. נשאר לקרוא את החדשות האחרונות, ולהיכנס רק עם סטופ כפקודה אצל הברוקר.'; tone = 'good';
    }
    const mark = { good: ['✓', 'תקין'], bad: ['✗', 'בעיה'], unk: ['?', 'לא ידוע'] };
    const li = (st, html) => `<li class="${st}"><i aria-hidden="true">${mark[st][0]}</i><span><span class="sr-only">${mark[st][1]}: </span>${html}</span></li>`;
    const newsBtn = !ctx.demo && /^[A-Z][A-Z0-9.\-]*$/.test(r.ticker)
      ? `<a class="btn small ghost" href="https://finance.yahoo.com/quote/${encodeURIComponent(r.ticker)}/news" target="_blank" rel="noopener">חדשות אחרונות על ${esc(r.ticker)}</a>` : '';
    return `<div class="card" id="check"><h3>בדיקה לפני כניסה</h3>
      <ul class="checks">${unproven && !r.exclude ? li('bad', 'המערכת עוד לא הוכיחה יתרון על המדד (הכרטיס העליון במסך הראשי)') : ''}${items.map((x) => li(x.st, x.html)).join('')}${li('unk', 'בדיקה ידנית: מה קרה בחדשות בשבוע האחרון')}</ul>
      <div class="verdict ${tone}">${esc(verdict)}</div>
      ${newsBtn ? `<div class="links" style="margin-top:10px">${newsBtn}</div>` : ''}</div>`;
  }

  // ------------------------------------------------------------------ position size by the user's own account (saved on this phone)
  const SIZING_DEFAULT = { account: 50000, risk: 1, maxPos: 10 };
  function mySizing() {
    const z = store.get('sizing') || {};
    const ok = (v, lo, hi) => isNum(v) && v >= lo && v <= hi;
    return {
      account: ok(z.account, 100, 1e9) ? z.account : SIZING_DEFAULT.account,
      risk: ok(z.risk, 0.1, 10) ? z.risk : SIZING_DEFAULT.risk,
      maxPos: ok(z.maxPos, 1, 100) ? z.maxPos : SIZING_DEFAULT.maxPos,
      custom: !!store.get('sizing'),
    };
  }
  // same rule as the cloud: a stop hit costs `risk`% of the account, and no position is bigger than `maxPos`%
  function sizePlan(r) {
    const plan = r.plan || {};
    if (!isNum(r.price) || !isNum(plan.stop) || plan.stop >= r.price) return plan;
    const z = mySizing();
    const perShare = r.price - plan.stop;
    let shares = Math.floor((z.account * z.risk / 100) / perShare);
    const maxValue = z.account * z.maxPos / 100;
    if (shares * r.price > maxValue) shares = Math.floor(maxValue / r.price);
    shares = Math.max(0, shares);
    return { ...plan, shares, value: shares * r.price, risk_amount: shares * perShare, pct_of_account: (shares * r.price) / z.account };
  }
  function saveSizing() {
    const read = (id) => parseFloat(String((document.getElementById(id) || {}).value || '').replace(/[,\s$₪%]/g, ''));
    const z = { account: read('acct'), risk: read('riskp'), maxPos: read('maxp') };
    const msg = document.getElementById('sizing-msg');
    if (!(z.account >= 100 && z.account <= 1e9)) { msg.textContent = 'גודל התיק צריך להיות בין 100 ל־1,000,000,000 דולר.'; return; }
    if (!(z.risk >= 0.1 && z.risk <= 10)) { msg.textContent = 'הסיכון לעסקה צריך להיות בין 0.1% ל־10%. מקובל: 0.5% עד 1%.'; return; }
    if (!(z.maxPos >= 1 && z.maxPos <= 100)) { msg.textContent = 'הפוזיציה המקסימלית צריכה להיות בין 1% ל־100%.'; return; }
    store.set('sizing', z);
    msg.textContent = 'נשמר. הכמויות במסכי המניות מחושבות עכשיו לפי התיק שלך.';
  }

  function pickCard(r, i, scanDate) {
    const chg = changeOver(r.closes, 63);
    const [rc, rl] = riskLevel(r.risk);
    const warn = (r.cons || [])[0];
    return `<article class="card pick" role="button" tabindex="0" data-open="${i}" aria-label="${esc(r.ticker)} ${esc(r.name)}">
      <div class="pick-head">
        <div class="pick-id"><span class="tk">${esc(r.ticker)}</span>${saveBtn(r)}<div class="nm">${esc(r.name)}</div>
          <div class="where">${esc([r.industry, r.country].filter(Boolean).join(' · '))}</div></div>
        <div style="padding-bottom:14px">${ring(r.upside, 'var(--accent)', 'פוטנציאל')}</div>
      </div>
      <div class="spark-row">${chartSlot('spark', { series: [{ v: (r.closes || []).slice(-63), color: 'var(--accent)', area: true }], axes: false, height: 38, label: `מחיר ${r.ticker} בשלושה חודשים` })}
        <span class="chg ${chg >= 0 ? 'up' : 'down'}">${pct(chg, 0, true)}<span class="muted small" style="font-weight:400"> 3 חודשים</span></span></div>
      ${(r.pros || []).length ? `<ul class="pick-reasons">${r.pros.slice(0, 2).map((p) => `<li>${esc(p)}</li>`).join('')}</ul>` : ''}
      ${warn ? `<div class="pick-warn"><b>שים לב:</b><span>${esc(warn)}</span></div>` : ''}
      ${probLine(r)}
      <div class="chips"><span class="chip ${rc}">סיכון <b>${Math.round(r.risk)}</b> · ${rl}</span>
        <span class="chip">מחיר <b class="num">${price(r.price)}</b></span>
        ${r.plan && r.plan.stop ? `<span class="chip">סטופ <b class="num">${price(r.plan.stop)}</b></span>` : ''}
        ${r.plan && r.plan.target ? `<span class="chip">יעד <b class="num">${price(r.plan.target)}</b></span>` : ''}
        ${r.plan && r.plan.trail_atr ? `<span class="chip">סטופ נגרר <b class="num">${pct(r.plan.trail_pct, 0)}</b> מתחת לשיא</span>` : ''}</div>
      <div class="chips">${checkChip(r, scanDate)}${r.deal ? `<span class="chip warn">${esc(r.deal.short)}</span>` : ''}</div>
    </article>`;
  }

  function viewToday() {
    const d = state.data;
    if (state.loading) return loader();
    if (d && state.view === 'simple') return viewTodaySimple(d);
    if (!d) {
      return `<div class="stack">${statusBanners()}<div class="card empty"><div class="loader" style="padding:16px 0"><div class="sweep"></div></div>
        <h3>הסריקה הראשונה עוד לא הסתיימה</h3>
        <p>המערכת סורקת עכשיו מניות אמיתיות בבורסה האמריקאית: מחירים, קניות מנהלים, אנליסטים, חדשות, מלחמות ומאקרו. בפעם הראשונה זה לוקח 30 עד 60 דקות. אחר כך הסריקה רצה לבד כל בוקר.</p>
        <p class="small">אם עברו יותר משעתיים ועדיין אין תוצאות, בדוק את לשונית Actions במאגר ב־GitHub.</p></div></div>`;
    }
    const res = d.results || [];
    const picks = res.map((r, i) => [r, i]).filter(([r]) => r.pick);
    const watch = res.map((r, i) => [r, i]).filter(([r]) => !r.pick && !r.exclude).slice(0, 12);
    const excluded = res.map((r, i) => [r, i]).filter(([r]) => r.exclude).slice(0, 8);
    const trusted = trustState(d) !== 'unproven' || state.demo;
    const m = d.market || {};
    const mk = m.score > 0.25 ? 'good' : m.score < -0.25 ? 'bad' : '';
    const hot = (d.themes || []).filter((t) => t.ratio >= 1.25).slice(0, 3);
    return `<div class="stack">
      ${statusBanners()}
      ${whatsNew()}
      ${tgStrip()}
      ${trustCard(d)}
      <div class="strip">
        <button class="pill ${mk}" data-tab="market"><span class="sw"></span>שוק ${esc(m.label || '')}</button>
        ${hot.map((t) => `<button class="pill warn" data-tab="market"><span class="sw"></span>${esc(t.he)} · פי ${t.ratio.toFixed(1)}</button>`).join('')}
      </div>
      <div class="section-title"><h2>${trusted ? 'המניות שנבחרו' : 'מועמדות לבדיקה'}</h2><span class="muted small">${picks.length} מתוך ${d.counts ? d.counts.deep : res.length} שנבדקו לעומק</span></div>
      ${d.pick_mode === 'model' ? `<p class="muted small" style="margin:-6px 0 0">המודל דירג את כל המניות שעברו את מסנני הנזילות. הבחירות הן המובילות שלו${isNum(d.band_frac) ? ` (מתוך ${pct(d.band_frac, 1)} העליונים)` : ''} שיש להן גם פוטנציאל ${N(60)} ומעלה, אחרי פסילה של סיכון גבוה ועסקאות רכישה, ועד ${N(3)} מאותו ענף.</p>` : ''}
      ${picks.length ? picks.map(([r, i]) => pickCard(r, i, d.date)).join('') : '<div class="card empty">אף מניה לא עברה היום את הסינון. זה תקין – לפעמים הדבר הנכון הוא לחכות.</div>'}
      ${excluded.length ? `<div class="section-title"><h2>סוננו היום</h2><span class="muted small">היו עוברות את הסף, אבל לא מתאימות</span></div>
      <div class="card rows">${excluded.map(([r, i]) => `<button class="row" data-open="${i}"><span class="score-chip">${Math.round(r.upside)}</span>
        <span class="grow"><span class="tk">${esc(r.ticker)}</span><span class="nm">${esc(r.name)}</span></span>
        <span class="chip bad">${esc(r.exclude)}</span></button>`).join('')}</div>` : ''}
      ${watch.length ? `<div class="section-title"><h2>קרובות לסף</h2><span class="muted small">לפי ציון הפוטנציאל</span></div>
      <div class="card rows">${watch.map(([r, i]) => `<button class="row" data-open="${i}"><span class="score-chip">${Math.round(r.upside)}</span>
        <span class="grow"><span class="tk">${esc(r.ticker)}</span><span class="nm">${esc(r.name)}</span></span>
        <span class="chip ${riskLevel(r.risk)[0]}">סיכון ${Math.round(r.risk)}</span></button>`).join('')}</div>` : ''}
      <p class="muted small" style="text-align:center">סריקה מ־${dmy(d.date)} · נסרקו ${d.counts ? d.counts.universe : '–'} מניות, ${d.counts ? d.counts.screened : '–'} עברו את מסנני הנזילות<br>כלי עזר למחקר, לא ייעוץ השקעות.</p>
    </div>`;
  }

  // ------------------------------------------------------------------ simple view
  // The radar answers one question: which stocks have the most signs pointing up today (not: will it rise).
  // On each card: potential, risk, the reasons in words, and the stop. Above: does the radar beat the S&P 500?
  function scoreboard(d) {
    const rv = d.review || {};
    const p = d.paper || {};
    const lines = [];
    let head, tone;
    const n = rv.graded || 0;
    if (n > 0 && isNum(rv.pick_ret) && isNum(rv.pick_spy)) {
      const beat = rv.pick_ret > rv.pick_spy;
      const clear = beat && rv.verdict === 'good';
      head = n < MIN_CLOSED ? 'עוד מוקדם לשפוט' : clear ? 'הרדאר מנצח את המדד' : beat ? 'הרדאר לפני המדד, אבל עוד לא בבירור' : 'הרדאר לא מנצח את המדד';
      tone = n < MIN_CLOSED ? 'warn' : clear ? 'good' : beat ? 'warn' : 'bad';
      lines.push(`חודש אחרי כל המלצה: הבחירות ${N(pct(rv.pick_ret, 1, true))} בממוצע, ${SPX} באותם ימים ${N(pct(rv.pick_spy, 1, true))} (${N(n)} בחירות${n < MIN_CLOSED ? `, צריך לפחות ${N(MIN_CLOSED)}` : ''}).`);
    } else {
      head = 'עוד אין תוצאות להשוואה';
      tone = 'warn';
      lines.push(`כל המלצה נבדקת חודש אחרי, מול ${SPX} באותם ימים.${rv.next_check ? ` הבדיקה הראשונה בסביבות ${N(dm(rv.next_check))}.` : ''}`);
    }
    if (p.n) lines.push(`תיק הנייר: ${N(pct(p.return_on_invested, 1, true))} מאז ${N(dm(p.since))}${isNum(p.spy_ret) ? `, ${SPX} באותה תקופה ${N(pct(p.spy_ret, 1, true))}` : ''}.`);
    const bottom = tone === 'good' ? 'גם יתרון מוכח לא מבטיח את העסקה הבאה. תמיד עם סטופ.'
      : tone === 'bad' ? 'כל עוד זה המצב, אין לרדאר יתרון ועדיף מדד כמו IVV. הרשימה רק לתרגול.'
        : 'עד אז: רק סכומים קטנים לתרגול, או תיק הנייר.';
    return `<section class="card trust ${tone === 'good' ? 'good' : 'warn'}" aria-label="הרדאר מול S&P 500">
      <div class="trust-head"><span class="dot"></span><h3>הרדאר מול ${SPX}: ${esc(head)}</h3></div>
      <ul class="trust-list">${lines.map((x) => `<li>${x}</li>`).join('')}</ul>
      <p class="trust-bottom">${esc(bottom)}</p></section>`;
  }
  // public Telegram channels (e.g. ביזנס 10): shown next to the stock, not part of the score
  const tgLabel = (c) => { const t = state.tg || (state.data && state.data.telegram) || {}; return ((t.labels || {})[c]) || c || 'טלגרם'; };
  function tgNames() {   // the channels this scan read, by their Hebrew names
    const t = (state.data && state.data.telegram) || {};
    const list = t.channels || Object.keys(t.labels || {});
    return list.map((c) => tgLabel(c)).join(', ');
  }
  function whenText(iso) {
    if (!iso) return '';
    const d = Math.floor(daysSince(iso));
    return d <= 0 ? 'היום' : d === 1 ? 'אתמול' : `לפני ${d} ימים`;
  }
  function tgChip(r) {
    const x = (r.tg || [])[0];
    return x ? `<span class="chip">הוזכרה ב${esc(tgLabel(x.c))} · ${whenText(x.d)}</span>` : '';
  }
  function tgCard(r) {
    const list = r.tg || [];
    if (!list.length) {
      return r.tg_n === 0 ? `<p class="muted small" style="margin:0">לא הוזכרה השבוע בערוצי הטלגרם (${esc(tgNames())}) – עוד לא בכותרות בישראל.</p>` : '';
    }
    return `<div class="card"><h3>בטלגרם</h3>
      <p class="muted small" style="margin:2px 0 8px">${r.tg_n > list.length ? `${N(r.tg_n)} אזכורים בשבוע האחרון, האחרונים:` : 'אזכורים בשבוע האחרון:'}</p>
      <div class="rows">${list.map((x) => `<a class="row" href="${esc(x.u)}" target="_blank" rel="noopener"><span class="grow"><span class="nm" style="white-space:normal;color:var(--ink)">${esc(x.t)}</span>
        <span class="nm">${esc(tgLabel(x.c))} · ${x.d ? dmy(x.d) : ''} · ${whenText(x.d)}</span></span></a>`).join('')}</div>
      <p class="muted small" style="margin:8px 0 0">הערוצים מסכמים בעברית חדשות שכבר פורסמו. אזכור אומר שהמניה כבר בכותרות בישראל – לא שהיא תעלה. הביקורת העצמית בודקת אם מניות שהוזכרו שם הצליחו יותר או פחות.</p></div>`;
  }
  // ---------------------------------------------------------------- saved stocks (the star)
  // Kept on this phone until deleted, whatever the radar shows. The list is also sent to the cloud (mode 'saved'),
  // so the daily scan analyses these stocks every day, picked or not.
  const savedGet = () => { const v = store.get('saved'); return v && Array.isArray(v.list) ? v : { list: [], updated: 0, dirty: false }; };
  const isSaved = (t) => savedGet().list.some((x) => x.t === t);
  function saveBtn(r, big) {
    const on = isSaved(r.ticker);
    return `<button class="save-btn${on ? ' on' : ''}${big ? ' big' : ''}" data-act="save" data-t="${esc(r.ticker)}" aria-pressed="${on}" aria-label="${on ? 'הסר מהשמורות' : 'שמור'} ${esc(r.ticker)}">${on ? ICON.starOn : ICON.star}${big ? `<span>${on ? 'שמורה' : 'שמור'}</span>` : ''}</button>`;
  }
  function findStock(t) {
    const res = (state.data && state.data.results) || [];
    const r = res.find((x) => x.ticker === t);
    if (r) return r;
    if (state.sheet && state.sheet.kind === 'detail' && state.sheet.r.ticker === t) return state.sheet.r;
    return null;
  }
  const lastNum = (a) => { for (let k = (a || []).length - 1; k >= 0; k--) if (isNum(a[k])) return a[k]; return null; };
  // the scan the stock's numbers came from (today's scan, or its own analysis opened from the search tab)
  function findCtx(t) {
    if (state.data && (state.data.results || []).some((x) => x.ticker === t)) return { date: state.data.date, spy_closes: state.data.spy_closes };
    if (state.sheet && state.sheet.kind === 'detail' && state.sheet.r.ticker === t) return state.sheet.ctx || {};
    return {};
  }
  // how the radar saw the stock at the moment of the star (also kept in the cloud by ci/saved.py)
  function makeSnap(r, ctx) {
    const pl = r.plan || {};
    return { date: ctx.date || null, price: r.price, upside: r.upside, risk: r.risk, stop: pl.stop, target: pl.target,
      pick: !!r.pick, why_not: r.why_not || null, pros: (r.pros || []).slice(0, 3), cons: (r.cons || []).slice(0, 2),
      tg_n: r.tg_n, prob: r.prob, earnings_date: r.earnings_date || null, spy: lastNum(ctx.spy_closes), name: r.name };
  }
  function toggleSave(t) {
    const v = savedGet();
    const i = v.list.findIndex((x) => x.t === t);
    if (i >= 0) { v.list.splice(i, 1); toast(`${t} הוסרה מהשמורות`); }
    else {
      const r = findStock(t) || { ticker: t };
      v.list.unshift({ t, name: r.name || '', at: new Date().toISOString().slice(0, 10), price: isNum(r.price) ? r.price : null,
        snap: r.upside !== undefined ? makeSnap(r, findCtx(t)) : null });
      toast(`${t} נשמרה בלשונית "שמורות" עד שתמחק אותה`);
    }
    v.updated = Date.now(); v.dirty = true;
    store.set('saved', v);
    state.unsaveAsk = null;
    scheduleSync();
    if (state.sheet) renderSheet(true);
    render();
  }
  let syncT = null;
  function scheduleSync() { clearTimeout(syncT); syncT = setTimeout(syncSaved, 12000); }
  async function syncSaved() {
    const v = savedGet();
    if (!v.dirty || !cloudReady() || PREVIEW || state.demo) return;
    if (state.job) { syncT = setTimeout(syncSaved, 60000); return; }   // never in the middle of the user's own run
    const repo = ghRepo();
    try {
      await gh(`/repos/${repo.owner}/${repo.repo}/actions/workflows/radar.yml/dispatches`, {
        method: 'POST', body: JSON.stringify({ ref: store.get('branch', 'main'), inputs: { mode: 'saved', symbols: v.list.map((x) => x.t).join(' ') } }),
      });
      v.syncAt = Date.now(); store.set('saved', v);
    } catch (e) { /* tried again on the next load */ }
  }
  // the cloud copy: adopt it when this phone has no unsent change (a change made on another device), confirm a sync
  async function reconcileSaved() {
    if (PREVIEW || state.demo) return;
    const cloud = await getJSON('saved.json');
    const v = savedGet();
    const mine = v.list.map((x) => x.t);
    const theirs = cloud && Array.isArray(cloud.tickers) ? cloud.tickers : null;
    const same = theirs && theirs.length === mine.length && theirs.every((t) => mine.includes(t));
    const items = (cloud && cloud.items) || {};
    const fromCloud = (t) => {
      const it = items[t] || {}, sn = it.snap || null;
      return { t, name: (sn && sn.name) || '', at: String(it.since || cloud.updated || '').slice(0, 10), price: sn ? sn.price : null, snap: sn };
    };
    if (theirs && same) { if (v.dirty) v.dirty = false; }
    else if (theirs && !v.dirty) v.list = theirs.map((t) => v.list.find((x) => x.t === t) || fromCloud(t));
    else if (!theirs && mine.length) v.dirty = true;
    if (theirs && !cloud.items && mine.length) v.dirty = true;   // an older cloud copy without snapshots: send again
    v.list.forEach((x) => { if (!x.snap && items[x.t] && items[x.t].snap) { x.snap = items[x.t].snap; if (!isNum(x.price)) x.price = x.snap.price; } });
    store.set('saved', v);
    if (v.dirty && (!v.syncAt || Date.now() - v.syncAt > 15 * 60e3)) syncSaved();
    render();
  }
  async function loadSavedInfo(t) {
    state.savedInfo[t] = null;   // loading
    const f = await getJSON(`tickers/${encodeURIComponent(t)}.json`);
    state.savedInfo[t] = f && f.result ? { date: f.date, r: f.result, spark_dates: f.spark_dates, demo: f.demo, spy_closes: f.spy_closes } : false;
    if (!state.sheet) render();
  }
  function viewSaved() {
    const v = savedGet();
    if (state.loading) return loader();
    const head = `<div class="section-title"><h2>המניות השמורות שלי</h2>${v.list.length ? `<span class="muted small">${N(v.list.length)} מניות</span>` : ''}</div>`;
    if (!v.list.length) {
      return `<div class="stack">${head}<div class="card empty"><span class="saved-star big">${ICON.starOn}</span>
        <h3>עוד לא שמרת מניות</h3>
        <p>לחיצה על הכוכב ${ICON.star.replace('<svg', '<svg width="16" height="16" style="vertical-align:-3px"')} ליד מניה – בכרטיס במסך "היום" או בראש המסך של המניה – שומרת אותה כאן. היא נשארת עד שתמחק אותה, גם כשהרדאר לא בוחר או לא מציג אותה.</p></div></div>`;
    }
    return `<div class="stack">${head}${savedSection()}</div>`;
  }
  // a saved stock as it was at the star, and as the radar sees it now (one line per number, no tables)
  function savedNow(t) {
    const d = state.data || {};
    const res = d.results || [];
    const i = res.findIndex((r) => r.ticker === t);
    if (i >= 0) return { r: res[i], i, date: d.date, spy: lastNum(d.spy_closes), dates: d.spark_dates };
    if (state.savedInfo[t] === undefined) loadSavedInfo(t);
    const info = state.savedInfo[t];
    if (info) return { r: info.r, i: -1, date: info.date, spy: lastNum(info.spy_closes) || lastNum(d.spy_closes), dates: info.spark_dates, info: true };
    return null;
  }
  const arrow = (a, b, fmt, better) => {
    if (!isNum(a) && !isNum(b)) return '–';
    if (!isNum(b) || !isNum(a)) return fmt(isNum(b) ? b : a);
    const diff = b - a;
    const cls = Math.abs(diff) < 1e-9 ? '' : (diff > 0) === (better > 0) ? 'up' : 'down';
    const sign = Math.abs(diff) < 0.5 ? 'בלי שינוי' : diff > 0 ? '▲' : '▼';
    return `כששמרת ${fmt(a)} · עכשיו <b class="${cls}">${fmt(b)}</b> <span class="${cls}">${sign}</span>`;
  };
  const statusText = (x) => (x.pick ? 'נבחרה' : x.why_not && WHY_NOT[String(x.why_not).split(':')[0]] ? `לא נבחרה (${WHY_NOT[String(x.why_not).split(':')[0]]})` : 'לא נבחרה');
  function savedCard(s) {
    if (state.unsaveAsk === s.t) {
      return `<div class="card saved-item"><div class="row" style="cursor:default;border:0"><span class="grow"><b>למחוק את ${esc(s.t)} מהשמורות?</b>
        <span class="nm wrap">תמונת המצב מהרגע ששמרת תימחק איתה.</span></span>
        <button class="btn small" data-act="unsave" data-t="${esc(s.t)}">מחק</button><button class="btn small ghost" data-act="unsave-no">ביטול</button></div></div>`;
    }
    const sn = s.snap || null;
    const now = savedNow(s.t);
    const r = now && now.r;
    const name = (r && r.name) || (sn && sn.name) || s.name;
    const p0 = sn && isNum(sn.price) ? sn.price : s.price;
    const sameDay = sn && now && sn.date && sn.date === now.date;
    const lines = [];
    // price, and the S&P 500 over the same days
    if (r && isNum(p0) && isNum(r.price) && !sameDay) {
      const chg = r.price / p0 - 1;
      lines.push(['מחיר', `${price(p0)} כששמרת · עכשיו ${price(r.price)} <b class="${chg >= 0 ? 'up' : 'down'}">${pct(chg, 1, true)}</b>`]);
      if (sn && isNum(sn.spy) && isNum(now.spy)) {
        const spy = now.spy / sn.spy - 1;
        const ex = chg - spy;
        lines.push(['מול S&P 500', `המדד באותם ימים ${pct(spy, 1, true)} · המניה <b class="${ex >= 0 ? 'up' : 'down'}">${ex >= 0 ? 'הקדימה' : 'פיגרה אחרי'} את המדד ב־${pct(Math.abs(ex), 1)}</b>`]);
      }
    } else if (isNum(p0)) lines.push(['מחיר', `${price(p0)} כששמרת`]);
    if (sn && r && !sameDay) {
      lines.push(['פוטנציאל', arrow(sn.upside, r.upside, (x) => `<span class="num">${Math.round(x)}</span>`, 1)]);
      lines.push(['סיכון', arrow(sn.risk, r.risk, (x) => `<span class="num">${Math.round(x)}</span> ${riskLevel(x)[1]}`, -1)]);
      if (now.i >= 0) {
        const was = statusText(sn), is = statusText(r);
        lines.push(['מצב', was === is ? `${esc(is)} – גם כששמרת וגם היום` : `כששמרת: ${esc(was)} · היום: <b>${esc(is)}</b>`]);
      }
      if (isNum(sn.tg_n) || isNum(r.tg_n)) lines.push(['טלגרם', `${N(sn.tg_n || 0)} אזכורים בשבוע ששמרת · ${N(r.tg_n || 0)} בשבוע האחרון`]);
    } else if (sn) {
      lines.push(['פוטנציאל', `<span class="num">${Math.round(sn.upside)}</span>`]);
      lines.push(['סיכון', `<span class="num">${Math.round(sn.risk)}</span> ${riskLevel(sn.risk)[1]}`]);
      lines.push(['מצב', esc(statusText(sn))]);
    }
    // the plan suggested at the star: where the price is now against it
    if (sn && (isNum(sn.stop) || isNum(sn.target))) {
      const closes = (r && now.i >= 0 && r.closes) || [], dates = (now && now.dates) || [];
      const after = dates.map((dt, k) => [dt, closes[k]]).filter(([dt, c]) => isNum(c) && sn.date && dt > sn.date);
      const hitStop = isNum(sn.stop) ? after.find(([, c]) => c <= sn.stop) : null;
      const hitTgt = isNum(sn.target) ? after.find(([, c]) => c >= sn.target) : null;
      const parts = [];
      if (isNum(sn.stop)) {
        parts.push(hitStop ? `סטופ ${price(sn.stop)}: <b class="down">נסגרה מתחתיו ב־${dm(hitStop[0])}</b>`
          : r && !sameDay ? `סטופ ${price(sn.stop)}: המחיר ${pct(r.price / sn.stop - 1, 0)} מעליו` : `סטופ ${price(sn.stop)}`);
      }
      if (isNum(sn.target)) {
        parts.push(hitTgt ? `יעד ${price(sn.target)}: <b class="up">הגיעה אליו ב־${dm(hitTgt[0])}</b>`
          : r && !sameDay ? `יעד ${price(sn.target)}: עוד ${pct(sn.target / r.price - 1, 0, true)}` : `יעד ${price(sn.target)}`);
      }
      lines.push(['התוכנית כששמרת', parts.join(' · ')]);
    }
    if (sn && sn.earnings_date && sn.earnings_date >= String(now ? now.date : sn.date)) lines.push(['דוח רבעוני', dmy(sn.earnings_date)]);
    const head = `${sn && sn.date ? `תמונת מצב מהסריקה של ${dm(sn.date)}` : `נשמרה ב־${dm(s.at)}`}${s.at ? ` · שמרת ${ago(s.at + 'T12:00:00Z') === 'עכשיו' ? 'היום' : ago(s.at + 'T12:00:00Z')}` : ''}`;
    const nowLine = !now ? '<span class="chip warn">אין עדיין ניתוח חדש – הסריקה הבאה תנתח אותה</span>'
      : sameDay ? '<span class="chip">נשמרה מהסריקה האחרונה – ההשוואה תתחיל מהסריקה הבאה</span>'
        : now.info ? `<span class="chip">עכשיו = הניתוח מ־${dm(now.date)}</span>` : `<span class="chip">עכשיו = הסריקה של ${dm(now.date)}</span>`;
    const open = now ? (now.i >= 0 ? `data-open="${now.i}"` : `data-act="open-saved" data-t="${esc(s.t)}"`) : '';
    return `<div class="card saved-item">
      <div class="saved-top"><div class="grow"><span class="tk">${esc(s.t)}</span>${name ? ` <span class="nm"><bdi>${esc(name)}</bdi></span>` : ''}
        <div class="muted small">${head}</div></div>
        <button class="icon-btn small x-btn" data-act="unsave-ask" data-t="${esc(s.t)}" aria-label="מחק את ${esc(s.t)} מהשמורות">✕</button></div>
      <div class="chips" style="margin:6px 0 4px">${nowLine}</div>
      <div class="snap-lines">${lines.map(([k, v]) => `<div class="snap-line"><span class="k">${esc(k)}:</span> ${v}</div>`).join('')}</div>
      ${sn && (sn.pros || []).length ? `<details class="snap-why"><summary>למה הרדאר אהב אותה כששמרת</summary><ul class="list pros">${sn.pros.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
        ${(sn.cons || []).length ? `<ul class="list cons">${sn.cons.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}</details>` : ''}
      <div class="s-actions" style="justify-content:flex-start;margin-top:8px">
        ${open ? `<button class="btn small ghost" ${open}>המצב המלא עכשיו</button>` : ''}
        ${!now && cloudReady() ? `<button class="btn small ghost" data-run="ticker" data-sym="${esc(s.t)}">נתח עכשיו</button>` : ''}</div>
    </div>`;
  }
  function savedSection() {
    const v = savedGet();
    const sync = !cloudReady() ? 'שמורות בטלפון הזה. כדי שהסריקה היומית תנתח אותן כל בוקר – חיבור לענן בהגדרות.'
      : v.dirty ? 'השינוי נשלח לענן. מהסריקה הבאה הסריקה היומית תנתח אותן.' : 'הסריקה היומית מנתחת אותן כל בוקר, גם כשהן לא נבחרות.';
    return `<p class="muted small" style="margin:-6px 0 0">בכל מניה: איך הרדאר ראה אותה ברגע ששמרת, ואיך הוא רואה אותה עכשיו. ${sync}</p>
      ${v.list.map(savedCard).join('')}`;
  }
  // ---------------------------------------------------------------- Telegram: connection strip, screen, manual pull
  async function loadTg() {
    state.tg = null;   // loading
    const j = await getJSON(state.demo ? 'demo/telegram.json' : 'telegram.json');
    state.tg = j || false;
    if (state.sheet && state.sheet.kind === 'telegram') renderSheet(true);
    else if (!state.sheet) render();
  }
  // the newest of: telegram.json (with the log) and the summary inside the scan
  function tgInfo() {
    const d = state.data || {};
    const f = state.tg || null, s = d.telegram || null;
    if (!f && !s) return null;
    const fAt = f && f.updated, sAt = s && (s.read_at || d.generated);
    const cur = f && (!sAt || (fAt && fAt >= sAt))
      ? { at: fAt, mode: f.mode, per: f.per, posts: f.posts, stats: f.stats, week: f.week, channels: f.channels, reach: f.reach }
      : { at: sAt, mode: s.mode || 'scan', per: s.per, posts: s.posts, stats: s.stats, week: s.stocks, channels: s.channels, reach: s.reach };
    cur.channels = cur.channels || Object.keys(cur.per || (s && s.labels) || {});
    cur.reads = f ? f.reads : null;
    cur.history = f ? f.history : null;
    return cur;
  }
  function tgState(i) {
    if (!i) return ['', 'עוד לא נקרא', 'הקריאה הראשונה תהיה בסריקה הבאה'];
    const failed = i.channels.filter((c) => tgFailed(i, c));
    if (!i.posts && failed.length) return ['bad', 'לא מחובר', 'בקריאה האחרונה לא התקבלה אף הודעה'];
    if (failed.length) return ['warn', 'מחובר חלקית', `לא הצלחתי לקרוא את: ${failed.map((c) => tgLabel(c)).join(', ')}`];
    if (i.at && daysSince(i.at) > 3.5) return ['warn', 'לא עודכן', `הקריאה האחרונה ${ago(i.at)}`];
    return ['good', 'מחובר', ''];
  }
  // a channel failed when it did not answer; one that answered but posted nothing this week is fine
  const tgFailed = (i, c) => (i.reach && i.reach[c] ? !i.reach[c].ok : !!(i.per && !i.per[c]));
  const tgRunning = () => !!(state.job && state.job.mode === 'telegram');
  function tgStrip() {
    if (state.tg === undefined) loadTg();
    const i = tgInfo();
    if (!i && !tgRunning()) return '';
    const [tone, head] = tgState(i);
    const line = tgRunning() ? 'משיכה ידנית רצה עכשיו בענן'
      : `נקרא ${ago(i.at)} · ${N(i.channels.length)} ערוצים · ${N(i.posts || 0)} הודעות · ${N((i.week || []).length)} מניות השבוע`;
    return `<button class="tg-strip ${tone}" data-act="open-tgs" aria-label="טלגרם: ${esc(head)}. לחץ לפרטים">
      <span class="dot${tgRunning() ? ' pulse' : ''}"></span>
      <span class="grow"><b>טלגרם: ${esc(head)}</b><span>${line}</span></span><span class="chev" aria-hidden="true">‹</span></button>`;
  }
  function openTelegram() {
    const sh = { kind: 'telegram' };
    if (state.sheet) { state.sheet = sh; renderSheet(); pushSheet(true); } else { state.sheet = sh; renderSheet(); pushSheet(); }
    loadTg();
  }
  const hm = (iso) => { const t = new Date(iso); return isFinite(t) ? `${String(t.getDate()).padStart(2, '0')}/${String(t.getMonth() + 1).padStart(2, '0')} ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}` : ''; };
  const tgMode = (m) => (m === 'manual' ? 'משיכה ידנית' : 'סריקה יומית');
  function tgStockRows(list, hist) {
    const res = (state.data && state.data.results) || [];
    return list.map((x) => {
      const top = (x.tg || [])[0] || {};
      const idx = res.findIndex((r) => r.ticker === x.ticker);
      const pick = idx >= 0 && res[idx].pick;
      const tag = pick ? '<span class="chip good">בחירה של היום</span>' : idx >= 0 ? '<span class="chip">נסרקה היום</span>' : '<span class="chip">לא ברדאר היום</span>';
      const when = hist
        ? `${N(x.n)} ${x.n === 1 ? 'אזכור' : 'אזכורים'} · ${x.first && dm(x.first) !== dm(x.last) ? `${dm(x.first)} עד ${dm(x.last)}` : dm(x.last)} · ${esc(Object.keys(x.channels || {}).map((c) => tgLabel(c)).join(', '))}`
        : `${N(x.n)} ${x.n === 1 ? 'אזכור' : 'אזכורים'} · האחרון ב${esc(tgLabel(top.c))} ${whenText(top.d)}`;
      const inner = `<span class="grow"><span class="tk">${esc(x.ticker)}</span>${x.name && x.name !== x.ticker ? ` <span class="nm"><bdi>${esc(x.name)}</bdi></span>` : ''}
          <span class="nm wrap">${when}</span>
          <span class="nm wrap" style="color:var(--ink)">${esc(top.t || '')}</span></span>${tag}`;
      return idx >= 0 ? `<div class="row" data-open="${idx}" role="button" tabindex="0">${inner}</div>`
        : `<a class="row" href="${esc(top.u || '#')}" target="_blank" rel="noopener">${inner}</a>`;
    }).join('');
  }
  function viewTelegram() {
    const i = tgInfo();
    const [tone, head, sub] = tgState(i);
    const repo = ghRepo();
    const wf = repo ? `https://github.com/${repo.owner}/${repo.repo}/actions/workflows/radar.yml` : '';
    let pull;
    if (tgRunning()) {
      pull = banner('info', 'המשיכה רצה עכשיו בענן', `התחילה ${ago(new Date(state.job.since).toISOString())}. בדרך כלל 3 עד 5 דקות (אם סריקה רצה באותו זמן – אחריה). המסך יתעדכן לבד.`);
    } else if (state.job) {
      pull = '<p class="muted small" style="margin:0">כבר רצה פעולה אחרת בענן. אפשר למשוך מהטלגרם כשהיא תסתיים.</p>';
    } else if (cloudReady()) {
      pull = `<button class="btn" data-run="telegram">${ICON.refresh.replace('<svg', '<svg width="18" height="18"')} משוך עכשיו מהטלגרם</button>
        <p class="muted small" style="margin:0">קורא עכשיו את כל הערוצים, בלי סריקה מלאה. לוקח 3 עד 5 דקות.</p>`;
    } else {
      pull = `<button class="btn" data-act="tg-howto">${ICON.refresh.replace('<svg', '<svg width="18" height="18"')} משוך עכשיו מהטלגרם</button>
        ${state.sheet && state.sheet.howto ? `<div class="banner warn"><span class="dot"></span><div class="grow"><b>האפליקציה עוד לא מחוברת ל־GitHub</b>
          <span>חיבור חד־פעמי בהגדרות ("חיבור לענן") – ואז הכפתור הזה מושך מיד. בינתיים אפשר למשוך דרך GitHub:</span>
          <ol class="steps" style="margin:6px 0 0">
            <li>פתח: <a href="${esc(wf)}" target="_blank" rel="noopener" class="mono" style="word-break:break-all">${esc(wf)}</a></li>
            <li>לחץ <span class="kbd">Run workflow</span> (הפעלת זרימת עבודה).</li>
            <li>בשדה <span class="kbd">mode</span> מחק את <span class="kbd">scan</span> וכתוב <span class="kbd">telegram</span>.</li>
            <li>לחץ על הכפתור הירוק <span class="kbd">Run workflow</span>.</li></ol>
          <div style="margin-top:8px"><button class="btn small ghost" data-act="settings">לחיבור לענן</button></div></div></div>` : ''}`;
    }
    const st = (i && i.stats) || {};
    const chanRows = i ? i.channels.map((c) => {
      const s = st[c] || {};
      const n = s.n != null ? s.n : ((i.per || {})[c] || 0);
      const failed = tgFailed(i, c);
      const newest = i.reach && i.reach[c] && i.reach[c].newest;
      return `<a class="row" href="https://t.me/s/${encodeURIComponent(c)}" target="_blank" rel="noopener">
        <span class="sdot ${n ? 'good' : failed ? 'bad' : ''}"></span><span class="grow">
        <span class="tk" style="font-family:inherit">${esc(tgLabel(c))}</span>
        <span class="nm wrap">${n ? `${N(n)} הודעות נקראו${s.from ? ` · מ־${dm(s.from)} עד ${dm(s.to)}` : ''}`
          : failed ? 'לא הצלחתי לקרוא את הערוץ בקריאה האחרונה'
            : `הערוץ נקרא, אבל לא פרסם השבוע${newest ? ` (ההודעה האחרונה מ־${dmy(newest)})` : ''}`}</span>
        ${s.last_t ? `<span class="nm wrap">האחרונה שנקראה (${hm(s.to)}): <span style="color:var(--ink)">${esc(s.last_t)}</span></span>` : ''}</span></a>`;
    }).join('') : '';
    const week = (i && i.week) || [];
    const h = i && i.history;
    const reads = (i && i.reads) || [];
    const loading = state.tg === null;
    return `<div class="sheet-bar"><div class="topbar-in"><button class="icon-btn" data-act="close" aria-label="חזרה">${ICON.back}</button>
        <div class="brand"><h1>טלגרם</h1></div></div></div>
      <div class="sheet-in">
        <div class="card stack" style="gap:10px">
          <div class="tg-head ${tone}"><span class="dot"></span><div class="grow"><b>${esc(head)}</b>
            <span class="muted small">${i ? `קריאה אחרונה: ${hm(i.at)} (${ago(i.at)}) · ${tgMode(i.mode)}` : esc(sub)}</span>
            ${sub && i ? `<span class="small" style="display:block;color:var(--warn)">${esc(sub)}</span>` : ''}</div></div>
          ${pull}
        </div>
        ${i ? `<div class="card"><h3>הערוצים</h3>
          <p class="muted small" style="margin:2px 0 8px">ערוצים ציבוריים – נקראים בלי חשבון טלגרם. בכל קריאה נקראות ההודעות של השבוע האחרון. לחיצה על ערוץ פותחת אותו בטלגרם.</p>
          <div class="rows">${chanRows}</div></div>
        <div class="card"><h3>השבוע: מניות שהוזכרו (${N(week.length)})</h3>
          ${week.length ? `<p class="muted small" style="margin:2px 0 8px">מניה שנסרקה היום – לחיצה פותחת אותה. אחרת – לחיצה פותחת את ההודעה בטלגרם.</p><div class="rows">${tgStockRows(week)}</div>`
            : '<p class="muted small">אף מניה אמריקאית לא הוזכרה השבוע בערוצים.</p>'}</div>` : ''}
        <div class="card"><h3>עד היום: כל מה שנלקח</h3>
          ${h ? `<p class="muted small" style="margin:2px 0 8px">מאז ${dmy(h.since)}: ${N(h.n_mentions)} אזכורים של ${N(h.n_stocks)} מניות, ב־${N(h.reads)} קריאות של הערוצים. הודעה שנקראה שוב לא נספרת פעמיים.</p>
            ${(h.stocks || []).length ? `<div class="rows">${tgStockRows(h.stocks.slice(0, state.sheet && state.sheet.allTg ? 200 : 15), true)}</div>
              ${h.stocks.length > 15 && !(state.sheet && state.sheet.allTg) ? `<button class="btn small ghost" style="margin-top:8px" data-act="tg-all">הצג את כל ${N(h.stocks.length)} המניות</button>` : ''}` : ''}`
            : `<p class="muted small">${loading ? 'טוען…' : 'היומן מתחיל להיאסף מהקריאה הבאה (הסריקה היומית או משיכה ידנית).'}</p>`}</div>
        ${reads.length ? `<div class="card"><h3>קריאות אחרונות</h3><div class="rows">${reads.slice(0, 12).map((r) => `<div class="row" style="cursor:default"><span class="grow">
            <span class="nm wrap" style="color:var(--ink)">${hm(r.ts)} · ${tgMode(r.mode)} · ${N(r.posts)} הודעות</span>
            <span class="nm wrap">${Object.keys(r.per || {}).map((c) => `${esc(tgLabel(c))} ${N(r.per[c])}`).join(' · ')}</span></span>
            <span class="sdot ${r.posts ? 'good' : 'bad'}"></span></div>`).join('')}</div></div>` : ''}
        <p class="muted small">האזכורים מוצגים ליד המניות ולא משפיעים על הציון. הביקורת העצמית בודקת אם מניות שהוזכרו הצליחו יותר או פחות.</p>
      </div>`;
  }
  function simpleCard(r, i, scanDate) {
    const [rc, rl] = riskLevel(r.risk);
    const pl = r.plan || {};
    const pros = (r.pros || []).slice(0, 3);
    return `<article class="card scard" role="button" tabindex="0" data-open="${i}" aria-label="${esc(r.ticker)} ${esc(r.name)}">
      <div class="s-head"><span class="tk">${esc(r.ticker)}</span><span class="nm">${esc(r.name)}</span>${saveBtn(r)}</div>
      <div class="s-nums">
        <div class="s-num"><span class="eyebrow">פוטנציאל</span><b class="num">${Math.round(r.upside)}</b><span class="muted small">מתוך 100</span></div>
        <div class="s-num ${rc}"><span class="eyebrow">סיכון</span><b>${rl}</b><span class="muted small">${Math.round(r.risk)} מתוך 100</span></div>
        <div class="s-num"><span class="eyebrow">סטופ</span><b class="num">${price(pl.stop)}</b><span class="muted small">${isNum(pl.stop_pct) ? `${pct(-pl.stop_pct, 0)} מתחת למחיר` : ''}</span></div>
      </div>
      ${pros.length ? `<ul class="pick-reasons">${pros.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
      <div class="chips">${checkChip(r, scanDate)}${r.deal ? `<span class="chip warn">${esc(r.deal.short)}</span>` : ''}${tgChip(r)}</div>
    </article>`;
  }
  function viewTodaySimple(d) {
    const res = d.results || [];
    const picks = res.map((r, i) => [r, i]).filter(([r]) => r.pick);
    return `<div class="stack">
      ${statusBanners()}${whatsNew()}${tgStrip()}${scoreboard(d)}
      <div class="section-title"><h2>המניות של היום</h2><span class="muted small">${dmy(d.date)}</span></div>
      <p class="muted small" style="margin:-6px 0 0">הרדאר מראה אצל אילו מניות יש היום הכי הרבה סימנים לעלייה – לא האם הן יעלו. כל מניה כאן כבר עברה את הסינון. מסתכלים על ארבעה דברים: פוטנציאל, סיכון, הסיבות והסטופ. בלי סטופ אין תוכנית.</p>
      ${picks.length ? picks.map(([r, i]) => simpleCard(r, i, d.date)).join('') : '<div class="card empty">אף מניה לא עברה היום את הסינון. זה תקין – לפעמים הדבר הנכון הוא לחכות.</div>'}
      <div class="s-actions"><button class="btn small ghost" data-act="view-full">תצוגה מלאה</button><button class="btn small ghost" data-act="guide">איך קוראים</button></div>
    </div>`;
  }
  function viewDetailSimple() {
    const { r, ctx } = state.sheet;
    const [rc, rl] = riskLevel(r.risk);
    const pl = r.plan || {};
    const pros = r.pros || [];
    const warn = (r.cons || []).slice(0, 3);
    const facts = [['מחיר אחרון', price(r.price)], pl.stop ? ['סטופ', `${price(pl.stop)} (${pct(pl.stop_pct)})`] : null,
      pl.target ? ['יעד', `${price(pl.target)} (${pct(pl.target_pct, 0, true)})`] : null,
      ['יציאה לכל המאוחר', `אחרי ${pl.days || 63} ימי מסחר`]].filter(Boolean);
    return `<div class="sheet-bar"><div class="topbar-in">
        <button class="icon-btn" data-act="close" aria-label="חזרה">${ICON.back}</button>
        <div class="brand"><h1 class="tk" style="font:600 19px/1 var(--font-data)">${esc(r.ticker)}</h1></div>${saveBtn(r, true)}</div></div>
      <div class="sheet-in">
        ${ctx.demo ? banner('info', 'מניה בדויה (דמו)', 'כל הנתונים כאן לדוגמה בלבד.') : ''}
        ${r.deal ? banner(r.exclude ? 'bad' : 'warn', r.exclude ? `${r.deal.short}: לא נבחרת` : r.deal.short, dealBody(r.deal)) : ''}
        ${r.lesson ? banner('warn', 'לא נבחרה בגלל לקח שלמדתי', `${r.lesson.text}: בבדיקה העצמית מצאתי שמניות כאלה מפסידות אצלי.`) : ''}
        <div class="d-head"><div class="grow"><div class="nm">${esc(r.name)}</div>
          <div class="muted small">${esc([r.sector, r.industry].filter(Boolean).join(' · '))}</div>
          <div class="price-line"><span class="px">${price(r.price)}</span></div></div></div>
        <div class="card"><h3>ארבעת הדברים</h3>
          <div class="s-item"><div class="s-k"><span>פוטנציאל</span><b class="num">${Math.round(r.upside)}</b></div>
            <p>כמה סימנים מצביעים לעלייה ומסכימים ביניהם. 50 = ניטרלי. זה לא אחוז סיכוי.</p></div>
          <div class="s-item"><div class="s-k"><span>סיכון</span><b class="${rc === 'bad' ? 'down' : rc === 'good' ? 'up' : ''}">${rl} (${Math.round(r.risk)})</b></div>
            <p>כמה המניה קופצת ביום רגיל, ועד כמה יש אירועים קרובים כמו דוח רבעוני. סיכון גבוה אומר שגם הירידות יהיו חדות.</p></div>
          <div class="s-item"><div class="s-k"><span>למה נבחרה</span></div>
            ${pros.length ? `<ul class="list pros">${pros.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p>אין סיבות מפורטות.</p>'}
            <p class="muted small">סיבה שלא ברורה – שווה לשאול עליה ספציפית.</p></div>
          <div class="s-item"><div class="s-k"><span>סטופ</span><b class="num">${price(pl.stop)}</b></div>
            <p>המחיר שבו מודים שהרעיון לא עבד ויוצאים${isNum(pl.stop_pct) ? ` (${pct(-pl.stop_pct, 0)} מתחת למחיר)` : ''}. אם אפשר, שמים אותו כפקודה אצל הברוקר מיד אחרי הקנייה. אם לא – בודקים פעם ביום אחרי הסגירה.</p></div>
        </div>
        ${warn.length ? `<div class="card"><h3>שים לב</h3><ul class="list cons">${warn.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
        ${tgCard(r)}
        ${checklistCard(r, ctx)}
        <div class="card"><h3>התוכנית</h3><p class="small" style="margin:2px 0 10px">${esc(planText(pl))}</p>
          <div class="facts">${facts.map(([k, v]) => `<div class="fact"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('')}</div>
          <p class="muted small" style="margin:10px 0 0">רושמים לעצמך: תאריך, מחיר קנייה וסטופ. אם המחיר יורד לסטופ – יוצאים, בלי להזיז אותו.</p></div>
        <button class="btn ghost" data-act="detail-full">כל הפרטים והגרפים</button>
        <p class="muted small">נותח ב־${dmy(ctx.date)} · כלי עזר למחקר, לא ייעוץ השקעות.</p>
      </div>`;
  }
  function viewResults() {
    const d = state.data;
    if (!d) return state.loading ? loader() : '<div class="card empty">אין עדיין נתונים.</div>';
    if (state.review === undefined) loadReview();
    const rv = state.review;
    const concl = rv ? (rv.conclusions || []).slice(0, 5) : [];
    const p = d.paper || {};
    const closed = (p.closed_positions || []).slice(0, 6);
    const why = { stop: 'בסטופ', gap: 'בפער מתחת לסטופ', target: 'ביעד', time: 'בסוף הזמן' };
    return `<div class="stack">
      ${scoreboard(d)}
      <div class="card"><h3>מה המערכת למדה</h3>
        ${concl.length ? `<ul class="concl">${concl.map((x) => `<li class="${esc(x.tone)}">${esc(x.text)}</li>`).join('')}</ul>` : `<p class="muted">${rv === undefined ? 'טוען…' : 'עוד אין מסקנות.'}</p>`}
        <button class="btn small ghost" style="margin-top:10px" data-act="open-learn">כל הפרטים</button></div>
      ${tgStrip()}
      <div class="card"><h3>תיק הנייר</h3>
        <p class="muted small" style="margin:2px 0 8px">המערכת "קונה" את הבחירות שלה בלי כסף, עם סטופ, ועוקבת. ${N(p.open || 0)} פתוחות · ${N(p.closed || 0)} נסגרו.</p>
        ${closed.length ? `<div class="rows">${closed.map((x) => `<div class="row" style="cursor:default"><span class="grow"><span class="tk">${esc(x.ticker)}</span>
          <span class="nm">${dm(x.since)} עד ${dm(x.until)} · ${why[x.reason] || ''}</span></span><span class="chip ${x.ret >= 0 ? 'good' : 'bad'}"><b>${pct(x.ret, 1, true)}</b></span></div>`).join('')}</div>` : '<p class="muted small">עוד לא נסגרה אף עסקה.</p>'}
      </div>
      <div class="s-actions"><button class="btn small ghost" data-act="view-full">תצוגה מלאה</button></div>
    </div>`;
  }

  // ------------------------------------------------------------------ views: market
  function viewMarket() {
    const d = state.data;
    if (!d) return state.loading ? loader() : '<div class="card empty">אין עדיין נתוני שוק.</div>';
    const m = d.market || {};
    const tone = m.score > 0.25 ? 'up' : m.score < -0.25 ? 'down' : '';
    const themes = d.themes || [];
    const themeRows = themes.map((t) => {
      const w = Math.min(100, (t.ratio / 2.5) * 100);
      const col = t.ratio >= 1.25 ? 'var(--warn)' : t.ratio <= 0.8 ? 'var(--accent)' : 'var(--muted)';
      const word = t.ratio >= 1.25 ? 'עלייה' : t.ratio <= 0.8 ? 'רגיעה' : 'יציב';
      const eff = (t.sectors || []).map((s) => ({ g: s.group, e: t.escalation * s.tilt })).filter((x) => Math.abs(x.e) >= 0.1)
        .sort((a, b) => Math.abs(b.e) - Math.abs(a.e)).slice(0, 4);
      return `<div style="display:flex;flex-direction:column;gap:6px;padding:10px 0;border-top:1px solid var(--line)">
        <div style="display:flex;justify-content:space-between;gap:8px"><b>${esc(t.he)}</b><span class="small" style="color:${col};font-weight:700">${word} · פי ${t.ratio.toFixed(1)}</span></div>
        <div class="track" style="position:relative" role="img" aria-label="פי ${t.ratio.toFixed(1)} מהרגיל"><i style="width:${w}%;background:${col}"></i>
          <span style="position:absolute;top:-3px;bottom:-3px;inset-inline-start:40%;width:2px;background:var(--ink-2)"></span></div>
        ${eff.length ? `<div class="chips">${eff.map((x) => `<span class="chip ${x.e > 0 ? 'good' : 'bad'}">${GROUP_HE[x.g] || x.g} ${x.e > 0 ? '↑' : '↓'}</span>`).join('')}</div>` : ''}
      </div>`;
    }).join('');
    return `<div class="stack">
      ${state.demo ? banner('info', 'נתוני דמו', 'המספרים במסך הזה לדוגמה בלבד.') : ''}
      <div class="card"><div class="eyebrow">מצב השוק</div><h2 class="${tone}" style="font:26px/1.2 var(--font-display);margin:4px 0 6px">${esc(m.label || '–')}</h2>
        <ul class="list">${(m.notes || []).map((n) => `<li>${esc(n)}</li>`).join('')}</ul>
        ${m.risk_off ? '<p class="small" style="color:var(--bad);margin:8px 0 0">במצב בריחה מסיכון המערכת בוחרת פחות מניות ומחמירה בסף.</p>' : ''}</div>
      <div class="card"><h3>מלחמות ומתיחות</h3>
        <p class="muted small" style="margin:2px 0 4px">היקף הסיקור העולמי בשבוע האחרון לעומת החודשיים שלפניו. הקו האנכי = רמה רגילה. החיצים מראים לאילו ענפים זה בדרך כלל טוב (↑) או רע (↓).</p>
        ${themeRows || '<div class="empty">אין נתונים כרגע</div>'}</div>
      ${(d.countries || []).length ? `<div class="card"><h3>מדינות</h3><p class="muted small" style="margin:2px 0 6px">לחברות זרות שנסחרות בארה"ב ונבדקו היום</p>
        <ul class="list">${d.countries.map((c) => `<li>${esc(c.text[0])}${c.text[1] ? `<br><span style="color:var(--warn)">${esc(c.text[1])}</span>` : ''}</li>`).join('')}</ul></div>` : ''}
      ${tgStrip()}
      <p class="muted small">מקורות: מאקרו – FRED · מלחמות ומתיחות – GDELT · מדינות – IMF והבנק העולמי${d.telegram ? ' · אזכורים בישראל – ערוצי טלגרם ציבוריים' : ''}.</p>
    </div>`;
  }

  // ------------------------------------------------------------------ views: performance
  function viewPerf() {
    const d = state.data;
    if (!d) return state.loading ? loader() : '<div class="card empty">אין עדיין נתונים.</div>';
    const p = d.paper || { n: 0 };
    const lr = d.learning || {};
    let paper;
    if (!p.n) {
      paper = '<div class="card empty"><h3>תיק מסחר על נייר</h3><p>המערכת "קונה" כל יום את המניות שבחרה ועוקבת אחריהן עם סטופ. התוצאות יופיעו כאן אחרי כמה ימי סריקה.</p></div>';
    } else {
      const tot = p.return_on_invested;
      const openRows = (p.open_positions || []).map((x) => `<div class="row" style="cursor:default"><span class="grow"><span class="tk">${esc(x.ticker)}</span>
          <span class="nm">${x.status === 'pending' ? 'תיפתח במחיר הפתיחה של יום המסחר הבא' : `כניסה ${price(x.entry)} · עכשיו ${price(x.last)} · סטופ ${price(x.stop)}${isNum(x.target) ? ` · יעד ${price(x.target)}` : ''}`}</span></span>
          ${x.status === 'pending' ? '<span class="chip">ממתינה</span>' : `<span class="chip ${x.ret >= 0 ? 'good' : 'bad'}"><b>${pct(x.ret, 1, true)}</b></span>`}</div>`).join('');
      const closedRows = (p.closed_positions || []).slice(0, 8).map((x) => `<div class="row" style="cursor:default"><span class="grow"><span class="tk">${esc(x.ticker)}</span>
          <span class="nm">${dm(x.since)} עד ${dm(x.until)} · ${x.reason === 'stop' ? 'נסגרה בסטופ' : x.reason === 'gap' ? 'נפתחה בפער מתחת לסטופ' : x.reason === 'target' ? 'הגיעה ליעד' : 'נסגרה בסוף תקופת ההחזקה'}</span></span>
          <span class="chip ${x.ret >= 0 ? 'good' : 'bad'}"><b>${pct(x.ret, 1, true)}</b></span></div>`).join('');
      paper = `<div class="card"><h3>תיק מסחר על נייר</h3><p class="muted small" style="margin:2px 0 10px">מאז ${dmy(p.since)} · ${p.open} פתוחות · ${p.closed} נסגרו${p.pending ? ` · ${p.pending} ממתינות` : ''}</p>
        <div class="duo"><div><div class="eyebrow">התיק</div><span class="big ${tot >= 0 ? 'up' : 'down'}">${pct(tot, 1, true)}</span></div>
        <div><div class="eyebrow">S&P 500 באותה תקופה</div><span class="big" style="color:var(--ink-2)">${pct(p.spy_ret, 1, true)}</span></div></div>
        <div class="chips" style="margin-top:10px"><span class="chip">עסקאות מרוויחות <b>${pct(p.win_rate)}</b></span><span class="chip">רווח ממוצע <b>${pct(p.avg_win, 1, true)}</b></span><span class="chip">הפסד ממוצע <b>${pct(p.avg_loss, 1, true)}</b></span></div>
        ${openRows ? `<div class="eyebrow" style="margin-top:14px">פוזיציות פתוחות</div><div class="rows">${openRows}</div>` : ''}
        ${closedRows ? `<div class="eyebrow" style="margin-top:14px">נסגרו לאחרונה</div><div class="rows">${closedRows}</div>` : ''}</div>`;
    }
    let learn;
    if (!lr.n) {
      learn = '<div class="card"><h3>מה עובד?</h3><p class="muted">אחרי 30 יום המערכת בודקת כל המלצה מול מה שקרה בפועל, ומכיילת לבד את המשקל של כל אות. התוצאות יופיעו כאן.</p></div>';
    } else {
      const pk = lr.picks || {}, al = lr.all || {};
      learn = `<div class="card"><h3>מה עובד?</h3><p class="muted small" style="margin:2px 0 10px">${lr.n} המלצות נבדקו מול התוצאה ${lr.horizon || 30} יום אחרי. ביצועים עודפים = מעבר ל־S&P 500.</p>
        <div class="duo"><div><div class="eyebrow">המניות שנבחרו</div><span class="big sm ${pk.avg_excess >= 0 ? 'up' : 'down'}">${pct(pk.avg_excess, 1, true)}</span><span class="muted small">ניצחו את המדד ב־${pct(pk.hit_rate)} מהמקרים</span></div>
        <div><div class="eyebrow">כל המניות שנבדקו</div><span class="big sm" style="color:var(--ink-2)">${pct(al.avg_excess, 1, true)}</span><span class="muted small">לשם השוואה</span></div></div>
        <div class="chips" style="margin:10px 0"><span class="chip">עלו 30%+ בשלב כלשהו: <b>${pct(pk.explosive_rate)}</b> מהבחירות</span><span class="chip">לעומת <b>${pct(al.explosive_rate)}</b> מכל המניות</span></div>
        <div class="eyebrow" style="margin:6px 0 4px">כמה כל אות הקדים את התוצאה (מתאם)</div>
        ${divHead('הטעה', 'עזר')}
        ${(lr.signals || []).map((s) => divRow(s.label, Math.max(-1, Math.min(1, s.ic / 0.2)), (s.ic >= 0 ? '+' : '') + s.ic.toFixed(2), s.factor ? `משקל ×${s.factor.toFixed(2)}` : '')).join('')}
        <p class="muted small" style="margin:8px 0 0">אות שעזר מקבל משקל גבוה יותר בציון, אות שהטעה מקבל משקל נמוך יותר. הבדיקה המלאה, לפי תוכנית המסחר עצמה – בלשונית למידה.</p>
        <button class="btn small ghost" style="margin-top:8px" data-tab="learn">מה למדתי</button></div>`;
    }
    return `<div class="stack">${state.demo ? banner('info', 'נתוני דמו', 'הביצועים במסך הזה מחושבים על שוק מדומה ואינם אומרים דבר על השוק האמיתי.') : ''}${viewModel()}${paper}${learn}${viewBacktest()}</div>`;
  }

  // the model tournament: each candidate on period A (used to choose) and period B (the final, untouched check)
  function tournament(md) {
    const v = md.validation || {};
    const cands = v.candidates || [];
    if (!cands.length) return '';
    const all = (v.money && v.money.all) || {}, allA = (v.money_sel && v.money_sel.all) || {};
    const best = Math.max(0.005, ...cands.flatMap((c) => [Math.abs(c.sel.ret || 0), Math.abs(c.fin.ret || 0)]), Math.abs(all.ret || 0), Math.abs(allA.ret || 0));
    const row = (label, a, b, chosen) => `<div class="tour-row${chosen ? ' chosen' : ''}"><div class="tour-name">${esc(label)}${chosen ? ' <span class="chip good">נבחר</span>' : ''}</div>
      <div class="tour-vals"><span>תקופה א׳ <b class="${a > 0 ? 'up' : 'down'}">${pct(a, 1, true)}</b></span><span>בדיקה סופית <b class="${b > 0 ? 'up' : 'down'}">${pct(b, 1, true)}</b></span></div>
      <div class="tour-bar"><i style="width:${Math.min(100, Math.abs(b || 0) / best * 100).toFixed(0)}%;background:${(b || 0) >= (all.ret || 0) ? 'var(--good)' : 'var(--bad)'}"></i></div></div>`;
    return `<div class="tour"><div class="eyebrow" style="margin:0 0 6px">התחרות בין המודלים – עסקה ממוצעת של ${v.top_n || 10} המובילות</div>
      ${cands.map((c) => row(c.label, c.sel.ret, c.fin.ret, c.kind === v.kind)).join('')}
      ${row('כל המניות (לשם השוואה)', allA.ret, all.ret, false)}
      <p class="muted small" style="margin:6px 0 0">תקופה א׳ (${dmy(v.sel_start)}–${dmy(v.sel_end)}) שימשה רק לבחירת המודל. הבדיקה הסופית (${dmy(v.test_start)}–${dmy(v.test_end)}) לא שימשה לשום החלטה – לכן היא המדד האמיתי. עובר רק מודל שמנצח את כל המניות בשתיהן.</p></div>`;
  }

  // exit plans: the model's same top picks under each plan (period A chooses, the final check confirms)
  function exitPlans(md) {
    const v = md.validation || {};
    const ps = v.exit_plans || [];
    if (!ps.length) return '';
    const best = Math.max(0.002, ...ps.flatMap((x) => [Math.abs(x.sel_month || 0), Math.abs(x.fin_month || 0)]));
    const row = (x) => `<div class="tour-row${x.chosen ? ' chosen' : ''}"><div class="tour-name">${esc(x.he)}${x.chosen ? ' <span class="chip good">בשימוש</span>' : ''}</div>
      <div class="tour-vals"><span>תקופה א׳ <b class="${(x.sel_month || 0) > 0 ? 'up' : 'down'}">${pct(x.sel_month, 1, true)}</b></span><span>בדיקה סופית <b class="${(x.fin_month || 0) > 0 ? 'up' : 'down'}">${pct(x.fin_month, 1, true)}</b></span></div>
      <div class="tour-bar"><i style="width:${Math.min(100, Math.abs(x.fin_month || 0) / best * 100).toFixed(0)}%;background:${(x.fin_month || 0) > 0 ? 'var(--good)' : 'var(--bad)'}"></i></div>
      ${x.fin ? `<div class="muted small">בבדיקה הסופית: עסקה ממוצעת ${pct(x.fin.ret, 1, true)} · ברווח ${pct(x.fin.win)} · החזקה ${Math.round(x.fin.days || 0)} ימי מסחר</div>` : ''}</div>`;
    return `<div class="tour"><div class="eyebrow" style="margin:0 0 6px">תוכניות יציאה – רווח לחודש של כסף מושקע, על אותן מניות שהמודל בחר</div>
      ${ps.map(row).join('')}
      <p class="muted small" style="margin:6px 0 0">הבחירה נעשית בתקופה א׳ בלבד. תוכנית חדשה מחליפה את הקודמת רק אם ניצחה אותה גם בבדיקה הסופית, ב־0.2% לחודש לפחות. רווח לחודש = העסקה הממוצעת חלקי ימי ההחזקה, כי כסף שמשתחרר מוקדם עובד שוב.</p></div>`;
  }

  function viewModel() {
    if (state.model === undefined) { loadModel(); return `<div class="card"><h3>מודל הסיכוי</h3><p class="muted">טוען…</p></div>`; }
    const run = cloudReady() ? '<button class="btn small ghost" data-run="train">אמן מחדש</button>' : '';
    const md = state.model;
    if (!md) {
      return `<div class="card"><h3>מודל הסיכוי</h3><p class="muted">המודל לומד מ־5 שנות היסטוריה אמיתית אילו מניות הגיעו ליעד לפני הסטופ, ונבדק על שנים שלא ראה – גם בכסף. האימון מתחיל לבד אחרי עדכון האפליקציה ולוקח 15 עד 40 דקות. אחריו יופיע כאן איך הוא הצליח בבדיקה.</p>${run}</div>`;
    }
    const v = md.validation || {};
    const money = v.money || {};
    const mm = money.model || {}, ma = money.all || {}, mt = money.tech || {};
    const cov = md.coverage || {};
    const isR = md.target_mode ? md.target_mode !== 'pct' : false;
    const passed = typeof v.passed === 'boolean' ? v.passed : (isNum(v.auc) && v.auc > 0.52 && (v.top_lift || 0) >= 1.1);
    const outdated = state.data && state.data.model == null && !state.demo;
    const question = isR
      ? `השאלה: קונים במחיר הפתיחה של היום שאחרי. הסטופ במרחק ${md.stop_atr || 2.5} תנודות יומיות ממוצעות, והיעד פי ${md.target_r || 3} מהמרחק הזה (למשל סטופ 8% מתחת ויעד 24% מעל). האם המניה תגיע ליעד לפני הסטופ, תוך 3 חודשים? ככה מניה סוערת לא מקבלת יתרון רק בגלל שהיא זזה הרבה.`
      : `השאלה: מניה נקנית במחיר הפתיחה של היום שאחרי, עם סטופ לפי התנודה שלה. האם היא תעלה ${pct(md.target || 0.3)} תוך 3 חודשים, לפני שתרד לסטופ?`;
    // average trade result: model picks vs the old screen vs all stocks vs the S&P 500 over the same days
    const rows = [['10 המובילות של המודל בכל שבוע', mm.ret], ['הסינון הטכני הישן', mt.ret], ['כל המניות', ma.ret], ['S&P 500 באותם ימים', isNum(ma.spy) ? ma.spy : mm.spy]]
      .filter(([, x]) => isNum(x));
    const maxAbs = Math.max(0.01, ...rows.map(([, x]) => Math.abs(x)));
    const moneyRows = rows.map(([l, x]) => divRow(l, x / maxAbs, pct(x, 1, true))).join('');
    const byRank = (v.rank_deciles || []).length === 10;
    const dec = byRank ? v.rank_deciles : (v.deciles || []);
    const rets = dec.map((d) => (isNum(d.ret) ? d.ret : 0));
    const maxPos = Math.max(0, ...rets), maxNeg = Math.max(0, ...rets.map((x) => -x));
    const posShare = maxPos + maxNeg > 0 ? (100 * maxPos) / (maxPos + maxNeg) : 100;
    const lbl = (x) => LRM + (x > 0 ? '+' : '') + (x * 100).toFixed(1);
    const decBars = dec.map((d, i) => {
      const x = rets[i];
      const up = x > 0 && maxPos ? Math.max(2, (x / maxPos) * 82) : 0, dn = x < 0 && maxNeg ? Math.max(2, (-x / maxNeg) * 82) : 0;
      return `<div class="dec2-col" title="קבוצה ${i + 1}: עסקה ממוצעת ${pct(x, 1, true)}, הגיעו ליעד ${pct(d.rate)}">
        <div class="dec2-up" style="height:${posShare.toFixed(1)}%">${x > 0 ? `<span class="dec-v">${lbl(x)}</span><i style="height:${up.toFixed(0)}%"></i>` : ''}</div>
        <div class="dec2-dn" style="height:${(100 - posShare).toFixed(1)}%">${x < 0 ? `<i style="height:${dn.toFixed(0)}%"></i><span class="dec-v">${lbl(x)}</span>` : ''}</div></div>`;
    }).join('');
    const ww = md.what_worked || [];
    const good = ww.filter((x) => x.lift >= 1.1).slice(0, 6);
    const bad = ww.filter((x) => x.lift <= 0.9).slice(-4).reverse();
    const wwRow = (x) => divRow(x.text, Math.max(-1, Math.min(1, (x.lift - 1) / 0.75)), pct(x.rate, 1),
      `פי ${x.lift.toFixed(1)} מהממוצע${isNum(x.ret) ? ` · עסקה ממוצעת ${pct(x.ret, 1, true)}` : ''}`);
    const years = (v.by_year || []).filter((y) => isNum(y.top));
    const yearChip = (y) => isNum(y.top_ret)
      ? `<span class="chip ${y.top_ret > y.base_ret ? 'good' : 'bad'}">${y.year}: <b>${pct(y.top_ret, 1, true)}</b> מול ${pct(y.base_ret, 1, true)}</span>`
      : `<span class="chip ${y.top > y.base ? 'good' : 'bad'}">${y.year}: <b>${pct(y.top)}</b> מול ${pct(y.base)}</span>`;
    return `<div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><h3>מודל הסיכוי</h3>${run}</div>
      <p class="muted small" style="margin:2px 0 10px">אומן ב־${dmy(md.trained)}${md.demo ? ' · דמו' : ''} · ${md.n_tickers} מניות · ${md.years} שנים · ${Number(md.n_rows || 0).toLocaleString('en-US')} תצפיות</p>
      ${outdated ? banner('info', 'מודל חדש בדרך', 'המודל השמור עונה על השאלה הקודמת, ולכן הוא לא בשימוש עד שהאימון החדש יסתיים.') : ''}
      ${passed ? '' : banner('warn', 'המודל לא עבר את הבדיקה', v.candidates ? 'כדי לעבור, המניות שהמודל בוחר צריכות להרוויח יותר מכל המניות גם בתקופת הבחירה וגם בבדיקה הסופית. זה לא קרה, ולכן הוא לא משפיע כרגע על הציונים. הוא יאומן מחדש לבד בעוד חודש.' : 'בשנים שהוא לא ראה, העסקאות שהוא בחר לא היו טובות מהממוצע (בכסף או באחוז ההצלחה), ולכן הוא לא משפיע כרגע על הציונים. הוא יאומן מחדש לבד בעוד חודש.')}
      ${tournament(md)}
      ${exitPlans(md)}
      <p class="small" style="margin:0 0 8px">${question}</p>
      <p class="small" style="margin:0 0 10px">${v.candidates ? `הבדיקה הסופית: ${dmy(v.test_start)} עד ${dmy(v.test_end)} – תקופה שהמודל לא ראה ושלא שימשה גם לבחירה בין המודלים.` : `הבדיקה ההוגנת: המודל למד רק משנים מוקדמות, ונבדק על ${dmy(v.test_start)} עד ${dmy(v.test_end)} – תקופה שלא ראה.`} כל עסקה מדומה כמו שהיא הייתה קורה באמת: יעד, סטופ או יציאה אחרי 3 חודשים, כולל קפיצות במחיר, עמלות ומרווח קנייה־מכירה שגדל ככל שהמניה פחות נזילה.</p>
      ${rows.length ? `<div class="eyebrow" style="margin:4px 0 4px">עסקה ממוצעת לפי התוכנית${v.candidates ? ' – בדיקה סופית' : ''}</div>${divHead('הפסד', 'רווח')}${moneyRows}` : ''}
      <div class="chips" style="margin:10px 0">
        ${isNum(v.top_rate) ? `<span class="chip ${passed ? 'good' : ''}">הגיעו ליעד <b>${pct(v.top_rate)}</b> מול ${pct(v.base)} בממוצע</span>` : ''}
        ${isNum(mm.win) ? `<span class="chip">עסקאות ברווח <b>${pct(mm.win)}</b></span>` : ''}
        ${isNum(mm.stop) ? `<span class="chip">נעצרו בסטופ <b>${pct(mm.stop)}</b></span>` : ''}
        ${isNum(mm.days) ? `<span class="chip">החזקה ממוצעת <b>${Math.round(mm.days)}</b> ימי מסחר</span>` : ''}
        ${isNum(mm.ret_2x_cost) ? `<span class="chip ${mm.ret_2x_cost > 0 ? '' : 'bad'}">גם עם עלויות כפולות: <b>${pct(mm.ret_2x_cost, 1, true)}</b></span>` : ''}
        ${isNum(v.auc) ? `<span class="chip">AUC <b class="num">${v.auc.toFixed(2)}</b></span>` : ''}</div>
      ${years.length ? `<div class="eyebrow" style="margin:4px 0 4px">שנה אחרי שנה: ${isNum(years[0].top_ret) ? 'עסקה ממוצעת של המובילות מול כל המניות' : 'המובילות מול הממוצע'}</div><div class="chips">${years.map(yearChip).join('')}</div>` : ''}
      ${dec.length && dec.some((d) => isNum(d.ret)) ? `<div class="eyebrow" style="margin:14px 0 4px">10 קבוצות לפי ${byRank ? 'המקום בדירוג היומי של המודל' : 'הדירוג של המודל'} – עסקה ממוצעת בכל קבוצה (%)</div>
        <div class="dec2">${decBars}</div><div class="dec-axis"><span>דירוג נמוך</span><span>דירוג גבוה</span></div>
        <p class="muted small" style="margin:4px 0 0">אם המודל טוב, העמודות עולות מקבוצה לקבוצה. מניה שנופלת בקבוצה שהפסידה בממוצע לא נבחרת.</p>` : ''}
      ${good.length || bad.length ? `<div class="eyebrow" style="margin:14px 0 4px">מה עבד ב־5 השנים האחרונות (כל אחד לבד)</div>
        ${divHead('פחות מהממוצע', 'יותר מהממוצע')}${good.map(wwRow).join('')}${bad.map(wwRow).join('')}
        <p class="muted small" style="margin:6px 0 0">המספר = כמה מהמניות במצב הזה הגיעו ליעד לפני הסטופ.</p>` : ''}
      <p class="muted small" style="margin:10px 0 0">AUC מודד כמה טוב המודל מדרג: 0.5 = ניחוש, 0.6 ומעלה = כוח ניבוי שימושי בשוק ההון. הבדיקה כוללת רק מניות שנסחרות היום (מניות שנמחקו חסרות), ולכן קצת אופטימית.${cov.insider === false ? ' באימון הזה נתוני המנהלים ההיסטוריים לא היו זמינים.' : ''} עסקאות של שבועות סמוכים חופפות בזמן, ולכן זה ממוצע לעסקה ולא תשואה של תיק. המודל מתאמן מחדש לבד כל חודש.</p></div>`;
  }
  async function loadModel() {
    const md = await getJSON(state.demo ? 'demo/model.json' : 'model.json');
    state.model = md || null;
    if (state.tab === 'perf' || state.tab === 'today') render();
  }

  function viewBacktest() {
    if (state.backtest === undefined) { loadBacktest(); return `<div class="card"><h3>בדיקה לאחור</h3><p class="muted">טוען…</p></div>`; }
    const run = cloudReady() ? '<button class="btn small ghost" data-run="backtest">הרץ בדיקה לאחור</button>' : '';
    const bt = state.backtest;
    if (!bt) return `<div class="card"><h3>בדיקה לאחור</h3><p class="muted">עדיין לא הורצה בדיקה לאחור. היא בודקת איך אותות המחיר היו מצליחים ב־5 השנים האחרונות.</p>${run}</div>`;
    const m = bt.metrics || {};
    const eq = (m.eq || []).map((x) => x - 1), sp = (m.spy_eq || []).map((x) => x - 1);
    const good = m.ic_mean > 0.02 && (m.ic_t || 0) > 2;
    return `<div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><h3>בדיקה לאחור</h3>${run}</div>
      <p class="muted small" style="margin:2px 0 10px">${dmy(bt.date)}${bt.demo ? ' · דמו' : ''} · ${bt.n_tickers} מניות · ${(m.years || 0).toFixed(1)} שנים · ${m.trades} עסקאות</p>
      <div class="duo"><div><div class="eyebrow">תשואה שנתית</div><span class="big sm ${m.cagr >= m.spy_cagr ? 'up' : 'down'}">${pct(m.cagr, 1, true)}</span></div>
      <div><div class="eyebrow">S&P 500</div><span class="big sm" style="color:var(--ink-2)">${pct(m.spy_cagr, 1, true)}</span></div></div>
      <div class="legend" style="margin:10px 0 4px"><span><i></i>האסטרטגיה</span><span><i class="dash"></i>S&P 500</span></div>
      ${chartSlot('chart', { series: [{ v: eq, color: 'var(--accent)', name: 'אסטרטגיה' }, { v: sp, color: 'var(--muted)', name: 'S&P', dash: true, width: 1.6 }], height: 190, tip: true,
        fmtY: (t) => pct(t, 0, true), fmtTip: (v) => pct(v, 1, true), fmtX: (i) => (m.dates || [])[i] || '', label: 'עקומת הון של האסטרטגיה מול S&P 500' })}
      <div class="chips" style="margin-top:10px"><span class="chip">ירידה מקסימלית <b>${pct(m.max_dd)}</b></span><span class="chip">עלו 30%+ <b>${pct(m.explosive)}</b> לעומת ${pct(m.base_explosive)}</span><span class="chip">עסקאות ברווח <b>${pct(m.win_rate)}</b></span></div>
      <p class="small" style="margin:10px 0 0">${good ? 'לציון המחיר היה כוח ניבוי מובהק סטטיסטית בתקופה שנבדקה.' : 'כוח הניבוי של ציון המחיר לבדו היה חלש או לא מובהק – לכן המערכת משלבת אותו עם שאר האותות.'}</p>
      <p class="muted small" style="margin:6px 0 0">הבדיקה כוללת רק מניות שנסחרות היום ובודקת רק אותות מחיר, ולכן אופטימית מהמציאות.</p></div>`;
  }
  async function loadBacktest() {
    const bt = state.demo ? await getJSON('demo/backtest.json') : (await getJSON('backtest.json'));
    state.backtest = bt || null;
    if (state.tab === 'perf') render();
  }

  // ------------------------------------------------------------------ views: self-review (what it learned)
  const WHY_NOT = {
    upside: 'ציון הפוטנציאל נמוך מהסף', risk: 'ציון הסיכון גבוה מהסף', edge: 'ברמת הסיכוי שלה עסקאות הפסידו בבדיקה',
    deal: 'בתהליך רכישה או פשיטת רגל', rank: 'לא נכנסה לרשימת המובילות',
    band: 'לא בין המובילות של המודל היום', sector: 'כבר נבחרו 3 מניות מאותו ענף',
    filters: 'לא עוברת את מסנני הנזילות והמחיר (נבדקת כי שמרת אותה)', fund: 'קרן או תעודת סל, לא חברה',
  };
  const LESSON_ST = {
    adopted: ['good', 'אומץ'], adopted_live: ['good', 'אומץ לפי התוצאות בפועל'], pending: ['warn', 'ממתין לבדיקה בהיסטוריה'],
    rejected: ['', 'נדחה: לא החזיק בהיסטוריה'], watch: ['', 'תצפית'], revoked: ['', 'בוטל'], strength: ['good', 'כאן צדקתי'],
  };
  const VERDICT = { right: ['good', 'צדקתי'], partial: ['warn', 'חצי הצלחה'], wrong: ['bad', 'טעיתי'] };
  const CAUSE = {
    market: 'ירידה של השוק כולו', earnings: 'דוח רבעוני באמצע העסקה', reversal: 'היו ברווח והתהפכו',
    fast: 'נעצרו בסטופ תוך שבוע', entry: 'נקודת כניסה בעייתית', stock: 'המניה עצמה חלשה',
  };
  async function loadReview() {
    const rv = await getJSON(state.demo ? 'demo/review.json' : 'review.json');
    state.review = rv || null;
    if (state.tab === 'learn' || state.tab === 'results') render();
  }
  function reviewHow() {
    return `<div class="card"><h3>איך המערכת בודקת את עצמה</h3><ul class="list">
      <li>כל מניה שנבדקה לעומק נשמרת עם מה שחזיתי עליה: סטופ, יעד, הסיכוי שהראיתי, הסיבות, ולמה נבחרה או לא.</li>
      <li>אחרי 21 ימי מסחר (כחודש) כל המלצה נבדקת מול מה שקרה באמת, בהשוואה למניות שבדקתי ולא בחרתי באותו יום. כך ירידה של השוק כולו לא נחשבת טעות בבחירה.</li>
      <li>כל עסקה שנסגרת (יעד, סטופ או 3 חודשים) מקבלת ניתוח: מה אמרתי, מה קרה ולמה.</li>
      <li>אות שעוזר לבחור מקבל משקל גבוה יותר בציון, ואות שמטעה מקבל משקל נמוך יותר.</li>
      <li>דפוס הפסד שחוזר הופך ללקח רק אם גם 5 שנות היסטוריה מאשרות אותו. כך המערכת לא "לומדת" ממזל רע. לקח שמתברר כשגוי מבוטל לבד.</li>
      <li>הסיכוי שמוצג מתוקן לפי מה שקרה בפועל להמלצות ברמת סיכוי דומה.</li>
      <li>אם המודל מצליח בפועל פחות מבבדיקה, המשקל שלו יורד והוא מאומן מחדש מוקדם.</li></ul></div>`;
  }
  function pmItem(p) {
    const [tone, lbl] = VERDICT[p.verdict] || ['', ''];
    const list = (title, xs) => (xs || []).length ? `<span class="eyebrow">${title}</span><ul>${xs.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
    return `<div class="pm"><div class="pm-top"><span class="tk">${esc(p.ticker)}</span><span class="chip ${tone}">${lbl}</span><b class="${p.ret >= 0 ? 'up' : 'down'}">${pct(p.ret, 1, true)}</b></div>
      <div class="pm-head">${esc(p.head)}</div>
      <div class="muted small">${dm(p.entry_date)} עד ${dm(p.exit_date)}${isNum(p.spy) ? ` · S&P 500 באותם ימים ${pct(p.spy, 1, true)}` : ''}${isNum(p.prob) ? ` · הסיכוי שהראיתי ${pct(p.prob)}` : ''}</div>
      ${list('מה אמרתי', p.said)}${list(p.verdict === 'wrong' ? 'למה טעיתי' : 'הערה', p.causes)}</div>`;
  }
  function lessonItem(x) {
    const [tone, lbl] = LESSON_ST[x.status] || ['', x.status];
    const t = x.test || {};
    const hist = t.sel && t.fin && isNum(t.sel.delta) && isNum(t.fin.delta)
      ? `<p class="muted small" style="margin:4px 0 0">ב־5 שנות היסטוריה: דילוג עליהן היה משנה את העסקה הממוצעת של המובילות ב־${N(pct(t.sel.delta, 2, true))} בתקופה א׳ וב־${N(pct(t.fin.delta, 2, true))} בבדיקה הסופית.</p>` : '';
    return `<div class="lesson"><div class="lesson-top"><b>${esc(x.text)}</b><span class="chip ${tone}">${esc(lbl)}</span></div>
      <p class="small" style="margin:4px 0 0">אצלי בפועל, חודש אחרי: ${N(pct(x.with, 1, true))} לעומת ${N(pct(x.rest, 1, true))} בשאר המניות (${N(x.n)} מניות).</p>${hist}</div>`;
  }
  function viewLearn() {
    const d = state.data;
    if (!d) return state.loading ? loader() : '<div class="card empty">אין עדיין נתונים.</div>';
    if (state.review === undefined) { loadReview(); return `<div class="card"><h3>מה למדתי</h3><p class="muted">טוען…</p></div>`; }
    const rv = state.review;
    const demo = state.demo ? banner('info', 'נתוני דמו', 'הביקורת במסך הזה מחושבת על שוק מדומה ואינה אומרת דבר על השוק האמיתי.') : '';
    if (!rv) {
      return `<div class="stack">${banner('info', 'הביקורת העצמית מתחילה מהסריקה הבאה', 'מהסריקה הראשונה אחרי העדכון כל המלצה נשמרת עם מה שחזיתי עליה, ונבדקת אחרי כחודש מול מה שקרה באמת.')}${reviewHow()}</div>`;
    }
    const c = rv.counts || {}, s = rv.score || {}, fin = rv.final || {}, cal = rv.calibration || {};
    const head = `<div class="card"><h3>מה למדתי</h3><p class="muted small" style="margin:2px 0 10px">נכון ל־${dmy(rv.date)} · ${N(c.ideas || 0)} מניות נשמרו מאז ${N(dm(c.first || rv.date))} · ${N(c.graded || 0)} כבר נבדקו מול התוצאה</p>
      <ul class="concl">${(rv.conclusions || []).map((x) => `<li class="${esc(x.tone)}">${esc(x.text)}</li>`).join('')}</ul></div>`;
    let score = '';
    if (s.n) {
      const rows = [['המניות שבחרתי', s.pick_ret], ['מניות שבדקתי ולא בחרתי', s.other_ret], ['S&P 500 באותם ימים', s.pick_spy]].filter(([, x]) => isNum(x));
      const mx = Math.max(0.01, ...rows.map(([, x]) => Math.abs(x)));
      const vtxt = { good: ['good', 'הבחירה מוסיפה ערך'], bad: ['bad', 'הבחירה גרועה מהמניות שדחיתי'], unclear: ['', 'עוד לא ברור'], none: ['', 'עוד מעט מדי נתונים'] }[s.verdict] || ['', ''];
      score = `<div class="card"><h3>חודש אחרי ההמלצה</h3><p class="muted small" style="margin:2px 0 10px">כל מניה פעם אחת לכל רעיון (גם אם הומלצה כמה ימים ברצף), לפי תוכנית המסחר: סטופ, יעד, עמלות.</p>
        ${divHead('הפסד', 'רווח')}${rows.map(([l, x]) => divRow(l, x / mx, pct(x, 1, true))).join('')}
        <div class="chips" style="margin-top:10px"><span class="chip ${vtxt[0]}">${vtxt[1]}</span>
          ${isNum(s.edge) ? `<span class="chip">יתרון על המניות שלא בחרתי <b>${pct(s.edge, 1, true)}</b></span>` : ''}
          ${isNum(s.pick_win) ? `<span class="chip">בחירות ברווח <b>${pct(s.pick_win)}</b></span>` : ''}
          <span class="chip">בחירות שנבדקו <b>${s.n}</b></span></div>
        ${fin.n ? `<div class="eyebrow" style="margin:14px 0 6px">עסקאות שנסגרו לפי התוכנית</div><div class="chips">
          <span class="chip good">ביעד <b>${fin.target}</b></span><span class="chip bad">בסטופ <b>${fin.stop}</b></span><span class="chip">בסוף 3 חודשים <b>${fin.time}</b></span>
          ${isNum(fin.ret) ? `<span class="chip ${fin.ret > 0 ? 'good' : 'bad'}">עסקה ממוצעת <b>${pct(fin.ret, 1, true)}</b></span>` : ''}
          ${fin.open ? `<span class="chip">עוד פתוחות <b>${fin.open}</b></span>` : ''}</div>` : ''}</div>`;
    }
    let calib = '';
    if ((cal.groups || []).length) {
      const mx = Math.max(0.05, ...cal.groups.flatMap((g) => [g.prob, g.actual]));
      calib = `<div class="card"><h3>הסיכוי שהראיתי מול מה שקרה</h3><p class="muted small" style="margin:2px 0 8px">מניות שעברו כבר 3 חודשים, בשלוש קבוצות לפי הסיכוי שהראיתי.</p>
        ${cal.groups.map((g) => `<div class="cal-row"><div class="cal-lbl"><span>אמרתי ${N(pct(g.prob))}</span><span>בפועל ${N(pct(g.actual))} <span class="muted">(${N(g.n)})</span></span></div>
          <div class="cal-bars"><i class="said" style="width:${(g.prob / mx * 100).toFixed(0)}%"></i><i class="real" style="width:${(g.actual / mx * 100).toFixed(0)}%"></i></div></div>`).join('')}
        <div class="legend" style="margin-top:6px"><span><i style="border-top-color:var(--ink-2);opacity:.5"></i>מה שהראיתי</span><span><i></i>מה שקרה</span></div></div>`;
    }
    const pms = rv.post_mortems || [];
    const lc = rv.loss_causes || {};
    const causeRows = lc.n ? Object.entries(lc.by || {}).map(([k, v]) => divRow(CAUSE[k] || k, -v / lc.n, `${v}`)).join('') : '';
    const shown = pms.filter((p) => state.pmShow === 'all' || (state.pmShow === 'wrong' ? p.verdict === 'wrong' : p.verdict !== 'wrong'));
    const seg = [['all', 'הכול'], ['wrong', 'טעיתי'], ['right', 'צדקתי']].map(([k, l]) => `<button data-act="pm-show" data-k="${k}" aria-pressed="${state.pmShow === k}">${l}</button>`).join('');
    const pmCard = pms.length ? `<div class="card"><h3>ניתוח כל עסקה שנסגרה</h3>
      ${causeRows ? `<div class="eyebrow" style="margin:4px 0 4px">הסיבה העיקרית ב־${lc.n} העסקאות שהפסידו</div>${causeRows}` : ''}
      <div class="seg" role="group" aria-label="אילו עסקאות להציג" style="margin-top:12px">${seg}</div>
      <div class="pms">${shown.slice(0, state.pmAll ? 30 : 6).map(pmItem).join('') || '<p class="muted small">אין עסקאות כאלה ברשימה.</p>'}</div>
      ${shown.length > 6 && !state.pmAll ? `<button class="btn small ghost" data-act="pm-all">הצג עוד ${shown.length - 6}</button>` : ''}</div>` : '';
    const sigs = rv.signals || [];
    const fac = rv.factors || {};
    const sigCard = sigs.length ? `<div class="card"><h3>אילו אותות עזרו ואילו הטעו</h3><p class="muted small" style="margin:2px 0 8px">מתאם בין ציון האות לתוצאה אחרי חודש, בין המניות שבדקתי באותו יום. 0 = לא עוזר.</p>
      ${divHead('הטעה', 'עזר')}${sigs.map((x) => divRow(x.label, Math.max(-1, Math.min(1, x.ic / 0.2)), (x.ic >= 0 ? '+' : '') + x.ic.toFixed(2),
        `${x.n} מניות${isNum(fac[x.key]) ? ` · משקל ×${fac[x.key].toFixed(2)}` : ''}${x.verdict === 'helped' ? ' · עוזר' : x.verdict === 'misled' ? ' · מטעה' : ''}`)).join('')}</div>` : '';
    const les = rv.lessons || [];
    const conds = (rv.conditions || []).filter((x) => isNum(x.diff) && x.n >= 10);
    const condCard = les.length || conds.length ? `<div class="card"><h3>לקחים</h3>
      ${les.length ? les.map(lessonItem).join('') : '<p class="muted small">עוד לא נמצא דפוס הפסד חזק מספיק כדי לשנות משהו.</p>'}
      ${conds.length ? `<div class="eyebrow" style="margin:14px 0 4px">כל המצבים שנבדקו: המניות במצב הזה מול שאר המניות, חודש אחרי</div>${divHead('גרוע יותר', 'טוב יותר')}
        ${conds.map((x) => divRow(x.text, Math.max(-1, Math.min(1, x.diff / 0.05)), pct(x.diff, 1, true), `${x.n} מניות`)).join('')}` : ''}</div>` : '';
    const filt = rv.filters || [];
    const filtCard = filt.length ? `<div class="card"><h3>האם המסננים צדקו</h3><p class="muted small" style="margin:2px 0 8px">מניות שלא בחרתי, לפי הסיבה, מול המניות שבחרתי – חודש אחרי.</p>
      ${divHead('צדק', 'החמיץ')}${filt.map((x) => divRow(x.text, Math.max(-1, Math.min(1, (x.diff || 0) / 0.05)), pct(x.diff, 1, true), `${x.n} מניות`)).join('')}</div>` : '';
    const log = rv.log || [];
    const logCard = log.length ? `<div class="card"><h3>מה שיניתי ומתי</h3><ul class="log">${log.slice(0, 20).map((x) => `<li><span class="when">${dm(x.date)}</span><span>${esc(x.text)}</span></li>`).join('')}</ul></div>` : '';
    return `<div class="stack">${demo}${head}${score}${calib}${pmCard}${sigCard}${condCard}${filtCard}${logCard}${reviewHow()}</div>`;
  }

  // ------------------------------------------------------------------ views: search
  function viewSearch() {
    const d = state.data || { results: [] };
    if (state.tickers === undefined) loadTickerIndex();
    const prev = (state.tickers || []).slice(0, 12);
    const msg = state.searchMsg;
    let msgHtml = '';
    if (msg) {
      if (msg.kind === 'missing') {
        msgHtml = `<div class="card"><h3><span class="tk">${esc(msg.sym)}</span> עוד לא נבדקה</h3>
          ${cloudReady() ? `<p class="muted">אפשר לנתח אותה עכשיו בענן. זה לוקח בדרך כלל 2 עד 5 דקות, והתוצאה תיפתח כאן לבד.</p><button class="btn" data-run="ticker" data-sym="${esc(msg.sym)}">נתח את ${esc(msg.sym)} עכשיו</button>`
          : `<p class="muted">${PREVIEW ? 'בתצוגה המקדימה אפשר לחפש רק מניות מהדמו (למשל DMO005). אחרי ההתקנה תוכל לנתח כל מניה אמריקאית.' : 'כדי לנתח מניה חדשה מהטלפון, חבר את האפליקציה לענן בהגדרות (פעם אחת).'}</p>
             ${PREVIEW ? '' : '<button class="btn ghost" data-act="settings">פתח הגדרות</button>'}`}</div>`;
      } else if (msg.kind === 'bad') msgHtml = `<div class="banner warn"><span class="dot"></span><div class="grow">${esc(msg.text)}</div></div>`;
    }
    const picks = (d.results || []).filter((r) => r.pick);
    return `<div class="stack">
      <form class="card stack" data-form="search" style="gap:10px">
        <div class="field"><label for="sym">סימול מניה בבורסה האמריקאית</label>
        <div class="search-row"><input class="input" id="sym" name="sym" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="NVDA" value="${esc(state.search)}" maxlength="12">
        <button class="btn" type="submit">נתח</button></div></div>
        <p class="muted small" style="margin:0">לדוגמה: ESLT (אלביט), TEVA (טבע), NVDA (אנבידיה). מניות שנבדקו בסריקה האחרונה נפתחות מיד.</p>
      </form>
      ${msgHtml}
      ${picks.length ? `<div class="section-title"><h2>מהסריקה האחרונה</h2></div><div class="chips">${picks.map((r) => `<button class="chip" style="border:0;cursor:pointer" data-sym="${esc(r.ticker)}" data-act="find"><b class="tk">${esc(r.ticker)}</b></button>`).join('')}</div>` : ''}
      ${prev.length ? `<div class="section-title"><h2>ניתוחים קודמים</h2></div><div class="card rows">${prev.map((t) => `<button class="row" data-act="find" data-sym="${esc(t.ticker)}">
        <span class="grow"><span class="tk">${esc(t.ticker)}</span><span class="nm">${esc(t.name)}</span></span><span class="muted small">${dmy(t.date)}</span></button>`).join('')}</div>` : ''}
    </div>`;
  }
  async function loadTickerIndex() {
    const idx = await getJSON(state.demo ? 'demo/tickers/index.json' : 'tickers/index.json');
    state.tickers = (idx && idx.tickers) || [];
    if (state.tab === 'search') render();
  }
  async function findTicker(sym) {
    sym = String(sym || '').toUpperCase().replace(/[^A-Z0-9.\-]/g, '');
    state.search = sym;
    state.searchMsg = null;
    if (!sym) return;
    const res = (state.data && state.data.results) || [];
    const i = res.findIndex((r) => r.ticker === sym);
    if (i >= 0) { openDetail(res[i], { spark_dates: state.data.spark_dates, date: state.data.date, demo: state.demo, spy_closes: state.data.spy_closes }); return; }
    const f = await getJSON(`${state.demo ? 'demo/' : ''}tickers/${encodeURIComponent(sym)}.json`);
    if (f && f.result) { openDetail(f.result, { spark_dates: f.spark_dates, date: f.date, demo: f.demo, spy_closes: f.spy_closes || (state.data && state.data.spy_closes) }); return; }
    state.searchMsg = { kind: 'missing', sym };
    render();
  }

  // ------------------------------------------------------------------ detail sheet
  function openDetail(r, ctx, replace) {
    state.sheet = { kind: 'detail', r, ctx, period: 63 };
    renderSheet();
    pushSheet(replace);
  }
  function probExplain(r) {
    const h = r.prob_hist;
    const plan = r.plan || {};
    const what = isPctRule(r)
      ? `עלו ${pct(rulePct(r))} תוך 3 חודשים לפני שירדו לסטופ`
      : `הגיעו ליעד${isNum(plan.target_pct) ? ` (${pct(plan.target_pct, 0, true)})` : ''} תוך 3 חודשים לפני שירדו לסטופ${isNum(plan.stop_pct) ? ` (${pct(plan.stop_pct)})` : ''}`;
    let out = `<p class="small" style="margin:10px 0 0">מתוך מניות שנראו כמו המניה הזו ב־5 השנים האחרונות (מחיר ומגמה, קניות מנהלים, תגובה לדוחות ומצב השוק), ${pct(r.prob)} ${what}. הממוצע של כל המניות: ${pct(r.prob_base)}.</p>`;
    if (h && isNum(h.ret)) {
      const tone = h.ret > 0 ? 'up' : 'down';
      out += `<div class="hist"><div class="eyebrow">מה קרה בעבר לעסקאות עם סיכוי דומה</div>
        <div class="hist-row"><span>עסקה ממוצעת</span><b class="${tone}">${pct(h.ret, 1, true)}</b></div>
        ${isNum(h.win) ? `<div class="hist-row"><span>עסקאות ברווח</span><b>${pct(h.win)}</b></div>` : ''}
        ${isNum(h.stop) ? `<div class="hist-row"><span>נעצרו בסטופ</span><b>${pct(h.stop)}</b></div>` : ''}
        <p class="muted small" style="margin:6px 0 0">לפי ${Number(h.n || 0).toLocaleString('en-US')} עסקאות מדומות בשנים שהמודל לא ראה, כולל עמלות ומרווח. ${h.ret > 0 ? '' : 'בממוצע הן הפסידו, ולכן מניה כזו לא נבחרת. '}</p></div>`;
    }
    const lv = r.prob_live;
    if (lv && isNum(lv.rate)) {
      const fixed = isNum(r.prob_model) && Math.abs(r.prob - r.prob_model) >= 0.005;
      out += `<div class="hist"><div class="eyebrow">מה קרה בפועל להמלצות שלי ברמת סיכוי הזו</div>
        <div class="hist-row"><span>הגיעו ליעד</span><b>${pct(lv.rate)}</b></div>
        ${isNum(lv.ret) ? `<div class="hist-row"><span>עסקה ממוצעת</span><b class="${lv.ret > 0 ? 'up' : 'down'}">${pct(lv.ret, 1, true)}</b></div>` : ''}
        <p class="muted small" style="margin:6px 0 0">לפי ${N(lv.n)} המלצות שלי שכבר הסתיימו.${fixed ? ` הסיכוי למעלה תוקן לפי זה (המודל לבדו אמר ${N(pct(r.prob_model))}).` : ''}</p></div>`;
    }
    out += `<p class="muted small" style="margin:8px 0 0">זה סיכוי, לא הבטחה. גם כשהסיכוי טוב, לרוב המניה לא תגיע ליעד, ולכן הסטופ וגודל הפוזיציה חשובים. הסטופ והיעד מחושבים ממחיר הסגירה האחרון. אם קונים במחיר אחר, שמור על אותו מרחק. איך המודל נבדק – בלשונית ביצועים.</p>`;
    return out;
  }

  function planText(pl) {
    pl = pl || {};
    const days = pl.days || 63;
    const span = days <= 25 ? 'חודש' : days <= 45 ? 'חודשיים' : '3 חודשים';
    const size = 'הכמות נקבעת כך שפגיעה בסטופ תעלה אחוז קבוע מהתיק.';
    const chosen = state.data && state.data.plan && state.data.plan.key && state.data.plan.key !== 'base' ? ' התוכנית הזו נבחרה כי ניצחה את התוכנית הקודמת גם בבדיקה על היסטוריה.' : '';
    if (pl.trail_atr) {
      return `הסטופ מתחיל לפי התנודה הרגילה של המניה ועולה יחד עם המחיר: תמיד ${pl.trail_atr} תנודות יומיות מתחת למחיר הסגירה הגבוה ביותר מאז הכניסה. ${pl.target ? 'יש גם יעד.' : 'אין יעד – נותנים לרווח לרוץ.'} ${size} אם הסטופ לא נפגע תוך ${span} – יוצאים.${chosen}`;
    }
    if (!pl.target) {
      return `סטופ חירום רחוק (${pl.stop_atr || 5} תנודות יומיות מתחת למחיר), בלי יעד. ${size} יוצאים אחרי ${span}.${chosen}`;
    }
    const rr = isNum(pl.target_pct) && isNum(pl.stop_pct) && pl.stop_pct ? Math.round(-pl.target_pct / pl.stop_pct * 10) / 10 : 3;
    return `הסטופ נקבע לפי התנודה הרגילה של המניה (${pl.stop_atr || 2.5} תנודות יומיות ממוצעות), והיעד פי ${rr} מהמרחק לסטופ. ${size} אם לא הגיעה לאף אחד מהם תוך ${span} – יוצאים.${chosen}`;
  }
  // the deal text starts with its own label ("בתהליך רכישה: ..."), which the banner title already shows
  const dealBody = (deal) => String(deal.text || '').replace(new RegExp('^' + String(deal.short || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*:\\s*'), '');
  function viewDetail() {
    const { r, ctx, period } = state.sheet;
    const dates = ctx.spark_dates || [];
    const closes = r.closes || [];
    const n = Math.min(period, closes.length);
    const v = closes.slice(-n), dts = dates.slice(-n);
    const chg = changeOver(closes, n - 1);
    const [rc, rl] = riskLevel(r.risk);
    const plan = sizePlan(r);
    const z = mySizing();
    const facts = [
      ['מחיר אחרון', price(r.price)],
      plan.stop ? ['סטופ מוצע', `${price(plan.stop)} (${pct(plan.stop_pct)})`] : null,
      plan.target ? ['יעד מוצע', `${price(plan.target)} (${pct(plan.target_pct, 0, true)})`] : null,
      isNum(plan.shares) && plan.stop ? ['כמות מוצעת', plan.shares ? `${plan.shares} מניות` : 'אפילו מניה אחת חורגת מהסיכון'] : null,
      plan.value ? ['שווי הפוזיציה', money(plan.value)] : null,
      plan.risk_amount ? ['הפסד מקסימלי בסטופ', money(plan.risk_amount)] : null,
      isNum(r.move_3m) ? ['תנודה טיפוסית ל־3 חודשים', `±${pct(r.move_3m)}`] : null,
      isNum(r.market_cap) ? ['שווי שוק', money(r.market_cap)] : null,
      r.earnings_date ? ['דוח רבעוני הבא', dmy(r.earnings_date)] : null,
      isNum(r.model_pct) ? ['דירוג המודל היום', `${pct(Math.max(0.001, 1 - r.model_pct), 1)} העליונים`] : null,
    ].filter(Boolean);
    const links = !ctx.demo && /^[A-Z][A-Z0-9.\-]*$/.test(r.ticker) ? `<div class="links">
      <a class="btn small ghost" href="https://finance.yahoo.com/quote/${encodeURIComponent(r.ticker)}" target="_blank" rel="noopener">Yahoo Finance</a>
      <a class="btn small ghost" href="https://www.tradingview.com/symbols/${encodeURIComponent(r.ticker)}/" target="_blank" rel="noopener">TradingView</a>
      <a class="btn small ghost" href="https://finviz.com/quote.ashx?t=${encodeURIComponent(r.ticker)}" target="_blank" rel="noopener">Finviz</a></div>` : '';
    return `<div class="sheet-bar"><div class="topbar-in">
        <button class="icon-btn" data-act="close" aria-label="חזרה">${ICON.back}</button>
        <div class="brand"><h1 class="tk" style="font:600 19px/1 var(--font-data)">${esc(r.ticker)}</h1></div>
        ${saveBtn(r, true)}
        ${cloudReady() && !ctx.demo ? `<button class="btn small ghost" data-run="ticker" data-sym="${esc(r.ticker)}">${ICON.refresh.replace('<svg', '<svg width="16" height="16"')} נתח מחדש</button>` : ''}
      </div></div>
      <div class="sheet-in">
        ${ctx.demo ? banner('info', 'מניה בדויה (דמו)', 'כל הנתונים כאן לדוגמה בלבד.') : ''}
        ${r.deal ? banner(r.exclude ? 'bad' : 'warn', r.exclude ? `${r.deal.short}: לא נבחרת` : r.deal.short, dealBody(r.deal)) : r.exclude ? banner('bad', `${r.exclude}: לא נבחרת`, '') : ''}
        ${r.lesson ? banner('warn', 'לא נבחרה בגלל לקח שלמדתי', `${r.lesson.text}: בבדיקה העצמית מצאתי שמניות כאלה מפסידות אצלי, ולכן היא לא נבחרת. הפרטים בלשונית למידה.`) : ''}
        ${!r.pick && !r.lesson && WHY_NOT[r.why_not] ? `<p class="muted small" style="margin:0">לא נבחרה היום: ${esc(WHY_NOT[r.why_not])}.</p>` : ''}
        <div class="d-head"><div class="grow"><div class="nm">${esc(r.name)}</div>
          <div class="muted small">${esc([r.sector, r.industry, r.country].filter(Boolean).join(' · '))}</div>
          <div class="price-line"><span class="px">${price(r.price)}</span><span class="chg ${chg >= 0 ? 'up' : 'down'}">${pct(chg, 1, true)}</span></div></div></div>
        <div class="card"><div class="rings">${ring(r.upside, 'var(--accent)', 'פוטנציאל עלייה', true)}${ring(r.risk, riskColor(r.risk), `סיכון ${rl}`, true)}
          <div class="small muted" style="align-self:center;flex:1;min-width:0">פוטנציאל: כמה אותות מצביעים לעלייה (50 = ניטרלי, לא אחוז סיכוי). סיכון: תנודתיות, אירועים קרובים ומצב השוק.</div></div></div>
        ${checklistCard(r, ctx)}
        ${isNum(r.prob) && r.prob_base ? `<div class="card"><h3>מודל הסיכוי</h3>${probLine(r)}${probExplain(r)}</div>` : ''}
        <div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px"><h3>מחיר</h3>
          <div class="seg" role="group" aria-label="תקופה">${[[21, 'חודש'], [63, '3 חודשים'], [130, '6 חודשים']].map(([p, l]) => `<button data-period="${p}" aria-pressed="${p === period}">${l}</button>`).join('')}</div></div>
          ${chartSlot('chart', { series: [{ v, color: 'var(--accent)', area: true, name: '' }], refs: [plan.stop ? { y: plan.stop, label: 'סטופ', color: 'var(--bad)' } : null, plan.target ? { y: plan.target, label: 'יעד', color: 'var(--good)' } : null].filter(Boolean), height: 210, tip: true,
            fmtY: (t) => (t >= 100 ? t.toFixed(0) : t.toFixed(t >= 10 ? 1 : 2)), fmtTip: (x) => price(x), fmtX: (i, full) => (full ? dmy(dts[i]) : dm(dts[i] || '')), label: `גרף מחיר ${r.ticker}` })}
          ${plan.stop ? `<div class="legend" style="margin-top:6px"><span><i></i>מחיר</span><span><i class="dash" style="border-top-color:var(--bad)"></i>סטופ מוצע</span>${plan.target ? '<span><i class="dash" style="border-top-color:var(--good)"></i>יעד מוצע</span>' : ''}</div>` : ''}</div>
        ${(r.pros || []).length ? `<div class="card"><h3>למה נבחרה</h3><ul class="list pros">${r.pros.map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>` : ''}
        ${(r.cons || []).length ? `<div class="card"><h3>שים לב</h3><ul class="list cons">${r.cons.map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>` : ''}
        ${tgCard(r)}
        <div class="card"><h3>תוכנית מוצעת</h3><p class="muted small" style="margin:2px 0 10px">${esc(planText(r.plan))}</p>
          <div class="facts">${facts.map(([k, val]) => `<div class="fact"><div class="k">${esc(k)}</div><div class="v">${esc(val)}</div></div>`).join('')}</div>
          ${plan.stop ? `<p class="muted small" style="margin:10px 0 0">הכמות מחושבת לפי תיק של ${N(money(z.account))} וסיכון של ${N(z.risk + '%')} לעסקה${z.custom ? '' : ' (ברירת מחדל)'}. <button class="linkish" data-act="settings-sizing">שינוי</button></p>` : ''}</div>
        <div class="card"><h3>כל האותות</h3><p class="muted small" style="margin:2px 0 8px">כל אות מקבל ציון בין שלילי לחיובי. אותות בלי נתונים לא מוצגים.</p>
          ${divHead('שלילי', 'חיובי')}${(r.signals || []).map((s) => divRow(s.label, s.score, (s.score >= 0 ? '+' : '') + s.score.toFixed(2))).join('')}</div>
        ${(r.news || []).length ? `<div class="card news"><h3>כותרות אחרונות</h3><p class="muted small" style="margin:2px 0 6px">הכותרות המקוריות, באנגלית</p>
          ${r.news.map((x) => x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener"><span class="lbl">${esc(x.label || '')}</span>${esc(x.title)}</a>` : `<a><span class="lbl">${esc(x.label || '')}</span>${esc(x.title)}</a>`).join('')}</div>` : ''}
        ${links}
        <p class="muted small">נותח ב־${dmy(ctx.date)} · כיסוי נתונים ${pct(r.coverage)} · כלי עזר למחקר, לא ייעוץ השקעות.</p>
      </div>`;
  }

  // ------------------------------------------------------------------ guide: how to read the app and what to do with it
  function openGuide() {
    store.set('seen', APP_VERSION);
    if (state.sheet) { state.sheet = { kind: 'guide' }; renderSheet(); pushSheet(true); } else { state.sheet = { kind: 'guide' }; renderSheet(); pushSheet(); }
    render();
  }
  function viewGuide() {
    const li = (b, t) => `<li><b>${esc(b)}</b> ${esc(t)}</li>`;
    return `<div class="sheet-bar"><div class="topbar-in"><button class="icon-btn" data-act="close" aria-label="חזרה">${ICON.back}</button>
        <div class="brand"><h1>איך קוראים את הרדאר</h1></div></div></div>
      <div class="sheet-in guide">
        <div class="card"><h3>ארבעה דברים בכל מניה</h3><ul class="list">
          <li>הרדאר עונה על שאלה אחת: אצל אילו מניות יש היום הכי הרבה סימנים שמצביעים על עלייה. הוא לא עונה על "האם היא תעלה".</li>
          ${li('פוטנציאל:', 'כמה סימנים מסכימים ביניהם. 50 = ניטרלי. מתחת ל־60 מניה לא נבחרת.')}
          ${li('סיכון:', 'כמה המניה קופצת. סיכון גבוה אומר שגם הירידות יהיו חדות.')}
          ${li('הסיבות:', 'במילים, למשל "מנהלים קנו מניות מכספם הפרטי". סיבה שלא ברורה – שואלים עליה.')}
          ${li('סטופ:', 'המחיר שבו מודים שהרעיון לא עבד ויוצאים. בלי סטופ אין תוכנית.')}
          <li>הנתון החשוב ביותר נמצא בראש המסך: האם הבחירות של הרדאר מנצחות את S&amp;P 500. אם אחרי כמה חודשים לא – אין לו יתרון, ועדיף מדד.</li></ul></div>
        <div class="card"><h3>בקצרה</h3><ul class="list">
          <li>כל בוקר המערכת עוברת על כ־6,000 מניות אמריקאיות ומשאירה עד 10. היא מסננת, היא לא יודעת לחזות.</li>
          <li>הכרטיס העליון במסך הראשי אומר אם המערכת כבר הוכיחה שהיא מנצחת את S&amp;P 500. כל עוד לא, הרשימה היא לבדיקה ולמעקב בתיק הנייר, לא לקנייה.</li>
          <li>כל עסקה, גם על נייר: סטופ מראש, כמות לפי החישוב, ויציאה ביעד, בסטופ או אחרי 3 חודשים.</li></ul></div>
        <div class="card"><h3>הכרטיס של מניה</h3><ul class="list">
          ${li('פוטנציאל (העיגול, 0 עד 100):', 'כמה מהאותות מצביעים לעלייה, בשקלול הביטחון בכל אחד. 50 = ניטרלי. מתחת ל־60 מניה לא נבחרת. כשמודל הסיכוי עבר את הבדיקה, הוא בוחר מבין המניות שעברו את הסף. זה לא אחוז סיכוי.')}
          ${li('תוצאה צפויה לעסקה:', 'מה הרוויחה בממוצע עסקה במניות שעמדו באותו מקום בדירוג של המודל, בבדיקה על שנים שהוא לא ראה, מתוקן לפי מה שקרה בפועל להמלצות של המערכת. לידה – הממוצע של כל המניות.')}
          ${li('הגרף והאחוז לידו:', 'המחיר ב־3 החודשים האחרונים. קפיצה חדה בסוף הגרף שאחריה קו שטוח היא סימן מוכר להצעת רכישה.')}
          ${li('נקודות ירוקות:', 'הסיבות העיקריות שהמניה עלתה בסינון.')}
          ${li('שים לב:', 'האזהרה החשובה ביותר נגד המניה.')}
          ${li('סיכון (0 עד 100):', 'תנודתיות, אירועים קרובים כמו דוח רבעוני, מחיר מתוח ומצב השוק. עד 35 נמוך, מ־60 גבוה.')}
          ${li('מחיר:', 'מחיר הסגירה האחרון בסריקה.')}
          ${li('סטופ:', 'המחיר שבו יוצאים בהפסד. הוא נקבע לפי התנודה הרגילה של המניה: 2.5 תנודות יומיות ממוצעות מתחת למחיר.')}
          ${li('יעד:', 'פי 3 מהמרחק לסטופ. סטופ 10% מתחת למחיר = יעד 30% מעליו.')}
          ${li('בדיקה לפני כניסה:', 'רשימה אוטומטית של סיבות לוותר: עסקת רכישה, דוח קרוב, מחיר יעד של אנליסטים מתחת למחיר, מחיר מתוח, הנפקה. הפירוט במסך המניה.')}
          ${li('סיכוי להגיע ליעד:', 'מופיע בשורה הקטנה, רק כשמודל הסיכוי עבר את הבדיקה על שנים שלא ראה. המודל בוחר לפי הרווח הצפוי ולא לפי הסיכוי להגיע ליעד, ולכן מניה נבחרת יכולה להגיע ליעד בתדירות רגילה ועדיין להרוויח יותר.')}</ul></div>
        <div class="card"><h3>הפס שמעל הרשימה</h3><ul class="list">
          ${li('שוק ניטרלי, חיובי או שלילי:', 'מצב הכלכלה והשוק: ריבית, אינפלציה, אבטלה ומדד הפחד VIX. במצב שלילי המערכת בוחרת פחות מניות ומחמירה בסף.')}
          ${li('נושא · פי X:', 'כמה מדברים בעולם על הנושא בשבוע האחרון לעומת החודשיים שלפניו. "מתקפות סייבר · פי 1.5" = פי 1.5 מהרגיל, וזה מעלה מעט את הציון של ענפים שנהנים מזה. לחיצה פותחת את לשונית השוק.')}</ul></div>
        <div class="card"><h3>מה עושים עם זה</h3><ol class="steps">
          <li>מסתכלים על הכרטיס העליון. אם המערכת עוד לא הוכיחה יתרון, עוקבים רק בתיק הנייר.</li>
          <li>פותחים מניה ועוברים על "בדיקה לפני כניסה". עסקת רכישה, דוח בעוד פחות מ־10 ימים או הנפקה: מוותרים.</li>
          <li>קוראים את החדשות של השבוע האחרון (כפתור במסך המניה).</li>
          <li>אם נכנסים: הכמות לפי החישוב במסך המניה (את גודל התיק מגדירים בהגדרות), והסטופ נכנס כפקודה אמיתית אצל הברוקר באותו יום. את הסטופ לא מורידים.</li>
          <li>יוצאים ביעד, בסטופ או אחרי 3 חודשים, מה שקורה קודם.</li>
          <li>פעם בשבוע: לשונית ביצועים. השאלה היחידה שחשובה שם היא אם תיק הנייר מנצח את S&amp;P 500.</li>
          <li>לשונית למידה: איפה המערכת צדקה ואיפה טעתה בהמלצות של עצמה, למה, ומה היא שינתה בעקבות זה.</li></ol></div>
        <div class="card"><h3>מונחים</h3><ul class="list">
          ${li('S&P 500:', 'מדד של 500 החברות הגדולות בארה"ב. אפשר לקנות אותו בקרן סל אחת. אם המערכת לא מנצחת אותו לאורך זמן, אין סיבה לבחור מניות לבד.')}
          ${li('תיק נייר:', 'עסקאות מדומות בלי כסף אמיתי, כדי לבדוק אם השיטה עובדת לפני שמסכנים כסף.')}
          ${li('עסקת רכישה:', 'חברה אחרת מסכימה לקנות את כל המניות במחיר קבוע. המניה קופצת לאזור המחיר הזה ונתקעת שם, ולכן כמעט אין בה מה להרוויח. אם העסקה מתבטלת, היא נופלת.')}
          ${li('דוח רבעוני:', 'פעם ברבעון החברה מפרסמת תוצאות. ביום הזה המניה יכולה לזוז 10% עד 20% לכל כיוון.')}
          ${li('הנפקה (דילול):', 'החברה מוכרת מניות חדשות כדי לגייס כסף. יש יותר מניות, ולרוב המחיר יורד.')}
          ${li('מחיר יעד של אנליסטים:', 'ההערכה הממוצעת של בתי ההשקעות למחיר המניה בעוד שנה. הם טועים הרבה, אבל יעד מתחת למחיר הנוכחי הוא סימן אזהרה.')}
          ${li('RSI:', 'מדד מ־0 עד 100 לקצב העלייה בשבועיים האחרונים. מעל 75 = עלתה מהר מדי, ולרוב בא תיקון.')}
          ${li('ממוצע 50 ימים:', 'המחיר הממוצע בכ־10 השבועות האחרונים. מניה שנסחרת הרבה מעליו מתוחה.')}
          ${li('מחזור מסחר:', 'כמה מניות נסחרו. עלייה במחיר עם מחזור גבוה = כסף גדול נכנס.')}
          ${li('שורט סקוויז:', 'שורט = הימור על ירידה. כשהמחיר עולה, מי שהימר על ירידה נאלץ לקנות, וזה מקפיץ את המחיר עוד. זה עובד גם הפוך, ולכן תנודתי.')}</ul></div>
        <div class="card"><h3>מה המערכת לא יודעת</h3><ul class="list">
          <li>אף מערכת לא יודעת לחזות זינוק בוודאות. מניה עם פוטנציאל לעלייה חדה יכולה גם לרדת חדה.</li>
          <li>מחירים, אנליסטים וחדשות מגיעים מ־Yahoo Finance דרך ספרייה לא רשמית, ולפעמים חסרים. כשחסר משהו חשוב, זה כתוב בכרטיס העליון.</li>
          <li>כשלא מתקבלות כותרות, אותות החדשות לא פועלים. מקור חדשות נוסף וחינמי: הרשמה ב־<span class="kbd">finnhub.io</span>, ואת המפתח מוסיפים ב־GitHub כ־Secret בשם <span class="kbd">FINNHUB_API_KEY</span>.</li>
          <li>כלי עזר לסינון ולמחקר, לא ייעוץ השקעות.</li></ul></div>
      </div>`;
  }

  // ------------------------------------------------------------------ settings sheet
  function openSettings(anchor) {
    state.sheet = { kind: 'settings', anchor };
    renderSheet();
    pushSheet();
    if (anchor) setTimeout(() => { const el = document.getElementById(anchor); if (el) el.scrollIntoView({ block: 'start' }); }, 30);
  }
  function viewSettings() {
    const repo = ghRepo();
    const tok = store.get('token', '');
    const conn = store.get('conn');
    const install = state.installEvt ? '<button class="btn" data-act="install">התקן את האפליקציה</button>' : '';
    return `<div class="sheet-bar"><div class="topbar-in"><button class="icon-btn" data-act="close" aria-label="חזרה">${ICON.back}</button>
        <div class="brand"><h1>הגדרות</h1></div></div></div>
      <div class="sheet-in">
        <div class="card stack" style="gap:10px"><h3>חיבור לענן</h3>
          ${PREVIEW ? '<p class="muted" style="margin:0">בתצוגה המקדימה אין חיבור לענן. אחרי ההתקנה תוכל להפעיל מכאן סריקה וניתוח של כל מניה.</p>' : `
          <p class="muted small" style="margin:0">הסריקה היומית רצה לבד. החיבור הזה נדרש רק כדי להפעיל מהטלפון ניתוח של מניה חדשה, סריקה או בדיקה לאחור. ההוראות ליצירת המפתח נמצאות במדריך ההקמה, שלב 9.</p>
          <div class="field"><label for="repo">מאגר ב־GitHub</label><input class="input mono" id="repo" value="${esc(repo ? repo.owner + '/' + repo.repo : '')}" placeholder="username/stock-radar" style="text-align:left"></div>
          <div class="field"><label for="tok">מפתח גישה (Token)</label><input class="input mono" id="tok" type="password" value="${esc(tok)}" placeholder="github_pat_..." style="text-align:left" autocomplete="off"></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" data-act="save-conn">שמור ובדוק</button>${tok ? '<button class="btn ghost" data-act="forget">מחק מפתח</button>' : ''}</div>
          ${conn ? `<div class="banner ${conn.ok ? 'good' : 'bad'}"><span class="dot"></span><div class="grow">${esc(conn.text)}</div></div>` : ''}
          <p class="muted small" style="margin:0">המפתח נשמר רק בטלפון הזה.</p>`}
        </div>
        <div class="card stack" style="gap:10px"><h3>תצוגה</h3>
          <div class="seg" role="group" aria-label="תצוגה"><button data-act="view-simple" aria-pressed="${state.view === 'simple'}">פשוטה</button><button data-act="view-full" aria-pressed="${state.view !== 'simple'}">מלאה</button></div>
          <p class="muted small" style="margin:0">פשוטה: המניות של היום, ארבעת הדברים שצריך בכל אחת, והשוואה ל־S&amp;P 500. מלאה: כל המסכים, המודלים והבדיקות.</p></div>
        <div class="card stack" style="gap:10px" id="sizing"><h3>התיק שלי</h3>
          <p class="muted small" style="margin:0">לפי זה מחושבת הכמות בכל מניה: אם הסטופ נפגע, ההפסד הוא האחוז שבחרת מהתיק. נשמר רק בטלפון הזה.</p>
          <div class="field"><label for="acct">גודל התיק לחישוב (בדולרים)</label><input class="input mono" id="acct" inputmode="decimal" value="${mySizing().account}" style="text-align:left"></div>
          <div class="field"><label for="riskp">הפסד מקסימלי לעסקה (אחוז מהתיק)</label><input class="input mono" id="riskp" inputmode="decimal" value="${mySizing().risk}" style="text-align:left"></div>
          <div class="field"><label for="maxp">פוזיציה מקסימלית (אחוז מהתיק)</label><input class="input mono" id="maxp" inputmode="decimal" value="${mySizing().maxPos}" style="text-align:left"></div>
          <div><button class="btn" data-act="save-sizing">שמור</button></div>
          <div class="muted small" id="sizing-msg" aria-live="polite"></div>
          <p class="muted small" style="margin:0">ברירת המחדל: 50,000$, 1% ו־10%. תיק הנייר וההודעה בטלגרם ממשיכים לפי הקובץ config.yaml.</p></div>
        ${cloudReady() ? `<div class="card stack" style="gap:10px"><h3>הפעלה ידנית</h3>
          <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn ghost" data-run="scan">הרץ סריקה עכשיו</button><button class="btn ghost" data-run="backtest">הרץ בדיקה לאחור</button><button class="btn ghost" data-run="train">אמן את מודל הסיכוי</button><button class="btn ghost" data-run="test-alert">שלח הודעת בדיקה</button><button class="btn ghost" data-run="telegram">משוך מהטלגרם</button></div>
          <p class="muted small" style="margin:0">סריקה מלאה או אימון של המודל לוקחים 15 עד 40 דקות. אפשר לסגור את האפליקציה בינתיים. המודל מתאמן לבד פעם בחודש.</p></div>` : ''}
        <div class="card stack" style="gap:8px"><h3>התקנה על מסך הבית</h3>${install}
          <p style="margin:0"><b>אנדרואיד (Chrome):</b> תפריט ⋮ למעלה, ואז "התקנת אפליקציה" או "הוספה למסך הבית".</p>
          <p style="margin:0"><b>אייפון (Safari):</b> כפתור השיתוף (ריבוע עם חץ), ואז "הוסף למסך הבית".</p></div>
        <div class="card stack" style="gap:10px" id="setup"><h3>מדריך הקמה (פעם אחת, מהטלפון)</h3>
          <p class="muted small" style="margin:0">הסריקה רצה כל יום בחינם בשרתים של GitHub, והאפליקציה מתארחת שם. לא צריך מחשב.</p>
          <ol class="steps">
            <li>פתח חשבון חינמי ב־<span class="kbd">github.com</span> והתחבר.</li>
            <li>צור מאגר חדש: כפתור <span class="kbd">+</span> ואז <span class="kbd">New repository</span>. בשם כתוב <span class="kbd">stock-radar</span>, בחר <span class="kbd">Public</span>, סמן <span class="kbd">Add a README file</span> ולחץ <span class="kbd">Create repository</span>.</li>
            <li>במאגר: <span class="kbd">Settings</span> ואז <span class="kbd">Pages</span>. תחת Source בחר <span class="kbd">GitHub Actions</span>.</li>
            <li>עדיין ב־Settings: <span class="kbd">Secrets and variables</span>, <span class="kbd">Actions</span>, <span class="kbd">New repository secret</span>. בשם כתוב <span class="kbd">SEC_EMAIL</span> ובערך את האימייל שלך (דרישה של רשות ניירות הערך האמריקאית).</li>
            <li>חזור לדף הראשי של המאגר: <span class="kbd">Add file</span> ואז <span class="kbd">Create new file</span>. בשם הקובץ כתוב בדיוק <span class="kbd">.github/workflows/radar.yml</span>, הדבק את התוכן שלמטה ולחץ <span class="kbd">Commit changes</span>.
              <div style="display:flex;gap:8px;margin:8px 0;flex-wrap:wrap"><button class="btn small ghost" data-act="copy-name">העתק את שם הקובץ</button><button class="btn small" data-act="copy-wf">העתק את התוכן</button></div>
              <div class="muted small" id="copy-msg" aria-live="polite"></div>
              <pre class="code" id="wf">${esc(WORKFLOW)}</pre></li>
            <li><span class="kbd">Add file</span> ואז <span class="kbd">Upload files</span>. בחר את קובץ ה־zip של רדאר המניות שהורדת לטלפון ולחץ <span class="kbd">Commit changes</span>. זה מפעיל את ההתקנה.</li>
            <li>חכה כ־5 דקות. בלשונית <span class="kbd">Actions</span> תופיע ריצה עם וי ירוק. פתח בדפדפן <span class="kbd">https://שם-המשתמש.github.io/stock-radar</span>. בהתחלה תופיע הודעה שהסריקה הראשונה רצה, ואחרי כשעה יופיעו המניות. אם לא הופיעה ריצה, או שהיא נכשלה: ודא את שלב 3, ואז <span class="kbd">Actions</span>, <span class="kbd">radar</span>, <span class="kbd">Run workflow</span>.</li>
            <li>התקן את האפליקציה על מסך הבית (ההוראות למעלה).</li>
            <li>לא חובה: כדי להפעיל ניתוחים מהטלפון, צור מפתח: בתמונת הפרופיל ב־GitHub <span class="kbd">Settings</span>, <span class="kbd">Developer settings</span>, <span class="kbd">Personal access tokens</span>, <span class="kbd">Fine-grained tokens</span>, <span class="kbd">Generate new token</span>. ב־Repository access בחר רק את <span class="kbd">stock-radar</span>, ובהרשאות תן ל־<span class="kbd">Actions</span> את <span class="kbd">Read and write</span>. העתק את המפתח והדבק אותו למעלה בחיבור לענן.</li>
          </ol></div>
        <div class="card stack" style="gap:8px"><h3>התראות לטלגרם</h3>
          <p style="margin:0">בטלגרם פתח שיחה עם <span class="kbd">@BotFather</span>, שלח <span class="kbd">/newbot</span> ובחר שם. תקבל טוקן. שלח הודעה כלשהי לבוט החדש, ואז פתח בדפדפן <span class="kbd">api.telegram.org/bot&lt;הטוקן&gt;/getUpdates</span> והעתק את המספר שמופיע אחרי <span class="kbd">"chat":{"id":</span>.</p>
          <p style="margin:0">ב־GitHub הוסף שני Secrets כמו בשלב 4: <span class="kbd">TELEGRAM_BOT_TOKEN</span> ו־<span class="kbd">TELEGRAM_CHAT_ID</span>. מעכשיו תקבל כל בוקר סיכום של הבחירות.</p></div>
        <div class="card stack" style="gap:6px"><h3>על המערכת</h3>
          <p style="margin:0" class="small">מחירים, אנליסטים, שורט, אופציות וחדשות – Yahoo Finance · קניות מנהלים ותאריכי דוחות – SEC · מאקרו – FRED · מלחמות ומתיחות – GDELT · מדינות – IMF והבנק העולמי · חוזים ממשלתיים – USASpending.gov</p>
          <p style="margin:0" class="small muted">המערכת היא כלי עזר לסינון ולמחקר, לא ייעוץ השקעות. ציון גבוה פירושו סיכוי טוב מהממוצע לפי הנתונים, לא הבטחה. מניות עם פוטנציאל לעלייה חדה יכולות גם לרדת חדה.</p></div>
      </div>`;
  }

  // ------------------------------------------------------------------ GitHub cloud runs
  function ghRepo() {
    const saved = store.get('repo');
    if (saved && saved.owner) return saved;
    if (/\.github\.io$/i.test(location.hostname)) {
      const owner = location.hostname.split('.')[0];
      const seg = location.pathname.split('/').filter(Boolean)[0];
      return { owner, repo: seg || `${owner}.github.io` };
    }
    return null;
  }
  const cloudReady = () => !PREVIEW && !!store.get('token') && !!ghRepo();
  async function gh(path, opts = {}) {
    const headers = { Accept: 'application/vnd.github+json', Authorization: 'Bearer ' + store.get('token', ''), 'X-GitHub-Api-Version': '2022-11-28' };
    if (opts.body) headers['Content-Type'] = 'application/json';
    let r;
    try { r = await fetch('https://api.github.com' + path, { ...opts, headers }); }
    catch (e) { throw new Error('אין חיבור לאינטרנט'); }
    if (r.status === 204) return null;
    if (!r.ok) {
      const map = { 401: 'המפתח לא תקין או שפג תוקפו', 403: 'למפתח אין הרשאה מתאימה (צריך Actions: Read and write)', 404: 'המאגר או קובץ ההרצה לא נמצאו. בדוק את שם המאגר ושהמפתח מורשה עליו', 422: 'GitHub דחה את הבקשה. ודא שקובץ radar.yml הועתק כמו שהוא' };
      throw new Error(map[r.status] || `GitHub החזיר שגיאה ${r.status}`);
    }
    return r.json();
  }
  async function saveConnection() {
    const repoTxt = (document.getElementById('repo').value || '').trim().replace(/^https?:\/\/github\.com\//, '').replace(/\/$/, '');
    const tok = (document.getElementById('tok').value || '').trim();
    const m = repoTxt.match(/^([\w.-]+)\/([\w.-]+)$/);
    if (m) store.set('repo', { owner: m[1], repo: m[2] }); else store.del('repo');
    if (tok) store.set('token', tok); else store.del('token');
    const repo = ghRepo();
    if (!repo || !tok) { store.set('conn', { ok: false, text: 'מלא את שם המאגר ואת המפתח' }); renderSheet(); return; }
    try {
      const info = await gh(`/repos/${repo.owner}/${repo.repo}`);
      store.set('branch', info.default_branch || 'main');
      await gh(`/repos/${repo.owner}/${repo.repo}/actions/workflows/radar.yml`);
      store.set('conn', { ok: true, text: `מחובר ל־${repo.owner}/${repo.repo}` });
    } catch (e) { store.set('conn', { ok: false, text: e.message }); }
    renderSheet();
    render();
  }
  async function startRun(mode, symbols) {
    if (!cloudReady()) { openSettings(); return; }
    if (state.job) { toast('כבר רצה פעולה בענן. חכה שתסתיים.'); return; }
    const repo = ghRepo();
    try {
      await gh(`/repos/${repo.owner}/${repo.repo}/actions/workflows/radar.yml/dispatches`, {
        method: 'POST', body: JSON.stringify({ ref: store.get('branch', 'main'), inputs: { mode, symbols: symbols || '' } }),
      });
    } catch (e) { toast(e.message); return; }
    state.job = { mode, symbols: symbols || '', since: Date.now(), state: 'queued' };
    store.set('job', state.job);
    toast(mode === 'ticker' ? `הניתוח של ${symbols} התחיל. זה לוקח כמה דקות.` : mode === 'telegram' ? 'המשיכה מהטלגרם התחילה בענן. 3 עד 5 דקות.' : 'ההרצה התחילה בענן.');
    render();
    pollJob();
  }
  let pollT = null;
  async function pollJob() {
    clearTimeout(pollT);
    const job = state.job;
    if (!job || PREVIEW) return;
    const repo = ghRepo();
    if (!repo || !store.get('token')) return;
    if (Date.now() - job.since > 3 * 3600e3) { finishJob(false, 'ההרצה לא הסתיימה אחרי 3 שעות'); return; }
    try {
      const data = await gh(`/repos/${repo.owner}/${repo.repo}/actions/workflows/radar.yml/runs?event=workflow_dispatch&per_page=5`);
      const run = (data.workflow_runs || []).find((x) => new Date(x.created_at).getTime() >= job.since - 90e3);
      if (run) {
        job.run = run.id; job.url = run.html_url; job.state = run.status;
        store.set('job', job);
        if (run.status === 'completed') { finishJob(run.conclusion === 'success', null, run.conclusion); return; }
      }
    } catch (e) { /* try again later */ }
    if (state.tab === 'today' && !state.sheet) render();
    else if (state.sheet && state.sheet.kind === 'telegram') renderSheet(true);
    pollT = setTimeout(pollJob, 15000);
  }
  async function finishJob(ok, reason, conclusion) {
    const job = state.job;
    state.job = null;
    store.del('job');
    if (savedGet().dirty) scheduleSync();
    await loadAll();
    if (!ok && conclusion === 'cancelled') { toast('ההרצה בוטלה ב־GitHub (ריצה אחרת תפסה את מקומה בתור). אפשר להפעיל שוב.'); return; }
    if (!ok) { toast(reason || (state.status && state.status.message) || 'ההרצה בענן נכשלה'); return; }
    if (job.mode === 'ticker') {
      const sym = (job.symbols || '').split(/\s+/)[0];
      toast(`הניתוח של ${sym} מוכן`);
      state.tab = 'search';
      render();
      findTicker(sym);
    } else if (job.mode === 'train') {
      toast('המודל אומן. סריקה חדשה עם הסיכויים מתחילה לבד ותסתיים בעוד כשעה.');
      state.tab = 'perf'; render();
    } else if (job.mode === 'telegram') {
      await loadTg();
      const t = state.tg;
      const ran = t && t.mode === 'manual' && new Date(t.updated).getTime() >= job.since - 120e3;
      toast(ran ? `המשיכה מהטלגרם הסתיימה: ${t.posts} הודעות`
        : 'המשיכה לא רצה: ריצה אחרת בענן תפסה את מקומה בתור. הנתונים עודכנו מהריצה האחרונה – אפשר ללחוץ שוב.');
      openTelegram();
    } else toast(job.mode === 'test-alert' ? 'הודעת הבדיקה נשלחה' : 'ההרצה הסתיימה והנתונים עודכנו');
  }

  // ------------------------------------------------------------------ shell
  function loader() { return `<div class="loader"><div class="sweep"></div><span>טוען את הסריקה…</span></div>`; }
  const navTabs = () => (state.view === 'simple'
    ? [['today', 'היום'], ['saved', 'שמורות'], ['results', 'תוצאות'], ['search', 'חיפוש']]
    : [['today', 'היום'], ['saved', 'שמורות'], ['market', 'שוק'], ['perf', 'ביצועים'], ['learn', 'למידה'], ['search', 'חיפוש']]);
  const TABS = ['today', 'saved', 'market', 'perf', 'learn', 'results', 'search'];
  function setView(v) {
    state.view = v;
    store.set('view', v);
    if (v === 'simple' && !['today', 'saved', 'results', 'search'].includes(state.tab)) state.tab = 'today';
    if (v === 'full' && state.tab === 'results') state.tab = 'today';
    const sheet = state.sheet;
    shell();
    state.sheet = sheet;
    render();
  }
  function shell() {
    app.innerHTML = `
      <header class="topbar"><div class="topbar-in">
        <div class="brand">${RADAR_MARK}<h1>רדאר מניות</h1></div>
        <button class="day-btn" data-act="days" id="day-btn" aria-label="בחירת יום">${ICON.cal}<span id="day-lbl">–</span></button>
        <button class="icon-btn" data-act="guide" aria-label="איך קוראים את הרדאר">${ICON.help}</button>
        <button class="icon-btn" data-act="settings" aria-label="הגדרות">${ICON.gear}</button>
      </div></header>
      <main id="main"></main>
      <nav class="nav" aria-label="ניווט"><div class="nav-in">
        ${navTabs().map(([k, l]) => `<button data-tab="${k}">${ICON[k]}<span>${l}</span>${k === 'saved' ? '<b class="nav-badge" hidden></b>' : ''}</button>`).join('')}
      </div></nav>
      <section class="sheet" id="sheet" hidden></section>
      <div id="modal-root"></div>
      <div class="toast" id="toast" role="status" hidden></div>`;
  }
  function render() {
    charts.clear();
    const main = document.getElementById('main');
    const views = { today: viewToday, saved: viewSaved, market: viewMarket, perf: viewPerf, learn: viewLearn, results: viewResults, search: viewSearch };
    main.innerHTML = (views[state.tab] || viewToday)();
    const badge = document.querySelector('.nav-badge');
    if (badge) { const n = savedGet().list.length; badge.textContent = n; badge.hidden = !n; }
    document.querySelectorAll('.nav button').forEach((b) => b.setAttribute('aria-current', b.dataset.tab === state.tab ? 'page' : 'false'));
    const lbl = document.getElementById('day-lbl');
    if (lbl) lbl.textContent = state.data ? dm(state.viewing || state.data.date) : '–';
    drawCharts(main);
    if (state.sheet) renderSheet(true);
  }
  function renderSheet(keepScroll) {
    const el = document.getElementById('sheet');
    if (!state.sheet) { el.hidden = true; el.innerHTML = ''; document.body.style.overflow = ''; return; }
    const top = el.scrollTop;
    el.innerHTML = state.sheet.kind === 'detail' ? (state.view === 'simple' && !state.sheet.full ? viewDetailSimple() : viewDetail())
      : state.sheet.kind === 'guide' ? viewGuide() : state.sheet.kind === 'telegram' ? viewTelegram() : viewSettings();
    el.hidden = false;
    document.body.style.overflow = 'hidden';
    drawCharts(el);
    el.scrollTop = keepScroll ? top : 0;
  }
  let sheetPushed = false;
  function pushSheet(replace) {
    try {
      if (replace && sheetPushed) history.replaceState({ sheet: 1 }, '');
      else { history.pushState({ sheet: 1 }, ''); sheetPushed = true; }
    } catch (e) { sheetPushed = false; }
  }
  function closeSheet() {
    if (sheetPushed) { try { history.back(); return; } catch (e) { /* fall through */ } }
    state.sheet = null; renderSheet();
  }
  window.addEventListener('popstate', () => { sheetPushed = false; if (state.sheet) { state.sheet = null; renderSheet(); } });

  let toastT;
  function toast(text) {
    const el = document.getElementById('toast');
    el.textContent = text; el.hidden = false;
    clearTimeout(toastT); toastT = setTimeout(() => { el.hidden = true; }, 4500);
  }

  function openDays() {
    const days = (state.index && state.index.days) || [];
    const root = document.getElementById('modal-root');
    const body = state.demo || !days.length
      ? '<p class="muted">היסטוריית הסריקות תופיע כאן אחרי כמה ימים של סריקות אמיתיות.</p>'
      : `<div class="rows">${days.slice(0, 60).map((d) => `<button class="row" data-day="${esc(d.date)}"><span class="grow"><b>${dmy(d.date)}</b>
          <span class="nm">${d.picks.length ? d.picks.join(' · ') : 'אין בחירות'}</span></span><span class="muted small">${esc(d.label || '')}</span></button>`).join('')}</div>`;
    root.innerHTML = `<div class="modal" data-act="modal-bg"><div class="modal-card" role="dialog" aria-label="סריקות קודמות"><h3 style="margin-bottom:8px">סריקות קודמות</h3>${body}</div></div>`;
  }
  async function loadDay(day) {
    document.getElementById('modal-root').innerHTML = '';
    const latest = state.data && !state.viewing ? state.data.date : null;
    if (day === latest) return;
    const d = await getJSON(`history/${day}.json`);
    if (!d) { toast('לא הצלחתי לטעון את הסריקה מהתאריך הזה'); return; }
    state.data = d; state.viewing = day; state.tab = 'today';
    render();
    window.scrollTo(0, 0);
  }

  // ------------------------------------------------------------------ events
  document.addEventListener('click', async (e) => {
    const t = e.target.closest('[data-tab],[data-open],[data-act],[data-run],[data-period],[data-day]');
    if (!t) return;
    if (t.dataset.tab) {
      state.tab = t.dataset.tab; state.searchMsg = null;
      render(); window.scrollTo(0, 0);
      try { history.replaceState(history.state, '', '#' + state.tab); } catch (err) { /* ignore */ }
      return;
    }
    if (t.dataset.open != null && state.data) {
      const r = state.data.results[+t.dataset.open];
      openDetail(r, { spark_dates: state.data.spark_dates, date: state.data.date, demo: state.demo, spy_closes: state.data.spy_closes });
      return;
    }
    if (t.dataset.period) { state.sheet.period = +t.dataset.period; renderSheet(true); return; }
    if (t.dataset.day) { loadDay(t.dataset.day); return; }
    if (t.dataset.run) { startRun(t.dataset.run, t.dataset.sym); return; }
    const act = t.dataset.act;
    if (act === 'close') closeSheet();
    else if (act === 'settings') { if (state.sheet) { state.sheet = { kind: 'settings' }; renderSheet(); pushSheet(true); } else openSettings(); }
    else if (act === 'days') openDays();
    else if (act === 'guide') openGuide();
    else if (act === 'seen') { store.set('seen', APP_VERSION); render(); }
    else if (act === 'pm-all') { state.pmAll = true; render(); }
    else if (act === 'view-full') setView('full');
    else if (act === 'view-simple') setView('simple');
    else if (act === 'detail-full') { state.sheet.full = true; renderSheet(); }
    else if (act === 'open-learn') { setView('full'); state.tab = 'learn'; render(); }
    else if (act === 'open-tgs') openTelegram();
    else if (act === 'save') toggleSave(t.dataset.t);
    else if (act === 'unsave-ask') { state.unsaveAsk = t.dataset.t; render(); }
    else if (act === 'unsave') toggleSave(t.dataset.t);
    else if (act === 'unsave-no') { state.unsaveAsk = null; render(); }
    else if (act === 'open-saved') {
      const info = state.savedInfo[t.dataset.t];
      if (info) openDetail(info.r, { spark_dates: info.spark_dates, date: info.date, demo: info.demo, spy_closes: info.spy_closes });
    }
    else if (act === 'tg-howto') { state.sheet.howto = true; renderSheet(true); }
    else if (act === 'tg-all') { state.sheet.allTg = true; renderSheet(true); }
    else if (act === 'pm-show') { state.pmShow = t.dataset.k || 'all'; state.pmAll = false; render(); }
    else if (act === 'save-sizing') saveSizing();
    else if (act === 'settings-sizing') { state.sheet = { kind: 'settings', anchor: 'sizing' }; renderSheet(); pushSheet(true); setTimeout(() => { const el = document.getElementById('sizing'); if (el) el.scrollIntoView({ block: 'start' }); }, 30); }
    else if (act === 'modal-bg' && e.target === t) document.getElementById('modal-root').innerHTML = '';
    else if (act === 'today-latest') loadAll();
    else if (act === 'find') { state.tab = 'search'; render(); findTicker(t.dataset.sym); }
    else if (act === 'save-conn') saveConnection();
    else if (act === 'forget') { store.del('token'); store.del('conn'); renderSheet(true); render(); }
    else if (act === 'install' && state.installEvt) { state.installEvt.prompt(); state.installEvt = null; renderSheet(true); }
    else if (act === 'copy-wf' || act === 'copy-name') {
      const msg = document.getElementById('copy-msg');
      const text = act === 'copy-wf' ? WORKFLOW : '.github/workflows/radar.yml';
      try { await navigator.clipboard.writeText(text); msg.textContent = act === 'copy-wf' ? 'התוכן הועתק. עכשיו הדבק אותו ב־GitHub.' : 'שם הקובץ הועתק.'; }
      catch (err) {
        if (act === 'copy-wf') {
          const pre = document.getElementById('wf'); const range = document.createRange(); range.selectNodeContents(pre);
          const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
          msg.textContent = 'הטקסט סומן. לחץ "העתק" בתפריט של הטלפון.';
        } else msg.textContent = 'לא הצלחתי להעתיק. הקלד את השם ידנית.';
      }
    }
  });
  document.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('article[data-open]')) { e.preventDefault(); e.target.click(); }
    if (e.key === 'Escape') { if (document.getElementById('modal-root').innerHTML) document.getElementById('modal-root').innerHTML = ''; else if (state.sheet) closeSheet(); }
  });
  document.addEventListener('submit', (e) => {
    if (e.target.dataset.form === 'search') { e.preventDefault(); findTicker(e.target.sym.value); }
  });
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); state.installEvt = e; });
  window.addEventListener('hashchange', () => {
    const k = (location.hash || '').slice(1);
    if (k === 'setup' || k === 'settings') openSettings(k === 'setup' ? 'setup' : null);
    else if (k === 'guide') openGuide();
    else if (TABS.includes(k) && k !== state.tab) { state.tab = k; render(); }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && state.job) pollJob(); });

  // ------------------------------------------------------------------ start
  shell();
  const h = (location.hash || '').slice(1);
  if (TABS.includes(h)) state.tab = h;
  render();
  loadAll().then(() => {
    if (h === 'setup' || h === 'settings') openSettings(h === 'setup' ? 'setup' : null);
    else if (h === 'guide') openGuide();
    if (state.job) pollJob();
    else maybeAutoScan();
  });
  if (!PREVIEW && 'serviceWorker' in navigator && location.protocol === 'https:') {
    // when an updated version of the app takes over, reload once so the new screens show right away
    const hadController = !!navigator.serviceWorker.controller;
    let reloaded = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (hadController && !reloaded && !state.sheet) { reloaded = true; location.reload(); }
    });
    navigator.serviceWorker.register('sw.js').catch(() => { /* offline support is optional */ });
  }
})();
