# Build Task: agnostic_mechanical_arb_bot

Build a complete, production-grade Polymarket merge arbitrage bot. All files must be real, working code — no placeholders.

## What This Bot Does

Exploits merge arbitrage on Polymarket:
1. Scans all active markets for YES + NO ask prices that sum to < $1.00 (after fees)
2. Buys both YES and NO shares
3. Calls CTF mergePositions() on Polygon to immediately collect $1.00 USDC
4. No waiting for market resolution — instant profit

## Tech Stack
- Python 3.11+ for scanning, strategy, capital management, API clients
- Rust for on-chain transaction execution (CTF merge calls), wallet management
- Python calls Rust binary via subprocess for on-chain ops
- SQLite for trade logging

## Polymarket Fee Structure
- NON-crypto/sports markets (politics, news, etc.): ZERO fees — threshold is any gap < $1.00
- Crypto markets: fee = C * p * 0.25 * (p * (1-p))^2, max 1.56% at p=0.50
- NCAAB/Serie A: fee = C * p * 0.0175 * (p * (1-p))^1, max 0.44%
- MAKERS pay ZERO fees and earn daily USDC rebates (20-25% of fee pool)
- Strategy: use maker (limit) orders where possible to avoid all fees

## Full Project Structure

```
agnostic_mechanical_arb_bot/
├── README.md
├── pyproject.toml
├── requirements.txt
├── .env.example
├── config.toml
├── main.py
├── scanner/
│   ├── __init__.py
│   ├── gamma_client.py
│   ├── clob_client.py
│   ├── opportunity_detector.py
│   └── fee_calculator.py
├── strategy/
│   ├── __init__.py
│   ├── merge_arb.py
│   ├── maker_arb.py
│   └── capital_recycler.py
├── executor/
│   ├── __init__.py
│   ├── order_placer.py
│   ├── position_tracker.py
│   └── merge_trigger.py
├── chain/
│   ├── Cargo.toml
│   ├── src/
│   │   ├── main.rs
│   │   ├── lib.rs
│   │   ├── merge.rs
│   │   ├── wallet.rs
│   │   ├── contracts.rs
│   │   └── types.rs
│   └── abi/
│       ├── ctf.json
│       └── ctf_exchange.json
├── capital/
│   ├── __init__.py
│   ├── manager.py
│   └── allocator.py
├── db/
│   ├── __init__.py
│   ├── models.py
│   └── store.py
├── utils/
│   ├── __init__.py
│   ├── logger.py
│   └── alerts.py
└── tests/
    ├── test_fee_calculator.py
    ├── test_opportunity_detector.py
    ├── test_merge_arb.py
    └── test_capital_manager.py
```

## Key Implementation Details

### Polymarket API Endpoints
- Gamma API: https://gamma-api.polymarket.com
  - GET /markets?active=true&closed=false&limit=100
  - GET /markets/{condition_id}
- CLOB API: https://clob.polymarket.com
  - GET /book?token_id={token_id}
  - POST /order (requires EIP-712 signature)
  - DELETE /order/{order_id}

### Polygon Contract Addresses
- CTF (ConditionalTokens): 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
- CTFExchange: 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
- USDC on Polygon: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
- Polygon RPC: https://polygon-rpc.com

### CTF Merge (Rust)
Function: mergePositions(collateralToken, parentCollectionId, conditionId, partition, amount)
- collateralToken = USDC address
- parentCollectionId = bytes32(0)
- conditionId = market's condition_id  
- partition = [1, 2]
- amount = shares in wei (6 decimals)
- Must approve CTF contract to spend YES and NO tokens first

### config.toml
```toml
[scanner]
poll_interval_seconds = 30
min_profit_pct = 0.10
max_trade_size_usdc = 100.0
markets_per_batch = 50

[capital]
total_capital_usdc = 300.0
max_allocation_pct = 0.30
kelly_fraction = 0.25

[execution]
mode = "maker"
maker_spread_buffer = 0.002

[chain]
polygon_rpc = "https://polygon-rpc.com"
gas_buffer_multiplier = 1.2

[alerts]
log_level = "INFO"
webhook_url = ""
```

### main.py CLI modes
- python main.py scan       # dry run, print opportunities
- python main.py run        # live trading
- python main.py run --dry-run
- python main.py status     # show P&L, positions
- python main.py merge --condition-id <id> --amount <n>

## Code Requirements
- Full type hints (Python)
- async/await for all API calls (use httpx)
- Proper error handling, never crash on API failures
- Retry with exponential backoff
- All secrets via env vars
- Docstrings on all public functions
- Working unit tests with mocked HTTP calls

## README Must Cover
1. What this bot does (merge arb explanation)
2. Prerequisites
3. Setup
4. Usage (all CLI modes)
5. Strategy explanation
6. Risk warnings
7. ASCII architecture diagram

Build all files completely. When done, run:
openclaw system event --text "Done: agnostic_mechanical_arb_bot built" --mode now
