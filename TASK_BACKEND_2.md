# Backend Dev — Sprint 2 Completion

The codebase is mostly built. Two things remain:

## 1. Add REST API server to main.py

Add a `python main.py serve` command that starts an aiohttp (or fastapi) REST server on port 8080.
Add `aiohttp>=3.9.0` or `fastapi>=0.111.0` + `uvicorn>=0.29.0` to requirements.txt.

Endpoints to implement (all return JSON):

GET /api/status
  Returns: { "running": bool, "mode": "live|dry_run|stopped", "capital_total": float,
             "capital_available": float, "pnl_today": float, "pnl_alltime": float,
             "active_positions": int, "uptime_seconds": int }

GET /api/opportunities
  Returns: list of current arb opportunities from scanner
  Each: { "market_id": str, "question": str, "strategy": str, "yes_price": float,
          "no_price": float, "combined": float, "profit_pct": float, "capital_needed": float }

GET /api/positions
  Returns: list of open positions from DB
  Each: { "id": int, "market_id": str, "question": str, "strategy": str,
          "entry_price": float, "current_price": float, "target_price": float,
          "stop_loss": float, "size_usdc": float, "unrealized_pnl": float, "age_hours": float }

GET /api/trades?limit=100&strategy=merge_arb
  Returns: paginated trade history from DB

GET /api/markets
  Returns: list of active Polymarket markets with microstructure scores
  Each: { "market_id": str, "question": str, "yes_price": float, "no_price": float,
          "spread": float, "imbalance": float, "microstructure_score": int,
          "signal": str, "volume_24h": float }

GET /api/wallet
  Returns: { "address": "0x666464ed833f297086c6ec8b72eba752546d07c2",
             "usdc_balance": float, "polygon_matic": float }

GET /api/strategies
  Returns: per-strategy performance stats
  Each: { "name": str, "win_rate": float, "avg_profit": float, "total_trades": int,
          "capital_deployed": float, "annualized_return": float }

GET /api/pnl/history
  Returns: list of { "timestamp": str, "cumulative_pnl": float, "strategy": str }
  for equity curve chart

Add CORS headers to all responses (allow all origins for local dashboard use).

## 2. Add missing test for enhanced Kelly allocator

Create tests/test_allocator.py with:
- Test basic Kelly calculation
- Test execution probability adjustment
- Test max position size capping at 50% order book depth

When done, run:
openclaw system event --text "Done: REST API server + allocator tests complete" --mode now
