/**
 * api.js — All fetch calls to the bot's REST backend (localhost:8080).
 * Falls back to mock data when the backend is unreachable so the
 * dashboard is usable standalone.
 */

const BASE = 'http://localhost:8080/api';

// ─── Generic fetch wrapper ────────────────────────────────────────────────

async function apiFetch(path, params = {}) {
  const url = new URL(BASE + path);
  Object.entries(params).forEach(([k, v]) => v != null && url.searchParams.set(k, v));
  try {
    const res = await fetch(url.toString(), {
      headers: { 'Accept': 'application/json' },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    console.warn(`[api] ${path} failed (${e.message}), using mock data`);
    return null;
  }
}

// ─── Endpoints ────────────────────────────────────────────────────────────

export async function fetchStatus()         { return (await apiFetch('/status'))        ?? MOCK.status; }
export async function fetchOpportunities()  { return (await apiFetch('/opportunities')) ?? MOCK.opportunities; }
export async function fetchPositions()      { return (await apiFetch('/positions'))     ?? MOCK.positions; }
export async function fetchTrades(opts={})  { return (await apiFetch('/trades', opts))  ?? MOCK.trades; }
export async function fetchMarkets()        { return (await apiFetch('/markets'))       ?? MOCK.markets; }
export async function fetchWallet()         { return (await apiFetch('/wallet'))        ?? MOCK.wallet; }
export async function fetchStrategies()     { return (await apiFetch('/strategies'))    ?? MOCK.strategies; }
export async function fetchPnlHistory()     { return (await apiFetch('/pnl/history'))   ?? MOCK.pnlHistory; }

// ─── Mock data (used when backend not running) ────────────────────────────

const now = Date.now();
const ts  = (offsetMs=0) => new Date(now + offsetMs).toISOString();

const MOCK = {

  status: {
    bot_status: 'DRY_RUN',
    total_capital: 5000.00,
    free_capital: 3120.50,
    today_pnl: 14.32,
    today_pnl_pct: 0.29,
    alltime_pnl: 187.44,
    active_positions: 3,
    uptime_seconds: 14400,
  },

  opportunities: [
    { id: 'op1', market: 'Will Trump win 2026 midterms?',  strategy: 'MERGE_ARB',     yes: 0.4820, no: 0.5040, combined: 0.9860, profit_pct: 1.40, max_profit: 22.4  },
    { id: 'op2', market: 'BTC above $100k by June 2026?',  strategy: 'MEAN_REV',      yes: 0.3910, no: 0.6200, combined: 1.0110, profit_pct: -1.1, max_profit: -5.2  },
    { id: 'op3', market: 'Fed rate cut in May 2026?',       strategy: 'MERGE_ARB',     yes: 0.5620, no: 0.4240, combined: 0.9860, profit_pct: 1.40, max_profit: 18.9  },
    { id: 'op4', market: 'NCAAB: Duke wins March Madness?', strategy: 'PRICE_MAGNET',  yes: 0.2200, no: 0.7700, combined: 0.9900, profit_pct: 1.00, max_profit: 11.5  },
    { id: 'op5', market: 'GPT-5 released before July 2026?',strategy: 'MERGE_ARB',     yes: 0.7100, no: 0.2750, combined: 0.9850, profit_pct: 1.50, max_profit: 31.0  },
    { id: 'op6', market: 'ETH above $4k by Q3 2026?',      strategy: 'MEAN_REV',      yes: 0.4400, no: 0.5500, combined: 0.9900, profit_pct: 1.00, max_profit: 14.2  },
    { id: 'op7', market: 'DOGE reaches $1 in 2026?',       strategy: 'PRICE_MAGNET',  yes: 0.0890, no: 0.9050, combined: 0.9940, profit_pct: 0.60, max_profit: 7.8   },
    { id: 'op8', market: 'US recession in 2026?',           strategy: 'MERGE_ARB',     yes: 0.3010, no: 0.6840, combined: 0.9850, profit_pct: 1.50, max_profit: 28.1  },
  ],

  positions: [
    { id: 'pos1', market: 'Fed rate cut in May 2026?',       strategy: 'MERGE_ARB',    entry: 0.9860, current: 0.9910, target: 1.0000, stop: 0.9700, unrealized_pnl: 8.32,  age_minutes: 45  },
    { id: 'pos2', market: 'NCAAB: Duke wins March Madness?', strategy: 'PRICE_MAGNET', entry: 0.9900, current: 0.9870, target: 1.0000, stop: 0.9750, unrealized_pnl: -4.12, age_minutes: 120 },
    { id: 'pos3', market: 'GPT-5 released before July 2026?',strategy: 'MERGE_ARB',    entry: 0.9850, current: 0.9920, target: 1.0000, stop: 0.9700, unrealized_pnl: 12.18, age_minutes: 18  },
  ],

  trades: Array.from({ length: 40 }, (_, i) => ({
    id: i + 1,
    timestamp: ts(-(i * 3600000 + Math.random() * 3600000)),
    market: ['Fed rate cut?','BTC $100k?','Trump midterms?','Duke NCAA?','GPT-5 July?'][i % 5],
    strategy: ['MERGE_ARB','MEAN_REV','PRICE_MAGNET','MERGE_ARB','MERGE_ARB'][i % 5],
    side: i % 3 === 0 ? 'SHORT' : 'LONG',
    size: (100 + Math.random() * 400).toFixed(2),
    entry: (0.97 + Math.random() * 0.03).toFixed(4),
    exit: i < 30 ? (0.98 + Math.random() * 0.02).toFixed(4) : null,
    fees: (0.1 + Math.random() * 0.5).toFixed(4),
    pnl: (Math.random() * 30 - 8).toFixed(4),
    status: ['merged','merged','merged','failed','open'][i % 5],
  })),

  markets: Array.from({ length: 60 }, (_, i) => {
    const yes = +(0.1 + Math.random() * 0.8).toFixed(4);
    const no  = +(1 - yes - (Math.random() * 0.04 - 0.02)).toFixed(4);
    const combined = +(yes + no).toFixed(4);
    const signals = ['MERGE_ARB','MEAN_REV','PRICE_MAGNET','DEPENDENCY','NONE'];
    const regimes = ['tight','wide','tight'];
    return {
      id: `mkt${i}`,
      question: [
        'Will the Fed cut rates in May 2026?',
        'BTC above $100k by June 2026?',
        'Trump wins 2026 midterms?',
        'Duke wins March Madness?',
        'GPT-5 before July 2026?',
        'ETH above $4k Q3 2026?',
        'DOGE reaches $1 in 2026?',
        'US recession in 2026?',
        'Ukraine ceasefire by EOY?',
        'Apple releases AR glasses in 2026?',
      ][i % 10],
      yes_price: yes,
      no_price: no,
      combined,
      regime: regimes[i % 3],
      imbalance: +(Math.random() * 2 - 1).toFixed(3),
      micro_score: Math.round(Math.random() * 100),
      signal: signals[i % 5],
      volume_24h: Math.round(1000 + Math.random() * 99000),
    };
  }),

  wallet: {
    address: '0x666464ed833f297086c6ec8b72eba752546d07c2',
    usdc_balance: 3120.50,
    matic_balance: 4.21,
    holdings: [
      { market: 'Fed rate cut May 2026?', token: 'YES', amount: 412.5, price: 0.562, value: 231.83 },
      { market: 'GPT-5 before July 2026?', token: 'YES', amount: 800.0, price: 0.710, value: 568.00 },
      { market: 'GPT-5 before July 2026?', token: 'NO',  amount: 800.0, price: 0.275, value: 220.00 },
      { market: 'Duke NCAA?', token: 'YES', amount: 350.0, price: 0.220, value: 77.00 },
      { market: 'Duke NCAA?', token: 'NO',  amount: 350.0, price: 0.770, value: 269.50 },
    ],
    transactions: Array.from({ length: 20 }, (_, i) => ({
      hash: `0x${Math.random().toString(16).slice(2).padEnd(64,'0')}`,
      type: ['buy','sell','merge','buy'][i % 4],
      amount: (50 + Math.random() * 500).toFixed(2),
      timestamp: ts(-(i * 7200000)),
    })),
  },

  strategies: [
    { name: 'MERGE_ARB',    color: '#00ff88', win_rate: 84.2, avg_profit: 1.82, total_trades: 128, capital: 1200, ann_return: 47.3, max_drawdown: 3.1,  weekly: [12,18,9,22,15,8,19,24,11,16,20,13] },
    { name: 'MEAN_REV',     color: '#3b82f6', win_rate: 61.4, avg_profit: 0.94, total_trades: 57,  capital: 800,  ann_return: 28.6, max_drawdown: 8.4,  weekly: [4,7,2,9,5,3,8,11,4,7,6,5] },
    { name: 'PRICE_MAGNET', color: '#a855f7', win_rate: 72.0, avg_profit: 1.21, total_trades: 75,  capital: 500,  ann_return: 35.2, max_drawdown: 5.7,  weekly: [6,10,5,14,8,4,11,13,7,9,12,8] },
    { name: 'DEPENDENCY',   color: '#f59e0b', win_rate: 55.0, avg_profit: 0.62, total_trades: 20,  capital: 300,  ann_return: 18.4, max_drawdown: 11.2, weekly: [1,3,0,5,2,1,4,5,2,3,4,2] },
    { name: 'CROSS_MARKET', color: '#ec4899', win_rate: 66.7, avg_profit: 1.45, total_trades: 12,  capital: 200,  ann_return: 31.0, max_drawdown: 6.8,  weekly: [2,4,1,6,3,2,5,6,3,4,5,3] },
  ],

  pnlHistory: (() => {
    const points = 90;
    const strategies = ['MERGE_ARB','MEAN_REV','PRICE_MAGNET','DEPENDENCY','CROSS_MARKET'];
    const labels = Array.from({ length: points }, (_, i) => {
      const d = new Date(now - (points - 1 - i) * 86400000);
      return d.toLocaleDateString('en-US', { month:'short', day:'numeric' });
    });
    let running = { MERGE_ARB:0, MEAN_REV:0, PRICE_MAGNET:0, DEPENDENCY:0, CROSS_MARKET:0 };
    const data = {};
    strategies.forEach(s => data[s] = []);
    const totals = [];
    labels.forEach(() => {
      let dayTotal = 0;
      strategies.forEach(s => {
        const daily = (Math.random() * 4 - 0.8) * { MERGE_ARB:2.5, MEAN_REV:1.2, PRICE_MAGNET:1.5, DEPENDENCY:0.6, CROSS_MARKET:0.8 }[s];
        running[s] = +(running[s] + daily).toFixed(2);
        data[s].push(running[s]);
        dayTotal += daily;
      });
      totals.push(+(Object.values(running).reduce((a,b) => a+b, 0)).toFixed(2));
    });
    return { labels, data, totals };
  })(),
};
