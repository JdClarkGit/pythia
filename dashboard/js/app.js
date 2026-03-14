/**
 * app.js — Main entry point for index.html.
 * Handles polling loop, header stats, and orchestrates all panels.
 */

import { fetchStatus, fetchPnlHistory } from './api.js';
import { renderOpportunities, renderPositions, showOrderBookModal } from './positions.js';
import { createDonutChart, updateDonutChart, createEquityChart, updateEquityChart } from './charts.js';

// ─── State ────────────────────────────────────────────────────────────────

let donutChart  = null;
let equityChart = null;
let pollTimer   = null;
let lastUpdate  = null;

const POLL_INTERVAL_MS = 10_000; // 10 s

// ─── Boot ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Initialise charts with empty data so canvas is ready
  donutChart = createDonutChart('donut-canvas', [
    { label: 'Available',      value: 0, color: '#00ff88' },
    { label: 'Merge Arb',      value: 0, color: '#10b981' },
    { label: 'Mean Reversion', value: 0, color: '#3b82f6' },
    { label: 'Price Magnet',   value: 0, color: '#a855f7' },
    { label: 'Cross-Market',   value: 0, color: '#ec4899' },
  ]);

  // First paint
  updateAll();

  // Polling loop
  pollTimer = setInterval(updateAll, POLL_INTERVAL_MS);

  // Countdown ticker
  setInterval(updateCountdown, 1000);
});

// ─── Full refresh ─────────────────────────────────────────────────────────

async function updateAll() {
  flashRefreshDot();
  lastUpdate = Date.now();

  await Promise.all([
    updateHeader(),
    updateOpportunities(),
    updatePositions(),
    updateCapitalDonut(),
    updateEquityCurve(),
  ]);
}

// ─── Header stats ─────────────────────────────────────────────────────────

async function updateHeader() {
  const s = await fetchStatus();
  if (!s) return;

  // Status pill
  const statusEl  = document.getElementById('status-pill');
  const statusKey = s.bot_status?.toLowerCase().replace('_','-') ?? 'stopped';
  if (statusEl) {
    statusEl.className = `status-pill ${statusKey}`;
    statusEl.innerHTML = `<span class="dot"></span>${s.bot_status ?? 'UNKNOWN'}`;
  }

  // Nav stats
  setText('nav-capital',  `$${fmt2(s.total_capital)}`);

  // Stat cards
  setText('stat-capital', `$${fmt2(s.total_capital)}`);
  setText('stat-free',    `$${fmt2(s.free_capital)}`);

  const todayEl = document.getElementById('stat-today-pnl');
  if (todayEl) {
    todayEl.textContent = `${s.today_pnl >= 0 ? '+' : ''}$${fmt2(Math.abs(s.today_pnl))}`;
    todayEl.className = `stat-card-value ${s.today_pnl >= 0 ? 'positive' : 'negative'}`;
  }
  setText('stat-today-pct', `${s.today_pnl_pct >= 0 ? '+' : ''}${s.today_pnl_pct.toFixed(2)}%`);

  const alltimeEl = document.getElementById('stat-alltime-pnl');
  if (alltimeEl) {
    alltimeEl.textContent = `${s.alltime_pnl >= 0 ? '+' : ''}$${fmt2(Math.abs(s.alltime_pnl))}`;
    alltimeEl.className = `stat-card-value ${s.alltime_pnl >= 0 ? 'positive' : 'negative'}`;
  }

  setText('stat-positions', s.active_positions ?? 0);

  const uptimeEl = document.getElementById('stat-uptime');
  if (uptimeEl) uptimeEl.textContent = formatUptime(s.uptime_seconds ?? 0);

  // Nav dot
  const navDot = document.querySelector('.nav-brand .dot');
  if (navDot) {
    navDot.className = `dot ${statusKey}`;
  }
}

// ─── Opportunity feed ─────────────────────────────────────────────────────

async function updateOpportunities() {
  await renderOpportunities('opps-tbody', opp => showOrderBookModal(opp));
}

// ─── Active positions ─────────────────────────────────────────────────────

async function updatePositions() {
  await renderPositions('positions-tbody');
}

// ─── Capital donut ────────────────────────────────────────────────────────

async function updateCapitalDonut() {
  const s = await fetchStatus();
  if (!s) return;

  const total    = s.total_capital || 1;
  const free     = s.free_capital  || 0;
  const inTrades = total - free;

  // Approximate allocation split (backend should provide this breakdown)
  const donutData = [
    { label: 'Available',      value: free,             color: '#00ff88' },
    { label: 'Merge Arb',      value: inTrades * 0.50,  color: '#10b981' },
    { label: 'Mean Reversion', value: inTrades * 0.25,  color: '#3b82f6' },
    { label: 'Price Magnet',   value: inTrades * 0.15,  color: '#a855f7' },
    { label: 'Cross-Market',   value: inTrades * 0.10,  color: '#ec4899' },
  ];

  if (donutChart) {
    updateDonutChart(donutChart, donutData);
  }

  // Legend
  const legendEl = document.getElementById('donut-legend');
  if (legendEl) {
    legendEl.innerHTML = donutData.map(d => `
      <div class="legend-item">
        <div class="legend-dot" style="background:${d.color}"></div>
        <span class="legend-label">${d.label}</span>
        <span class="legend-value">$${fmt2(d.value)}</span>
        <span class="legend-pct">${((d.value/total)*100).toFixed(1)}%</span>
      </div>`).join('');
  }
}

// ─── Equity curve ─────────────────────────────────────────────────────────

async function updateEquityCurve() {
  const data = await fetchPnlHistory();
  if (!data) return;

  if (!equityChart) {
    equityChart = createEquityChart('equity-canvas', data);
  } else {
    updateEquityChart(equityChart, data);
  }
}

// ─── UI helpers ──────────────────────────────────────────────────────────

function flashRefreshDot() {
  const dot = document.getElementById('refresh-dot');
  if (!dot) return;
  dot.classList.add('active');
  setTimeout(() => dot.classList.remove('active'), 800);
}

function updateCountdown() {
  const el = document.getElementById('refresh-countdown');
  if (!el || !lastUpdate) return;
  const elapsed = Math.floor((Date.now() - lastUpdate) / 1000);
  const remaining = Math.max(0, Math.floor(POLL_INTERVAL_MS/1000) - elapsed);
  el.textContent = `${remaining}s`;
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function fmt2(n) { return Number(n).toLocaleString('en-US', { minimumFractionDigits:2, maximumFractionDigits:2 }); }

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}
