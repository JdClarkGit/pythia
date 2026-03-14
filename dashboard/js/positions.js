/**
 * positions.js — Active positions panel + order book depth modal.
 */

import { fetchPositions, fetchOpportunities } from './api.js';

const STRATEGY_TAGS = {
  MERGE_ARB:    'tag-merge',
  MEAN_REV:     'tag-mean-rev',
  PRICE_MAGNET: 'tag-price-mag',
  DEPENDENCY:   'tag-dependency',
  CROSS_MARKET: 'tag-cross',
};

// ─── Positions Panel ──────────────────────────────────────────────────────

export async function renderPositions(tbodyId) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;

  const positions = await fetchPositions();

  if (!positions || positions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-state">No open positions</td></tr>`;
    return;
  }

  tbody.innerHTML = positions.map(p => {
    const pnlClass = p.unrealized_pnl >= 0 ? 'pos' : 'neg';
    const pnlSign  = p.unrealized_pnl >= 0 ? '+' : '';
    const progress = Math.min(1, Math.max(0, (p.current - p.stop) / (p.target - p.stop)));
    const trending = p.current > p.entry ? 'pos' : 'neg';
    const age      = formatAge(p.age_minutes);
    const tagClass = STRATEGY_TAGS[p.strategy] ?? 'tag-none';

    return `<tr>
      <td title="${escHtml(p.market)}">${escHtml(truncate(p.market, 38))}</td>
      <td><span class="tag ${tagClass}">${p.strategy}</span></td>
      <td class="num right">${p.entry.toFixed(4)}</td>
      <td class="num right ${trending}">${p.current.toFixed(4)}</td>
      <td class="num right text-muted">${p.target.toFixed(4)}</td>
      <td class="num right text-muted">${p.stop.toFixed(4)}</td>
      <td class="num right ${pnlClass}">${pnlSign}$${Math.abs(p.unrealized_pnl).toFixed(2)}</td>
      <td class="num right text-muted">${age}</td>
    </tr>`;
  }).join('');
}

// ─── Opportunity Feed ─────────────────────────────────────────────────────

export async function renderOpportunities(tbodyId, onRowClick) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;

  const opps = await fetchOpportunities();

  if (!opps || opps.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state">Scanning for opportunities...</td></tr>`;
    return;
  }

  tbody.innerHTML = opps.map(o => {
    const profitable  = o.profit_pct >= 0;
    const profitClass = profitable ? 'pos' : 'neg';
    const profitSign  = o.profit_pct >= 0 ? '+' : '';
    const tagClass    = STRATEGY_TAGS[o.strategy] ?? 'tag-none';

    return `<tr class="opp-row" data-id="${o.id}">
      <td title="${escHtml(o.market)}">${escHtml(truncate(o.market, 40))}</td>
      <td><span class="tag ${tagClass}">${o.strategy}</span></td>
      <td class="num right">${o.yes.toFixed(4)}</td>
      <td class="num right">${o.no.toFixed(4)}</td>
      <td class="num right">${(o.yes + o.no).toFixed(4)}</td>
      <td class="num right ${profitClass}">${profitSign}${o.profit_pct.toFixed(2)}%</td>
      <td class="right">
        ${profitable
          ? `<button class="btn btn-primary" style="padding:3px 10px;font-size:0.72rem">TRADE</button>`
          : `<span class="text-muted" style="font-size:0.72rem">—</span>`}
      </td>
    </tr>`;
  }).join('');

  // Attach row click handler
  if (onRowClick) {
    tbody.querySelectorAll('tr.opp-row').forEach(tr => {
      tr.addEventListener('click', e => {
        if (e.target.tagName === 'BUTTON') return;
        const opp = opps.find(o => o.id === tr.dataset.id);
        if (opp) onRowClick(opp);
      });
    });
  }
}

// ─── Order Book Depth Modal ───────────────────────────────────────────────

export function showOrderBookModal(opp) {
  const existing = document.getElementById('ob-modal');
  if (existing) existing.remove();

  // Simulate order book data
  const levels = 8;
  const bids = Array.from({ length: levels }, (_, i) => ({
    price: +(opp.yes - 0.005 * (i+1)).toFixed(4),
    size:  +(50 + Math.random() * 500).toFixed(0),
  }));
  const asks = Array.from({ length: levels }, (_, i) => ({
    price: +(opp.yes + 0.005 * (i+1)).toFixed(4),
    size:  +(50 + Math.random() * 500).toFixed(0),
  }));
  const maxSize = Math.max(...bids.map(b=>b.size), ...asks.map(a=>a.size));

  const modal = document.createElement('div');
  modal.id = 'ob-modal';
  modal.className = 'modal-overlay fade-in';
  modal.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <span class="modal-title">${escHtml(truncate(opp.market, 50))}</span>
        <button class="modal-close" id="ob-close">✕</button>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div>
          <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin-bottom:8px">YES Order Book</div>
          <table style="width:100%;font-size:0.8rem">
            <thead><tr><th style="text-align:left;color:var(--text-muted);font-weight:500;padding:4px 8px">Price</th><th style="text-align:right;color:var(--text-muted);font-weight:500;padding:4px 8px">Size</th></tr></thead>
            <tbody>
              ${asks.slice().reverse().map(a => `
                <tr>
                  <td style="padding:2px 8px;position:relative">
                    <div style="position:absolute;top:0;left:0;height:100%;width:${(a.size/maxSize*100).toFixed(0)}%;background:rgba(239,68,68,0.08);border-radius:2px"></div>
                    <span class="num neg" style="position:relative">${a.price.toFixed(4)}</span>
                  </td>
                  <td class="num right" style="padding:2px 8px">${a.size}</td>
                </tr>`).join('')}
              <tr style="border-top:1px solid var(--border);border-bottom:1px solid var(--border)">
                <td class="num" style="padding:4px 8px;color:var(--accent);font-weight:600">SPREAD</td>
                <td class="num right" style="padding:4px 8px;color:var(--accent)">${(asks[0].price - bids[0].price).toFixed(4)}</td>
              </tr>
              ${bids.map(b => `
                <tr>
                  <td style="padding:2px 8px;position:relative">
                    <div style="position:absolute;top:0;left:0;height:100%;width:${(b.size/maxSize*100).toFixed(0)}%;background:rgba(0,255,136,0.08);border-radius:2px"></div>
                    <span class="num pos" style="position:relative">${b.price.toFixed(4)}</span>
                  </td>
                  <td class="num right" style="padding:2px 8px">${b.size}</td>
                </tr>`).join('')}
            </tbody>
          </table>
        </div>
        <div>
          <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin-bottom:8px">Opportunity Details</div>
          ${detailRow('Strategy', `<span class="tag ${STRATEGY_TAGS[opp.strategy]??'tag-none'}">${opp.strategy}</span>`)}
          ${detailRow('YES Ask', `<span class="num">${opp.yes.toFixed(4)}</span>`)}
          ${detailRow('NO Ask', `<span class="num">${opp.no.toFixed(4)}</span>`)}
          ${detailRow('Combined', `<span class="num ${opp.profit_pct>=0?'pos':'neg'}">${(opp.yes+opp.no).toFixed(4)}</span>`)}
          ${detailRow('Net Profit', `<span class="num ${opp.profit_pct>=0?'pos':'neg'}">${opp.profit_pct>=0?'+':''}${opp.profit_pct.toFixed(3)}%</span>`)}
          ${detailRow('Max $ Profit', `<span class="num ${opp.profit_pct>=0?'pos':'neg'}">$${Math.abs(opp.max_profit).toFixed(2)}</span>`)}
        </div>
      </div>
    </div>`;

  document.body.appendChild(modal);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.getElementById('ob-close').addEventListener('click', () => modal.remove());
}

function detailRow(label, value) {
  return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:0.82rem">
    <span style="color:var(--text-muted)">${label}</span>${value}</div>`;
}

// ─── Helpers ──────────────────────────────────────────────────────────────

function truncate(s, n) { return s.length > n ? s.slice(0, n) + '…' : s; }
function escHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function formatAge(minutes) {
  if (minutes < 60)  return `${minutes}m`;
  if (minutes < 1440) return `${Math.floor(minutes/60)}h ${minutes%60}m`;
  return `${Math.floor(minutes/1440)}d`;
}
