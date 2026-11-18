// Pool Strip Reader interface. No framework: screens are template strings and
// every control is wired through data-action attributes (the page's CSP allows
// no inline script).
import { findStrip, readStrip, readStripEitherWay, roundTo, stripPresent, findChartGrid, measureChartGrid, chartCounts, chartGridLayout, chartProblems } from './strip.js';
import { labToCss } from './color.js';

const $ = (selector, root = document) => root.querySelector(selector);
const app = $('#app'), sheet = $('#sheet');
const KEYS = ['fc', 'ta', 'ph', 'cya'];
const STATUS_WORD = { ok: 'In range', low: 'Low', high: 'High', unsure: 'Unclear' };
// Colour never carries a status by itself: low and high also get an arrow, so
// the app reads the same to someone who cannot tell the colours apart.
const STATUS_MARK = { ok: '', low: '↓', high: '↑', unsure: '?' };
const PRODUCT_HELP = {
  chlorine: 'Strength is the available chlorine on the label (600 g/kg = 60%).',
  acid: 'Liquid pool acid is hydrochloric acid, usually 28-33%. Dry acid is sodium bisulphate.',
  alkalinityUp: 'Sodium bicarbonate.', phUp: 'Sodium carbonate.', stabiliser: 'Cyanuric acid.',
};
let S = null;          // latest server state
let tab = 'home';
let scan = null;       // the test being captured
let dipAt = null;      // when the strip left the water
let cam = null;        // the live camera while the light box is in use
let labDraft = null;   // a pool shop's printed report being typed in: its id and the picture of it

// Per-phone choices (which lens, where the rings sit) live on the phone itself:
// they depend on the phone, not on the pool.
const store = {
  get(key, fallback = null) { try { const v = localStorage.getItem('pool-' + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { value === null ? localStorage.removeItem('pool-' + key) : localStorage.setItem('pool-' + key, JSON.stringify(value)); } catch { /* private mode */ } },
};

const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, body) {
  const response = await fetch('api/' + path, body === undefined ? { cache: 'no-store' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) { S = null; renderLogin(); throw new Error(data.error || 'Please sign in.'); }
  if (!response.ok) throw new Error(data.error || 'Something went wrong.');
  for (const chart of [data.chart, data.boxChart]) if (chart) for (const param of Object.values(chart.params)) for (const swatch of param.swatches) swatch.css = labToCss(swatch.lab);
  return data;
}

function toast(text, bad = false) {
  const el = $('#toast');
  el.textContent = text; el.className = 'show' + (bad ? ' bad' : '');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { el.className = ''; }, 3200);
}

// --- Formatting -------------------------------------------------------------
function ago(iso) {
  const minutes = (Date.now() - new Date(iso)) / 60000;
  if (minutes < 2) return 'just now';
  if (minutes < 60) return `${Math.round(minutes)} minutes ago`;
  if (minutes < 36 * 60) { const h = Math.round(minutes / 60); return `${h} hour${h === 1 ? '' : 's'} ago`; }
  const d = Math.round(minutes / 1440); return `${d} days ago`;
}
const dayTime = iso => new Date(iso).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const dateShort = iso => new Date(iso).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
const timeShort = iso => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const dayOnly = iso => new Date(iso).toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'short' });

function shown(key, reading) {
  const param = S.chart.params[key], value = reading.value;
  const text = key === 'ph' ? value.toFixed(1) : String(+value.toFixed(1));
  if (reading.confidence === 'lab') return text;   // a shop's meter gives a figure, not a step on the strip's chart
  const last = param.swatches[param.swatches.length - 1].value, first = param.swatches[0].value;
  if (value >= last) return key === 'ph' ? '≥' + text : text + '+';
  if (value <= first && key === 'ph') return '≤' + text;
  return text;
}
const plain = (key, value) => key === 'ph' ? (+value).toFixed(1) : String(+(+value).toFixed(1));

// Position of a value along the chart, with the swatches evenly spaced like
// the bottle's own chart.
function position(key, value) {
  const sw = S.chart.params[key].swatches, n = sw.length - 1;
  if (value <= sw[0].value) return 0;
  for (let i = 0; i < n; i++) if (value <= sw[i + 1].value) return (i + (value - sw[i].value) / (sw[i + 1].value - sw[i].value)) / n;
  return 1;
}

// Ideal ranges for a set of readings. Only chlorine moves: its minimum follows
// stabiliser, by the rule the server sends (the same one its advice uses).
function idealFor(readings) {
  const ideal = { ...S.settings.ideal }, rule = S.rules.fc, [from, to] = S.rules.cyaPhWindow;
  const cya = readings.cya, ph = readings.ph;
  const known = cya.confidence !== 'poor' && ph.confidence !== 'poor' && ph.value >= from && ph.value <= to;
  const low = Math.min(rule.cap, !known ? rule.stabilisedMin : cya.value < rule.cyaFrom ? rule.plainMin
    : Math.max(rule.stabilisedMin, Math.ceil(cya.value / rule.ratio * 2) / 2));
  ideal.fc = [low, low + rule.span];
  return ideal;
}
const currentIdeal = () => S.current?.ideal || S.settings.ideal;
// A strip in the light box is compared with the chart as the box sees it; any other photo with the built-in chart.
const chartFor = source => (source === 'box' && S.boxChart) || S.chart;

function scaleBar(key, reading, status) {
  const param = S.chart.params[key], sw = param.swatches;
  const stops = sw.map((s, i) => `${s.css} ${(i / (sw.length - 1) * 100).toFixed(1)}%`).join(', ');
  const [lo, hi] = currentIdeal()[key];
  const a = position(key, lo) * 100, b = position(key, hi) * 100, at = position(key, reading.value) * 100;
  const from = position(key, reading.low ?? reading.value) * 100, to = position(key, reading.high ?? reading.value) * 100;
  return `<div class="scale">
    <div class="scale-ideal" style="left:${a}%;width:${b - a}%"><span>aim</span></div>
    <div class="scale-track" style="background:linear-gradient(90deg, ${stops})"></div>
    ${to - from > 1 ? `<div class="scale-range" style="left:${from}%;width:${to - from}%"></div>` : ''}
    <div class="scale-mark ${status}" style="left:${at}%"></div>
    <div class="scale-ends"><span>${plain(key, sw[0].value)}</span><span>${plain(key, sw[sw.length - 1].value)}</span></div>
  </div>`;
}

// --- Screens ----------------------------------------------------------------
function shell(content) {
  const tabs = [['home', 'Today', 'M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z'], ['history', 'History', 'M4 19V5m0 14h16M8 15l3-4 3 2 4-6'], ['settings', 'Settings', 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm0-5v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1']];
  app.innerHTML = `<main class="view">${content}</main>
    <nav class="tabs">${tabs.map(([id, label, path]) => `<button class="tab ${tab === id ? 'on' : ''}" data-action="tab" data-tab="${id}">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg><span>${label}</span></button>`).join('')}
      <button class="fab" data-action="new-test" aria-label="New test"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg><span>Test</span></button>
    </nav>`;
}

// Redraws the current screen. The scrolling pane is rebuilt each time, so its
// position is carried over unless the screen itself changed.
function render(toTop = false) {
  if (!S) return;
  if (S.needsSetup) return setupView();
  if (calibrationDue()) return calibWelcome();
  const top = toTop ? 0 : $('.view')?.scrollTop || 0;
  shell(tab === 'home' ? home() : tab === 'history' ? history() : settingsView());
  $('.view').scrollTop = top;
}

// The light box still to be calibrated: asked once, full screen, on each phone a person uses (straight after
// they describe their pool), and after that a reminder on the Today screen until it is done or they say they
// have no box. The full-screen step is only shown where the live camera can work, which the sidebar of Home
// Assistant is not.
const liveCamera = () => !!navigator.mediaDevices?.getUserMedia;
const askedKey = () => 'calib-asked-' + S.me.username;
let calibAsked = false;   // also kept here, for a browser that will not store anything (private browsing)
const calibrationDue = () => S.needsCalibration && liveCamera() && !calibAsked && !store.get(askedKey());
const calibAnswered = () => { calibAsked = true; store.set(askedKey(), true); };
let firstVisit = false;   // set when the pool was described a moment ago: the chemicals on the Settings screen are still to check
const CHECK_CHEMICALS = 'Now check your chemicals against their labels.';

function calibReminder() {
  if (!S.needsCalibration) return '';
  return `<div class="nudge action"><strong>Your light box is not calibrated yet</strong>
    <p>Until it is, strips read in the box are compared with a colour chart measured in daylight, and read less accurately. It takes about five minutes, once.${liveCamera() ? '' : ' It needs the camera, so open the app from its own address or Home Screen icon to do it.'}</p>
    <div class="nudge-actions">${liveCamera() ? '<button class="primary small" data-action="calib-home">Show me how</button>' : ''}<button class="link" data-action="no-light-box">I don't have a light box</button></div></div>`;
}

function nudgeCards() {
  return calibReminder() + S.insights.nudges.map(n => `<div class="nudge ${n.level}"><strong>${esc(n.title)}</strong><p>${esc(n.text)}</p></div>`).join('');
}

function home() {
  const c = S.current;
  if (!c) return `<header class="hero empty"><div class="wave"></div><h1>Welcome to your pool</h1>
      <p>Dip a strip, take one photo, and get exact amounts for your ${(S.settings.volumeLitres / 1000).toLocaleString()} kL pool.</p>
      <button class="primary big" data-action="new-test">Take the first test</button></header>${nudgeCards()}`;
  const good = !c.issues.length && !c.unsure.length && !c.dosedSince;
  const due = S.insights.nextTestDue ? new Date(S.insights.nextTestDue) : null;
  const dueText = !due ? '' : due < Date.now() ? ' · test due now' : ` · next test ${dayOnly(due.toISOString()).split(',')[0]}`;
  const test = S.tests[0];
  const open = c.steps.filter(s => !s.doneDoseId).length;
  return `<header class="hero ${good ? 'good' : 'attention'}"><div class="wave"></div>
      <span class="badge">${c.dosedSince ? 'Test again' : good ? 'All good' : c.steps.length ? `${open || 'No'} step${open === 1 ? '' : 's'} to do` : c.unsure.length && !c.issues.length ? 'Retake the test' : 'Keep an eye on it'}</span>
      <h1>${esc(c.headline)}</h1><p>Tested ${ago(c.at)}${test.source === 'lab' ? ' at the pool shop' : ''}${dueText}</p></header>
    ${nudgeCards()}
    <section class="readings">${KEYS.map(key => {
      const r = test.readings[key], status = c.status[key], param = S.chart.params[key];
      return `<article class="reading ${status}"><div class="reading-head"><h2>${esc(param.short)}</h2><span class="chip ${status}">${STATUS_WORD[status]}</span></div>
        <div class="reading-value">${shown(key, r)}${status === 'unsure' ? '?' : ''}<small>${esc(param.unit)}</small></div>${scaleBar(key, r, status)}
        <p class="muted">${status === 'unsure' ? 'Poor colour match: a guess, not a reading' : `${test.source === 'lab' ? 'Pool shop · ' : ''}Aim for ${c.ideal[key][0]}–${c.ideal[key][1]}${key === 'fc' && c.chlorineBasis === 'stabilised' ? ' at this stabiliser level' : ''}${r.low !== r.high ? ` · likely ${plain(key, r.low)}–${plain(key, r.high)}` : ''}`}</p></article>`;
    }).join('')}</section>
    ${plan(c)}${insights(c)}`;
}

function plan(c) {
  if (!c.steps.length && !c.notes.length) return `<section class="card"><h2>Nothing to add</h2><p class="muted">Everything is inside the range to aim for. Carry on with your normal routine and test again when the next reminder comes.</p></section>`;
  return `<section class="card plan"><h2>${c.steps.length ? 'What to do, in this order' : 'What to know'}</h2>
    ${c.steps.map(s => `<div class="step ${s.doneDoseId ? 'done' : ''}">
      <div class="step-num">${s.doneDoseId ? '✓' : s.order}</div>
      <div class="step-body"><h3>${esc(s.title)}</h3>
        <div class="step-amount">${esc(s.amountText)} <span>${esc(s.productName)}</span></div>
        <p>${esc(s.how)}</p>
        <details><summary>Why</summary><p>${esc(s.why)}</p></details>
        ${s.doneDoseId ? `<button class="ghost small" data-action="undo-dose" data-id="${s.doneDoseId}">Added · undo</button>`
          : `<button class="primary small" data-action="dose" data-key="${s.key}">I've added it</button>`}
      </div></div>`).join('')}
    ${c.notes.map(n => `<p class="note">${esc(n)}</p>`).join('')}
    ${c.safety ? `<p class="safety">${esc(c.safety)}</p>` : ''}
    ${c.retestHours ? `<p class="muted">Retest ${c.retestHours >= 48 ? `in ${Math.round(c.retestHours / 24)} days` : 'tomorrow'}; you'll get a reminder.</p>` : ''}</section>`;
}

function insights(c) {
  const b = c.balance, parts = [];
  if (S.insights.trends.length) parts.push(`<div class="insight"><h3>Trends</h3>${S.insights.trends.map(t => `<p>${esc(t.text)}</p>`).join('')}</div>`);
  parts.push(b.available
    ? `<div class="insight"><h3>Water balance <span class="chip ${b.state === 'balanced' ? 'ok' : b.state === 'corrosive' ? 'low' : 'high'}">${b.index > 0 ? '+' : ''}${b.index.toFixed(2)} · ${b.state}</span></h3><p>${esc(b.text)}</p><p class="muted">Saturation index at ${b.tempC}°C. Between −0.3 and +0.3 is balanced.</p></div>`
    : `<div class="insight"><h3>Water balance</h3><p class="muted">${esc(b.text)}</p></div>`);
  return `<section class="card">${parts.join('')}</section>`;
}

// --- History ----------------------------------------------------------------
function chartSvg(key) {
  const tests = [...S.tests].reverse().slice(-30);
  if (tests.length < 2) return '';
  const W = 320, H = 96, pad = 6;
  const t0 = +new Date(tests[0].at), t1 = +new Date(tests[tests.length - 1].at) || t0 + 1;
  const x = t => pad + (W - 2 * pad) * ((+new Date(t) - t0) / Math.max(1, t1 - t0));
  const y = v => H - pad - (H - 2 * pad) * position(key, v);
  const [lo, hi] = currentIdeal()[key];
  const points = tests.map(t => [x(t.at), y(t.readings[key].value), S.statuses[t.id][key], t.source === 'lab']);
  return `<svg class="trend" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(S.chart.params[key].label)} over time">
    <rect class="band" x="0" y="${y(hi)}" width="${W}" height="${y(lo) - y(hi)}"/>
    <polyline points="${points.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ')}"/>
    ${points.map(p => (p[3] ? `<rect class="shop" x="${(p[0] - 6).toFixed(1)}" y="${(p[1] - 6).toFixed(1)}" width="12" height="12" rx="2"/>` : '') + marker(...p)).join('')}</svg>`;
}

// A point's shape says where it sits: triangle up above the range, triangle
// down below it, ring for an unclear reading, dot when in range.
function marker(x, y, status) {
  const at = `${x.toFixed(1)} ${y.toFixed(1)}`;
  if (status === 'high') return `<path class="high" d="M${at}m-4.4 3.4h8.8l-4.4-7.4z"/>`;
  if (status === 'low') return `<path class="low" d="M${at}m-4.4-3.4h8.8l-4.4 7.4z"/>`;
  return `<circle class="${status}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.2"/>`;
}

function history() {
  if (!S.tests.length) return `<header class="page"><h1>History</h1></header><section class="card"><p class="muted">Your tests will appear here, with a chart for each reading.</p></section>`;
  return `<header class="page"><h1>History</h1><p class="muted">${S.tests.length} test${S.tests.length === 1 ? '' : 's'}</p></header>
    ${S.tests.length > 1 ? `<section class="card charts">${KEYS.map(key => `<div><h3>${esc(S.chart.params[key].label)} <span class="muted">now ${shown(key, S.tests[0].readings[key])}</span></h3>${chartSvg(key)}</div>`).join('')}
      <p class="muted">The shaded band is the range to aim for. ▲ above it · ▼ below it · ○ unclear${S.tests.some(t => t.source === 'lab') ? ' · □ pool shop test' : ''}.</p></section>` : ''}
    ${stripCheckCard()}
    <section class="card"><h2>Tests</h2>${S.tests.map(t => `<button class="row" data-action="open-test" data-id="${t.id}">
      <span class="row-when">${dateShort(t.at)}<small>${timeShort(t.at)} · ${SOURCE_WORD[t.source] || 'by hand'}</small></span>
      <span class="row-values">${KEYS.map(k => `<i class="${S.statuses[t.id][k]}" title="${esc(S.chart.params[k].label)}: ${STATUS_WORD[S.statuses[t.id][k]]}">${shown(k, t.readings[k])}${STATUS_MARK[S.statuses[t.id][k]]}</i>`).join('')}</span></button>`).join('')}
      <p class="muted legend">Chlorine · alkalinity · pH · stabiliser &nbsp; ↓ low · ↑ high · ? unclear</p></section>
    ${S.doses.length ? `<section class="card"><h2>Chemicals added</h2>${S.doses.map(d => `<div class="row static"><span class="row-when">${dateShort(d.at)}<small>${esc(S.settings.products[d.product].name)}</small></span>
      <span class="row-values"><b>${amount(d.amount, d.unit)}</b><button class="ghost small" data-action="undo-dose" data-id="${d.id}">Remove</button></span></div>`).join('')}</section>` : ''}`;
}

const SOURCE_WORD = { photo: 'photo', box: 'light box', lab: 'pool shop' };
const amount = (value, unit) => unit === 'g' ? (value >= 1000 ? `${+(value / 1000).toFixed(2)} kg` : `${+value} g`) : (value >= 1000 ? `${+(value / 1000).toFixed(2)} L` : `${+value} ml`);

function openTest(id) {
  const t = S.tests.find(x => x.id === id);
  if (!t) return;
  if (t.source === 'lab') return labDetail(t);
  sheet.innerHTML = `<div class="sheet-head"><h2>${dayTime(t.at)}</h2><button class="icon" data-action="close" aria-label="Close">✕</button></div>
    <div class="sheet-body">${t.hasPhoto ? `<img class="photo" src="api/photo?id=${t.id}" alt="The strip photographed for this test">`
      : t.source !== 'manual' ? '<p class="muted">The photo of this strip is no longer kept: photos are deleted after three months. The readings are unaffected.</p>' : ''}
    <div class="detail">${KEYS.map(k => `<div><span>${esc(S.chart.params[k].label)}</span><b class="${S.statuses[t.id][k]}">${shown(k, t.readings[k])} ${esc(S.chart.params[k].unit)} · ${STATUS_WORD[S.statuses[t.id][k]]}</b>
      <small>${t.readings[k].manual || t.readings[k].confidence === 'manual' ? 'set by hand' : `photo · ${t.readings[k].confidence} match${t.readings[k].confidence === 'poor' ? ' (not used for dosing)' : ''}`}</small></div>`).join('')}</div>
    <button class="danger" data-action="delete-test" data-id="${t.id}">Delete this test</button></div>`;
  sheet.showModal();
}

// --- Settings ---------------------------------------------------------------
function field(label, path, value, attrs = '', help = '') {
  return `<label class="field"><span>${label}</span><input data-setting="${path}" value="${esc(value ?? '')}" ${attrs}>${help ? `<small>${help}</small>` : ''}</label>`;
}

// A month picker built from an ordinary list. The phone's own month control
// ignores the page's sizing and spills out of its column, so it is not used.
function monthField(label, path, value) {
  const now = new Date(), months = [];
  for (let i = -12; i <= 72; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() + i, 1);
    months.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`);
  }
  if (value && !months.includes(value)) months.push(value);
  months.sort();
  const name = ym => new Date(+ym.slice(0, 4), +ym.slice(5) - 1, 1).toLocaleDateString([], { month: 'short', year: 'numeric' });
  return `<label class="field"><span>${label}</span><select data-setting="${path}">${months.map(m => `<option value="${m}" ${m === value ? 'selected' : ''}>${name(m)}</option>`).join('')}</select></label>`;
}

function settingsView() {
  const s = S.settings, r = S.runtime, https = r.https || {}, me = S.me, plaster = s.surface === 'gunite';
  const num = 'type="number" inputmode="decimal" step="any"';
  return `<header class="page"><h1>Settings</h1><p class="muted">Changes save as you make them.</p></header>
    <section class="card"><h2>Appearance</h2>
      <div class="segmented">${[['auto', 'Auto'], ['light', 'Light'], ['dark', 'Dark']].map(([value, label]) =>
        `<button class="ghost ${(store.get('theme') || 'auto') === value ? 'on' : ''}" data-action="theme" data-value="${value}">${label}</button>`).join('')}</div>
      <p class="muted">Auto follows the phone's own light or dark setting. The choice is kept on this phone.</p></section>
    <section class="card"><h2>Pool</h2>${field('Volume (litres)', 'volumeLitres', s.volumeLitres, num)}
      <label class="field"><span>Surface</span><select data-setting="surface">${Object.entries(S.surfaces).map(([value, label]) => `<option value="${value}" ${s.surface === value ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select>
        <small>${plaster ? 'Gunite, marbelite and other plaster finishes. The doses are the same for every surface; the handling advice and the water balance notes follow it.' : 'Chlorine granules must never be left lying on this surface. The advice is worded for it.'}</small></label></section>
    <section class="card"><h2>Chemicals</h2><p class="muted">Doses are worked out from the strength on each label, so check these against yours.</p>
      ${Object.entries(s.products).map(([key, p]) => `<div class="product"><h3>${{ chlorine: 'Chlorine', acid: 'Acid (lowers pH)', alkalinityUp: 'Alkalinity increaser', phUp: 'pH increaser', stabiliser: 'Stabiliser' }[key]}</h3>
        ${field('Product', `products.${key}.name`, p.name, 'type="text" maxlength="60"')}
        ${key === 'acid' ? `<label class="field"><span>Type</span><select data-setting="products.acid.kind"><option value="liquid" ${p.kind === 'liquid' ? 'selected' : ''}>Liquid (hydrochloric)</option><option value="dry" ${p.kind === 'dry' ? 'selected' : ''}>Dry (granules)</option></select></label>` : ''}
        ${field('Strength %', `products.${key}.strength`, p.strength, num)}
        <small class="muted">${PRODUCT_HELP[key]}${key === 'chlorine' && s.surface === 'fibreglass' ? ' Most granular chlorine is calcium hypochlorite, which some fibreglass pool makers advise against: check your pool\'s care guide.' : ''}</small></div>`).join('')}</section>
    <section class="card"><h2>Test strips</h2><div class="pair">${field('Strips left', 'strips.remaining', s.strips.remaining, num)}${monthField('Bottle expires', 'strips.expiry', s.strips.expiry)}</div></section>
    <section class="card"><h2>Reminders</h2>${me.owner ? `<div class="pair">${field('Test every (days)', 'testIntervalDays', s.testIntervalDays, 'type="number" inputmode="numeric" min="1" max="30" data-int')}${field('Hot day from (°C)', 'hotDayC', s.hotDayC, num)}</div>
      <label class="switch"><input type="checkbox" data-setting="notifications" ${s.notifications ? 'checked' : ''}><span>Send reminders through Home Assistant</span></label>`
      : `${field('Test every (days)', 'testIntervalDays', s.testIntervalDays, 'type="number" inputmode="numeric" min="1" max="30" data-int')}<p class="muted">A reminder appears on the Today screen when a test is due.</p>`}</section>
    <section class="card"><h2>Light box</h2><p class="muted">The printed box the phone lies on. It photographs every strip under the phone's own torch, from the same distance, in the same place.</p>
      <label class="switch"><input type="checkbox" data-local="box" ${store.get('box') ? 'checked' : ''}><span>Use the light box for tests on this phone</span></label>
      <div class="pair"><button class="ghost" data-action="box-live">Camera check</button><button class="ghost" data-action="calib-home">Calibrate colours</button></div>
      <p class="muted">Ring positions: ${store.get('box-marks') ? 'remembered. <button class="link" data-action="box-forget">Forget them</button>' : 'not set yet (you place them on the first capture)'}.<br>
        Colour chart for the box: ${S.boxChart.custom ? `measured in the box on ${dateShort(S.boxChart.calibratedAt)}` : 'built-in, not yet measured in the box'}.</p></section>
    <section class="card"><h2>Water balance</h2><p class="muted">Optional. With a calcium hardness figure from a pool shop, the app can tell whether the water is ${plaster ? 'etching or scaling your plaster' : 'likely to leave scale'}.</p>
      <div class="pair">${field('Calcium (ppm)', 'calciumHardness', s.calciumHardness, num + ' placeholder="unknown" data-nullable')}${field('Water temp (°C)', 'waterTempC', s.waterTempC, num, S.weather.waterTempC != null ? `Live: ${S.weather.waterTempC}°C from Home Assistant` : me.owner ? 'Used when no sensor is linked' : 'Roughly what the water is now')}</div></section>
    ${peopleCard()}${accountCard()}
    <section class="card"><h2>Where the advice comes from</h2>
      <p class="muted">Every threshold and dose rule is taken from public-health guidance, a standard, or published research. Tap one to see what it is used for.</p>
      ${S.sources.map(source => `<details class="source"><summary>${esc(source.title)}</summary><p>${esc(source.used)}</p><a href="${esc(source.url)}" target="_blank" rel="noopener">Open the source</a></details>`).join('')}
      <p class="muted">A strip resolves about one colour step, and CDC notes strips are less accurate than drop kits. Checking against a drop kit or a pool-shop test a few times a season is worthwhile, and it is the only way to get calcium hardness.</p></section>
    <section class="card"><h2>About</h2><dl class="about">
      <dt>Version</dt><dd>${esc(S.version)}</dd>
      ${me.owner ? `<dt>Home Assistant</dt><dd>${r.ha.enabled ? (r.ha.connected ? `Sensors updated ${r.ha.lastPublish ? ago(r.ha.lastPublish) : ''}` : 'Not reachable yet') : 'Not linked (running outside Home Assistant)'}</dd>
      <dt>Own HTTPS address</dt><dd>${https.enabled ? esc(`${https.state}${https.hostname ? ' · ' + https.hostname : ''}`) : 'Off'}</dd>` : ''}
      <dt>Colour chart</dt><dd>${esc(S.chart.product)}<br><small>${esc(S.chart.basis)}</small></dd></dl>
      ${canInstall() ? '<button class="ghost" data-action="install-guide">Put Pool on the Home Screen</button>' : ''}
      <a class="button ghost" href="api/backup" download>Download a backup</a></section>`;
}

async function saveSetting(input) {
  const path = input.dataset.setting.split('.');
  let value;
  if (input.type === 'checkbox') value = input.checked;
  else if (input.type === 'number') {
    if (input.value.trim() === '') { if (!('nullable' in input.dataset)) return toast('Enter a number.', true); value = null; }
    else value = 'int' in input.dataset ? parseInt(input.value, 10) : parseFloat(input.value);
  } else value = input.value;
  const patch = {}; let node = patch;
  path.forEach((part, i) => { node[part] = i === path.length - 1 ? value : {}; node = node[part]; });
  try { S = await api('settings', patch); toast('Saved'); if (input.tagName === 'SELECT') render(); }
  catch (error) { toast(error.message, true); render(); }
}

// --- New test ---------------------------------------------------------------
function newTest() {
  closeCamera(); scan = null;
  const box = store.get('box');
  const shop = '<button class="ghost" data-action="lab-home">Add a pool shop test</button>';
  sheet.innerHTML = `<div class="sheet-head"><h2>New test</h2><button class="icon" data-action="close" aria-label="Close">✕</button></div>
    <div class="sheet-body"><ol class="prep">
      ${box ? '<li><p>Tap <b>Take reading</b> below. The camera and torch come on. Lay the phone on the box, camera in the cut-out.</p></li>' : ''}
      <li><p><b>Dip</b> a strip elbow-deep for 2 seconds, away from the returns.</p><button class="ghost small" data-action="dip" id="dip">Start 2-second timer</button></li>
      ${box ? `<li><p><b>Shake once</b>, lay it in the tray channel with the pads up and pointing away from you, and slide the tray shut.</p></li>
        <li><p><b>Leave it.</b> The photo is taken as soon as the strip is seen and steady.</p></li></ol>
        <button class="primary big" data-action="box-live">Take reading</button>
        <div class="pair"><button class="ghost" data-action="camera">Normal photo</button><button class="ghost" data-action="manual">Enter by hand</button></div>${shop}`
      : `<li><p><b>Shake once</b> and lay it pads-up on a sheet of <b>white paper</b>, in open shade (not direct sun).</p></li>
        <li><p><b>Photograph straight away</b> from directly above, with the strip filling about half the picture and nothing else on the paper.</p></li></ol>
        <p class="muted">White paper matters: it stops the phone over-brightening the strip, which is what throws colours off.</p>
        <button class="primary big" data-action="camera">Take photo</button>
        <div class="pair"><button class="ghost" data-action="library">Choose a photo</button><button class="ghost" data-action="manual">Enter by hand</button></div>${shop}`}</div>`;
  if (!sheet.open) sheet.showModal();
}

// --- Light box: live camera ---------------------------------------------------
function cameraProblem(error) {
  if (!navigator.mediaDevices?.getUserMedia) return 'The live camera only works from the app\'s own HTTPS address, not inside the Home Assistant sidebar. Use a normal photo here, or open the app from its Home Screen icon.';
  if (error?.name === 'NotAllowedError') return 'Camera access was refused. Allow the camera for this site in the phone\'s settings, then try again.';
  if (error?.name === 'NotFoundError' || error?.name === 'OverconstrainedError') return 'That camera is not available on this device. Pick another lens.';
  return 'The camera could not be started. Close other apps using it and try again.';
}

// Open the back camera, preferring the remembered lens, then the ultra-wide
// (the only one that focuses at light-box distance), and switch the torch on.
async function openCamera(label) {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('unsupported');
  const size = { width: { ideal: 3840 }, height: { ideal: 2160 } };
  let stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: 'environment' }, ...size } });
  // Back cameras only: the front ones face the wrong way and were being picked for having "Ultra Wide" in their name.
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput' && d.label && !/front/i.test(d.label));
  const want = devices.find(d => d.label === label) || devices.find(d => /ultra/i.test(d.label));
  if (want && stream.getVideoTracks()[0].label !== want.label) {
    stream.getTracks().forEach(t => t.stop());
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { deviceId: { exact: want.deviceId }, ...size } }); }
    catch { stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: 'environment' }, ...size } }); }
  }
  const track = stream.getVideoTracks()[0], caps = track.getCapabilities?.() || {};
  // A combined multi-lens camera shows its widest (closest-focusing) view at minimum zoom.
  if (caps.zoom && !/ultra/i.test(track.label)) await track.applyConstraints({ advanced: [{ zoom: caps.zoom.min }] }).catch(() => {});
  let torch = false;
  if (!track.getCapabilities || caps.torch) torch = await track.applyConstraints({ advanced: [{ torch: true }] }).then(() => true, () => false);
  return { stream, track, devices, torch, label: (want && want.label) || track.label };
}

function closeCamera() {
  if (!cam) return;
  clearInterval(cam.timer);
  cam.stream.getTracks().forEach(t => t.stop());   // stopping the track also switches the torch off
  cam = null;
}

function grab(video, maxSide) {
  const k = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(video.videoWidth * k); canvas.height = Math.round(video.videoHeight * k);
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return { canvas, image: ctx.getImageData(0, 0, canvas.width, canvas.height) };
}

// Ring positions are remembered as fractions of the picture. They only apply
// while the picture has the same orientation as when they were set.
function savedMarks(key, w, h) {
  const m = store.get(key);
  if (!m || m.landscape !== (w > h)) return null;
  return Object.fromEntries(Object.entries(m).filter(([, p]) => p && typeof p === 'object').map(([name, p]) => [name, { x: p.x * w, y: p.y * h }]));
}
const markFractions = marks => { const { width: w, height: h } = scan.canvas; return Object.fromEntries(Object.entries(marks).map(([name, p]) => [name, { x: p.x / w, y: p.y / h }])); };
function rememberMarks(key, marks) {
  store.set(key, { ...markFractions(marks), landscape: scan.canvas.width > scan.canvas.height });
}

async function boxLive(mode = { kind: 'test' }) {
  closeCamera(); scan = null;
  const calib = mode.kind === 'calib';
  sheet.innerHTML = `<div class="sheet-head"><h2>${calib ? 'Calibrate colours' : 'Reading the strip'}</h2><button class="icon" data-action="close" aria-label="Close">✕</button></div>
    <div class="sheet-body"><div class="stage"><div class="live"><video id="live" autoplay muted playsinline></video><canvas id="live-rings"></canvas></div></div>
    <p id="live-status" class="muted" role="status">Starting the camera…</p>
    <button class="primary big" data-action="box-capture" disabled>Capture now</button>
    <label class="field"><span>Lens</span><select id="lens" disabled><option>Finding cameras…</option></select><small id="live-facts"></small></label>
    ${calib ? `<button class="ghost" data-action="calib-home">Back</button>` : ''}</div>`;
  if (!sheet.open) sheet.showModal();
  let opened;
  try { opened = await openCamera(store.get('lens')); }
  catch (error) { const status = $('#live-status'); if (status) { status.textContent = cameraProblem(error); status.className = 'warning'; } return; }
  const video = $('#live');
  if (!video) { opened.stream.getTracks().forEach(t => t.stop()); return; }   // the sheet was closed while permission was being asked
  cam = { ...opened, mode, video, steady: 0, last: null };
  video.srcObject = cam.stream; await video.play().catch(() => {});
  const lens = $('#lens');
  lens.innerHTML = cam.devices.map(d => `<option ${d.label === cam.label ? 'selected' : ''}>${esc(d.label)}</option>`).join('') || `<option>${esc(cam.track.label || 'Back camera')}</option>`;
  lens.disabled = cam.devices.length < 2;
  $('[data-action=box-capture]').disabled = false;
  cam.timer = setInterval(boxTick, 400);
}

function drawRings(canvas, frame, read) {
  canvas.width = frame.canvas.width; canvas.height = frame.canvas.height;
  if (!read) return;
  const ctx = canvas.getContext('2d'), unit = canvas.width / 100;
  for (const key of KEYS) {
    const r = read.readings[key];
    ctx.beginPath(); ctx.arc(r.x, r.y, Math.max(r.half * 1.6, unit * 1.2), 0, Math.PI * 2);
    ctx.lineWidth = Math.max(2, unit * 0.5); ctx.strokeStyle = '#0b1620'; ctx.stroke();
    ctx.lineWidth = Math.max(1, unit * 0.25); ctx.strokeStyle = '#35c6dc'; ctx.stroke();
  }
}

function boxTick() {
  const video = cam?.video, status = $('#live-status');
  if (!video || !status) return closeCamera();
  if (!video.videoWidth) return;
  $('#live-facts').textContent = `${video.videoWidth}×${video.videoHeight} · torch ${cam.torch ? 'on' : 'not available on this lens'}`;
  const frame = grab(video, 720), rings = $('#live-rings');
  if (cam.mode.kind === 'calib') {
    const marks = findChartGrid(frame.image, S.chart);
    drawRings(rings, frame, null);
    if (!marks) { status.textContent = 'Looking for the chart… Slide the calibration slide in with the chart facing up.'; return; }
    const grid = measureChartGrid(frame.image, marks, chartCounts(S.chart)), problems = chartProblems(grid, S.chart);
    drawChartRings(rings.getContext('2d'), grid, problems, p => p, rings.width / 100);
    const burnt = problems.filter(q => q.code === 'glare').length;
    status.textContent = burnt ? `Chart found, but the torch is reflecting off ${burnt} swatch${burnt > 1 ? 'es' : ''} (red rings). Pull the slide out a little until they clear.`
      : 'Chart found. Tap Capture.';
    return;
  }
  const marks = savedMarks('box-marks', frame.canvas.width, frame.canvas.height);
  if (!marks) {
    drawRings(rings, frame, null);
    status.textContent = store.get('box-marks') ? 'The picture is turned compared with when the rings were set. Tap Capture and the strip will be looked for instead.'
      : 'First time: put any strip in the tray (a dry one is fine), shut it, then tap Capture to place the rings once.';
    return;
  }
  const read = readStrip(frame.image, marks.first, marks.last, chartFor('box'));
  drawRings(rings, frame, read);
  const here = stripPresent(read), signature = KEYS.flatMap(key => read.readings[key].lab);
  const still = here && cam.last && signature.every((v, i) => Math.abs(v - cam.last[i]) < 2.5);
  cam.last = signature; cam.steady = still ? cam.steady + 1 : 0;
  status.textContent = !here ? 'Waiting for a strip in the tray…' : 'Strip found, holding steady…';
  if (cam.steady >= 3) boxCapture();
}

function boxCapture() {
  if (!cam?.video?.videoWidth) return;
  const mode = cam.mode, frame = grab(cam.video, 2400), { width: w, height: h } = frame.canvas;
  closeCamera();
  if (mode.kind === 'calib') {
    const found = findChartGrid(frame.image, S.chart), saved = savedMarks('chart-marks', w, h);
    const marks = found || (saved && saved.a && saved.b && saved.c ? saved : { a: { x: w * 0.3, y: h * 0.45 }, b: { x: w * 0.7, y: h * 0.45 }, c: { x: w * 0.3, y: h * 0.7 } });
    scan = { ...frame, kind: 'calib', marks, auto: !!found, zoom: true, source: 'box' };
    measure();
  } else {
    const fixed = savedMarks('box-marks', w, h);
    scan = { ...frame, marks: fixed, fixed: !!fixed, auto: true, zoom: true, overrides: {}, at: new Date().toISOString(), late: dipAt && Date.now() - dipAt > 45000, source: 'box' };
    if (fixed) measure();
    if (!fixed || !stripPresent(scan.result)) {
      const found = findStrip(frame.image, chartFor('box'));
      scan.marks = found || fixed || { first: { x: w / 2, y: h * 0.35 }, last: { x: w / 2, y: h * 0.65 } };
      scan.auto = !!(found || fixed); scan.zoom = scan.auto;
      measure();
    }
  }
  review();
}

// --- Light box: measuring the bottle's chart ---------------------------------
// Rings on every swatch of a measured chart; the ones with a problem in red.
function drawChartRings(ctx, grid, problems, to, unit, scale = 1) {
  const bad = new Set(problems.filter(q => q.code !== 'bright').map(q => `${q.row},${q.column}`));
  grid.forEach((row, r) => row.forEach((cell, k) => {
    const p = to(cell), flagged = bad.has(`${r},${k}`);
    ctx.beginPath(); ctx.arc(p.x, p.y, Math.max(cell.half * scale * 1.25, unit * 1.1), 0, Math.PI * 2);
    ctx.lineWidth = Math.max(2, unit * 0.55); ctx.strokeStyle = '#0b1620'; ctx.stroke();
    ctx.lineWidth = Math.max(1, unit * 0.3); ctx.strokeStyle = flagged ? '#ff5a4f' : '#ffffff'; ctx.stroke();
  }));
}

// How to calibrate, step by step: shown to a new person as their next step, and under Settings -> Calibrate colours.
const CALIB_WHY = 'This teaches the app what the colour chart on your strip bottle looks like under your box\'s own light, on your phone. Every strip read in the box is then compared with that, which is what makes the readings accurate.';
const CALIB_NEEDS = 'You need the light box, the white calibration slide (the flat one with four corner marks), and the label from your bottle of test strips.';
function calibSteps(button) {
  return `<ol class="prep">
    <li><p><b>Peel the label</b> off the strip bottle and cut out the colour chart: the block of coloured squares, about 47 mm across. Trim close, so that no edge of the label curls up.</p></li>
    <li><p><b>Stick the chart flat</b> on the calibration slide, inside the four corner marks. Any way up.</p></li>
    <li><p><b>Push the slide into the box</b> where the strip tray goes, chart facing up, until it stops.</p></li>
    <li><p><b>Tap ${button}</b> below and allow the camera when the phone asks. The torch comes on. Lay the phone on the box, screen up, with its camera pushed snugly into the cut-out in the lid.</p></li>
    <li><p><b>Wait for the rings.</b> The app finds the chart and puts a ring on every colour. A red ring means the torch is reflecting off that square: pull the slide out a little until none is red. Then tap <b>Capture</b>.</p></li>
    <li><p><b>Check and save.</b> Each row of colours should step evenly from left to right, as on the label. Tap <b>Use this calibration</b>.</p></li></ol>`;
}

// A person's next step after describing their pool (and anyone's whose box has never been calibrated).
function calibWelcome() {
  if (sheet.open) sheet.close();
  app.innerHTML = `<div class="login welcome"><span class="boot-drop"></span><h1>Next: calibrate your light box</h1>
    <p class="muted">${CALIB_WHY} It takes about five minutes and is done once, then again whenever you start a new bottle of strips.</p>
    <p class="muted">${CALIB_NEEDS}</p>
    ${calibSteps('Start calibration')}
    <button class="primary big" data-action="calib-start">Start calibration</button>
    <button class="ghost" data-action="calib-later">Not now, remind me</button>
    <button class="link" data-action="no-light-box">I don't have a light box</button></div>`;
}

function calibHome() {
  closeCamera(); scan = null;
  const box = S.boxChart;
  sheet.innerHTML = `<div class="sheet-head"><h2>Calibrate colours</h2><button class="icon" data-action="close" aria-label="Close">✕</button></div>
    <div class="sheet-body"><p class="muted">${CALIB_WHY} Do it once per phone and box, and again for a new bottle.</p>
      <p class="muted">${CALIB_NEEDS}</p>
      ${calibSteps('Open the camera')}
      <p class="muted">Now in use for the box: ${box.custom ? `the chart measured on ${dateShort(box.calibratedAt)}` : 'the built-in chart, which was measured in daylight'}.</p>
      <button class="primary big" data-action="calib-live">Open the camera</button>
      ${box.custom ? '<button class="ghost" data-action="calib-reset">Go back to the built-in chart</button>' : ''}</div>`;
  if (!sheet.open) sheet.showModal();
}

const CHART_WARNINGS = {
  glare: n => `The torch is reflecting off ${n} swatch${n > 1 ? 'es' : ''} (red rings), which would read far too pale. Pull the slide out a little and capture again.`,
  bright: () => 'The white of the label is close to blown out, so colours will read a little pale. Capture again with the slide pulled out slightly.',
  missed: n => `${n} ring${n > 1 ? 's are' : ' is'} not on a coloured swatch (red rings). Drag the three blue rings onto the swatches they name.`,
  dark: n => `${n} swatch${n > 1 ? 'es are' : ' is'} in deep shadow (red rings). Check the torch is on and the slide is not pulled out too far.`,
  uneven: n => `${n} swatch${n > 1 ? 'es look' : ' looks'} uneven (red rings): a reflection, a crease in the label, or a ring that is off its swatch.`,
};

function calibReview() {
  const chart = S.chart, counts = {};
  for (const q of scan.problems) counts[q.code] = (counts[q.code] || 0) + 1;
  const warnings = Object.keys(CHART_WARNINGS).filter(code => counts[code]).map(code => CHART_WARNINGS[code](counts[code]));
  const serious = counts.glare || counts.dark || counts.missed;
  sheet.innerHTML = `<div class="sheet-head"><h2>Check the chart</h2><button class="icon" data-action="close" aria-label="Close">✕</button></div>
    <div class="sheet-body"><div class="stage"><canvas id="stage"></canvas></div>
    <p class="muted hint">${scan.auto ? 'Every ring should sit inside its swatch. If they are off, drag the three blue rings.' : 'I could not find the chart on its own. Drag the three blue rings onto the swatches they name; the others follow.'}
      <button class="link" data-action="zoom">${scan.zoom ? 'Show whole photo' : 'Zoom to the chart'}</button></p>
    ${warnings.map(w => `<p class="warning">${esc(w)}</p>`).join('')}
    <div class="calib">${chart.padOrder.map((key, r) => `<div><span>${esc(chart.params[key].short)}</span><div class="chips">${scan.grid[r].map((cell, k) =>
      `<i style="background:${labToCss(cell.lab)}"><b>${plain(key, chart.params[key].swatches[k].value)}</b></i>`).join('')}</div></div>`).join('')}
      <p class="muted">The colours as measured. Each row should step evenly from left to right, as it does on the label.</p></div>
    <div class="pair sticky"><button class="${serious ? 'primary' : 'ghost'}" data-action="calib-live">Retake</button><button class="${serious ? 'ghost' : 'primary'}" data-action="calib-use">${serious ? 'Use it anyway' : 'Use this calibration'}</button></div></div>`;
  if (!sheet.open) sheet.showModal();
  drawStage();
}

function dipTimer(button) {
  let left = 2; button.disabled = true; button.textContent = 'Dip now… 2';
  const tick = setInterval(() => {
    left--;
    if (left > 0) { button.textContent = `Dip now… ${left}`; return; }
    clearInterval(tick); dipAt = Date.now(); button.textContent = 'Out! Shake once, then photograph';
    if (navigator.vibrate) navigator.vibrate(120);
  }, 1000);
}

async function loadPhoto(file) {
  if (!file) return;
  sheet.innerHTML = `<div class="sheet-body busy"><span class="boot-drop"></span><p>Reading the strip…</p></div>`;
  try {
    const url = URL.createObjectURL(file), img = new Image();
    img.src = url; await img.decode();
    const k = Math.min(1, 1400 / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(img.naturalWidth * k); canvas.height = Math.round(img.naturalHeight * k);
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height); URL.revokeObjectURL(url);
    const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const found = findStrip(image, S.chart);
    const marks = found || { first: { x: canvas.width / 2, y: canvas.height * 0.22 }, last: { x: canvas.width / 2, y: canvas.height * 0.78 } };
    scan = { canvas, image, marks, auto: !!found, zoom: !!found, overrides: {}, at: new Date().toISOString(), late: dipAt && Date.now() - dipAt > 45000, source: 'photo' };
    measure(); review();
  } catch (error) { toast('That photo could not be opened. Try again.', true); newTest(); }
}

function measure() {
  if (scan.kind === 'calib') {
    const counts = chartCounts(S.chart);
    scan.grid = measureChartGrid(scan.image, scan.marks, counts);
    scan.problems = chartProblems(scan.grid, S.chart);
    scan.pitch = chartGridLayout(scan.marks, counts).pitch;
    return;
  }
  // Rings remembered for the light box already know which end is the chlorine
  // pad; anything placed by eye or by search is tried both ways round.
  const chart = chartFor(scan.source);
  const result = scan.fixed ? readStrip(scan.image, scan.marks.first, scan.marks.last, chart)
    : readStripEitherWay(scan.image, scan.marks.first, scan.marks.last, chart);
  scan.fixed = false;
  scan.result = result; scan.marks = { first: result.layout.first, last: result.layout.last }; scan.pitch = result.layout.pitch;
  scan.points = KEYS.map(key => ({ ...result.readings[key], label: S.chart.params[key].short, tiny: { fc: 'Cl', ta: 'Alk', ph: 'pH', cya: 'Stab' }[key] }));
}

const BOX_WARNINGS = {
  bright: 'The white tray is blown out, so colours read too pale. Add white paper to the floor of the box beside the tray and capture again.',
  dark: 'The picture is dark. Check the torch came on and the lid is seated.',
  offchart: 'A pad colour does not sit on the chart. Check the rings are on the pads.',
};

function manualTest() {
  const readings = {};
  for (const key of KEYS) { const [lo, hi] = S.settings.ideal[key]; readings[key] = { value: roundTo((lo + hi) / 2, S.chart.params[key].step) }; }
  scan = { source: 'manual', overrides: Object.fromEntries(KEYS.map(k => [k, readings[k].value])), result: { readings, warnings: [] }, at: new Date().toISOString() };
  review();
}

function finalReadings() {
  const out = {};
  for (const key of KEYS) {
    const r = scan.result.readings[key], over = scan.overrides[key];
    out[key] = over !== undefined ? { value: over, low: over, high: over, manual: true, lab: r.lab }
      : { value: r.value, low: r.low, high: r.high, confidence: r.confidence, lab: r.lab };
  }
  return out;
}

function review() {
  if (scan.kind === 'calib') return calibReview();
  const box = scan.source === 'box', photo = box || scan.source === 'photo', final = finalReadings();
  const warnings = scan.result.warnings.map(w => (box && BOX_WARNINGS[w.code]) || w.text);
  if (scan.late) warnings.push('More than 45 seconds passed since the dip. Strip colours keep changing, so a fresh strip gives a truer reading.');
  sheet.innerHTML = `<div class="sheet-head"><h2>${photo ? 'Check the reading' : 'Enter readings'}</h2><button class="icon" data-action="close" aria-label="Close">✕</button></div>
    <div class="sheet-body">
    ${photo ? `<div class="stage"><canvas id="stage"></canvas></div>
      <p class="muted hint">${scan.auto ? 'Rings should sit on the four pads. Drag an end ring if they are off.' : 'I could not find the strip on its own. Drag the two large rings onto the first and last pads.'}
        <button class="link" data-action="zoom">${scan.zoom ? 'Show whole photo' : 'Zoom to strip'}</button>
        ${box ? '· <button class="link" data-action="box-remember">Only remember these ring positions</button>' : ''}</p>` : '<p class="muted">Tap the colour that matches each pad on the bottle.</p>'}
    ${warnings.map(w => `<p class="warning">${esc(w)}</p>`).join('')}
    <div class="matches">${(ideal => KEYS.map(key => {
      const param = chartFor(scan.source).params[key], r = scan.result.readings[key], f = final[key], manual = scan.overrides[key] !== undefined;
      const weak = !manual && r.confidence === 'poor';
      const status = weak ? 'unsure' : f.value < ideal[key][0] ? 'low' : f.value > ideal[key][1] ? 'high' : 'ok';
      return `<div class="match ${weak ? 'weak' : ''}"><div class="match-head"><h3>${esc(param.label)}</h3>
          <span class="match-value ${status}">${shown(key, f)}${STATUS_MARK[status]}<small>${esc(param.unit)}</small></span></div>
        <div class="swatches">${param.swatches.map(s => `<button class="swatch ${manual && s.value === f.value ? 'picked' : ''}" style="background:${s.css}" data-action="pick" data-key="${key}" data-value="${s.value}"><span>${plain(key, s.value)}</span></button>`).join('')}
          <i class="pointer ${manual ? 'manual' : ''}" style="left:${(0.5 + position(key, f.value) * (param.swatches.length - 1)) / param.swatches.length * 100}%"></i></div>
        <p class="muted">${manual ? (photo ? `Set by hand. <button class="link" data-action="unpick" data-key="${key}">Use the photo reading (${shown(key, r)})</button>` : 'Tap a colour to change.')
          : weak ? '<b>Poor match: this is a guess.</b> Tap the colour you see on the pad, or retake. Left as it is, no dose will be based on it.'
          : `${r.confidence === 'good' ? 'Clear match' : 'Reasonable match'}${r.low !== r.high ? `, likely ${plain(key, r.low)}–${plain(key, r.high)}` : ''}. Tap a colour to override.`}</p></div>`;
    }).join(''))(idealFor(final))}</div>
    <div class="pair sticky"><button class="ghost" data-action="${box ? 'box-live' : 'new-test'}">${photo ? 'Retake' : 'Back'}</button><button class="primary" data-action="save-test">Save reading</button></div></div>`;
  if (!sheet.open) sheet.showModal();
  if (photo) drawStage();
}

function viewRect() {
  const { canvas, marks } = scan;
  if (!scan.zoom) return { x: 0, y: 0, w: canvas.width, h: canvas.height };
  const m = scan.pitch * 1.3;
  // The chart's fourth corner is implied by its three rings.
  const points = scan.kind === 'calib' ? [marks.a, marks.b, marks.c, { x: marks.b.x + marks.c.x - marks.a.x, y: marks.b.y + marks.c.y - marks.a.y }] : [marks.first, marks.last];
  let x0 = Math.min(...points.map(p => p.x)) - m, x1 = Math.max(...points.map(p => p.x)) + m;
  let y0 = Math.min(...points.map(p => p.y)) - m, y1 = Math.max(...points.map(p => p.y)) + m;
  x0 = Math.max(0, x0); y0 = Math.max(0, y0); x1 = Math.min(canvas.width, x1); y1 = Math.min(canvas.height, y1);
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

function drawStage() {
  const stage = $('#stage');
  if (!stage) return;
  const view = scan.view = scan.dragging ? scan.view : viewRect();
  const scale = Math.min(1, 1100 / Math.max(view.w, view.h)) * (window.devicePixelRatio > 1 ? 1 : 1);
  stage.width = Math.round(view.w * scale); stage.height = Math.round(view.h * scale);
  const ctx = stage.getContext('2d');
  ctx.drawImage(scan.canvas, view.x, view.y, view.w, view.h, 0, 0, stage.width, stage.height);
  const to = p => ({ x: (p.x - view.x) * scale, y: (p.y - view.y) * scale });
  if (scan.kind === 'calib') return drawChartStage(ctx, to, scale, stage.width / 100);
  const first = to(scan.marks.first), last = to(scan.marks.last), unit = stage.width / 100;
  ctx.lineWidth = Math.max(2, unit * 0.5); ctx.strokeStyle = 'rgba(255,255,255,.55)'; ctx.setLineDash([unit * 1.5, unit * 1.5]);
  ctx.beginPath(); ctx.moveTo(first.x, first.y); ctx.lineTo(last.x, last.y); ctx.stroke(); ctx.setLineDash([]);
  const lastIndex = scan.points.length - 1;
  scan.points.forEach((r, i) => {
    const p = scan.dragging ? to({ x: scan.marks.first.x + (scan.marks.last.x - scan.marks.first.x) * i / lastIndex, y: scan.marks.first.y + (scan.marks.last.y - scan.marks.first.y) * i / lastIndex }) : to(r);
    const end = i === 0 || i === lastIndex, radius = Math.max(r.half * scale * 1.5, unit * (end ? 4.5 : 3));
    ctx.beginPath(); ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
    ctx.lineWidth = Math.max(2.5, unit * (end ? 0.9 : 0.6)); ctx.strokeStyle = '#0b1620'; ctx.stroke();
    ctx.lineWidth = Math.max(1.5, unit * (end ? 0.55 : 0.35)); ctx.strokeStyle = end ? '#35c6dc' : '#ffffff'; ctx.stroke();
    // Labels sit beside a vertical strip, and above a horizontal one (shortened so they do not collide).
    const sideways = Math.abs(last.x - first.x) > Math.abs(last.y - first.y);
    ctx.font = `600 ${Math.max(12, unit * 3.4)}px system-ui, sans-serif`; ctx.textBaseline = 'middle'; ctx.textAlign = sideways ? 'center' : 'left';
    const roomy = p.x + radius + unit * 1.5 + ctx.measureText(r.label).width <= stage.width;
    const label = sideways || !roomy ? (r.tiny || r.label) : r.label, wide = ctx.measureText(label).width;
    const tx = sideways ? p.x : p.x + radius + unit * 1.5 + wide > stage.width ? p.x - radius - unit * 1.5 - wide : p.x + radius + unit * 1.5;
    const ty = sideways ? (p.y - radius - unit * 2.6 < unit * 2 ? p.y + radius + unit * 2.6 : p.y - radius - unit * 2.6) : p.y;
    ctx.lineWidth = Math.max(3, unit * 0.8); ctx.strokeStyle = 'rgba(11,22,32,.85)'; ctx.strokeText(label, tx, ty);
    ctx.fillStyle = '#fff'; ctx.fillText(label, tx, ty);
  });
}

function drawChartStage(ctx, to, scale, unit) {
  const chart = S.chart, counts = chartCounts(chart);
  // While a ring is being dragged the swatch rings follow it; the colours are measured again on release.
  const grid = scan.dragging ? chartGridLayout(scan.marks, counts).cells.map(row => row.map(p => ({ ...p, half: scan.pitch * 0.17 }))) : scan.grid;
  drawChartRings(ctx, grid, scan.dragging ? [] : scan.problems, to, unit, scale);
  const last = key => chart.params[key].swatches.length - 1, firstKey = chart.padOrder[0], lastKey = chart.padOrder[chart.padOrder.length - 1];
  const handles = [['a', `${chart.params[firstKey].short} ${plain(firstKey, chart.params[firstKey].swatches[0].value)}`],
    ['b', `${chart.params[firstKey].short} ${plain(firstKey, chart.params[firstKey].swatches[last(firstKey)].value)}`],
    ['c', `${chart.params[lastKey].short} ${plain(lastKey, chart.params[lastKey].swatches[0].value)}`]];
  const centre = to({ x: (scan.marks.b.x + scan.marks.c.x) / 2, y: (scan.marks.b.y + scan.marks.c.y) / 2 });
  ctx.font = `600 ${Math.max(12, unit * 3.2)}px system-ui, sans-serif`; ctx.textBaseline = 'middle'; ctx.textAlign = 'center';
  for (const [name, label] of handles) {
    const p = to(scan.marks[name]), radius = Math.max(scan.pitch * scale * 0.42, unit * 3.2);
    ctx.beginPath(); ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
    ctx.lineWidth = Math.max(2.5, unit * 0.9); ctx.strokeStyle = '#0b1620'; ctx.stroke();
    ctx.lineWidth = Math.max(1.5, unit * 0.55); ctx.strokeStyle = '#35c6dc'; ctx.stroke();
    // Each name sits outside the chart, on the far side of its ring from the chart's middle.
    const away = Math.hypot(p.x - centre.x, p.y - centre.y) || 1, reach = radius + unit * 3;
    const wide = ctx.measureText(label).width / 2 + unit;
    const tx = Math.min(ctx.canvas.width - wide, Math.max(wide, p.x + (p.x - centre.x) / away * reach)), ty = Math.min(ctx.canvas.height - unit * 2.5, Math.max(unit * 2.5, p.y + (p.y - centre.y) / away * reach));
    ctx.lineWidth = Math.max(3, unit * 0.8); ctx.strokeStyle = 'rgba(11,22,32,.85)'; ctx.strokeText(label, tx, ty);
    ctx.fillStyle = '#fff'; ctx.fillText(label, tx, ty);
  }
}

function stagePoint(event) {
  const stage = $('#stage'), box = stage.getBoundingClientRect(), view = scan.view;
  return { x: view.x + (event.clientX - box.left) / box.width * view.w, y: view.y + (event.clientY - box.top) / box.height * view.h };
}

sheet.addEventListener('pointerdown', event => {
  if (event.target.id !== 'stage' || !scan) return;
  const p = stagePoint(event), d = m => Math.hypot(m.x - p.x, m.y - p.y);
  const which = Object.keys(scan.marks).reduce((best, name) => d(scan.marks[name]) < d(scan.marks[best]) ? name : best);
  if (d(scan.marks[which]) > Math.max(scan.view.w, scan.view.h) * 0.25) return;
  scan.dragging = which; event.target.setPointerCapture(event.pointerId); event.preventDefault();
});
sheet.addEventListener('pointermove', event => {
  if (!scan?.dragging) return;
  scan.marks[scan.dragging] = stagePoint(event); drawStage();
});
const endDrag = () => {
  if (!scan?.dragging) return;
  scan.dragging = null; scan.auto = true; measure(); review();
};
sheet.addEventListener('pointerup', endDrag); sheet.addEventListener('pointercancel', endDrag);

async function saveTest(button) {
  button.disabled = true; button.textContent = 'Saving…';
  const body = { id: newId(), at: scan.at, source: scan.source, readings: finalReadings() };
  if (scan.source !== 'manual') {
    if (scan.source === 'box') rememberMarks('box-marks', scan.marks);
    const k = Math.min(1, 1280 / Math.max(scan.canvas.width, scan.canvas.height)), small = document.createElement('canvas');
    small.width = Math.round(scan.canvas.width * k); small.height = Math.round(scan.canvas.height * k);
    small.getContext('2d').drawImage(scan.canvas, 0, 0, small.width, small.height);
    body.photo = small.toDataURL('image/jpeg', 0.85);
    body.marks = markFractions(scan.marks);
  }
  try {
    S = await api('tests', body);
    sheet.close(); scan = null; dipAt = null; tab = 'home'; render(true);
    toast(S.runtime.ha.enabled ? 'Saved · Home Assistant updated' : 'Saved');
  } catch (error) { toast(error.message, true); button.disabled = false; button.textContent = 'Save reading'; }
}

const CLOSE = '<button class="icon" data-action="close" aria-label="Close">✕</button>';

// --- Pool shop tests ----------------------------------------------------------
// A test done at a shop's counter is kept in the history beside the strip tests.
// It arrives as a link to the shop's report (the server fetches the figures), or
// is typed in from the printout, with a picture of the page if wanted.
const REPORT_PAGE = 'https://solutions.waterlinkconnect.com/treatment-reports/';
const factor = key => S.labFactors.find(f => f.key === key) || { label: key, unit: '' };
const labFigure = (key, value) => `${key === 'ph' ? (+value).toFixed(1) : +(+value).toFixed(2)}${factor(key).unit ? ' ' + factor(key).unit : ''}`;
const newId = () => [...crypto.getRandomValues(new Uint8Array(12))].map(b => b.toString(16).padStart(2, '0')).join('');

function labHome() {
  closeCamera(); scan = null;
  sheet.innerHTML = `<div class="sheet-head"><h2>Pool shop test</h2>${CLOSE}</div>
    <div class="sheet-body"><form class="lab-form" data-form="lab-link">
      <p class="muted">Paste the link the shop sent you by email or message. The readings, the date and time of the test, and what the shop advised are fetched from the report and kept in your history.</p>
      <label class="field"><span>Report link</span><input type="url" name="url" inputmode="url" required maxlength="300" placeholder="${REPORT_PAGE}…" ${NO_HELP}>
        <small>Works with WaterLink reports, which is what Builders and other shops with a LaMotte meter send.</small></label>
      <button class="primary big">Fetch the report</button></form>
      <button class="ghost" data-action="lab-manual">Enter a printed report by hand</button>
      <button class="ghost" data-action="new-test">Back</button></div>`;
  if (!sheet.open) sheet.showModal();
}

function labManual() {
  labDraft = { id: newId(), photo: null };
  // No date is filled in: a report typed in days later must go into the history at the time printed on it, not at "now".
  const local = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const input = f => `<label class="field"><span>${esc(f.label)}${f.unit ? ` (${esc(f.unit)})` : ''}</span><input type="number" inputmode="decimal" step="any" name="v-${f.key}" ${KEYS.includes(f.key) ? 'required' : 'placeholder="not tested"'}></label>`;
  sheet.innerHTML = `<div class="sheet-head"><h2>Printed report</h2>${CLOSE}</div>
    <form class="sheet-body" data-form="lab-manual">
      <label class="field"><span>Tested on</span><input type="datetime-local" name="at" required max="${local}"><small>The date and time printed in the top right corner of the report. The test goes into the history at that moment, not at today's date.</small></label>
      <div class="pair">${S.labFactors.filter(f => KEYS.includes(f.key)).map(input).join('')}</div>
      <h3>Anything else on the report</h3>
      <div class="pair">${S.labFactors.filter(f => !KEYS.includes(f.key)).map(input).join('')}</div>
      <label class="field"><span>Shop</span><input type="text" name="shop" maxlength="60" placeholder="optional" autocomplete="off"></label>
      <label class="field"><span>Notes</span><textarea name="note" maxlength="500" rows="3" placeholder="optional: what the shop advised"></textarea></label>
      <button type="button" class="ghost" data-action="lab-photo" id="paper-button">Photograph the printout</button>
      <p class="muted">The picture is kept with the test, so the shop's advice can be read again later. The figures are not read from it: type them in above.</p>
      <div class="pair sticky"><button type="button" class="ghost" data-action="lab-home">Back</button><button class="primary">Save test</button></div></form>`;
  if (!sheet.open) sheet.showModal();
}

async function loadPaper(file) {
  if (!file || !labDraft) return;
  try {
    const url = URL.createObjectURL(file), img = new Image();
    img.src = url; await img.decode();
    const k = Math.min(1, 1800 / Math.max(img.naturalWidth, img.naturalHeight)), canvas = document.createElement('canvas');
    canvas.width = Math.round(img.naturalWidth * k); canvas.height = Math.round(img.naturalHeight * k);
    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height); URL.revokeObjectURL(url);
    labDraft.photo = canvas.toDataURL('image/jpeg', 0.8);
    const button = $('#paper-button');
    if (button) button.textContent = 'Printout photographed ✓ · take it again';
  } catch { toast('That photo could not be opened. Try again.', true); }
}

function labSaved(data) {
  S = data; labDraft = null; tab = 'history'; render(true);
  const saved = S.tests.find(t => t.id === data.imported.id);
  if (saved) labDetail(saved, true);
  const done = data.imported, newest = S.tests[0]?.id === done.id;
  toast(done.calcium ? `Saved · calcium hardness set to ${done.calcium} ppm for the water balance`
    : done.calciumKept != null ? `Saved · calcium hardness in Settings left at ${done.calciumKept} ppm`
    : newest ? 'Saved' : 'Saved at its own date · newer tests still lead');
}

// How the strips have done against the shop's meter, over every shop test that has a strip test near it.
const VERDICT = { agrees: 'agrees', higher: 'strip read higher', lower: 'strip read lower' };
const NOT_COMPARED = {
  unclear: () => 'not compared: the strip was a poor colour match',
  apart: key => `not compared: more than ${S.checkWindow[key]} hours apart, and this reading moves faster than that`,
  dosed: () => 'not compared: something that changes this reading was added in between',
};
function stripCheckCard() {
  const checks = S.stripChecks || [];
  if (!S.tests.some(t => t.source === 'lab')) return '';
  const rows = KEYS.map(key => {
    const fair = checks.map(c => c.readings[key]).filter(r => VERDICT[r.verdict]);
    const count = verdict => fair.filter(r => r.verdict === verdict).length, last = fair[0];
    const parts = [count('higher') && `read higher ${count('higher')}×`, count('lower') && `read lower ${count('lower')}×`].filter(Boolean).join(' · ');
    return `<div><span>${esc(S.chart.params[key].label)}</span><b>${fair.length ? `agreed ${count('agrees')} of ${fair.length}` : 'no fair comparison yet'}</b>
      ${last ? `<small>${parts ? parts + ' · ' : ''}Latest: strip ${plain(key, last.strip)}, shop ${plain(key, last.shop)}</small>` : ''}</div>`;
  }).join('');
  return `<section class="card"><h2>Strips against the pool shop</h2>
    <p class="muted">${checks.length ? `From ${checks.length} shop test${checks.length === 1 ? '' : 's'} with a strip test close by. A strip agrees when the shop's figure is within half a colour step of what the strip showed.`
      : 'No strip test was done within two days of a shop test yet.'} For a fair check, dip a strip in the same water you take to the shop, before adding anything.</p>
    ${checks.length ? `<div class="detail">${rows}</div>` : ''}</section>`;
}

// `justSaved` is the first showing, straight after the report was added: it then says so at the top as well,
// because the confirmation at the bottom is a long scroll away.
function labDetail(t, justSaved = false) {
  const lab = t.lab || {}, st = S.statuses[t.id], check = (S.stripChecks || []).find(c => c.shopId === t.id), extras = Object.entries(lab.extras || {});
  const calcium = S.settings.calciumHardness, late = new Date(t.createdAt) - new Date(t.at) > 3600000;
  const where = [lab.shop, lab.company !== lab.shop && lab.company, lab.meter].filter(Boolean).map(esc).join(' · ');
  const hours = check ? Math.round(check.hours) : 0;
  const apart = !check ? '' : hours < 1 ? 'within the hour' : `${hours} hour${hours === 1 ? '' : 's'} ${check.stripLater ? 'later' : 'earlier'}`;
  const compared = k => { const r = check.readings[k]; return `Your strip, ${apart}: ${plain(k, r.strip)} · ${VERDICT[r.verdict] || NOT_COMPARED[r.verdict](k)}`; };
  sheet.innerHTML = `<div class="sheet-head"><h2>${dayTime(t.at)}</h2>${CLOSE}</div>
    <div class="sheet-body">${justSaved ? '<p class="note saved">✓ Report saved to your history.</p>' : ''}<p class="muted"><b>Pool shop test</b>${where ? ' · ' + where : ''}${lab.origin === 'manual' ? ' · typed in from the printout' : ''}${late ? `<br>Added to the history ${ago(t.createdAt)}, at the date and time of the test itself.` : ''}</p>
    ${t.hasPhoto ? `<img class="photo" src="api/photo?id=${t.id}" alt="The shop's printed report">` : ''}
    <div class="detail">${KEYS.map(k => `<div><span>${esc(S.chart.params[k].label)}</span><b class="${st[k]}">${labFigure(k, t.readings[k].value)} · ${STATUS_WORD[st[k]]}${STATUS_MARK[st[k]]}</b>
      ${check ? `<small>${esc(compared(k))}</small>` : ''}</div>`).join('')}</div>
    ${check ? '' : '<p class="muted">No strip test within two days of this one, so there is nothing to check the strips against. Next time, dip a strip in the same water you take to the shop.</p>'}
    ${extras.length ? `<h3>Also measured</h3><div class="detail">${extras.map(([key, value]) => `<div><span>${esc(factor(key).label)}</span><b>${esc(labFigure(key, value))}</b>
      ${key !== 'ch' ? '' : calcium === Math.round(value) ? '<small>Used for the water balance on the Today screen.</small>'
        : value >= 10 && value <= 2000 ? `<small>Settings has ${calcium == null ? 'no figure' : esc(calcium) + ' ppm'} for the water balance. <button class="link" data-action="use-calcium" data-id="${t.id}" data-value="${Math.round(value)}">Use ${Math.round(value)} instead</button></small>` : ''}</div>`).join('')}</div>` : ''}
    ${lab.conditions?.length ? `<p class="note">The shop noted: ${esc(lab.conditions.join(', ').toLowerCase())}.</p>` : ''}
    ${t.note ? `<p class="note">${esc(t.note)}</p>` : ''}
    ${lab.advice?.length ? `<details class="source"><summary>What the shop advised</summary>
      <p>Kept word for word, for reference. The plan on the Today screen is worked out from the readings by this app's own rules, so its products and amounts are not the shop's.</p>
      ${lab.advice.map(text => `<p class="advice">${esc(text)}</p>`).join('')}</details>` : ''}
    ${typeof lab.url === 'string' && lab.url.startsWith(REPORT_PAGE) ? `<a class="button ghost" href="${esc(lab.url)}" target="_blank" rel="noopener noreferrer">Open the shop's report</a>` : ''}
    <p class="note saved">✓ This report is saved in your history, at ${dayTime(t.at)}.</p>
    ${justSaved ? '<button class="primary big" data-action="close">Done</button>' : ''}
    <button class="danger" data-action="delete-test" data-id="${t.id}">Delete this report</button></div>`;
  if (!sheet.open) sheet.showModal();
  if (justSaved) $('.sheet-body', sheet).scrollTop = 0;
}

// --- People (the owner only) and the signed-in account ------------------------
const NO_HELP = 'autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false"';

// Twelve characters with nothing that reads two ways (no 0/o, 1/l/i).
function suggestPassword() {
  const letters = 'abcdefghjkmnpqrstuvwxyz23456789';
  const pick = [...crypto.getRandomValues(new Uint8Array(24))].filter(b => b < 248).slice(0, 12).map(b => letters[b % 31]).join('');
  return pick.length < 12 ? suggestPassword() : pick.replace(/(.{4})(?=.)/g, '$1-');
}

function peopleCard() {
  if (!S.me.owner) return '';
  return `<section class="card"><h2>People</h2>
    <p class="muted">Anyone you add gets a pool of their own: settings, history and light-box calibration. They cannot see yours, and Home Assistant only ever shows your pool.</p>
    ${S.people.map(p => `<button class="row" data-action="person" data-id="${p.id}"><span class="row-when">${esc(p.name)}<small>${esc(p.username)} · ${p.lastSeenAt ? 'signed in ' + ago(p.lastSeenAt) : 'has not signed in yet'}</small></span>
      <span class="row-values"><i class="${p.disabled ? 'unsure' : 'ok'}">${p.disabled ? 'Access off' : 'Active'}</i></span></button>`).join('')}
    <button class="ghost" data-action="person-add">Add a person</button>
    <p class="muted">${S.runtime.address ? `They sign in at ${esc(S.runtime.address)}` : 'Nobody else can sign in until the app has its own HTTPS address (standalone mode in the app configuration)'}.</p></section>`;
}

function accountCard() {
  const me = S.me;
  if (!me.session) return '';
  return `<section class="card"><h2>Account</h2>
    <p class="muted">Signed in as <b>${esc(me.name || me.username)}</b>${me.name ? ` (${esc(me.username)})` : ''}.${me.owner ? ' Your username and password are set in the app configuration in Home Assistant.' : ''} Signing out signs you out on every device.</p>
    ${me.owner ? '<button class="ghost" data-action="sign-out">Sign out</button>'
      : '<div class="pair"><button class="ghost" data-action="my-password">Change password</button><button class="ghost" data-action="sign-out">Sign out</button></div>'}</section>`;
}

function personAdd() {
  sheet.innerHTML = `<div class="sheet-head"><h2>Add a person</h2>${CLOSE}</div>
    <form class="sheet-body" data-form="person-add">
      <label class="field"><span>Name</span><input name="name" maxlength="40" required autocomplete="off"></label>
      <label class="field"><span>Username</span><input name="username" minlength="2" maxlength="32" required ${NO_HELP}><small>Letters, digits, dots or dashes, with no spaces.</small></label>
      <label class="field"><span>Password</span><input name="password" minlength="10" maxlength="200" required ${NO_HELP} value="${suggestPassword()}">
        <small>At least 10 characters. This one was made up for you. <button type="button" class="link" data-action="suggest-password">Make another</button></small></label>
      <button class="primary big">Add</button></form>`;
  sheet.showModal();
}

// The one moment a password is on screen: straight after the owner set it.
function handOver(title, username, password) {
  sheet.innerHTML = `<div class="sheet-head"><h2>${esc(title)}</h2>${CLOSE}</div>
    <div class="sheet-body"><dl class="about">${S.runtime.address ? `<dt>Address</dt><dd>${esc(S.runtime.address)}</dd>` : ''}
      <dt>Username</dt><dd>${esc(username)}</dd><dt>Password</dt><dd>${esc(password)}</dd></dl>
      <p class="muted">Pass these on now: the password is not shown again, though you can always set a new one. They can change it themselves under Settings.</p>
      <button class="primary big" data-action="close">Done</button></div>`;
  if (!sheet.open) sheet.showModal();
}

function openPerson(id) {
  const p = S.people.find(x => x.id === +id);
  if (!p) return;
  sheet.innerHTML = `<div class="sheet-head"><h2>${esc(p.name)}</h2>${CLOSE}</div>
    <div class="sheet-body"><dl class="about"><dt>Username</dt><dd>${esc(p.username)}</dd><dt>Added</dt><dd>${dateShort(p.createdAt)}</dd>
      <dt>Last signed in</dt><dd>${p.lastSeenAt ? ago(p.lastSeenAt) : 'Not yet'}</dd><dt>Access</dt><dd>${p.disabled ? 'Switched off' : 'On'}</dd></dl>
      <form class="card" data-form="person-password" data-id="${p.id}"><h2>New password</h2>
        <label class="field"><span>Password</span><input name="password" minlength="10" maxlength="200" required ${NO_HELP}>
          <small>Setting one signs them out everywhere. <button type="button" class="link" data-action="suggest-password">Make one up</button></small></label>
        <button class="primary">Set password</button></form>
      <button class="ghost" data-action="person-access" data-id="${p.id}">${p.disabled ? 'Switch access back on' : 'Switch access off'}</button>
      <button class="danger" data-action="person-remove" data-id="${p.id}">Remove and delete their data</button></div>`;
  sheet.showModal();
}

function myPassword() {
  sheet.innerHTML = `<div class="sheet-head"><h2>Change password</h2>${CLOSE}</div>
    <form class="sheet-body" data-form="my-password">
      <label class="field"><span>Current password</span><input type="password" name="current" autocomplete="current-password" required></label>
      <label class="field"><span>New password</span><input type="password" name="password" autocomplete="new-password" minlength="10" maxlength="200" required><small>At least 10 characters.</small></label>
      <button class="primary big">Change password</button></form>`;
  sheet.showModal();
}

// --- First launch on an iPhone: put the app on the Home Screen -------------------
// Opened from a link, the app is a page in Safari with Safari's bars round it. Added to the Home Screen it has
// an icon, opens full screen, and keeps its own sign-in and camera permission. So this comes first of all,
// before signing in: everything after it (the pool, the light box) is then done in the app itself.
// Asked once per browser. Never inside Home Assistant's sidebar, where the page is a frame in someone else's app.
const onIphone = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const onHomeScreen = () => navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
const framed = () => { try { return window.self !== window.top; } catch { return true; } };
const canInstall = () => onIphone() && !onHomeScreen() && !framed();
let installAsked = false;
const installDue = () => canInstall() && !installAsked && !store.get('install-asked');
const SHARE_ICON = '<svg class="inline-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V3m-4 4 4-4 4 4M8 10H6a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1h-2"/></svg>';

function installGuide() {
  if (sheet.open) sheet.close();
  // S is set when this is opened from Settings by someone already signed in, and not on a first launch.
  app.innerHTML = `<div class="login welcome"><span class="boot-drop"></span><h1>${S ? 'Put Pool on your Home Screen' : 'First, put Pool on your Home Screen'}</h1>
    <p class="muted">It then has its own icon and opens full screen like any other app, with no browser bars. It takes a minute${S ? '. The app on the Home Screen keeps its own sign-in, so you sign in once more there.' : ', and is best done now, before you sign in: the app on the Home Screen keeps its own sign-in.'}</p>
    <ol class="prep">
      <li><p><b>Tap the Share button</b> ${SHARE_ICON} in Safari. If you cannot see it, tap the three dots <b>•••</b> beside the address bar first, then <b>Share</b>.</p></li>
      <li><p><b>Scroll down the list</b> and tap <b>Add to Home Screen</b>. If it is not in the list, tap <b>More</b> at the end of it.</p></li>
      <li><p><b>Tap Add</b>, top right. If there is a switch called <b>Open as Web App</b>, leave it on.</p></li>
      <li><p><b>Close Safari</b>, find the <b>Pool</b> icon on your Home Screen and open it. Sign in there, and use it from there from now on.</p></li></ol>
    <p class="muted">No Share button or no "Add to Home Screen"? The link was probably opened inside another app, such as a message or mail app. Choose <b>Open in Safari</b> there first.</p>
    <button class="ghost" data-action="install-skip">${S ? 'Back to the app' : 'Carry on in the browser instead'}</button></div>`;
}

// --- First visit: a new person describes their own pool -------------------------
function setupView() {
  app.innerHTML = `<form class="login" data-form="setup"><span class="boot-drop"></span><h1>Welcome${S.me.name ? ', ' + esc(S.me.name) : ''}</h1>
    <p class="muted">Two things about your pool first. Every amount the app gives is worked out from them.</p>
    <label class="field"><span>Pool volume (litres)</span><input type="number" name="volumeLitres" inputmode="decimal" min="1000" max="2000000" step="any" required placeholder="for example 45000">
      <small>Length × width × average depth in metres, × 1000. The builder's paperwork usually has it.</small></label>
    <label class="field"><span>Pool surface</span><select name="surface" required><option value="" selected disabled>Choose…</option>${Object.entries(S.surfaces).map(([value, label]) => `<option value="${value}">${esc(label)}</option>`).join('')}</select></label>
    <button class="primary big">Continue</button>
    <button type="button" class="link" data-action="sign-out">Sign out</button></form>`;
}

// --- Sign-in ----------------------------------------------------------------
function renderLogin(message = '', typed = '') {
  if (sheet.open) sheet.close();
  const username = typed || store.get('user') || '';
  app.innerHTML = `<form class="login" data-form="login"><span class="boot-drop"></span><h1>Pool</h1>
    <label class="field"><span>Username</span><input type="text" name="username" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required value="${esc(username)}" ${username ? '' : 'autofocus'}></label>
    <label class="field"><span>Password</span><input type="password" name="password" autocomplete="current-password" required ${username ? 'autofocus' : ''}></label>
    ${message ? `<p class="warning">${esc(message)}</p>` : ''}<button class="primary big">Sign in</button></form>`;
}

const forms = {
  login: async (form, data) => {
    await api('login', { username: data.username, password: data.password });
    store.set('user', data.username.trim().toLowerCase());
    await start();
  },
  setup: async (form, data) => {
    S = await api('setup', { volumeLitres: parseFloat(data.volumeLitres), surface: data.surface });
    tab = 'settings'; firstVisit = true; render(true);
    // With the light box still to calibrate, that step comes next; the hint about chemicals follows it.
    if (!calibrationDue()) toast('Saved. ' + CHECK_CHEMICALS);
  },
  'person-add': async (form, data) => {
    S = await api('people', { action: 'add', name: data.name, username: data.username, password: data.password });
    render(); handOver(`${data.name.trim()} can sign in`, data.username.trim().toLowerCase(), data.password);
  },
  'person-password': async (form, data) => {
    const person = S.people.find(p => p.id === +form.dataset.id);
    S = await api('people', { action: 'password', id: person.id, password: data.password });
    render(); handOver(`New password for ${person.name}`, person.username, data.password);
  },
  'my-password': async (form, data) => {
    await api('password', { current: data.current, password: data.password });
    sheet.close(); toast('Password changed');
  },
  'lab-link': async (form, data) => {
    const button = form.querySelector('button.primary');
    button.textContent = 'Fetching the report…';
    try { labSaved(await api('lab', { url: data.url.trim() })); }
    finally { button.textContent = 'Fetch the report'; }
  },
  'lab-manual': async (form, data) => {
    const values = {};
    for (const f of S.labFactors) if (data['v-' + f.key]?.trim()) values[f.key] = parseFloat(data['v-' + f.key]);
    const when = new Date(data.at);
    if (isNaN(when)) throw new Error('Enter the date and time printed on the report.');
    const body = { id: labDraft.id, manual: { at: when.toISOString(), shop: data.shop, values, note: data.note } };
    if (labDraft.photo) body.photo = labDraft.photo;
    labSaved(await api('lab', body));
  },
};

// --- Wiring -----------------------------------------------------------------
const actions = {
  tab: el => { tab = el.dataset.tab; render(true); },
  person: el => openPerson(el.dataset.id),
  'person-add': personAdd,
  'suggest-password': el => { el.closest('form').elements.password.value = suggestPassword(); },
  'person-access': async el => {
    const person = S.people.find(p => p.id === +el.dataset.id);
    try { S = await api('people', { action: 'disable', id: person.id, disabled: !person.disabled }); sheet.close(); render(); toast(person.disabled ? 'Access switched on' : 'Access switched off'); }
    catch (error) { toast(error.message, true); }
  },
  'person-remove': async el => {
    const person = S.people.find(p => p.id === +el.dataset.id);
    if (!confirm(`Remove ${person.name}? Their tests, photos and calibration are deleted for good.`)) return;
    try { S = await api('people', { action: 'remove', id: person.id }); sheet.close(); render(); toast('Removed'); } catch (error) { toast(error.message, true); }
  },
  'my-password': myPassword,
  'sign-out': async () => {
    try { await api('logout', {}); } catch { /* signing out of a session that has already ended */ }
    S = null; renderLogin();
  },
  'new-test': newTest,
  'lab-home': labHome,
  'lab-manual': labManual,
  'lab-photo': () => $('#paper').click(),
  'use-calcium': async el => {
    try { S = await api('settings', { calciumHardness: +el.dataset.value }); render(); openTest(el.dataset.id); toast('Water balance now uses this figure'); } catch (error) { toast(error.message, true); }
  },
  close: () => sheet.close(),
  theme: el => { store.set('theme', el.dataset.value === 'auto' ? null : el.dataset.value); window.poolApplyTheme?.(); render(); },
  'box-live': () => boxLive(),
  'box-capture': boxCapture,
  'box-remember': () => { rememberMarks('box-marks', scan.marks); toast('Ring positions remembered'); newTest(); },
  'box-forget': () => { store.set('box-marks', null); render(); toast('Forgotten'); },
  'calib-home': calibHome,
  'install-guide': () => { installAsked = false; store.set('install-asked', null); installGuide(); },
  'install-skip': () => { installAsked = true; store.set('install-asked', true); start().catch(error => toast(error.message || 'Could not reach the pool app.', true)); },
  // From the first-visit step: the box is switched on for tests on this phone, and the camera opens over the app.
  'calib-start': () => { store.set('box', true); calibAnswered(); render(true); boxLive({ kind: 'calib' }); },
  'calib-later': () => { calibAnswered(); render(true); toast(firstVisit ? 'The reminder is on the Today screen. ' + CHECK_CHEMICALS : 'It stays on the Today screen until it is done'); },
  'no-light-box': async () => {
    try { S = await api('lightbox', { have: false }); render(true); toast(firstVisit ? 'Noted. ' + CHECK_CHEMICALS : 'Noted. Settings → Light box if that changes'); } catch (error) { toast(error.message, true); }
  },
  'calib-live': () => boxLive({ kind: 'calib' }),
  'calib-use': async el => {
    const chart = S.chart, params = Object.fromEntries(chart.padOrder.map((key, r) => [key, scan.grid[r].map(cell => cell.lab)]));
    // The picture is kept with the calibration so a doubtful one can be looked at later.
    const k = Math.min(1, 1600 / Math.max(scan.canvas.width, scan.canvas.height)), small = document.createElement('canvas');
    small.width = Math.round(scan.canvas.width * k); small.height = Math.round(scan.canvas.height * k);
    small.getContext('2d').drawImage(scan.canvas, 0, 0, small.width, small.height);
    el.disabled = true;
    try {
      S = await api('chart', { params, marks: markFractions(scan.marks), photo: small.toDataURL('image/jpeg', 0.88) });
      rememberMarks('chart-marks', scan.marks); store.set('calib', null); store.set('calib-marks', null);
      sheet.close(); scan = null; render(); toast('Calibration in use');
    } catch (error) { toast(error.message, true); el.disabled = false; }
  },
  'calib-reset': async () => {
    if (!confirm('Go back to the built-in chart? The box calibration is discarded.')) return;
    try { S = await api('chart', { reset: true }); sheet.close(); render(); toast('Built-in chart in use'); } catch (error) { toast(error.message, true); }
  },
  dip: dipTimer,
  camera: () => $('#camera').click(),
  library: () => $('#library').click(),
  manual: manualTest,
  zoom: () => { scan.zoom = !scan.zoom; review(); },
  pick: el => { scan.overrides[el.dataset.key] = +el.dataset.value; review(); },
  unpick: el => { delete scan.overrides[el.dataset.key]; review(); },
  'save-test': saveTest,
  'open-test': el => openTest(el.dataset.id),
  dose: async el => {
    const step = S.current.steps.find(s => s.key === el.dataset.key);
    el.disabled = true;
    try { S = await api('doses', { testId: S.current.testId, stepKey: step.key, product: step.product, amount: step.amount }); render(); toast('Logged'); }
    catch (error) { toast(error.message, true); el.disabled = false; }
  },
  'undo-dose': async el => {
    try { S = await api('void', { group: 'doses', id: el.dataset.id }); render(); toast('Removed'); } catch (error) { toast(error.message, true); }
  },
  'delete-test': async el => {
    if (!confirm('Delete this test? Its readings leave the history and Home Assistant shows the previous test.')) return;
    try { S = await api('void', { group: 'tests', id: el.dataset.id }); sheet.close(); render(); toast('Deleted'); } catch (error) { toast(error.message, true); }
  },
};

document.addEventListener('click', event => {
  const el = event.target.closest('[data-action]');
  if (el && actions[el.dataset.action]) { event.preventDefault(); actions[el.dataset.action](el); }
});
document.addEventListener('change', event => {
  if (event.target.dataset.setting) saveSetting(event.target);
  if (event.target.dataset.local) store.set(event.target.dataset.local, event.target.checked);
  if (event.target.id === 'lens' && cam) { store.set('lens', event.target.value); boxLive(cam.mode); }
  if (event.target.id === 'camera' || event.target.id === 'library') { loadPhoto(event.target.files[0]); event.target.value = ''; }
  if (event.target.id === 'paper') { loadPaper(event.target.files[0]); event.target.value = ''; }
});
document.addEventListener('submit', async event => {
  const form = event.target, handler = forms[form.dataset.form];
  if (!handler) return;
  event.preventDefault();
  const button = form.querySelector('button.primary');
  button.disabled = true;
  try { await handler(form, Object.fromEntries(new FormData(form))); }
  catch (error) { if (form.dataset.form === 'login') renderLogin(error.message, form.elements.username.value); else toast(error.message, true); }
  finally { button.disabled = false; }
});
sheet.addEventListener('click', event => { if (event.target === sheet) sheet.close(); });
sheet.addEventListener('close', closeCamera);
document.addEventListener('visibilitychange', () => { if (document.hidden && cam) { const mode = cam.mode; closeCamera(); cam = null; sheet.dataset.resume = JSON.stringify(mode); } else if (!document.hidden && sheet.dataset.resume) { const mode = JSON.parse(sheet.dataset.resume); delete sheet.dataset.resume; if (sheet.open && $('#live')) boxLive(mode); } });

async function start() {
  if (installDue()) return installGuide();
  const session = await fetch('api/session', { cache: 'no-store' }).then(r => r.json());
  if (!session.authenticated) return renderLogin();
  S = await api('state'); render();
}
async function refresh() {
  if (!S || S.needsSetup || $('.welcome') || sheet.open || document.hidden || document.activeElement?.dataset?.setting) return;
  try { const next = await api('state'); if (next.revision !== S.revision || tab === 'home') { S = next; render(); } } catch { /* offline: keep what we have */ }
}
document.addEventListener('visibilitychange', refresh);
setInterval(refresh, 5 * 60 * 1000);
// This file is loaded from v/<version>/app.js (see the server), and the worker is told the same version.
const VERSION = new URL(import.meta.url).pathname.match(/\/v\/([^/]+)\//)?.[1];
if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('sw.js' + (VERSION ? '?v=' + VERSION : '')).catch(() => {});
start().catch(error => { app.innerHTML = `<div class="boot"><p>${esc(error.message || 'Could not reach the pool app.')}</p><button class="primary" data-action="reload">Try again</button></div>`; });
actions.reload = () => location.reload();

// Test hook: lets an automated check feed an image without the file picker.
window.poolLoadPhoto = loadPhoto;
