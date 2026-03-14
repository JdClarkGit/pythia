/**
 * scanner.js — Sortable/filterable live market scanner table.
 */

import { fetchMarkets } from './api.js';

const SIGNAL_TAGS = {
  MERGE_ARB:    'tag-merge',
  MEAN_REV:     'tag-mean-rev',
  PRICE_MAGNET: 'tag-price-mag',
  DEPENDENCY:   'tag-dependency',
  NONE:         'tag-none',
};

let _markets    = [];
let _sortCol    = null;
let _sortDir    = 'asc';
let _filterText = '';
let _filterSig  = '';
let _tbody      = null;
let _countEl    = null;

export function initScanner({ tableId, searchId, signalFilterId, countId, refreshId }) {
  const table      = document.getElementById(tableId);
  _tbody           = table?.querySelector('tbody');
  _countEl         = countId   ? document.getElementById(countId)   : null;
  const searchEl   = searchId  ? document.getElementById(searchId)  : null;
  const sigFilter  = signalFilterId ? document.getElementById(signalFilterId) : null;
  const refreshEl  = refreshId ? document.getElementById(refreshId) : null;

  // Skeleton on first load
  renderSkeleton(8);

  // Sort headers
  table?.querySelectorAll('thead th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (_sortCol === col) {
        _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        _sortCol = col;
        _sortDir = 'asc';
      }
      table.querySelectorAll('thead th').forEach(h => h.classList.remove('sort-asc','sort-desc'));
      th.classList.add(_sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
      renderTable();
    });
  });

  // Search
  searchEl?.addEventListener('input', e => {
    _filterText = e.target.value.toLowerCase();
    renderTable();
  });

  // Signal filter
  sigFilter?.addEventListener('change', e => {
    _filterSig = e.target.value;
    renderTable();
  });

  // First fetch
  return refresh(refreshEl);
}

export async function refresh(refreshEl) {
  const dot = refreshEl?.querySelector('.refresh-dot');
  if (dot) { dot.classList.add('active'); setTimeout(() => dot.classList.remove('active'), 800); }

  _markets = await fetchMarkets();
  renderTable();
}

function filtered() {
  return _markets.filter(m => {
    const nameOk = !_filterText || m.question.toLowerCase().includes(_filterText);
    const sigOk  = !_filterSig  || m.signal === _filterSig;
    return nameOk && sigOk;
  });
}

function sorted(arr) {
  if (!_sortCol) return arr;
  return [...arr].sort((a, b) => {
    let av = a[_sortCol], bv = b[_sortCol];
    if (typeof av === 'string') av = av.toLowerCase(), bv = bv.toLowerCase();
    if (av < bv) return _sortDir === 'asc' ? -1 : 1;
    if (av > bv) return _sortDir === 'asc' ? 1 : -1;
    return 0;
  });
}

function renderTable() {
  if (!_tbody) return;
  const rows = sorted(filtered());
  if (_countEl) _countEl.textContent = rows.length;

  if (rows.length === 0) {
    _tbody.innerHTML = `<tr><td colspan="8" class="empty-state">No markets match filter</td></tr>`;
    return;
  }

  _tbody.innerHTML = rows.map(m => {
    const combined    = m.yes_price + m.no_price;
    const combClass   = combined < 1 ? 'pos' : combined > 1.005 ? 'neg' : '';
    const tagClass    = SIGNAL_TAGS[m.signal] ?? 'tag-none';
    const regimeClass = m.regime === 'tight' ? 'regime-tight' : 'regime-wide';
    const imbColor    = m.imbalance > 0
      ? `rgba(0,255,136,${Math.min(Math.abs(m.imbalance),1)})`
      : `rgba(239,68,68,${Math.min(Math.abs(m.imbalance),1)})`;
    const imbLeft     = m.imbalance >= 0 ? '50%' : `${50 + m.imbalance*50}%`;
    const imbWidth    = `${Math.abs(m.imbalance)*50}%`;
    const scoreWidth  = `${m.micro_score}%`;
    const scoreColor  = m.micro_score > 66 ? '#00ff88' : m.micro_score > 33 ? '#f59e0b' : '#ef4444';

    return `<tr data-id="${m.id}">
      <td class="market-question">${escHtml(m.question)}</td>
      <td class="num right">${m.yes_price.toFixed(4)}</td>
      <td class="num right">${m.no_price.toFixed(4)}</td>
      <td class="num right ${combClass}">${combined.toFixed(4)}</td>
      <td><span class="regime ${regimeClass}">${m.regime}</span></td>
      <td>
        <div class="imbalance-bar">
          <div class="imbalance-track">
            <div class="imbalance-fill" style="left:${imbLeft};width:${imbWidth};background:${imbColor}"></div>
          </div>
          <span class="imbalance-val ${m.imbalance>=0?'pos':'neg'}">${m.imbalance>=0?'+':''}${m.imbalance.toFixed(2)}</span>
        </div>
      </td>
      <td>
        <div class="score-bar">
          <div class="score-track"><div class="score-fill" style="width:${scoreWidth};background:${scoreColor}"></div></div>
          <span class="num" style="font-size:0.72rem;color:${scoreColor}">${m.micro_score}</span>
        </div>
      </td>
      <td><span class="tag ${tagClass}">${m.signal}</span></td>
      <td class="num right text-muted">$${Number(m.volume_24h).toLocaleString()}</td>
    </tr>`;
  }).join('');
}

function renderSkeleton(n) {
  if (!_tbody) return;
  _tbody.innerHTML = Array(n).fill(0).map(() =>
    `<tr>${Array(9).fill(0).map(()=>`<td><div class="skeleton skeleton-row" style="height:12px;width:${60+Math.random()*30}%"></div></td>`).join('')}</tr>`
  ).join('');
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
