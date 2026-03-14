"""Pydantic data models for the SQLite schema."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TradeStatus(str, enum.Enum):
    """Lifecycle states of a merge-arb trade."""

    PENDING = "pending"         # opportunity identified, orders not yet placed
    OPEN = "open"               # orders placed, waiting for fills
    PARTIAL = "partial"         # one leg filled
    FILLED = "filled"           # both legs filled, awaiting merge
    MERGED = "merged"           # mergePositions called, profit realised
    CANCELLED = "cancelled"     # one or both orders cancelled/expired
    FAILED = "failed"           # on-chain call failed


class Trade(BaseModel):
    """A single merge-arb trade record."""

    id: Optional[int] = Field(default=None, description="Auto-assigned row ID")
    condition_id: str = Field(description="Polymarket condition ID (bytes32 hex)")
    market_question: str = Field(default="", description="Human-readable question")
    market_category: str = Field(default="", description="news / sports / crypto …")

    yes_token_id: str = Field(description="ERC-1155 token ID for YES")
    no_token_id: str = Field(description="ERC-1155 token ID for NO")

    yes_order_id: Optional[str] = Field(default=None)
    no_order_id: Optional[str] = Field(default=None)

    yes_ask: float = Field(description="Ask price for YES at opportunity detection")
    no_ask: float = Field(description="Ask price for NO at opportunity detection")
    fee_total: float = Field(default=0.0, description="Estimated taker fee sum")

    amount_usdc: float = Field(description="USDC spent (trade size)")
    shares: float = Field(description="Number of share pairs bought")
    gross_profit: float = Field(description="1.00 * shares - amount_usdc before fees")
    net_profit: float = Field(description="gross_profit - fees")

    status: TradeStatus = Field(default=TradeStatus.PENDING)
    tx_hash: Optional[str] = Field(default=None, description="mergePositions tx hash")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    filled_at: Optional[datetime] = Field(default=None)
    merged_at: Optional[datetime] = Field(default=None)

    error: Optional[str] = Field(default=None, description="Last error message")


class Position(BaseModel):
    """Open conditional-token position (YES + NO held, not yet merged)."""

    condition_id: str
    yes_token_id: str
    no_token_id: str
    yes_amount: float = 0.0     # shares held
    no_amount: float = 0.0      # shares held
    usdc_cost: float = 0.0      # total USDC spent acquiring them
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CapitalSnapshot(BaseModel):
    """Point-in-time capital snapshot for P&L tracking."""

    id: Optional[int] = Field(default=None)
    usdc_balance: float
    open_positions_value: float
    realised_pnl: float
    unrealised_pnl: float
    total_trades: int
    winning_trades: int
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


class MicrostructureSignal(BaseModel):
    """Cached microstructure analysis result for a single market token."""

    id: Optional[int] = Field(default=None)
    market_id: str = Field(description="Polymarket condition ID")
    token_id: str = Field(description="ERC-1155 token ID analysed")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    imbalance: float = Field(default=0.0, description="(Q_bid - Q_ask) / (Q_bid + Q_ask)")
    spread: float = Field(default=0.0, description="ask_best - bid_best")
    net_flow: float = Field(default=0.0, description="Signed trade flow over window")
    vwap: float = Field(default=0.0, description="Volume-weighted avg fill price")
    score: float = Field(default=0.0, description="Composite score 0–100")


class MeanReversionPositionStatus(str, enum.Enum):
    """Lifecycle of a mean-reversion position."""

    OPEN = "open"
    FILLED = "filled"       # entry order filled, waiting for exit
    EXITED = "exited"       # exit order executed successfully
    STOPPED = "stopped"     # stop-loss triggered
    CANCELLED = "cancelled"


class MeanReversionPosition(BaseModel):
    """A mean-reversion strategy position (extreme or price-magnet)."""

    id: Optional[int] = Field(default=None)
    market_id: str = Field(description="Polymarket condition ID")
    token_id: str = Field(description="ERC-1155 token ID for the position")
    side: str = Field(description="'YES' or 'NO'")
    strategy_type: str = Field(description="'extreme_reversion' or 'price_magnet'")

    entry_price: float = Field(description="Limit price for the BUY order")
    target_price: float = Field(description="Limit price for the exit SELL order")
    stop_loss: float = Field(description="Stop-loss threshold price")

    shares: float = Field(default=0.0, description="Number of tokens purchased")
    usdc_spent: float = Field(default=0.0, description="USDC committed")

    entry_order_id: Optional[str] = Field(default=None)
    exit_order_id: Optional[str] = Field(default=None)

    status: MeanReversionPositionStatus = Field(default=MeanReversionPositionStatus.OPEN)
    realised_pnl: float = Field(default=0.0)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    filled_at: Optional[datetime] = Field(default=None)
    closed_at: Optional[datetime] = Field(default=None)
    error: Optional[str] = Field(default=None)


class DependencyType(str, enum.Enum):
    """Type of logical relationship between two markets."""

    SUBSET = "subset"               # B's YES is a logical subset of A's YES
    MUTUAL_EXCLUSIVE = "mutual_exclusive"  # A and B cannot both resolve YES
    STATE_NATIONAL = "state_national"      # State-level implies national-level
    TEAM_TOURNAMENT = "team_tournament"    # Team advance implies further win


class DependencyPair(BaseModel):
    """A detected logical dependency between two Polymarket markets."""

    id: Optional[int] = Field(default=None)
    market_a_id: str = Field(description="Condition ID of the broader / parent market")
    market_b_id: str = Field(description="Condition ID of the narrower / child market")
    dependency_type: DependencyType = Field(description="Nature of the relationship")

    price_a: float = Field(default=0.0, description="YES price of market A at detection")
    price_b: float = Field(default=0.0, description="YES price of market B at detection")
    expected_profit: float = Field(default=0.0, description="Estimated arb profit (USDC per share)")

    detected_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = Field(default=True, description="False once exploited or resolved")
