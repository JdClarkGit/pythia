# agnostic_mechanical_arb_bot

A production-grade **merge arbitrage bot** for [Polymarket](https://polymarket.com), written in Python + Rust.

---

## What is Merge Arbitrage?

On Polymarket, every binary market has a YES token and a NO token.
One pair of (1 YES + 1 NO) can **always** be redeemed for exactly **$1.00 USDC** via the Gnosis ConditionalTokens contract — regardless of how the market resolves.

If the sum of the best YES ask price and the best NO ask price is **less than $1.00** after fees, buying both and immediately merging yields instant, risk-free profit:

```
Profit = $1.00 − YES_ask − NO_ask − fees
```

No waiting for market resolution. The $1.00 is collected on-chain the moment `mergePositions()` succeeds.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                        main.py (CLI)                      │
│           scan | run | status | merge                     │
└────────────────────────┬─────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   ┌─────────────┐ ┌──────────┐ ┌─────────────┐
   │   scanner/  │ │ capital/ │ │  executor/  │
   │  gamma_api  │ │ manager  │ │order_placer │
   │  clob_api   │ │allocator │ │merge_trigger│
   │ opp_detect  │ │recycler  │ │pos_tracker  │
   │ fee_calc    │ └──────────┘ └──────┬──────┘
   └─────────────┘                     │ subprocess
          │                            ▼
          │                   ┌────────────────┐
          │  strategy/        │  chain/ (Rust) │
          └──► merge_arb ──►  │ chain_executor │
               maker_arb      │  mergePositions│
                              │  approve       │
                              └────────────────┘
          db/ (SQLite)
          └─ trades, positions, capital_snapshots
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| Rust | 1.78+ (stable) |
| `cargo` | bundled with Rust |

A funded Polygon wallet with:
- MATIC for gas
- USDC for buying tokens (start with ≥ $50 for meaningful testing)

---

## Setup

### 1. Clone & install Python deps

```bash
git clone <repo>
cd agnostic_mechanical_arb_bot

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Build the Rust binary

```bash
cd chain
cargo build --release
cd ..
```

The binary will be at `chain/target/release/chain_executor`.

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
PRIVATE_KEY=0xYOUR_PRIVATE_KEY
WALLET_ADDRESS=0xYOUR_WALLET_ADDRESS

# Required for order placement (obtain from polymarket.com/profile)
POLY_API_KEY=...
POLY_API_SECRET=...
POLY_API_PASSPHRASE=...

POLYGON_RPC_URL=https://polygon-rpc.com
```

### 4. Review `config.toml`

Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `scanner.min_profit_pct` | `0.10` | Minimum net profit in % (0.10 = 0.1 %) |
| `scanner.max_trade_size_usdc` | `100.0` | Max USDC per trade |
| `capital.total_capital_usdc` | `300.0` | Starting capital |
| `capital.kelly_fraction` | `0.25` | Quarter-Kelly position sizing |
| `execution.mode` | `"maker"` | `"maker"` or `"taker"` |

---

## Usage

### Scan for opportunities (read-only, no orders placed)

```bash
python main.py scan
python main.py scan --min-profit 0.20 --no-maker   # taker mode
```

### Start live trading

```bash
python main.py run               # maker mode (default)
python main.py run --dry-run     # simulate without real orders
python main.py run --mode taker  # taker mode (higher fees)
```

### View P&L and positions

```bash
python main.py status
```

### Manually trigger a merge (advanced)

```bash
python main.py merge \
  --condition-id 0xabc...def \
  --amount 10.5
```

### Run tests

```bash
pytest tests/ -v
```

---

## Strategy Explanation

### Taker Mode (simple, instant)

1. Detect YES ask + NO ask < $1.00 − fees
2. Place two market BUY orders simultaneously
3. When both fill, call `mergePositions()` via Rust binary
4. Collect $1.00 USDC per share-pair

**Fees (taker):**
- Politics/news markets: **0%** — any gap < $1.00 is pure profit
- Crypto markets: up to **1.5625%** at p=0.5 (formula: `2·p·0.25·(p·(1−p))²`)
- NCAAB/Serie A: up to **0.4375%** at p=0.5 (formula: `2·p·0.0175·(p·(1−p))`)

### Maker Mode (recommended)

1. Place limit BUY orders slightly inside the best ask
2. Makers pay **zero fees** and earn daily USDC rebates (20–25% of fee pool)
3. If both legs fill within timeout, trigger merge
4. Cancel unfilled orders after timeout

The maker spread buffer (default: 0.002 = 0.2¢) trades off fill probability vs. profit. Lower = more fills; higher = more profit per fill.

### Capital Sizing

Quarter-Kelly position sizing:

```
f* = (edge / (1 + edge)) × 0.25
```

where `edge = net_profit / cost`. Capped at `max_allocation_pct` of free capital and `max_trade_size_usdc`.

### Capital Recycling

After each successful merge, the returned USDC is immediately re-queued into the next best opportunity, maximising capital velocity.

---

## On-Chain Merge (Rust)

The Python bot shells out to `chain/target/release/chain_executor`:

```
chain_executor merge \
  --condition-id 0x... \
  --amount 1000000 \      # 1.0 share in 6-decimal units
  --rpc https://polygon-rpc.com
```

Internally this:
1. Checks `isApprovedForAll` on the CTF contract
2. Calls `setApprovalForAll` if not yet approved
3. Calls `CTF.mergePositions(usdc, bytes32(0), conditionId, [1,2], amount)`
4. Prints `{"tx_hash": "0x..."}` as JSON

Contract addresses (Polygon mainnet):

| Contract | Address |
|----------|---------|
| ConditionalTokens (CTF) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| CTFExchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| USDC | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |

---

## Database

SQLite (`arb_bot.db`) stores:

- **trades**: Full lifecycle record per trade
- **positions**: Open YES/NO token balances
- **capital_snapshots**: Point-in-time P&L snapshots

---

## Risk Warnings

> **This software is provided as-is for educational purposes. Use at your own risk.**

1. **Slippage risk**: Order books can change between scan and fill. Always use `min_profit_pct` to buffer against slippage.

2. **Partial-fill risk**: If only one leg fills, you hold a directional position. The bot will cancel the other leg, but you may need to sell the filled leg manually.

3. **Gas cost risk**: `mergePositions()` costs ~60,000–120,000 Polygon gas. With high MATIC prices this can erode small profits.

4. **RPC reliability**: Polygon RPCs can be slow or rate-limited. Use a premium RPC (Alchemy/QuickNode) for production.

5. **Smart contract risk**: Funds interact directly with Polymarket/Gnosis contracts. Review the contracts before depositing significant capital.

6. **API key security**: Never commit `.env` to version control. Use environment variables or a secrets manager.

7. **Start small**: Run with `--dry-run` first. Start with a small capital allocation ($50–$100) to validate end-to-end flow before scaling.

---

## Project Structure

```
agnostic_mechanical_arb_bot/
├── main.py                   # CLI entry point
├── config.toml               # Bot configuration
├── .env.example              # Environment variable template
├── scanner/
│   ├── gamma_client.py       # Gamma API (market metadata)
│   ├── clob_client.py        # CLOB API (order books, orders)
│   ├── opportunity_detector.py  # Scan + rank opportunities
│   └── fee_calculator.py    # Polymarket taker fee formulas
├── strategy/
│   ├── merge_arb.py          # Taker merge-arb strategy
│   ├── maker_arb.py          # Maker limit-order strategy
│   └── capital_recycler.py  # Re-deploy capital after merges
├── executor/
│   ├── order_placer.py       # Place + monitor YES/NO orders
│   ├── position_tracker.py  # Track open conditional positions
│   └── merge_trigger.py     # Shell out to Rust binary
├── capital/
│   ├── manager.py            # Capital ledger + reservations
│   └── allocator.py          # Kelly-criterion position sizer
├── db/
│   ├── models.py             # Pydantic models (Trade, Position…)
│   └── store.py              # Async SQLite store
├── utils/
│   ├── logger.py             # Rich structured logger
│   └── alerts.py             # Webhook alerting
├── chain/                    # Rust on-chain executor
│   ├── Cargo.toml
│   ├── src/
│   │   ├── main.rs           # CLI (merge / approve / balance)
│   │   ├── lib.rs
│   │   ├── merge.rs          # mergePositions logic
│   │   ├── wallet.rs         # Private key → EthereumWallet
│   │   ├── contracts.rs      # alloy sol! ABI bindings
│   │   └── types.rs          # Shared types + helpers
│   └── abi/
│       ├── ctf.json          # ConditionalTokens ABI
│       └── ctf_exchange.json # CTFExchange ABI
└── tests/
    ├── test_fee_calculator.py
    ├── test_opportunity_detector.py
    ├── test_merge_arb.py
    └── test_capital_manager.py
```
