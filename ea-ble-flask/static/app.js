/* ==========================================================
   EA-BLE Simulator - browser side
   1. Tab buttons: show one panel at a time (no page reload).
   2. Run buttons: send the inputs to the Flask API, then show
      the returned results in simple tables.
   ========================================================== */

const $ = id => document.getElementById(id);
const num = (x, d = 1) => (x == null || isNaN(x)) ? '–' : Number(x).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const time = s => s < 120 ? num(s, 1) + ' s' : s < 7200 ? num(s / 60, 1) + ' min' : num(s / 3600, 1) + ' h';

/* ---------------- 1. tabs ---------------- */
function showTab(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === name));
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  history.replaceState(null, '', '#' + name);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
document.querySelectorAll('[data-go]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.go)));
if (location.hash && $(location.hash.slice(1))) showTab(location.hash.slice(1));

/* ---------------- 2. talking to the Flask server ---------------- */
async function callApi(url, data, button) {
  const label = button.textContent;
  button.disabled = true; button.textContent = 'Running…';
  try {
    const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    if (!response.ok) throw new Error('Server error ' + response.status);
    return await response.json();
  } catch (err) {
    alert('Could not reach the simulator: ' + err.message);
    return null;
  } finally {
    button.disabled = false; button.textContent = label;
  }
}

/* ---------------- 3. reading the values typed by the user ---------------- */
/* Checks one number box against its min/max. Returns the number, or null if invalid. */
function readNumber(id, errorBox) {
  const box = $(id), v = Number(box.value), min = Number(box.min), max = Number(box.max);
  const ok = box.value.trim() !== '' && !isNaN(v) && v >= min && v <= max;
  box.classList.toggle('invalid', !ok);
  if (!ok) {
    const name = box.parentElement.firstChild.textContent.trim();
    errorBox.textContent = `${name} must be between ${min} and ${max}.`;
    box.focus();
  }
  return ok ? v : null;
}

/* Reads several boxes; returns {key: value} or null if any box is invalid. */
function readInputs(fields, errorId) {
  const errorBox = $(errorId); errorBox.textContent = '';
  const out = {};
  for (const [key, id] of Object.entries(fields)) {
    const v = readNumber(id, errorBox);
    if (v === null) return null;
    out[key] = v;
  }
  return out;
}

/* The five start values shared by the run forms (prefix m, p, r or c). */
const startFields = p => ({ hours: p + '-hours', soc: p + '-soc', capacitance: p + '-cap', distance: p + '-dist', seed: p + '-seed' });
const usedLine = text => `<p class="note">Start values used: ${text}</p>`;

/* Objective 1: run one simulation */
$('m-run').addEventListener('click', async () => {
  const start = readInputs(startFields('m'), 'm-error');
  if (!start) return;
  const r = await callApi('/api/model/run', { scenario: $('m-scenario').value, ...start }, $('m-run'));
  if (!r) return;
  const rows = r.rows.map(x => {
    const d = x.unit === '' ? 0 : 1, u = x.unit ? ' ' + x.unit : '';
    const bad = x.measure === 'Brown-outs' && x.static > 0 ? ' bad' : '';
    return `<tr><td>${x.measure}</td><td class="num static${bad}">${num(x.static, d)}${u}</td><td class="num proposed">${num(x.proposed, d)}${u}</td></tr>`;
  }).join('');
  $('m-results').innerHTML = `<h2>Results: ${r.title}</h2>
    <table><tr><th>Measure</th><th class="num">Static BLE</th><th class="num">Proposed</th></tr>${rows}</table>
    <div class="box soft"><b>Accuracy check.</b> Energy in − energy out should equal the change in stored energy.
      Error: <span class="proposed">${r.balance.proposed.toExponential(1)} J</span> (proposed) and
      <span class="static">${r.balance.static.toExponential(1)} J</span> (static), which is practically zero.</div>`;
});

/* Objective 2: one decision */
$('d-check').addEventListener('click', async () => {
  const input = readInputs({ soc: 'd-soc', harvest: 'd-ph', capacitance: 'd-cap' }, 'd-error');
  if (!input) return;
  const r = await callApi('/api/protocol/decision', input, $('d-check'));
  if (!r) return;
  $('d-result').innerHTML = r.mode === 'HIBERNATE'
    ? '<p><b class="bad">HIBERNATE</b> – the radio is switched off until energy returns.</p>'
    : `<table><tr><th>Mode</th><th>Advertising interval</th><th>TX power / channels</th><th>Energy budget</th></tr>
       <tr><td class="proposed">${r.mode}</td><td>${num(r.interval_ms, 0)} ms</td><td>${r.tx} dBm / ${r.channels}</td><td>${num(r.budget_uW)} µW</td></tr></table>
       <p class="note">More stored energy or more harvest gives a shorter interval (faster advertising).</p>`;
});

/* Objective 2: time in each mode */
$('p-run').addEventListener('click', async () => {
  const start = readInputs(startFields('p'), 'p-error');
  if (!start) return;
  const r = await callApi('/api/protocol/run', start, $('p-run'));
  if (!r) return;
  const head = '<tr><th>Scenario</th>' + r.columns.slice(0, 5).map(c => `<th class="num">${c}</th>`).join('') + '<th class="num">OFF</th></tr>';
  const rows = r.rows.map(x => `<tr><td>${x.scenario}</td>${x.modes.map(v => `<td class="num">${v} %</td>`).join('')}<td class="num">${x.off} %</td></tr>`).join('');
  $('p-results').innerHTML = `<table>${head}${rows}</table>
    <p class="note">Share of time in each mode. Active users stay in the high modes; quiet users drop to E-SAVING and SURVIVAL.</p>${usedLine(r.used)}`;
});

/* Objective 3: drawer test */
$('r-run').addEventListener('click', async () => {
  const start = readInputs(startFields('r'), 'r-error');
  if (!start) return;
  const r = await callApi('/api/reserve/drawer', start, $('r-run'));
  if (!r) return;
  const rows = r.rows.map(x => `<tr><td class="${x.style}">${x.config}</td><td class="num">${num(x.findable_h)} h</td>
    <td class="num">${x.first_brownout_h == null ? 'none' : num(x.first_brownout_h) + ' h'}</td><td class="num">${num(x.packets, 0)}</td></tr>`).join('');
  $('r-results').innerHTML = `<table><tr><th>Configuration</th><th class="num">Time findable</th><th class="num">First brown-out</th><th class="num">Packets received</th></tr>${rows}</table>
    <p class="note">With no harvest at all, the reserve keeps the key findable longer and the tag hibernates before it browns out.</p>${usedLine(r.used)}`;
});

/* Objective 4: comparison */
const FORMAT = { brown: x => num(x, 1), avail: x => num(x, 1) + ' %', latency: time, pdr: x => num(x, 2) + ' %', rx: x => num(x, 0), lost: x => num(x, 1) };
$('c-run').addEventListener('click', async () => {
  const start = readInputs({ ...startFields('c'), runs: 'c-runs' }, 'c-error');
  if (!start) return;
  const r = await callApi('/api/compare', start, $('c-run'));
  if (!r) return;
  $('c-summary').innerHTML = '<h2>Overall result</h2><div class="figures">' + r.overall.map(m =>
    `<div class="figure"><div class="what">${m.measure.replace(' (%)', '').replace(' (s)', '')}</div>
     <div class="value"><span class="static">${FORMAT[m.key](m.static)}</span> → <span class="proposed">${FORMAT[m.key](m.proposed)}</span></div>
     <div class="what">static → proposed</div></div>`).join('') + '</div>';
  let rows = '';
  r.scenarios.forEach(s => s.values.forEach((v, i) => {
    rows += `<tr><td>${i === 0 ? '<b>' + s.scenario + '</b>' : ''}</td><td>${v.measure.replace(' (%)', '').replace(' (s)', '')}</td>
      <td class="num static">${FORMAT[v.key](v.static)}</td><td class="num proposed">${FORMAT[v.key](v.proposed)}</td></tr>`;
  }));
  $('c-results').innerHTML = `<h2>Results per scenario (average of ${r.runs} run${r.runs > 1 ? 's' : ''}, ${r.hours} hours)</h2>
    <table><tr><th>Scenario</th><th>Measure</th><th class="num">Static BLE</th><th class="num">Proposed</th></tr>${rows}</table>${usedLine(r.used)}`;
});
