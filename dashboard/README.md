# ARB-BOT Dashboard

Real-time trading dashboard for the Polymarket merge-arb bot.

## Quick Start

Open `dashboard/index.html` directly in your browser — no build step, no server required.

> Because the pages use ES modules (`type="module"`), Chrome/Firefox block local-file module imports by default.
> Use one of these methods:

### Option 1 — Python HTTP server (recommended)

```bash
cd dashboard
python3 -m http.server 3000
# then open: http://localhost:3000
```

### Option 2 — Node / npx

```bash
cd dashboard
npx serve .
# then open: http://localhost:3000
```

### Option 3 — VS Code Live Server extension

Right-click `index.html` → "Open with Live Server".

---

## Pages

| Page | File | Refresh |
|------|------|---------|
| Main Dashboard | `index.html` | 10s |
| Strategy Performance | `strategies.html` | 30s |
| Market Scanner | `scanner.html` | 10s |
| Trade Log | `trades.html` | on-demand |
| Wallet | `wallet.html` | 30s |

---

## Backend

The dashboard calls the bot's REST API at `http://localhost:8080/api`.

Start the bot with the API server:

```bash
python main.py run --dry-run
```

When the backend is unreachable, the dashboard automatically falls back to
**mock data** so every panel still renders correctly.

### Endpoints consumed

```
GET /api/status           Bot status, capital, P&L summary
GET /api/opportunities    Live arb opportunities
GET /api/positions        Open positions
GET /api/trades           Trade history (?limit=&strategy=)
GET /api/markets          All active markets with signals
GET /api/wallet           Wallet balances and holdings
GET /api/strategies       Per-strategy performance
GET /api/pnl/history      Equity curve data (90 days)
```

---

## File Structure

```
dashboard/
├── index.html          Main dashboard (4 panels)
├── strategies.html     Strategy performance
├── scanner.html        Live market scanner
├── trades.html         Trade log with CSV export
├── wallet.html         Wallet balances + tx history
├── css/
│   ├── theme.css       Design tokens (colors, fonts, spacing)
│   └── main.css        Component styles + responsive layout
├── js/
│   ├── api.js          All fetch calls + mock data fallback
│   ├── charts.js       Chart.js wrappers (donut, equity, bar)
│   ├── scanner.js      Sortable/filterable market table
│   ├── positions.js    Positions table + order book modal
│   └── app.js          Main entry — polling loop, header stats
└── README.md
```

---

## Design

- Dark terminal theme — `#0a0a0a` background, `#1a1a2e` panels, `#00ff88` accents
- JetBrains Mono for all numbers
- Mobile responsive — panels stack on screens < 1024px
- Skeleton loading states on first load
- No frameworks — plain HTML5 + vanilla JS ES modules + Chart.js 4 via CDN
