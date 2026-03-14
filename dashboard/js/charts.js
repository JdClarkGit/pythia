/**
 * charts.js — Chart.js wrappers for the dashboard.
 * Requires Chart.js loaded globally via CDN.
 */

// ─── Shared defaults ──────────────────────────────────────────────────────

Chart.defaults.color = '#888899';
Chart.defaults.borderColor = '#2a2a45';
Chart.defaults.font.family = "'JetBrains Mono', monospace";
Chart.defaults.font.size = 11;

const STRATEGY_COLORS = {
  MERGE_ARB:    '#00ff88',
  MEAN_REV:     '#3b82f6',
  PRICE_MAGNET: '#a855f7',
  DEPENDENCY:   '#f59e0b',
  CROSS_MARKET: '#ec4899',
  total:        '#ffffff',
};

function hexAlpha(hex, a) {
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  return `rgba(${r},${g},${b},${a})`;
}

// ─── Capital Allocation Donut ─────────────────────────────────────────────

export function createDonutChart(canvasId, data) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  const labels = data.map(d => d.label);
  const values = data.map(d => d.value);
  const colors = data.map(d => d.color);

  const chart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors.map(c => hexAlpha(c, 0.8)),
        borderColor: colors,
        borderWidth: 1.5,
        hoverBorderWidth: 2,
        hoverOffset: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '72%',
      animation: { duration: 600, easing: 'easeOutQuart' },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => {
              const total = ctx.dataset.data.reduce((a,b)=>a+b,0);
              const pct = total > 0 ? ((ctx.parsed / total)*100).toFixed(1) : 0;
              return ` $${ctx.parsed.toFixed(2)} (${pct}%)`;
            },
          },
          backgroundColor: '#1a1a2e',
          borderColor: '#2a2a45',
          borderWidth: 1,
          padding: 10,
        },
      },
    },
  });

  return chart;
}

export function updateDonutChart(chart, data) {
  if (!chart) return;
  chart.data.datasets[0].data = data.map(d => d.value);
  chart.update('active');
}

// ─── P&L Equity Curve ────────────────────────────────────────────────────

export function createEquityChart(canvasId, pnlData) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  const strategies = Object.keys(pnlData.data);

  const datasets = strategies.map(s => ({
    label: s,
    data: pnlData.data[s],
    borderColor: STRATEGY_COLORS[s] ?? '#888899',
    backgroundColor: 'transparent',
    borderWidth: 1.5,
    pointRadius: 0,
    pointHoverRadius: 4,
    tension: 0.3,
    fill: false,
  }));

  datasets.push({
    label: 'Total',
    data: pnlData.totals,
    borderColor: STRATEGY_COLORS.total,
    backgroundColor: hexAlpha('#ffffff', 0.05),
    borderWidth: 2,
    pointRadius: 0,
    pointHoverRadius: 5,
    tension: 0.3,
    fill: 'origin',
  });

  const chart = new Chart(ctx, {
    type: 'line',
    data: { labels: pnlData.labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      animation: { duration: 400 },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: {
            boxWidth: 12,
            boxHeight: 2,
            padding: 16,
            usePointStyle: true,
            pointStyle: 'line',
          },
        },
        tooltip: {
          backgroundColor: '#1a1a2e',
          borderColor: '#2a2a45',
          borderWidth: 1,
          padding: 10,
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: $${ctx.parsed.y >= 0 ? '+' : ''}${ctx.parsed.y.toFixed(2)}`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: '#1e1e35' },
          ticks: { maxTicksLimit: 10, maxRotation: 0 },
        },
        y: {
          grid: { color: '#1e1e35' },
          ticks: { callback: v => `$${v >= 0 ? '+' : ''}${v.toFixed(0)}` },
        },
      },
    },
  });

  return chart;
}

export function updateEquityChart(chart, pnlData) {
  if (!chart) return;
  chart.data.labels = pnlData.labels;
  const strategies = Object.keys(pnlData.data);
  strategies.forEach((s, i) => {
    if (chart.data.datasets[i]) chart.data.datasets[i].data = pnlData.data[s];
  });
  const totalIdx = strategies.length;
  if (chart.data.datasets[totalIdx]) chart.data.datasets[totalIdx].data = pnlData.totals;
  chart.update('active');
}

// ─── Strategy bar chart (weekly buckets) ─────────────────────────────────

export function createStrategyBarChart(canvasId, strategies) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  const weeks = strategies[0]?.weekly?.length ?? 12;
  const labels = Array.from({ length: weeks }, (_, i) => `W${i+1}`);

  const datasets = strategies.map(s => ({
    label: s.name,
    data: s.weekly ?? [],
    backgroundColor: hexAlpha(s.color, 0.7),
    borderColor: s.color,
    borderWidth: 1,
    borderRadius: 3,
  }));

  const chart = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index' },
      animation: { duration: 400 },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: { boxWidth: 10, padding: 14 },
        },
        tooltip: {
          backgroundColor: '#1a1a2e',
          borderColor: '#2a2a45',
          borderWidth: 1,
          padding: 10,
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: $${ctx.parsed.y.toFixed(2)}`,
          },
        },
      },
      scales: {
        x: { grid: { display: false }, stacked: true },
        y: {
          grid: { color: '#1e1e35' },
          stacked: true,
          ticks: { callback: v => `$${v}` },
        },
      },
    },
  });

  return chart;
}
