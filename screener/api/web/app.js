/* Screener dashboard.
 *
 * Deliberately dependency-free vanilla JS: this is a local single-user tool and
 * a build step would add more maintenance than it saves.
 *
 * The guiding rule for this UI is that the scoring is never hidden. Every score
 * shown anywhere can be opened to reveal each parameter's measured value, its
 * weighted contribution in the same units as the total, and a sentence
 * explaining what it means — for the whole ranked list, not just the leaders.
 */

'use strict';

const state = {
  meta: null,
  status: null,
  ranking: null,
  config: null,
  historyRunId: null,
};

/* ----------------------------------------------------------- utilities */

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && body.detail;
    const message = typeof detail === 'string'
      ? detail
      : (detail && detail.message) || `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

function toast(message, isError = false) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.toggle('is-error', isError);
  node.hidden = false;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => { node.hidden = true; }, isError ? 8000 : 4000);
}

function formatTimestamp(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function relativeTime(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const minutes = Math.round((Date.now() - then) / 60000);
  if (Math.abs(minutes) < 1) return 'just now';
  if (minutes > 0) {
    if (minutes < 60) return `${minutes}m ago`;
    if (minutes < 1440) return `${Math.round(minutes / 60)}h ago`;
    return `${Math.round(minutes / 1440)}d ago`;
  }
  const ahead = -minutes;
  if (ahead < 60) return `in ${ahead}m`;
  if (ahead < 1440) return `in ${Math.round(ahead / 60)}h`;
  return `in ${Math.round(ahead / 1440)}d`;
}

/* Direction is never communicated by colour alone: each pill carries an arrow
   glyph and a text label as well. Required for colour-vision accessibility. */
function leanPill(direction, label) {
  const glyphs = { call: '▲', put: '▼', neutral: '—' };
  const pill = el('span', `lean lean-${direction || 'neutral'}`);
  pill.append(el('span', 'lean-glyph', glyphs[direction] || '—'));
  pill.append(el('span', null, label || 'No clear edge'));
  return pill;
}

/* ------------------------------------------------------------- loading */

async function loadMeta() {
  state.meta = await api('/api/meta');
  $('#disclaimer').textContent = state.meta.disclaimer;
}

async function loadStatus() {
  try {
    state.status = await api('/api/status');
  } catch (err) {
    return;
  }
  const last = state.status.last_run;
  const lastNode = $('#last-refresh');
  if (last) {
    const when = formatTimestamp(last.finished_at || last.started_at);
    const rel = relativeTime(last.finished_at || last.started_at);
    lastNode.textContent = `${when} (${rel})`;
    lastNode.title = `Run ${last.id} — ${last.status}, triggered by ${last.trigger}`;
  } else {
    lastNode.textContent = 'never';
  }

  const nextNode = $('#next-refresh');
  if (!state.status.scheduler_running) {
    nextNode.textContent = 'scheduler off';
  } else if (state.status.next_run_at) {
    nextNode.textContent =
      `${formatTimestamp(state.status.next_run_at)} (${relativeTime(state.status.next_run_at)})`;
    nextNode.title = `${state.status.next_run_trigger} — only fires while this app is running`;
  } else {
    nextNode.textContent = '—';
  }

  $('#refresh-btn').disabled = Boolean(state.status.refresh_in_progress);
  if (state.status.refresh_in_progress) $('#refresh-btn').textContent = 'Refreshing…';
  else $('#refresh-btn').textContent = 'Refresh Now';
}

async function loadRankings() {
  state.ranking = await api('/api/rankings/latest');
  renderRankings();
}

/* ------------------------------------------------------------ rankings */

function renderRankings() {
  const body = $('#rankings-body');
  const banner = $('#run-banner');
  body.textContent = '';
  banner.textContent = '';

  const data = state.ranking;
  if (!data || !data.available) {
    body.append(el('p', 'empty',
      (data && data.message) || 'No ranking yet. Press Refresh Now to build one.'));
    return;
  }

  const run = data.run || {};
  banner.append(el('span', null,
    `Run ${run.id} · ${formatTimestamp(run.started_at)} · ${run.trigger} · ${data.entries.length} tickers · posture: ${run.posture === 'sell' ? 'selling premium' : 'buying premium'}`));
  const tag = el('span', 'cfg-tag' + (run.is_current_config ? '' : ' is-different'),
    `config ${String(run.fingerprint || '').slice(0, 8)}`);
  tag.title = run.is_current_config
    ? 'Produced with your current settings.'
    : 'Produced with different settings from your current ones. Refresh to re-rank.';
  banner.append(tag);
  if (run.message) {
    const warn = el('span', 'badge is-partial', run.message.slice(0, 160));
    warn.title = run.message;
    banner.append(warn);
  }

  const topN = data.top_n || 5;
  const top = data.entries.filter((e) => e.rank <= topN);
  const rest = data.entries.filter((e) => e.rank > topN);
  const maxScore = Math.max(...data.entries.map((e) => e.composite), 1);

  if (top.length) {
    body.append(sectionLabel('Best candidates right now',
      `Top ${top.length} of ${data.entries.length} by composite score`));
    top.forEach((entry) => body.append(rankRow(entry, maxScore, true, run.id)));
  }
  if (rest.length) {
    body.append(sectionLabel('Rest of the watchlist', 'Ranked in order — click any row for its full breakdown'));
    rest.forEach((entry) => body.append(rankRow(entry, maxScore, false, run.id)));
  }
  loadConcentration(run.id, body);
}

/* The ranking scores each ticker alone, so it cannot see that its leaders move
   together. This is the portfolio view of the same list. */
async function loadConcentration(runId, body) {
  let data;
  try {
    data = await api(`/api/rankings/${runId}/concentration`);
  } catch (err) {
    return;
  }
  if (!data.available) return;

  const panel = el('div', 'note');
  const heading = el('strong', null,
    `Top ${data.nominal_positions} candidates ≈ ${data.effective_positions} independent `
    + `bet${data.effective_positions === 1 ? '' : 's'}`);
  panel.append(heading);
  if (data.average_correlation != null) {
    panel.append(el('span', null, `  ·  average correlation ${data.average_correlation.toFixed(2)}`));
  }
  (data.warnings || []).forEach((warning) => {
    panel.append(document.createElement('br'));
    panel.append(el('span', null, warning));
  });
  if (data.effective_positions < data.nominal_positions) {
    panel.classList.add('note-warn');
  }
  body.append(panel);
}

function sectionLabel(title, sub) {
  const wrap = el('div', 'section-label');
  wrap.append(el('h3', null, title));
  wrap.append(el('span', null, sub));
  return wrap;
}

function rankRow(entry, maxScore, isTop, runId) {
  const row = el('button', 'rank-row' + (isTop ? ' is-top' : ''));
  row.type = 'button';
  row.append(el('span', 'rank-num', `${entry.rank}`));

  const symWrap = el('span');
  symWrap.append(el('span', 'rank-sym', entry.symbol));
  if (entry.spot != null) {
    symWrap.append(document.createElement('br'));
    symWrap.append(el('span', 'rank-spot', `$${Number(entry.spot).toFixed(2)}`));
  }
  row.append(symWrap);

  const scoreCell = el('span', 'score-cell');
  const track = el('span', 'score-track');
  const fill = el('span', 'score-fill');
  fill.style.width = `${Math.max(2, (entry.composite / maxScore) * 100)}%`;
  track.append(fill);
  scoreCell.append(track);
  scoreCell.append(el('span', 'score-value', entry.composite.toFixed(1)));
  row.append(scoreCell);

  row.append(leanPill(entry.direction, entry.direction_label));

  const [counted, considered] = String(entry.completeness || '0/0').split('/').map(Number);
  const badge = el('span', 'badge' + (counted < considered ? ' is-partial' : ''),
    `${entry.completeness}`);
  badge.title = counted < considered
    ? `${considered - counted} parameter(s) could not be computed. Their weight was redistributed across the rest — open the row to see which and why.`
    : 'All configured parameters computed.';
  row.append(badge);

  row.addEventListener('click', () => openDetail(entry, runId));
  return row;
}

/* -------------------------------------------------------------- detail */

function openDetail(entry, runId) {
  const content = $('#detail-content');
  content.textContent = '';

  const head = el('div', 'detail-head');
  const titleWrap = el('div');
  const title = el('h2', null, entry.symbol);
  title.id = 'detail-title';
  titleWrap.append(title);
  titleWrap.append(leanPill(entry.direction, entry.direction_label));
  head.append(titleWrap);

  const hero = el('div', 'hero');
  hero.append(el('span', 'hero-value', entry.composite.toFixed(1)));
  hero.append(el('span', 'hero-label', '/ 100 composite'));
  head.append(hero);
  content.append(head);

  /* Headline stats */
  const stats = el('dl', 'stat-row');
  const addStat = (label, value, title) => {
    const stat = el('div', 'stat');
    stat.append(el('dt', null, label));
    stat.append(el('dd', null, value));
    if (title) stat.title = title;
    stats.append(stat);
  };
  addStat('Rank', `#${entry.rank}`);
  addStat('Conviction', `${(entry.conviction * 100).toFixed(0)}%`,
    'How strongly the stock-layer parameters agree on a direction. This also scales the options-layer contributions.');
  addStat('Net signal', entry.net_signal.toFixed(3),
    'Weighted mean of the signed stock signals, from -1 (fully bearish) to +1 (fully bullish).');
  addStat('Parameters used', entry.completeness);
  if (entry.spot != null) addStat('Last price', `$${Number(entry.spot).toFixed(2)}`);
  content.append(stats);

  if (entry.tie_break_note) {
    content.append(el('p', 'note', entry.tie_break_note));
  }

  /* How the score was built */
  content.append(el('h3', null, 'How this score was built'));
  content.append(el('p', 'param-why',
    entry.direction === 'put'
      ? 'This ticker reads bearish, so a positive contribution means the parameter supports the put lean. A negative contribution means that parameter disagrees with the overall direction.'
      : 'A positive contribution means the parameter supports the overall direction. A negative contribution means that parameter disagrees with it.'));
  const tableWrap = el('div', 'table-scroll');
  tableWrap.append(parameterTable(entry));
  content.append(tableWrap);

  /* Suggested contract */
  content.append(el('h3', null, 'Suggested contract'));
  if (entry.contract) {
    content.append(contractBlock(entry.contract));
  } else {
    const why = (entry.options_notes && entry.options_notes.length)
      ? entry.options_notes.join(' ')
      : 'No contract was selected.';
    content.append(el('p', 'note note-warn', why));
  }

  if (runId) {
    content.append(el('h3', null, 'Order ticket'));
    const ticketBox = el('div');
    ticketBox.append(el('p', 'param-why', 'Loading ticket…'));
    content.append(ticketBox);
    loadTicket(runId, entry.symbol, ticketBox);

    content.append(el('h3', null, 'Your decision'));
    content.append(el('p', 'param-why',
      'Recording what you do here is what lets the weights be measured instead of assumed. '
      + 'Marking what you skip matters as much as what you take — without it there is no '
      + 'comparison group.'));
    content.append(decisionControls(runId, entry));
  }

  if (entry.forward_returns && entry.forward_returns.length) {
    content.append(el('h3', null, 'What happened afterwards'));
    content.append(forwardReturnsBlock(entry));
  }

  content.append(el('p', 'note',
    state.meta ? state.meta.disclaimer : 'Personal research tool. Not financial advice.'));

  $('#detail-overlay').hidden = false;
  $('#detail-close').focus();
}

function parameterTable(entry) {
  const table = el('table', 'param-table');
  const thead = el('thead');
  const headRow = el('tr');
  ['Parameter', 'Value', 'Weight', 'Contribution', 'What it means'].forEach((label) => {
    headRow.append(el('th', null, label));
  });
  thead.append(headRow);
  table.append(thead);

  const tbody = el('tbody');
  const params = entry.parameters || [];
  const maxAbs = Math.max(0.01, ...params.map((p) => Math.abs(p.contribution || 0)));

  params.forEach((param) => {
    const row = el('tr', param.status === 'ok' ? '' : 'is-missing');

    const nameCell = el('td');
    nameCell.append(el('div', 'param-name', param.label));
    nameCell.append(el('div', 'param-layer',
      param.layer === 'stock' ? 'stock layer' : 'options layer'));
    if (param.status !== 'ok') {
      const pill = el('span', 'status-pill warn',
        param.status === 'not_applicable' ? 'n/a' : 'no data');
      nameCell.append(pill);
    }
    row.append(nameCell);

    row.append(el('td', 'param-value', param.status === 'ok' ? param.display_value : '—'));

    const weightCell = el('td', 'param-value');
    if (param.status === 'ok') {
      weightCell.textContent = `${param.weight}`;
      weightCell.title = `${param.effective_weight.toFixed(1)}% of the score after redistribution`;
    } else {
      weightCell.textContent = `${param.weight}`;
      weightCell.title = 'Weight redistributed across the parameters that did compute.';
    }
    row.append(weightCell);

    row.append(contributionCell(param, maxAbs));
    row.append(el('td', 'param-why', param.explanation || ''));
    tbody.append(row);
  });

  /* The breakdown literally adds up to the composite — show that. */
  const totalRow = el('tr', 'total-row');
  totalRow.append(el('td', null, 'Composite'));
  totalRow.append(el('td', null, ''));
  totalRow.append(el('td', null, ''));
  const totalCell = el('td', 'contrib-cell');
  totalCell.append(el('span', 'contrib-num pos', entry.composite.toFixed(2)));
  totalRow.append(totalCell);
  totalRow.append(el('td', 'param-why',
    'Sum of the contributions above, bounded to 0–100.'));
  tbody.append(totalRow);

  table.append(tbody);
  return table;
}

function contributionCell(param, maxAbs) {
  const cell = el('td', 'contrib-cell');
  if (param.status !== 'ok') {
    cell.append(el('span', 'contrib-num zero', '—'));
    return cell;
  }
  const value = param.contribution || 0;
  const bar = el('div', 'contrib-bar');
  bar.append(el('div', 'contrib-axis'));
  const fill = el('div', `contrib-fill ${value >= 0 ? 'pos' : 'neg'}`);
  fill.style.width = `${(Math.abs(value) / maxAbs) * 50}%`;
  bar.append(fill);
  cell.append(bar);

  const cls = value > 0.005 ? 'pos' : (value < -0.005 ? 'neg' : 'zero');
  cell.append(el('span', `contrib-num ${cls}`,
    `${value >= 0 ? '+' : ''}${value.toFixed(2)}`));
  return cell;
}

function contractBlock(contract) {
  const wrap = el('div', 'perf-card');
  const heading = `${contract.strike} ${contract.kind} · exp ${contract.expiry} · ${contract.dte}d`;
  wrap.append(el('h4', null, heading));
  const line = (label, value) => {
    const div = el('div', 'perf-line');
    div.append(el('span', null, label));
    div.append(el('span', 'val', value));
    wrap.append(div);
  };
  line('Delta', Number(contract.delta).toFixed(3));
  line('Mid', `$${Number(contract.mid).toFixed(2)}`);
  line('Bid / Ask', `$${Number(contract.bid).toFixed(2)} / $${Number(contract.ask).toFixed(2)}`);
  line('Spread', `${Number(contract.spread_pct).toFixed(1)}%`);
  line('Open interest', Number(contract.open_interest).toLocaleString());
  line('Implied vol', `${(Number(contract.implied_volatility) * 100).toFixed(1)}%`);
  wrap.append(el('p', 'param-why',
    'Delta is computed with Black-Scholes from this contract\'s implied volatility, since the data source publishes no greeks. Quotes may be stale outside market hours.'));
  return wrap;
}

function forwardReturnsBlock(entry) {
  const grid = el('div', 'perf-grid');
  entry.forward_returns.forEach((fr) => {
    const card = el('div', 'perf-card');
    card.append(el('h4', null, `+${fr.horizon_days} trading day${fr.horizon_days === 1 ? '' : 's'}`));
    const raw = el('div', 'perf-line');
    raw.append(el('span', null, 'Underlying move'));
    raw.append(el('span', `val ${fr.return_pct >= 0 ? 'pos' : 'neg'}`,
      `${fr.return_pct >= 0 ? '+' : ''}${fr.return_pct.toFixed(2)}%`));
    card.append(raw);

    const dir = el('div', 'perf-line');
    dir.append(el('span', null, 'Direction-adjusted'));
    dir.append(el('span', `val ${fr.directional_pct >= 0 ? 'pos' : 'neg'}`,
      `${fr.directional_pct >= 0 ? '+' : ''}${fr.directional_pct.toFixed(2)}%`));
    card.append(dir);
    grid.append(card);
  });
  return grid;
}

async function loadTicket(runId, symbol, container) {
  let data;
  try {
    data = await api(`/api/rankings/${runId}/${encodeURIComponent(symbol)}/ticket`);
  } catch (err) {
    container.textContent = '';
    container.append(el('p', 'note note-warn', `Could not build a ticket: ${err.message}`));
    return;
  }

  container.textContent = '';
  const pre = el('pre', 'ticket-pre', data.text);
  container.append(pre);

  const actions = el('div', 'settings-actions');
  const copy = el('button', 'btn-secondary', 'Copy ticket');
  copy.type = 'button';
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(data.text);
      toast('Ticket copied.');
    } catch (err) {
      toast('Could not copy — select the text manually.', true);
    }
  });
  actions.append(copy);

  const instruments = ['auto', 'option', 'equity', 'leveraged_etf'];
  const select = el('select');
  instruments.forEach((name) => {
    const option = el('option', null, name.replace('_', ' '));
    option.value = name;
    select.append(option);
  });
  select.addEventListener('change', async () => {
    container.textContent = '';
    container.append(el('p', 'param-why', 'Rebuilding…'));
    try {
      const rebuilt = await api(
        `/api/rankings/${runId}/${encodeURIComponent(symbol)}/ticket?instrument=${select.value}`);
      container.textContent = '';
      container.append(el('pre', 'ticket-pre', rebuilt.text));
      container.append(actions);
    } catch (err) {
      container.textContent = '';
      container.append(el('p', 'note note-warn', err.message));
    }
  });
  actions.append(select);
  actions.append(el('span', 'hint', 'Nothing is placed from here.'));
  container.append(actions);
}

function decisionControls(runId, entry) {
  const wrap = el('div', 'decision-row');
  const options = [
    ['taken', 'Taken'],
    ['passed', 'Passed'],
    ['watching', 'Watching'],
  ];

  const note = el('input');
  note.type = 'text';
  note.placeholder = 'Optional note — why you took or skipped it';
  note.className = 'decision-note';

  const buttons = options.map(([value, label]) => {
    const button = el('button', 'decision-btn', label);
    button.type = 'button';
    button.dataset.decision = value;
    button.addEventListener('click', async () => {
      try {
        await api('/api/decisions', {
          method: 'POST',
          body: JSON.stringify({
            run_id: runId, symbol: entry.symbol,
            decision: value, note: note.value || null,
          }),
        });
        buttons.forEach((b) => b.classList.toggle('is-active', b.dataset.decision === value));
        toast(`${entry.symbol} marked ${label.toLowerCase()}.`);
      } catch (err) {
        toast(`Could not record: ${err.message}`, true);
      }
    });
    return button;
  });

  buttons.forEach((b) => wrap.append(b));
  wrap.append(note);

  // Reflect anything already recorded for this run/ticker.
  api(`/api/decisions?run_id=${runId}`).then((data) => {
    const existing = (data.decisions || []).find((d) => d.symbol === entry.symbol);
    if (!existing) return;
    buttons.forEach((b) => b.classList.toggle('is-active', b.dataset.decision === existing.decision));
    if (existing.note) note.value = existing.note;
  }).catch(() => {});

  return wrap;
}

/* ------------------------------------------------------------- journal */

async function loadJournal() {
  const body = $('#journal-body');
  body.textContent = '';
  let data;
  try {
    data = await api('/api/calibration');
  } catch (err) {
    body.append(el('p', 'empty', `Could not load the journal: ${err.message}`));
    return;
  }

  const verdict = el('div', 'note' + (data.actionable ? '' : ' note-warn'));
  verdict.append(el('strong', null, data.verdict));
  body.append(verdict);

  const sample = el('div', 'perf-grid');
  const card = el('div', 'perf-card');
  card.append(el('h4', null, 'Sample'));
  const line = (label, value) => {
    const row = el('div', 'perf-line');
    row.append(el('span', null, label));
    row.append(el('span', 'val', String(value)));
    card.append(row);
  };
  line('Taken', data.sample.taken);
  line('Passed', data.sample.passed);
  line('Watching', data.sample.watching);
  line('Resolved', `${data.sample.resolved_taken} / ${data.sample.minimum_for_suggestions} needed`);
  sample.append(card);

  if (data.taken_mean != null || data.passed_mean != null) {
    const perf = el('div', 'perf-card');
    perf.append(el('h4', null, `Outcome at +${data.horizon_days} days`));
    const row = (label, value) => {
      const div = el('div', 'perf-line');
      div.append(el('span', null, label));
      const text = value == null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
      div.append(el('span', `val ${value >= 0 ? 'pos' : 'neg'}`, text));
      perf.append(div);
    };
    row('Taken avg', data.taken_mean);
    row('Passed avg', data.passed_mean);
    row('Selection edge', data.selection_edge);
    if (data.hit_rate != null) {
      const div = el('div', 'perf-line');
      div.append(el('span', null, 'Hit rate'));
      div.append(el('span', 'val', `${data.hit_rate.toFixed(0)}%`));
      perf.append(div);
    }
    sample.append(perf);
  }
  body.append(sample);

  (data.warnings || []).forEach((warning) => {
    body.append(el('p', 'note note-warn', warning));
  });

  if (!data.parameters || !data.parameters.length) {
    // Nothing has resolved yet, so an empty table with headers would just look broken.
    body.append(el('p', 'empty',
      'Per-parameter analysis appears once decisions resolve. Mark candidates as you go — '
      + 'the comparison needs both the ones you took and the ones you skipped.'));
    return;
  }

  body.append(el('h3', null, 'Which parameters separated winners from losers'));
  body.append(el('p', 'param-why',
    'Among the trades you took, the average contribution of each parameter in the winners '
    + 'versus the losers. A large gap means that parameter was doing real work.'));

  const table = el('table', 'param-table');
  const head = el('tr');
  ['Parameter', 'In winners', 'In losers', 'Separation', 'Weight', 'Suggestion']
    .forEach((label) => head.append(el('th', null, label)));
  // Element.append() returns undefined, so these cannot be chained.
  const thead = el('thead');
  thead.append(head);
  table.append(thead);

  const tbody = el('tbody');
  (data.parameters || []).forEach((param) => {
    const row = el('tr', param.suggested_weight == null ? 'is-missing' : '');
    row.append(el('td', 'param-name', param.label));
    const fmt = (v) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}`);
    row.append(el('td', 'param-value', fmt(param.winners_mean)));
    row.append(el('td', 'param-value', fmt(param.losers_mean)));
    row.append(el('td', 'param-value', fmt(param.separation)));
    row.append(el('td', 'param-value',
      param.suggested_weight == null
        ? String(param.current_weight)
        : `${param.current_weight} → ${param.suggested_weight}`));
    row.append(el('td', 'param-why', param.rationale));
    tbody.append(row);
  });
  table.append(tbody);
  const scroller = el('div', 'table-scroll');
  scroller.append(table);
  body.append(scroller);
}

function closeDetail() {
  $('#detail-overlay').hidden = true;
}

/* ------------------------------------------------------------- history */

async function loadHistory() {
  const body = $('#history-body');
  body.textContent = '';
  let data;
  try {
    data = await api('/api/runs?limit=100');
  } catch (err) {
    body.append(el('p', 'empty', `Could not load history: ${err.message}`));
    return;
  }
  if (!data.runs.length) {
    body.append(el('p', 'empty', 'No past rankings stored yet.'));
    return;
  }

  const list = el('div', 'history-list');
  data.runs.forEach((run) => {
    const row = el('button', 'history-row');
    row.type = 'button';

    const when = el('div');
    when.append(el('div', 'history-when', formatTimestamp(run.started_at)));
    when.append(el('div', 'history-meta', `Run ${run.id} · ${run.trigger}`));
    row.append(when);

    row.append(el('div', 'history-meta', `${run.entry_count} tickers`));
    row.append(el('div', 'history-meta', run.posture === 'sell' ? 'selling' : 'buying'));

    const tag = el('span', 'cfg-tag' + (run.is_current_config ? '' : ' is-different'),
      String(run.fingerprint).slice(0, 8));
    tag.title = run.is_current_config
      ? 'Same settings you are using now — directly comparable.'
      : 'Different settings from your current ones. Scores are not directly comparable with today\'s.';
    row.append(tag);

    row.append(el('div', 'history-meta', run.status));
    row.addEventListener('click', () => openHistoryRun(run.id));
    list.append(row);
  });
  body.append(list);
}

async function openHistoryRun(runId) {
  const content = $('#detail-content');
  content.textContent = '';
  content.append(el('p', 'empty', 'Loading…'));
  $('#detail-overlay').hidden = false;

  let ranking, performance;
  try {
    [ranking, performance] = await Promise.all([
      api(`/api/rankings/${runId}`),
      api(`/api/runs/${runId}/performance`),
    ]);
  } catch (err) {
    content.textContent = '';
    content.append(el('p', 'note note-warn', `Could not load run ${runId}: ${err.message}`));
    return;
  }

  content.textContent = '';
  const run = ranking.run || {};
  const title = el('h2', null, `Ranking from ${formatTimestamp(run.started_at)}`);
  title.id = 'detail-title';
  content.append(title);
  content.append(el('p', 'param-why',
    `Run ${run.id}, triggered by ${run.trigger}, using config ${String(run.fingerprint).slice(0, 8)}` +
    (run.is_current_config ? ' — the same settings you use now.'
                           : ' — different settings from your current ones, so these scores are not directly comparable with today\'s.')));

  if (performance && performance.available) {
    content.append(el('h3', null, `Did the top ${performance.top_n} outperform?`));
    const grid = el('div', 'perf-grid');
    performance.horizons.forEach((h) => {
      const card = el('div', 'perf-card');
      card.append(el('h4', null, `+${h.horizon_days} trading day${h.horizon_days === 1 ? '' : 's'}`));
      const line = (label, value, cls) => {
        const div = el('div', 'perf-line');
        div.append(el('span', null, label));
        div.append(el('span', `val ${cls || ''}`, value));
        card.append(div);
      };
      const fmt = (v) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`);
      line(`Top ${performance.top_n} avg`, fmt(h.top_avg), h.top_avg >= 0 ? 'pos' : 'neg');
      line('Rest avg', fmt(h.rest_avg), h.rest_avg >= 0 ? 'pos' : 'neg');
      line('Edge', fmt(h.edge), h.edge >= 0 ? 'pos' : 'neg');
      if (h.hit_rate != null) {
        line('Hit rate', `${h.hits}/${h.directional_picks} (${h.hit_rate}%)`);
      }
      grid.append(card);
    });
    content.append(grid);
    content.append(el('p', 'param-why',
      'Returns are the underlying\'s move after the ranking, adjusted for the direction called. ' +
      'Option P&L is deliberately not simulated — reliable historical contract marks are not available from this data source, and a fabricated fill would look authoritative while being fiction.'));
  } else {
    content.append(el('p', 'note',
      'Not enough time has passed since this ranking to measure forward returns yet.'));
  }

  content.append(el('h3', null, 'The ranking as it stood'));
  const maxScore = Math.max(...ranking.entries.map((e) => e.composite), 1);
  ranking.entries.forEach((entry) => {
    content.append(rankRow(entry, maxScore, entry.rank <= (ranking.top_n || 5), run.id));
  });
}

/* ------------------------------------------------------------ settings */

async function loadSettings() {
  const body = $('#settings-body');
  body.textContent = '';
  let data;
  try {
    data = await api('/api/config');
  } catch (err) {
    body.append(el('p', 'empty', `Could not load settings: ${err.message}`));
    return;
  }
  state.config = data.config;
  state.configFingerprint = data.fingerprint;
  renderSettings();
}

function renderSettings() {
  const body = $('#settings-body');
  body.textContent = '';
  const config = state.config;
  const specs = state.meta ? state.meta.parameters : [];

  const grid = el('div', 'settings-grid');

  /* --- weight presets --- */
  const presetBox = el('div', 'field-group');
  presetBox.append(el('h3', null, 'Weight preset'));
  presetBox.append(el('p', 'hint',
    'A preset only replaces the weights — your watchlist, posture and thresholds are left '
    + 'alone. Applying one overwrites any hand-tuned weights.'));
  const presetRow = el('div', 'settings-actions');
  api('/api/presets').then((data) => {
    data.presets.forEach((preset) => {
      const button = el('button',
        'btn-secondary' + (data.current === preset.name ? ' is-active-preset' : ''),
        preset.name.replace(/_/g, ' '));
      button.type = 'button';
      button.title = preset.description;
      button.addEventListener('click', async () => {
        try {
          const result = await api(`/api/presets/${preset.name}`, { method: 'POST' });
          toast(result.note);
          loadSettings();
        } catch (err) {
          toast(`Could not apply: ${err.message}`, true);
        }
      });
      presetRow.append(button);
    });
    if (data.current) {
      presetRow.append(el('span', 'hint', `Currently: ${data.current.replace(/_/g, ' ')}`));
    } else {
      presetRow.append(el('span', 'hint', 'Currently: custom weights'));
    }
  }).catch(() => {
    presetRow.append(el('span', 'hint', 'Presets unavailable.'));
  });
  presetBox.append(presetRow);
  grid.append(presetBox);

  /* --- watchlist and global knobs --- */
  const general = el('div', 'field-group');
  general.append(el('h3', null, 'Watchlist and general'));

  const watchField = el('div', 'field');
  watchField.append(el('label', null, 'Tickers'));
  watchField.append(el('p', 'hint',
    'One per line, or comma separated. Symbols are normalised to upper case and de-duplicated.'));
  const watchInput = el('textarea');
  watchInput.id = 'set-watchlist';
  watchInput.value = config.watchlist.join('\n');
  watchField.append(watchInput);
  general.append(watchField);

  const inline = el('div', 'inline-fields');
  inline.append(numberField('set-topn', 'Top candidates to surface', config.top_n, 1, 50, 1,
    'How many tickers appear in the "best candidates" section. The rest stay visible below.'));
  inline.append(selectField('set-posture', 'Options posture', config.posture, [
    ['buy', 'Buying premium (long calls/puts)'],
    ['sell', 'Selling premium (credit spreads / CSPs)'],
  ], 'Flips the meaning of IV Rank, earnings proximity and the contract targets.'));
  inline.append(selectField('set-gate', 'Conviction gate', config.conviction_gate ? 'on' : 'off', [
    ['on', 'On — options score scales with conviction'],
    ['off', 'Off — layers scored independently'],
  ], 'With the gate on, a great contract on a no-signal stock cannot manufacture a high score.'));
  general.append(inline);

  const inline2 = el('div', 'inline-fields');
  inline2.append(numberField('set-rate', 'Risk-free rate (decimal)', config.risk_free_rate, 0, 0.25, 0.001,
    'Used in the Black-Scholes delta calculation.'));
  inline2.append(numberField('set-oi', 'Minimum open interest', config.min_open_interest, 0, 10000, 1,
    'Contracts below this are ignored when selecting a suggestion.'));
  inline2.append(numberField('set-spread', 'Max bid/ask spread (%)', config.max_spread_pct, 1, 100, 1,
    'Wider quotes are treated as untradeable and skipped.'));
  general.append(inline2);
  grid.append(general);

  /* --- contract targets --- */
  const targets = el('div', 'field-group');
  targets.append(el('h3', null, 'Contract targets'));
  targets.append(el('p', 'hint',
    'Separate targets per posture. The active one depends on the posture selected above.'));
  [['buy', 'Buying premium', config.buy_target], ['sell', 'Selling premium', config.sell_target]]
    .forEach(([key, label, target]) => {
      targets.append(el('h3', null, label));
      const row = el('div', 'inline-fields');
      row.append(numberField(`set-${key}-dlo`, 'Delta low', target.delta_low, 0.01, 0.99, 0.01));
      row.append(numberField(`set-${key}-dhi`, 'Delta high', target.delta_high, 0.01, 0.99, 0.01));
      row.append(numberField(`set-${key}-tlo`, 'DTE low', target.dte_low, 1, 900, 1));
      row.append(numberField(`set-${key}-thi`, 'DTE high', target.dte_high, 1, 900, 1));
      targets.append(row);
    });
  grid.append(targets);

  /* --- per-parameter weights and thresholds --- */
  const params = el('div', 'field-group');
  params.append(el('h3', null, 'Parameters, weights and thresholds'));
  params.append(el('p', 'hint',
    'Weight 0 switches a parameter off entirely. Weights are relative: a parameter at 8 moves the score twice as much as one at 4.'));

  specs.forEach((spec) => {
    const paramConfig = config.parameters[spec.key] || { weight: spec.default_weight, thresholds: {} };
    const block = el('div', 'param-setting');

    const head = el('div', 'param-setting-head');
    head.append(el('strong', null, spec.label));
    head.append(el('span', 'param-layer', spec.layer === 'stock' ? 'stock layer' : 'options layer'));
    if (spec.posture_aware) {
      const tag = el('span', 'posture-tag', 'posture-aware');
      tag.title = 'The meaning of this parameter flips between buying and selling premium.';
      head.append(tag);
    }

    const control = el('div', 'weight-control');
    const slider = el('input');
    slider.type = 'range';
    slider.min = '0'; slider.max = '10'; slider.step = '0.5';
    slider.value = paramConfig.weight;
    slider.id = `weight-${spec.key}`;
    const readout = el('span', 'weight-value', String(paramConfig.weight));
    slider.addEventListener('input', () => { readout.textContent = slider.value; });
    control.append(el('span', 'param-layer', 'weight'));
    control.append(slider);
    control.append(readout);
    head.append(control);
    block.append(head);

    block.append(el('p', 'param-summary', spec.summary));

    if (spec.thresholds.length) {
      const thresholdGrid = el('div', 'threshold-grid');
      spec.thresholds.forEach((threshold) => {
        const current = paramConfig.thresholds && paramConfig.thresholds[threshold.key] != null
          ? paramConfig.thresholds[threshold.key]
          : threshold.default;
        thresholdGrid.append(numberField(
          `thr-${spec.key}-${threshold.key}`, threshold.label, current,
          threshold.minimum, threshold.maximum, threshold.step, threshold.help));
      });
      block.append(thresholdGrid);
    }
    params.append(block);
  });
  grid.append(params);

  const actions = el('div', 'settings-actions');
  const save = el('button', 'btn-primary', 'Save settings');
  save.type = 'button';
  save.addEventListener('click', saveSettings);
  actions.append(save);

  const saveRefresh = el('button', 'btn-secondary', 'Save and refresh now');
  saveRefresh.type = 'button';
  saveRefresh.addEventListener('click', async () => {
    if (await saveSettings()) triggerRefresh();
  });
  actions.append(saveRefresh);
  actions.append(el('span', 'hint', `Current config: ${state.configFingerprint || ''}`));
  grid.append(actions);

  body.append(grid);
}

function numberField(id, label, value, min, max, step, hint) {
  const field = el('div', 'field');
  const labelNode = el('label', null, label);
  labelNode.htmlFor = id;
  field.append(labelNode);
  const input = el('input');
  input.type = 'number';
  input.id = id;
  input.value = value;
  if (min != null) input.min = String(min);
  if (max != null) input.max = String(max);
  if (step != null) input.step = String(step);
  field.append(input);
  if (hint) field.append(el('p', 'hint', hint));
  return field;
}

function selectField(id, label, value, options, hint) {
  const field = el('div', 'field');
  const labelNode = el('label', null, label);
  labelNode.htmlFor = id;
  field.append(labelNode);
  const select = el('select');
  select.id = id;
  options.forEach(([optionValue, optionLabel]) => {
    const option = el('option', null, optionLabel);
    option.value = optionValue;
    if (optionValue === value) option.selected = true;
    select.append(option);
  });
  field.append(select);
  if (hint) field.append(el('p', 'hint', hint));
  return field;
}

async function saveSettings() {
  const config = JSON.parse(JSON.stringify(state.config));

  config.watchlist = $('#set-watchlist').value
    .split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
  config.top_n = Number($('#set-topn').value);
  config.posture = $('#set-posture').value;
  config.conviction_gate = $('#set-gate').value === 'on';
  config.risk_free_rate = Number($('#set-rate').value);
  config.min_open_interest = Number($('#set-oi').value);
  config.max_spread_pct = Number($('#set-spread').value);

  ['buy', 'sell'].forEach((key) => {
    config[`${key}_target`] = {
      delta_low: Number($(`#set-${key}-dlo`).value),
      delta_high: Number($(`#set-${key}-dhi`).value),
      dte_low: Number($(`#set-${key}-tlo`).value),
      dte_high: Number($(`#set-${key}-thi`).value),
    };
  });

  (state.meta ? state.meta.parameters : []).forEach((spec) => {
    const weightInput = $(`#weight-${spec.key}`);
    if (!weightInput) return;
    const thresholds = {};
    spec.thresholds.forEach((threshold) => {
      const input = $(`#thr-${spec.key}-${threshold.key}`);
      if (input) thresholds[threshold.key] = Number(input.value);
    });
    config.parameters[spec.key] = {
      weight: Number(weightInput.value),
      enabled: Number(weightInput.value) > 0,
      thresholds,
    };
  });

  if (!config.watchlist.length) {
    toast('The watchlist cannot be empty.', true);
    return false;
  }

  try {
    const result = await api('/api/config', { method: 'PUT', body: JSON.stringify(config) });
    state.config = result.config;
    state.configFingerprint = result.fingerprint;
    toast(result.note || 'Settings saved.');
    return true;
  } catch (err) {
    toast(`Could not save: ${err.message}`, true);
    return false;
  }
}

/* ------------------------------------------------------------- refresh */

async function triggerRefresh() {
  const button = $('#refresh-btn');
  button.disabled = true;
  button.textContent = 'Refreshing…';
  try {
    const summary = await api('/api/refresh?trigger=manual', { method: 'POST' });
    const failed = Object.keys(summary.failed || {}).length;
    toast(failed
      ? `Refreshed: ${summary.scored.length} ranked, ${failed} skipped. Open a row for details.`
      : `Refreshed ${summary.scored.length} tickers in ${summary.duration_seconds.toFixed(1)}s.`);
    await loadRankings();
  } catch (err) {
    if (err.status === 409) toast('A refresh is already running.', true);
    else toast(`Refresh failed: ${err.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = 'Refresh Now';
    loadStatus();
  }
}

/* ---------------------------------------------------------------- init */

function switchView(name) {
  document.querySelectorAll('.tab').forEach((tab) => {
    const active = tab.dataset.view === name;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('.view').forEach((view) => {
    view.classList.toggle('is-active', view.id === `view-${name}`);
  });
  if (name === 'history') loadHistory();
  if (name === 'journal') loadJournal();
  if (name === 'settings') loadSettings();
}

function init() {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => switchView(tab.dataset.view));
  });
  $('#refresh-btn').addEventListener('click', triggerRefresh);
  $('#detail-close').addEventListener('click', closeDetail);
  $('#detail-overlay').addEventListener('click', (event) => {
    if (event.target.id === 'detail-overlay') closeDetail();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeDetail();
  });

  loadMeta()
    .then(loadRankings)
    .catch((err) => toast(`Startup failed: ${err.message}`, true));
  loadStatus();
  // Keeps the "last / next refresh" clocks honest, and reveals a scheduled
  // refresh that completed while the tab was open.
  setInterval(async () => {
    const previousRunId = state.status && state.status.last_run && state.status.last_run.id;
    await loadStatus();
    const currentRunId = state.status && state.status.last_run && state.status.last_run.id;
    if (currentRunId && currentRunId !== previousRunId) loadRankings();
  }, 30000);
}

document.addEventListener('DOMContentLoaded', init);
