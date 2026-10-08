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
    today: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6-6"/><circle cx="15.5" cy="9" r="1.2" fill="currentColor" stroke="none"/></svg>',
    market: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.6 3.8 5.6 3.8 9s-1.2 6.4-3.8 9c-2.6-2.6-3.8-5.6-3.8-9S9.4 5.6 12 3z"/></svg>',
    perf: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 20h18"/><path d="M4 16l5-5 4 3 7-8"/><path d="M15 6h5v5"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
    back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
    cal: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/></svg>',
  };
  const RADAR_MARK = '<svg class="radar-mark" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="14" style="fill:var(--accent-soft)"/><circle cx="16" cy="16" r="14" fill="none" style="stroke:var(--accent)" stroke-width="1.5"/><circle cx="16" cy="16" r="8.5" fill="none" style="stroke:var(--accent)" stroke-width="1" opacity=".55"/><path d="M16 16L16 2A14 14 0 0 1 28.1 9z" style="fill:var(--accent)" opacity=".35"/><path d="M16 16L28.1 9" style="stroke:var(--accent)" stroke-width="1.6" stroke-linecap="round"/><circle cx="21.5" cy="11.5" r="2" style="fill:var(--good)"/></svg>';

  // ------------------------------------------------------------------ state
  const state = {
    tab: 'today', data: null, demo: false, status: null, index: null, viewing: null, loading: true,
    backtest: undefined, model: undefined, tickers: undefined, search: '', searchMsg: null, job: store.get('job'),
    installEvt: null, sheet: null,
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
    state.tickers = undefined;
    state.loading = false;
    render();
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

  // ------------------------------------------------------------------ views: today
  function statusBanners() {
    const out = [];
    const job = state.job;
    if (job) {
      const what = job.mode === 'ticker' ? `ניתוח ${job.symbols}` : job.mode === 'backtest' ? 'בדיקה לאחור' : job.mode === 'test-alert' ? 'הודעת בדיקה' : job.mode === 'train' ? 'אימון מודל הסיכוי' : 'סריקה';
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
    if (state.viewing) {
      out.push(banner('warn', `מוצגת הסריקה מ־${dmy(state.viewing)}`, '', '<div style="margin-top:6px"><button class="btn small ghost" data-act="today-latest">חזרה לסריקה האחרונה</button></div>'));
    }
    if (state.demo) {
      out.push(banner('info', 'נתוני דמו', PREVIEW
        ? 'זו תצוגה מקדימה עם מניות בדויות (DMO). אחרי ההתקנה יופיעו כאן מניות אמיתיות מהסריקה היומית.'
        : 'המניות כאן בדויות. הנתונים האמיתיים יופיעו אחרי שהסריקה הראשונה בענן תסתיים.'));
    } else if (state.data && !state.viewing && daysSince(state.data.date) > 4) {
      out.push(banner('warn', 'הנתונים לא התעדכנו כמה ימים', `הסריקה האחרונה מ־${dmy(state.data.date)}. בדוק בהגדרות או בלשונית Actions ב־GitHub.`));
    }
    return out.join('');
  }

  // chance (from the tested model) that the stock rises 30% before its stop is hit
  function probLine(r) {
    if (!isNum(r.prob) || !isNum(r.prob_base) || !r.prob_base) return '';
    const ratio = r.prob / r.prob_base;
    const tone = ratio >= 1.3 ? 'good' : ratio <= 0.7 ? 'bad' : '';
    const w = Math.min(100, (r.prob / Math.max(0.5, r.prob * 1.2)) * 100), bw = Math.min(100, (r.prob_base / Math.max(0.5, r.prob * 1.2)) * 100);
    return `<div class="prob ${tone}" role="img" aria-label="סיכוי ${pct(r.prob)} לעומת ממוצע ${pct(r.prob_base)}">
      <div class="prob-top"><span>סיכוי לעלות 30% לפני הסטופ</span><b>${pct(r.prob)}</b></div>
      <div class="prob-track"><i style="width:${w.toFixed(1)}%"></i><span class="base" style="inset-inline-start:${bw.toFixed(1)}%"></span></div>
      <div class="prob-sub">ממוצע כל המניות: ${pct(r.prob_base)}${ratio >= 1.15 ? ` · פי ${ratio.toFixed(1)}` : ratio <= 0.85 ? ' · נמוך מהממוצע' : ' · בערך כמו הממוצע'}</div></div>`;
  }

  function pickCard(r, i) {
    const chg = changeOver(r.closes, 63);
    const [rc, rl] = riskLevel(r.risk);
    const warn = (r.cons || [])[0];
    return `<article class="card pick" role="button" tabindex="0" data-open="${i}" aria-label="${esc(r.ticker)} ${esc(r.name)}">
      <div class="pick-head">
        <div class="pick-id"><span class="tk">${esc(r.ticker)}</span><div class="nm">${esc(r.name)}</div>
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
        ${r.plan && r.plan.stop ? `<span class="chip">סטופ <b class="num">${price(r.plan.stop)}</b></span>` : ''}</div>
    </article>`;
  }

  function viewToday() {
    const d = state.data;
    if (state.loading) return loader();
    if (!d) {
      return `<div class="stack">${statusBanners()}<div class="card empty"><div class="loader" style="padding:16px 0"><div class="sweep"></div></div>
        <h3>הסריקה הראשונה עוד לא הסתיימה</h3>
        <p>המערכת סורקת עכשיו מניות אמיתיות בבורסה האמריקאית: מחירים, קניות מנהלים, אנליסטים, חדשות, מלחמות ומאקרו. בפעם הראשונה זה לוקח 30 עד 60 דקות. אחר כך הסריקה רצה לבד כל בוקר.</p>
        <p class="small">אם עברו יותר משעתיים ועדיין אין תוצאות, בדוק את לשונית Actions במאגר ב־GitHub.</p></div></div>`;
    }
    const res = d.results || [];
    const picks = res.map((r, i) => [r, i]).filter(([r]) => r.pick);
    const watch = res.map((r, i) => [r, i]).filter(([r]) => !r.pick).slice(0, 12);
    const m = d.market || {};
    const mk = m.score > 0.25 ? 'good' : m.score < -0.25 ? 'bad' : '';
    const hot = (d.themes || []).filter((t) => t.ratio >= 1.25).slice(0, 3);
    return `<div class="stack">
      ${statusBanners()}
      <div class="strip">
        <button class="pill ${mk}" data-tab="market"><span class="sw"></span>שוק ${esc(m.label || '')}</button>
        ${hot.map((t) => `<button class="pill warn" data-tab="market"><span class="sw"></span>${esc(t.he)} · פי ${t.ratio.toFixed(1)}</button>`).join('')}
      </div>
      <div class="section-title"><h2>המניות שנבחרו</h2><span class="muted small">${picks.length} מתוך ${d.counts ? d.counts.deep : res.length} שנבדקו לעומק</span></div>
      ${picks.length ? picks.map(([r, i]) => pickCard(r, i)).join('') : '<div class="card empty">אף מניה לא עברה היום את סף הפוטנציאל והסיכון. זה תקין – לפעמים הדבר הנכון הוא לחכות.</div>'}
      ${watch.length ? `<div class="section-title"><h2>קרובות לסף</h2><span class="muted small">לפי ציון הפוטנציאל</span></div>
      <div class="card rows">${watch.map(([r, i]) => `<button class="row" data-open="${i}"><span class="score-chip">${Math.round(r.upside)}</span>
        <span class="grow"><span class="tk">${esc(r.ticker)}</span><span class="nm">${esc(r.name)}</span></span>
        <span class="chip ${riskLevel(r.risk)[0]}">סיכון ${Math.round(r.risk)}</span></button>`).join('')}</div>` : ''}
      <p class="muted small" style="text-align:center">סריקה מ־${dmy(d.date)} · נסרקו ${d.counts ? d.counts.universe : '–'} מניות, ${d.counts ? d.counts.screened : '–'} עברו את מסנני הנזילות<br>כלי עזר למחקר, לא ייעוץ השקעות.</p>
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
      <p class="muted small">מקורות: מאקרו – FRED · מלחמות ומתיחות – GDELT · מדינות – IMF והבנק העולמי.</p>
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
          <span class="nm">${x.status === 'pending' ? 'תיפתח במחיר הפתיחה של יום המסחר הבא' : `כניסה ${price(x.entry)} · עכשיו ${price(x.last)} · סטופ ${price(x.stop)}`}</span></span>
          ${x.status === 'pending' ? '<span class="chip">ממתינה</span>' : `<span class="chip ${x.ret >= 0 ? 'good' : 'bad'}"><b>${pct(x.ret, 1, true)}</b></span>`}</div>`).join('');
      const closedRows = (p.closed_positions || []).slice(0, 8).map((x) => `<div class="row" style="cursor:default"><span class="grow"><span class="tk">${esc(x.ticker)}</span>
          <span class="nm">${dm(x.since)} עד ${dm(x.until)} · ${x.reason === 'stop' ? 'נסגרה בסטופ' : 'נסגרה בסוף תקופת ההחזקה'}</span></span>
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
        <p class="muted small" style="margin:8px 0 0">אות שעזר מקבל משקל גבוה יותר בציון, אות שהטעה מקבל משקל נמוך יותר.</p></div>`;
    }
    return `<div class="stack">${state.demo ? banner('info', 'נתוני דמו', 'הביצועים במסך הזה מחושבים על שוק מדומה ואינם אומרים דבר על השוק האמיתי.') : ''}${viewModel()}${paper}${learn}${viewBacktest()}</div>`;
  }

  function viewModel() {
    if (state.model === undefined) { loadModel(); return `<div class="card"><h3>מודל הסיכוי</h3><p class="muted">טוען…</p></div>`; }
    const run = cloudReady() ? '<button class="btn small ghost" data-run="train">אמן מחדש</button>' : '';
    const md = state.model;
    if (!md) {
      return `<div class="card"><h3>מודל הסיכוי</h3><p class="muted">המודל לומד מ־5 שנות היסטוריה אמיתית אילו מניות עלו 30% לפני שהסטופ נפגע, ונבדק על שנים שלא ראה. האימון מתחיל לבד אחרי עדכון האפליקציה ולוקח 30 עד 60 דקות. אחריו יופיע כאן איך הוא הצליח בבדיקה.</p>${run}</div>`;
    }
    const v = md.validation || {};
    const cov = md.coverage || {};
    const q = isNum(v.auc) ? Math.max(0, Math.min(1, (v.auc - 0.52) / 0.08)) : 0;
    const passed = q > 0 && (v.top_lift || 0) >= 1.1;
    const dec = v.deciles || [];
    const maxRate = Math.max(0.01, ...dec.map((x) => x.rate || 0));
    const bars = dec.map((x, i) => `<div class="dec-col" title="קבוצה ${i + 1}: ${pct(x.rate)}"><span class="dec-v">${pct(x.rate)}</span><i style="height:${Math.max(3, (x.rate / maxRate) * 100).toFixed(0)}%"></i></div>`).join('');
    const ww = md.what_worked || [];
    const good = ww.filter((x) => x.lift >= 1.1).slice(0, 6);
    const bad = ww.filter((x) => x.lift <= 0.9).slice(-4).reverse();
    const wwRow = (x) => divRow(x.text, Math.max(-1, Math.min(1, (x.lift - 1) / 0.75)), pct(x.rate, 1), x.lift ? `פי ${x.lift.toFixed(1)} מהממוצע` : '');
    const years = (v.by_year || []).filter((y) => isNum(y.top));
    return `<div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><h3>מודל הסיכוי</h3>${run}</div>
      <p class="muted small" style="margin:2px 0 10px">אומן ב־${dmy(md.trained)}${md.demo ? ' · דמו' : ''} · ${md.n_tickers} מניות · ${md.years} שנים · ${Number(md.n_rows || 0).toLocaleString('en-US')} תצפיות</p>
      ${passed ? '' : banner('warn', 'המודל לא עבר את הבדיקה', 'בתקופה שהוא לא ראה הוא לא היה טוב מספיק מהממוצע, ולכן הוא לא משפיע כרגע על הציונים. הוא יאומן מחדש לבד בעוד חודש.')}
      <p class="small" style="margin:0 0 8px">השאלה: מניה נקנית במחיר הפתיחה של היום שאחרי, עם סטופ לפי התנודה שלה. האם היא תעלה 30% תוך 3 חודשים, לפני שתרד לסטופ?</p>
      <p class="small" style="margin:0 0 10px">הבדיקה ההוגנת: המודל למד רק משנים מוקדמות, ונבדק על ${dmy(v.test_start)} עד ${dmy(v.test_end)} – תקופה שלא ראה.</p>
      <div class="duo"><div><div class="eyebrow">${v.top_n || 10} המובילות של המודל בכל שבוע</div><span class="big sm ${passed ? 'up' : ''}">${pct(v.top_rate)}</span><span class="muted small">הגיעו ל־30% לפני הסטופ</span></div>
        <div><div class="eyebrow">כל המניות</div><span class="big sm" style="color:var(--ink-2)">${pct(v.base)}</span><span class="muted small">הממוצע, לשם השוואה</span></div></div>
      <div class="chips" style="margin:10px 0">${isNum(v.top_lift) ? `<span class="chip ${passed ? 'good' : ''}">פי <b>${v.top_lift.toFixed(1)}</b> מהממוצע</span>` : ''}
        ${isNum(v.tech_top_rate) ? `<span class="chip">הסינון הטכני הישן: <b>${pct(v.tech_top_rate)}</b></span>` : ''}
        ${isNum(v.auc) ? `<span class="chip">AUC <b class="num">${v.auc.toFixed(2)}</b></span>` : ''}</div>
      ${years.length ? `<div class="eyebrow" style="margin:4px 0 4px">שנה אחרי שנה (המובילות מול הממוצע)</div><div class="chips">${years.map((y) => `<span class="chip ${y.top > y.base ? 'good' : 'bad'}">${y.year}: <b>${pct(y.top)}</b> מול ${pct(y.base)}</span>`).join('')}</div>` : ''}
      ${dec.length ? `<div class="eyebrow" style="margin:14px 0 4px">10 קבוצות לפי הסיכוי שהמודל נתן – כמה באמת הגיעו ל־30%</div>
        <div class="dec">${bars}</div><div class="dec-axis"><span>סיכוי נמוך</span><span>סיכוי גבוה</span></div>
        <p class="muted small" style="margin:4px 0 0">אם המודל טוב, העמודות עולות מקבוצה לקבוצה.</p>` : ''}
      ${good.length || bad.length ? `<div class="eyebrow" style="margin:14px 0 4px">מה עבד ב־5 השנים האחרונות (כל אחד לבד)</div>
        ${divHead('פחות מהממוצע', 'יותר מהממוצע')}${good.map(wwRow).join('')}${bad.map(wwRow).join('')}
        <p class="muted small" style="margin:6px 0 0">המספר = כמה מהמניות במצב הזה הגיעו ל־30% לפני הסטופ. מניות תנודתיות מגיעות לשם יותר – אבל גם יורדות מהר, ולכן תמיד להסתכל גם על ציון הסיכון.</p>` : ''}
      <p class="muted small" style="margin:10px 0 0">AUC מודד כמה טוב המודל מדרג: 0.5 = ניחוש, 0.6 ומעלה = כוח ניבוי שימושי בשוק ההון. הבדיקה כוללת רק מניות שנסחרות היום (מניות שנמחקו חסרות), ולכן קצת אופטימית.${cov.insider === false ? ' באימון הזה נתוני המנהלים ההיסטוריים לא היו זמינים.' : ''} המודל מתאמן מחדש לבד כל חודש.</p></div>`;
  }
  async function loadModel() {
    const md = await getJSON(state.demo ? 'demo/model.json' : 'model.json');
    state.model = md || null;
    if (state.tab === 'perf') render();
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
    if (i >= 0) { openDetail(res[i], { spark_dates: state.data.spark_dates, date: state.data.date, demo: state.demo }); return; }
    const f = await getJSON(`${state.demo ? 'demo/' : ''}tickers/${encodeURIComponent(sym)}.json`);
    if (f && f.result) { openDetail(f.result, { spark_dates: f.spark_dates, date: f.date, demo: f.demo }); return; }
    state.searchMsg = { kind: 'missing', sym };
    render();
  }

  // ------------------------------------------------------------------ detail sheet
  function openDetail(r, ctx, replace) {
    state.sheet = { kind: 'detail', r, ctx, period: 63 };
    renderSheet();
    pushSheet(replace);
  }
  function viewDetail() {
    const { r, ctx, period } = state.sheet;
    const dates = ctx.spark_dates || [];
    const closes = r.closes || [];
    const n = Math.min(period, closes.length);
    const v = closes.slice(-n), dts = dates.slice(-n);
    const chg = changeOver(closes, n - 1);
    const [rc, rl] = riskLevel(r.risk);
    const plan = r.plan || {};
    const facts = [
      ['מחיר אחרון', price(r.price)],
      plan.stop ? ['סטופ מוצע', `${price(plan.stop)} (${pct(plan.stop_pct)})`] : null,
      plan.shares ? ['כמות מוצעת', `${plan.shares} מניות`] : null,
      plan.value ? ['שווי הפוזיציה', money(plan.value)] : null,
      plan.risk_amount ? ['הפסד מקסימלי בסטופ', money(plan.risk_amount)] : null,
      isNum(r.move_3m) ? ['תנודה טיפוסית ל־3 חודשים', `±${pct(r.move_3m)}`] : null,
      isNum(r.market_cap) ? ['שווי שוק', money(r.market_cap)] : null,
      r.earnings_date ? ['דוח רבעוני הבא', dmy(r.earnings_date)] : null,
    ].filter(Boolean);
    const links = !ctx.demo && /^[A-Z][A-Z0-9.\-]*$/.test(r.ticker) ? `<div class="links">
      <a class="btn small ghost" href="https://finance.yahoo.com/quote/${encodeURIComponent(r.ticker)}" target="_blank" rel="noopener">Yahoo Finance</a>
      <a class="btn small ghost" href="https://www.tradingview.com/symbols/${encodeURIComponent(r.ticker)}/" target="_blank" rel="noopener">TradingView</a>
      <a class="btn small ghost" href="https://finviz.com/quote.ashx?t=${encodeURIComponent(r.ticker)}" target="_blank" rel="noopener">Finviz</a></div>` : '';
    return `<div class="sheet-bar"><div class="topbar-in">
        <button class="icon-btn" data-act="close" aria-label="חזרה">${ICON.back}</button>
        <div class="brand"><h1 class="tk" style="font:600 19px/1 var(--font-data)">${esc(r.ticker)}</h1></div>
        ${cloudReady() && !ctx.demo ? `<button class="btn small ghost" data-run="ticker" data-sym="${esc(r.ticker)}">${ICON.refresh.replace('<svg', '<svg width="16" height="16"')} נתח מחדש</button>` : ''}
      </div></div>
      <div class="sheet-in">
        ${ctx.demo ? banner('info', 'מניה בדויה (דמו)', 'כל הנתונים כאן לדוגמה בלבד.') : ''}
        <div class="d-head"><div class="grow"><div class="nm">${esc(r.name)}</div>
          <div class="muted small">${esc([r.sector, r.industry, r.country].filter(Boolean).join(' · '))}</div>
          <div class="price-line"><span class="px">${price(r.price)}</span><span class="chg ${chg >= 0 ? 'up' : 'down'}">${pct(chg, 1, true)}</span></div></div></div>
        <div class="card"><div class="rings">${ring(r.upside, 'var(--accent)', 'פוטנציאל עלייה', true)}${ring(r.risk, riskColor(r.risk), `סיכון ${rl}`, true)}
          <div class="small muted" style="align-self:center;flex:1;min-width:0">פוטנציאל: כמה אותות מצביעים לעלייה, בשקלול הביטחון בכל אחד. סיכון: תנודתיות, אירועים קרובים ומצב השוק.</div></div></div>
        ${isNum(r.prob) && r.prob_base ? `<div class="card"><h3>מודל הסיכוי</h3>${probLine(r)}
          <p class="small" style="margin:10px 0 0">מתוך מניות שנראו כמו המניה הזו ב־5 השנים האחרונות (מחיר ומגמה, קניות מנהלים, תגובה לדוחות ומצב השוק), ${pct(r.prob)} עלו 30% תוך 3 חודשים לפני שירדו לסטופ. הממוצע של כל המניות: ${pct(r.prob_base)}.</p>
          <p class="muted small" style="margin:6px 0 0">זה סיכוי, לא הבטחה: גם כשהסיכוי טוב, ברוב המקרים המניה לא תגיע ל־30%. לכן הסטופ וגודל הפוזיציה חשובים. איך המודל נבדק – בלשונית ביצועים.</p></div>` : ''}
        <div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px"><h3>מחיר</h3>
          <div class="seg" role="group" aria-label="תקופה">${[[21, 'חודש'], [63, '3 חודשים'], [130, '6 חודשים']].map(([p, l]) => `<button data-period="${p}" aria-pressed="${p === period}">${l}</button>`).join('')}</div></div>
          ${chartSlot('chart', { series: [{ v, color: 'var(--accent)', area: true, name: '' }], refs: plan.stop ? [{ y: plan.stop, label: 'סטופ', color: 'var(--bad)' }] : [], height: 210, tip: true,
            fmtY: (t) => (t >= 100 ? t.toFixed(0) : t.toFixed(t >= 10 ? 1 : 2)), fmtTip: (x) => price(x), fmtX: (i, full) => (full ? dmy(dts[i]) : dm(dts[i] || '')), label: `גרף מחיר ${r.ticker}` })}
          ${plan.stop ? '<div class="legend" style="margin-top:6px"><span><i></i>מחיר</span><span><i class="dash" style="border-top-color:var(--bad)"></i>סטופ מוצע</span></div>' : ''}</div>
        ${(r.pros || []).length ? `<div class="card"><h3>למה נבחרה</h3><ul class="list pros">${r.pros.map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>` : ''}
        ${(r.cons || []).length ? `<div class="card"><h3>שים לב</h3><ul class="list cons">${r.cons.map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>` : ''}
        <div class="card"><h3>תוכנית מוצעת</h3><p class="muted small" style="margin:2px 0 10px">הסטופ נקבע לפי התנודה הרגילה של המניה, והכמות כך שפגיעה בסטופ תעלה אחוז קבוע מהתיק.</p>
          <div class="facts">${facts.map(([k, val]) => `<div class="fact"><div class="k">${esc(k)}</div><div class="v">${esc(val)}</div></div>`).join('')}</div></div>
        <div class="card"><h3>כל האותות</h3><p class="muted small" style="margin:2px 0 8px">כל אות מקבל ציון בין שלילי לחיובי. אותות בלי נתונים לא מוצגים.</p>
          ${divHead('שלילי', 'חיובי')}${(r.signals || []).map((s) => divRow(s.label, s.score, (s.score >= 0 ? '+' : '') + s.score.toFixed(2))).join('')}</div>
        ${(r.news || []).length ? `<div class="card news"><h3>כותרות אחרונות</h3><p class="muted small" style="margin:2px 0 6px">הכותרות המקוריות, באנגלית</p>
          ${r.news.map((x) => x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener"><span class="lbl">${esc(x.label || '')}</span>${esc(x.title)}</a>` : `<a><span class="lbl">${esc(x.label || '')}</span>${esc(x.title)}</a>`).join('')}</div>` : ''}
        ${links}
        <p class="muted small">נותח ב־${dmy(ctx.date)} · כיסוי נתונים ${pct(r.coverage)} · כלי עזר למחקר, לא ייעוץ השקעות.</p>
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
        ${cloudReady() ? `<div class="card stack" style="gap:10px"><h3>הפעלה ידנית</h3>
          <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn ghost" data-run="scan">הרץ סריקה עכשיו</button><button class="btn ghost" data-run="backtest">הרץ בדיקה לאחור</button><button class="btn ghost" data-run="train">אמן את מודל הסיכוי</button><button class="btn ghost" data-run="test-alert">שלח הודעת בדיקה</button></div>
          <p class="muted small" style="margin:0">סריקה מלאה או אימון של המודל לוקחים 30 עד 60 דקות. אפשר לסגור את האפליקציה בינתיים. המודל מתאמן לבד פעם בחודש.</p></div>` : ''}
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
    toast(mode === 'ticker' ? `הניתוח של ${symbols} התחיל. זה לוקח כמה דקות.` : 'ההרצה התחילה בענן.');
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
        if (run.status === 'completed') { finishJob(run.conclusion === 'success'); return; }
      }
    } catch (e) { /* try again later */ }
    if (state.tab === 'today' && !state.sheet) render();
    pollT = setTimeout(pollJob, 15000);
  }
  async function finishJob(ok, reason) {
    const job = state.job;
    state.job = null;
    store.del('job');
    await loadAll();
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
    } else toast(job.mode === 'test-alert' ? 'הודעת הבדיקה נשלחה' : 'ההרצה הסתיימה והנתונים עודכנו');
  }

  // ------------------------------------------------------------------ shell
  function loader() { return `<div class="loader"><div class="sweep"></div><span>טוען את הסריקה…</span></div>`; }
  function shell() {
    app.innerHTML = `
      <header class="topbar"><div class="topbar-in">
        <div class="brand">${RADAR_MARK}<h1>רדאר מניות</h1></div>
        <button class="day-btn" data-act="days" id="day-btn" aria-label="בחירת יום">${ICON.cal}<span id="day-lbl">–</span></button>
        <button class="icon-btn" data-act="settings" aria-label="הגדרות">${ICON.gear}</button>
      </div></header>
      <main id="main"></main>
      <nav class="nav" aria-label="ניווט"><div class="nav-in">
        ${[['today', 'היום'], ['market', 'שוק'], ['perf', 'ביצועים'], ['search', 'חיפוש']].map(([k, l]) => `<button data-tab="${k}">${ICON[k]}<span>${l}</span></button>`).join('')}
      </div></nav>
      <section class="sheet" id="sheet" hidden></section>
      <div id="modal-root"></div>
      <div class="toast" id="toast" role="status" hidden></div>`;
  }
  function render() {
    charts.clear();
    const main = document.getElementById('main');
    const views = { today: viewToday, market: viewMarket, perf: viewPerf, search: viewSearch };
    main.innerHTML = views[state.tab]();
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
    el.innerHTML = state.sheet.kind === 'detail' ? viewDetail() : viewSettings();
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
      openDetail(r, { spark_dates: state.data.spark_dates, date: state.data.date, demo: state.demo });
      return;
    }
    if (t.dataset.period) { state.sheet.period = +t.dataset.period; renderSheet(true); return; }
    if (t.dataset.day) { loadDay(t.dataset.day); return; }
    if (t.dataset.run) { startRun(t.dataset.run, t.dataset.sym); return; }
    const act = t.dataset.act;
    if (act === 'close') closeSheet();
    else if (act === 'settings') { if (state.sheet) { state.sheet = { kind: 'settings' }; renderSheet(); pushSheet(true); } else openSettings(); }
    else if (act === 'days') openDays();
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
    else if (['today', 'market', 'perf', 'search'].includes(k) && k !== state.tab) { state.tab = k; render(); }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && state.job) pollJob(); });

  // ------------------------------------------------------------------ start
  shell();
  const h = (location.hash || '').slice(1);
  if (['today', 'market', 'perf', 'search'].includes(h)) state.tab = h;
  render();
  loadAll().then(() => {
    if (h === 'setup' || h === 'settings') openSettings(h === 'setup' ? 'setup' : null);
    if (state.job) pollJob();
  });
  if (!PREVIEW && 'serviceWorker' in navigator && location.protocol === 'https:') {
    navigator.serviceWorker.register('sw.js').catch(() => { /* offline support is optional */ });
  }
})();
