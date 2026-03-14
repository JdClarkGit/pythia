# Frontend Developer Task

You are a senior frontend engineer. Build a real-time dashboard for the agnostic_mechanical_arb_bot.
Use plain HTML + vanilla JS + Chart.js (no framework needed, must work by opening index.html directly).
Place everything in a dashboard/ folder.

## Stack
- HTML5 + CSS3 (dark theme, trading terminal aesthetic)
- Vanilla JavaScript (ES modules)
- Chart.js for charts
- Fetch API to call the Python bot's REST endpoints (backend runs on localhost:8080)

## Pages / Panels

### 1. Main Dashboard (index.html)

Header:
- Bot status indicator (RUNNING / STOPPED / DRY RUN) with color dot
- Total capital: $XXX.XX
- Today's P&L: +$XX.XX (XX%)
- All-time P&L: +$XXX.XX
- Active positions count

Main grid (4 panels):

Panel A - Live Opportunity Feed
- Table: Market Name | Strategy | YES | NO | Combined | Profit% | Action
- Color code by strategy: merge=green, mean_reversion=blue, price_magnet=purple
- Auto-refreshes every 10 seconds
- Click row to see order book depth

Panel B - Active Positions  
- Table: Market | Entry | Current | Target | Stop | Unrealized P&L | Age
- Color: green if trending toward target, red if trending toward stop

Panel C - Capital Allocation Donut Chart
- Available / In Merge Arb / In Mean Reversion / In Price Magnet / In Cross-Market
- Updates in real time

Panel D - P&L Equity Curve
- Line chart: time vs cumulative P&L
- Shows all 5 strategy layers as separate lines
- Overlay: total

### 2. Strategy Performance (strategies.html)

Table per strategy showing:
- Win rate %
- Avg profit per trade
- Total trades
- Capital deployed
- Annualized return
- Max drawdown

Bar chart: Profit by strategy over time (weekly buckets)

### 3. Market Scanner (scanner.html)

Live table of ALL active Polymarket markets showing:
- Market name / question
- YES price | NO price | Combined
- Spread regime (tight/wide)
- Order book imbalance score (-1 to +1 color bar)
- Microstructure score (0-100)
- Strategy signal: MERGE_ARB / MEAN_REV / PRICE_MAGNET / DEPENDENCY / NONE
- Volume 24h

Sortable by any column. Search box to filter by market name.

### 4. Trade Log (trades.html)

Full history table:
- Timestamp | Market | Strategy | Side | Size | Entry | Exit | Fees | P&L
- Filter by date range, strategy, outcome (win/loss)
- Export to CSV button

Summary cards:
- Total trades | Win rate | Avg trade duration | Largest win | Largest loss

### 5. Wallet (wallet.html)

Show:
- Wallet address: 0x666464ed833f297086c6ec8b72eba752546d07c2
- USDC balance (fetch from Polygon RPC)
- Current YES/NO token holdings
- Transaction history (last 20)
- Bridge USDC button (links to Polygon bridge)

## API Endpoints to Call (these will be added to main.py by backend)

GET /api/status           -> bot status, capital, P&L summary
GET /api/opportunities    -> live arb opportunities list
GET /api/positions        -> open positions
GET /api/trades           -> trade history (with ?limit=&strategy= filters)
GET /api/markets          -> all active markets with signals
GET /api/wallet           -> wallet balance and holdings
GET /api/strategies       -> per-strategy performance stats
GET /api/pnl/history      -> equity curve data

## Design Requirements
- Dark terminal theme: #0a0a0a background, #1a1a2e panels, #00ff88 accents
- Monospace font for numbers (JetBrains Mono via Google Fonts)
- No page reloads — use setInterval + fetch for live updates
- Mobile responsive (strategy cards stack on mobile)
- Skeleton loading states while data loads

## Files to Create
dashboard/
├── index.html
├── strategies.html
├── scanner.html
├── trades.html
├── wallet.html
├── css/
│   ├── main.css
│   └── theme.css
├── js/
│   ├── api.js          (all fetch calls)
│   ├── charts.js       (Chart.js wrappers)
│   ├── scanner.js      (live market table)
│   ├── positions.js    (positions panel)
│   └── app.js          (main entry, polling loop)
└── README.md           (how to open / run)

When completely finished, run:
openclaw system event --text "Done: Frontend dashboard built — 5 pages, live scanner, equity curve, P&L tracking, strategy breakdown" --mode now
