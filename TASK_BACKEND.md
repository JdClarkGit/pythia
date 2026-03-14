# Backend Developer Task

You are a senior quant backend engineer. The base codebase at this directory is already built.
Your job: implement 4 advanced alpha strategies on top of it.

## Strategy 1: Mean Reversion at Extremes (90c/10c)

Create: strategy/mean_reversion.py

Logic:
- Scan all active markets for prices between $0.01-$0.15 (deep underdogs)
- Place limit BUY orders at $0.03-$0.15 (maker = zero fees)
- Set limit SELL orders at $0.87-$0.93 when filled
- Historical data shows: 6% of markets go from ~10c to 90c+ (800% return), 13% go 50c+ (500% return)
- Rough EV per $1 deployed: 0.06*8 + 0.13*5 - 0.81*1 = +$0.13 (13% per cycle, avg 83h hold)
- Enhanced Kelly for position sizing: f = (b*p - q) / b * sqrt(p)
  where b = potential profit %, p = estimated fill probability, q = 1-p

Key implementation:
- Scan Gamma API for markets where best_ask_yes < 0.15 OR best_ask_no < 0.15
- Filter: market must have >30 days to resolution (enough time for mean reversion)
- Filter: liquidity depth > $500 at target price
- Post maker limit orders (to avoid fees)
- Track positions in DB with entry price, target exit, stop loss at 0.005
- Never allocate more than 5% of capital to a single mean reversion bet

## Strategy 2: Price Magnet / 25c-75c Mean Reversion

Create: strategy/price_magnet.py

Logic:
- Markets statistically cluster at 0-5c, 25c, 50c, 75c, 95-100c
- After touching 75c/25c zone, market returns to 50c in ~54% of cases
- If market is at 75c: buy NO (priced at ~25c) with limit at 25c, target 50c (100% gain)
- If market is at 25c: buy YES (priced at ~25c), target 50c (100% gain)
- Rough model: 54 markets return to 50c (+100% each), 46 lose all = +8% on capital
- Average hold time to complete route: ~32 hours

Key implementation:
- Scan for markets in the 70-80c or 20-30c zone
- Check: is there a news/information catalyst? (if volume spiked recently, skip - it's info-driven)
- Check: orderbook imbalance. If imbalance > 0.3 in direction of move, skip (momentum not reversion)
- Check: post-trade drift on recent trades (did price continue or revert after last 3 large trades?)
- Place maker limit order at the zone
- Exit at 50c (limit sell)
- Stop loss: if price continues beyond 85c/15c, exit

## Strategy 3: Order Book Microstructure Signals

Create: scanner/microstructure.py

Implement these signals for every opportunity before execution:

1. Signed Trade Flow:
   net_flow[t-k, t] = sum(side_i * size_i for i in window)
   Positive = buy pressure, negative = sell pressure

2. Relative Trade Size:
   RTS = size_t / (Q_bestbid + Q_bestask)
   Normalize trade size to available liquidity

3. Spread Regime:
   spread = ask_best - bid_best
   narrow spread + strong buy flow = informational move (skip mean reversion)
   wide spread + sharp jump = temporary distortion (ENTER mean reversion)

4. Order Book Imbalance:
   imbalance = (Q_bestbid - Q_bestask) / (Q_bestbid + Q_bestask)
   +1 = pure buy pressure, -1 = pure sell pressure

5. Post-Trade Drift (requires recent trade history):
   impact_h = p[t+h] - p[t] for h = 1min, 5min, 15min
   If last 3 large trades show mean reversion (impact negative after buy) -> reversion likely
   If continuation -> skip

6. Extremity Score:
   extremity = min(p, 1-p)
   Lower extremity = higher info content per price move

7. VWAP Calculation:
   For order book depth, calculate actual expected fill price:
   vwap = sum(price_i * qty_i) / sum(qty_i) across order book levels until order filled
   Use this as the real entry price, not the best ask

All signals feed into a MicrostructureScore (0-100). Only execute if score > 60.

## Strategy 4: Enhanced Kelly Position Sizing

Update: capital/allocator.py

Replace basic Kelly with:

Standard Kelly: f = (b*p - q) / b
Enhanced Kelly (execution risk adjusted): f = (b*p - q) / b * sqrt(p_execution)

where:
- b = expected profit as fraction of bet (e.g., 0.05 for 5% merge arb profit)  
- p = win probability (1.0 for merge arb, estimated for other strategies)
- q = 1 - p
- p_execution = P(both legs fill at expected prices) = f(order_book_depth, trade_size)
  p_execution = min(depth_yes, depth_no) / trade_size clamped to [0, 1]
- Maximum bet: 50% of order book depth at target price (never move market more than this)
- Fractional Kelly: multiply by 0.25 (conservative) for mean reversion, 0.5 for merge arb

## Strategy 5: Cross-Market Dependency Detection (Simple Version)

Create: scanner/dependency_detector.py

Logic (simplified without Gurobi, use scipy for LP):
- Compare market pairs for logical dependencies:
  - Same event, different conditions (e.g., "Candidate wins" vs "Candidate wins by 5+")
  - Political: state winner vs national winner
  - Sports: team advances vs team wins championship
- Use simple rules to detect:
  - If P(A|B) = 1 (B implies A), then P(B) <= P(A) must hold
  - If P(A) + P(B) > 1 and A,B are mutually exclusive, arb exists
- Start with markets in same category (same tags in Gamma API)
- For each pair: check if YES prices are logically consistent
  - If market B's YES is a logical subset of market A's YES:
    price_B_yes MUST be <= price_A_yes (otherwise arb)
- When violation found: buy cheaper, sell/short more expensive
  (On Polymarket, "short" = buy the NO side)

For dependency screening, query the Gamma API for markets with overlapping tags.
Group by: election/state, sports/team/tournament.
Check price consistency within groups.

## Database Updates

Update db/models.py to add:
- MicrostructureSignal model (market_id, timestamp, imbalance, spread, net_flow, vwap, score)
- MeanReversionPosition model (market_id, entry_price, target_price, stop_loss, strategy_type)
- DependencyPair model (market_a_id, market_b_id, dependency_type, expected_profit)

## Requirements additions (add to requirements.txt):
- scipy>=1.13.0  (for LP optimization)
- scikit-learn>=1.5.0 (for XGBoost prep)
- xgboost>=2.0.0
- pandas>=2.2.0
- numpy>=1.26.0

## Testing
Add tests/test_mean_reversion.py, tests/test_microstructure.py with mocked data.

When completely finished, run:
openclaw system event --text "Done: Backend agent completed advanced strategies (mean reversion, price magnets, microstructure, enhanced Kelly, dependency detection)" --mode now
