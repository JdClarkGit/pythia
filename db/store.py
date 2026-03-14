"""Async SQLite store using aiosqlite."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import aiosqlite

from db.models import (
    CapitalSnapshot,
    DependencyPair,
    DependencyType,
    MeanReversionPosition,
    MeanReversionPositionStatus,
    MicrostructureSignal,
    Position,
    Trade,
    TradeStatus,
)
from utils.logger import get_logger

log = get_logger(__name__)

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id    TEXT NOT NULL,
    market_question TEXT DEFAULT '',
    market_category TEXT DEFAULT '',
    yes_token_id    TEXT NOT NULL,
    no_token_id     TEXT NOT NULL,
    yes_order_id    TEXT,
    no_order_id     TEXT,
    yes_ask         REAL NOT NULL,
    no_ask          REAL NOT NULL,
    fee_total       REAL DEFAULT 0,
    amount_usdc     REAL NOT NULL,
    shares          REAL NOT NULL,
    gross_profit    REAL NOT NULL,
    net_profit      REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    tx_hash         TEXT,
    created_at      TEXT NOT NULL,
    filled_at       TEXT,
    merged_at       TEXT,
    error           TEXT
);
"""

_CREATE_POSITIONS = """
CREATE TABLE IF NOT EXISTS positions (
    condition_id    TEXT PRIMARY KEY,
    yes_token_id    TEXT NOT NULL,
    no_token_id     TEXT NOT NULL,
    yes_amount      REAL DEFAULT 0,
    no_amount       REAL DEFAULT 0,
    usdc_cost       REAL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""

_CREATE_CAPITAL = """
CREATE TABLE IF NOT EXISTS capital_snapshots (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    usdc_balance            REAL NOT NULL,
    open_positions_value    REAL NOT NULL,
    realised_pnl            REAL NOT NULL,
    unrealised_pnl          REAL NOT NULL,
    total_trades            INTEGER NOT NULL,
    winning_trades          INTEGER NOT NULL,
    recorded_at             TEXT NOT NULL
);
"""

_CREATE_MICROSTRUCTURE = """
CREATE TABLE IF NOT EXISTS microstructure_signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   TEXT NOT NULL,
    token_id    TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    imbalance   REAL DEFAULT 0,
    spread      REAL DEFAULT 0,
    net_flow    REAL DEFAULT 0,
    vwap        REAL DEFAULT 0,
    score       REAL DEFAULT 0
);
"""

_CREATE_MEAN_REVERSION = """
CREATE TABLE IF NOT EXISTS mean_reversion_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id           TEXT NOT NULL,
    token_id            TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_type       TEXT NOT NULL,
    entry_price         REAL NOT NULL,
    target_price        REAL NOT NULL,
    stop_loss           REAL NOT NULL,
    shares              REAL DEFAULT 0,
    usdc_spent          REAL DEFAULT 0,
    entry_order_id      TEXT,
    exit_order_id       TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    realised_pnl        REAL DEFAULT 0,
    created_at          TEXT NOT NULL,
    filled_at           TEXT,
    closed_at           TEXT,
    error               TEXT
);
"""

_CREATE_DEPENDENCY_PAIRS = """
CREATE TABLE IF NOT EXISTS dependency_pairs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    market_a_id      TEXT NOT NULL,
    market_b_id      TEXT NOT NULL,
    dependency_type  TEXT NOT NULL,
    price_a          REAL DEFAULT 0,
    price_b          REAL DEFAULT 0,
    expected_profit  REAL DEFAULT 0,
    detected_at      TEXT NOT NULL,
    is_active        INTEGER DEFAULT 1
);
"""


class Store:
    """Thin async wrapper around the SQLite database.

    Args:
        db_path: Path to the SQLite file.  Use ``":memory:"`` for tests.
    """

    def __init__(self, db_path: str = "arb_bot.db") -> None:
        self._path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def open(self) -> None:
        """Open the database connection and create tables if missing."""
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(
            _CREATE_TRADES
            + _CREATE_POSITIONS
            + _CREATE_CAPITAL
            + _CREATE_MICROSTRUCTURE
            + _CREATE_MEAN_REVERSION
            + _CREATE_DEPENDENCY_PAIRS
        )
        await self._db.commit()
        log.debug("Database opened: %s", self._path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> "Store":
        await self.open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    async def insert_trade(self, trade: Trade) -> int:
        """Persist a new trade and return its auto-assigned ID.

        Args:
            trade: :class:`~db.models.Trade` instance (``id`` ignored).

        Returns:
            The new row ID.
        """
        assert self._db is not None
        cur = await self._db.execute(
            """
            INSERT INTO trades (
                condition_id, market_question, market_category,
                yes_token_id, no_token_id,
                yes_order_id, no_order_id,
                yes_ask, no_ask, fee_total,
                amount_usdc, shares, gross_profit, net_profit,
                status, tx_hash,
                created_at, filled_at, merged_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade.condition_id,
                trade.market_question,
                trade.market_category,
                trade.yes_token_id,
                trade.no_token_id,
                trade.yes_order_id,
                trade.no_order_id,
                trade.yes_ask,
                trade.no_ask,
                trade.fee_total,
                trade.amount_usdc,
                trade.shares,
                trade.gross_profit,
                trade.net_profit,
                trade.status.value,
                trade.tx_hash,
                trade.created_at.isoformat(),
                trade.filled_at.isoformat() if trade.filled_at else None,
                trade.merged_at.isoformat() if trade.merged_at else None,
                trade.error,
            ),
        )
        await self._db.commit()
        row_id = cur.lastrowid or 0
        log.debug("Inserted trade id=%d condition=%s", row_id, trade.condition_id)
        return row_id

    async def update_trade_status(
        self,
        trade_id: int,
        status: TradeStatus,
        *,
        tx_hash: Optional[str] = None,
        error: Optional[str] = None,
        filled_at: Optional[datetime] = None,
        merged_at: Optional[datetime] = None,
    ) -> None:
        """Update status (and optional fields) for an existing trade.

        Args:
            trade_id: Row ID of the trade.
            status: New :class:`~db.models.TradeStatus`.
            tx_hash: On-chain transaction hash (for MERGED status).
            error: Error message (for FAILED/CANCELLED status).
            filled_at: Timestamp when both legs were filled.
            merged_at: Timestamp when merge was confirmed.
        """
        assert self._db is not None
        await self._db.execute(
            """
            UPDATE trades SET
                status    = ?,
                tx_hash   = COALESCE(?, tx_hash),
                error     = COALESCE(?, error),
                filled_at = COALESCE(?, filled_at),
                merged_at = COALESCE(?, merged_at)
            WHERE id = ?
            """,
            (
                status.value,
                tx_hash,
                error,
                filled_at.isoformat() if filled_at else None,
                merged_at.isoformat() if merged_at else None,
                trade_id,
            ),
        )
        await self._db.commit()

    async def update_trade_orders(
        self, trade_id: int, yes_order_id: str, no_order_id: str
    ) -> None:
        """Store order IDs after placement.

        Args:
            trade_id: Row ID of the trade.
            yes_order_id: CLOB order ID for the YES leg.
            no_order_id: CLOB order ID for the NO leg.
        """
        assert self._db is not None
        await self._db.execute(
            "UPDATE trades SET yes_order_id=?, no_order_id=?, status=? WHERE id=?",
            (yes_order_id, no_order_id, TradeStatus.OPEN.value, trade_id),
        )
        await self._db.commit()

    async def get_trades_by_status(self, status: TradeStatus) -> list[Trade]:
        """Fetch all trades matching a given status.

        Args:
            status: Filter by this :class:`~db.models.TradeStatus`.

        Returns:
            List of :class:`~db.models.Trade` instances.
        """
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM trades WHERE status=? ORDER BY created_at DESC", (status.value,)
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_trade(dict(r)) for r in rows]

    async def get_all_trades(self, limit: int = 500) -> list[Trade]:
        """Return recent trades ordered by creation time.

        Args:
            limit: Maximum number of rows to return.

        Returns:
            List of :class:`~db.models.Trade` instances.
        """
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_trade(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    async def upsert_position(self, pos: Position) -> None:
        """Insert or update a position record.

        Args:
            pos: :class:`~db.models.Position` instance.
        """
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO positions
                (condition_id, yes_token_id, no_token_id, yes_amount, no_amount,
                 usdc_cost, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(condition_id) DO UPDATE SET
                yes_amount  = excluded.yes_amount,
                no_amount   = excluded.no_amount,
                usdc_cost   = excluded.usdc_cost,
                updated_at  = excluded.updated_at
            """,
            (
                pos.condition_id,
                pos.yes_token_id,
                pos.no_token_id,
                pos.yes_amount,
                pos.no_amount,
                pos.usdc_cost,
                pos.created_at.isoformat(),
                pos.updated_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def delete_position(self, condition_id: str) -> None:
        """Remove a position once it has been merged.

        Args:
            condition_id: Market condition ID.
        """
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM positions WHERE condition_id=?", (condition_id,)
        )
        await self._db.commit()

    async def get_open_positions(self) -> list[Position]:
        """Return all open (un-merged) positions.

        Returns:
            List of :class:`~db.models.Position` instances.
        """
        assert self._db is not None
        async with self._db.execute("SELECT * FROM positions") as cur:
            rows = await cur.fetchall()
        return [
            Position(
                condition_id=r["condition_id"],
                yes_token_id=r["yes_token_id"],
                no_token_id=r["no_token_id"],
                yes_amount=r["yes_amount"],
                no_amount=r["no_amount"],
                usdc_cost=r["usdc_cost"],
                created_at=datetime.fromisoformat(r["created_at"]),
                updated_at=datetime.fromisoformat(r["updated_at"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Capital snapshots
    # ------------------------------------------------------------------

    async def insert_snapshot(self, snap: CapitalSnapshot) -> None:
        """Persist a capital snapshot.

        Args:
            snap: :class:`~db.models.CapitalSnapshot` instance.
        """
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO capital_snapshots
                (usdc_balance, open_positions_value, realised_pnl, unrealised_pnl,
                 total_trades, winning_trades, recorded_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                snap.usdc_balance,
                snap.open_positions_value,
                snap.realised_pnl,
                snap.unrealised_pnl,
                snap.total_trades,
                snap.winning_trades,
                snap.recorded_at.isoformat(),
            ),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Microstructure signals
    # ------------------------------------------------------------------

    async def insert_microstructure_signal(self, sig: MicrostructureSignal) -> int:
        """Persist a microstructure signal snapshot.

        Args:
            sig: :class:`~db.models.MicrostructureSignal` instance.

        Returns:
            The new row ID.
        """
        assert self._db is not None
        cur = await self._db.execute(
            """
            INSERT INTO microstructure_signals
                (market_id, token_id, timestamp, imbalance, spread, net_flow, vwap, score)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                sig.market_id,
                sig.token_id,
                sig.timestamp.isoformat(),
                sig.imbalance,
                sig.spread,
                sig.net_flow,
                sig.vwap,
                sig.score,
            ),
        )
        await self._db.commit()
        return cur.lastrowid or 0

    async def get_latest_microstructure_signal(
        self, market_id: str
    ) -> Optional[MicrostructureSignal]:
        """Return the most recent signal for a market.

        Args:
            market_id: Condition ID to query.

        Returns:
            :class:`~db.models.MicrostructureSignal` or ``None``.
        """
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM microstructure_signals WHERE market_id=? ORDER BY timestamp DESC LIMIT 1",
            (market_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        r = dict(row)
        return MicrostructureSignal(
            id=r["id"],
            market_id=r["market_id"],
            token_id=r["token_id"],
            timestamp=datetime.fromisoformat(r["timestamp"]),
            imbalance=r["imbalance"],
            spread=r["spread"],
            net_flow=r["net_flow"],
            vwap=r["vwap"],
            score=r["score"],
        )

    # ------------------------------------------------------------------
    # Mean-reversion positions
    # ------------------------------------------------------------------

    async def insert_mean_reversion_position(self, pos: MeanReversionPosition) -> int:
        """Persist a new mean-reversion position.

        Args:
            pos: :class:`~db.models.MeanReversionPosition` instance.

        Returns:
            The new row ID.
        """
        assert self._db is not None
        cur = await self._db.execute(
            """
            INSERT INTO mean_reversion_positions (
                market_id, token_id, side, strategy_type,
                entry_price, target_price, stop_loss,
                shares, usdc_spent, entry_order_id, exit_order_id,
                status, realised_pnl, created_at, filled_at, closed_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pos.market_id,
                pos.token_id,
                pos.side,
                pos.strategy_type,
                pos.entry_price,
                pos.target_price,
                pos.stop_loss,
                pos.shares,
                pos.usdc_spent,
                pos.entry_order_id,
                pos.exit_order_id,
                pos.status.value,
                pos.realised_pnl,
                pos.created_at.isoformat(),
                pos.filled_at.isoformat() if pos.filled_at else None,
                pos.closed_at.isoformat() if pos.closed_at else None,
                pos.error,
            ),
        )
        await self._db.commit()
        return cur.lastrowid or 0

    async def update_mean_reversion_status(
        self,
        position_id: int,
        status: MeanReversionPositionStatus,
        *,
        exit_order_id: Optional[str] = None,
        realised_pnl: Optional[float] = None,
        filled_at: Optional[datetime] = None,
        closed_at: Optional[datetime] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update the status of a mean-reversion position.

        Args:
            position_id: Row ID of the position.
            status: New status.
            exit_order_id: CLOB order ID for the exit leg.
            realised_pnl: Actual P&L when position closes.
            filled_at: When entry was filled.
            closed_at: When position was fully closed.
            error: Error message if applicable.
        """
        assert self._db is not None
        await self._db.execute(
            """
            UPDATE mean_reversion_positions SET
                status         = ?,
                exit_order_id  = COALESCE(?, exit_order_id),
                realised_pnl   = COALESCE(?, realised_pnl),
                filled_at      = COALESCE(?, filled_at),
                closed_at      = COALESCE(?, closed_at),
                error          = COALESCE(?, error)
            WHERE id = ?
            """,
            (
                status.value,
                exit_order_id,
                realised_pnl,
                filled_at.isoformat() if filled_at else None,
                closed_at.isoformat() if closed_at else None,
                error,
                position_id,
            ),
        )
        await self._db.commit()

    async def get_open_mean_reversion_positions(self) -> list[MeanReversionPosition]:
        """Return all non-terminal mean-reversion positions.

        Returns:
            List of :class:`~db.models.MeanReversionPosition` instances.
        """
        assert self._db is not None
        terminal = (
            MeanReversionPositionStatus.EXITED.value,
            MeanReversionPositionStatus.STOPPED.value,
            MeanReversionPositionStatus.CANCELLED.value,
        )
        placeholders = ",".join("?" * len(terminal))
        async with self._db.execute(
            f"SELECT * FROM mean_reversion_positions WHERE status NOT IN ({placeholders})",
            terminal,
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_mr_position(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Dependency pairs
    # ------------------------------------------------------------------

    async def insert_dependency_pair(self, pair: DependencyPair) -> int:
        """Persist a newly detected dependency pair.

        Args:
            pair: :class:`~db.models.DependencyPair` instance.

        Returns:
            The new row ID.
        """
        assert self._db is not None
        cur = await self._db.execute(
            """
            INSERT INTO dependency_pairs
                (market_a_id, market_b_id, dependency_type, price_a, price_b,
                 expected_profit, detected_at, is_active)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                pair.market_a_id,
                pair.market_b_id,
                pair.dependency_type.value,
                pair.price_a,
                pair.price_b,
                pair.expected_profit,
                pair.detected_at.isoformat(),
                1 if pair.is_active else 0,
            ),
        )
        await self._db.commit()
        return cur.lastrowid or 0

    async def get_active_dependency_pairs(self) -> list[DependencyPair]:
        """Return all active (unexploited) dependency pairs.

        Returns:
            List of :class:`~db.models.DependencyPair` instances.
        """
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM dependency_pairs WHERE is_active=1 ORDER BY expected_profit DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_dep_pair(dict(r)) for r in rows]

    async def deactivate_dependency_pair(self, pair_id: int) -> None:
        """Mark a dependency pair as exploited / no longer active.

        Args:
            pair_id: Row ID.
        """
        assert self._db is not None
        await self._db.execute(
            "UPDATE dependency_pairs SET is_active=0 WHERE id=?", (pair_id,)
        )
        await self._db.commit()

    async def get_realised_pnl(self) -> float:
        """Sum net_profit across all MERGED trades.

        Returns:
            Total realised P&L in USDC.
        """
        assert self._db is not None
        async with self._db.execute(
            "SELECT COALESCE(SUM(net_profit), 0) FROM trades WHERE status='merged'"
        ) as cur:
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def get_trade_stats(self) -> dict[str, int | float]:
        """Return aggregate trade statistics.

        Returns:
            Dict with ``total``, ``merged``, ``failed``, ``win_rate``.
        """
        assert self._db is not None
        async with self._db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='merged' THEN 1 ELSE 0 END) AS merged,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
            FROM trades
            """
        ) as cur:
            row = await cur.fetchone()
        total = int(row["total"] or 0)
        merged = int(row["merged"] or 0)
        failed = int(row["failed"] or 0)
        win_rate = (merged / total * 100) if total > 0 else 0.0
        return {"total": total, "merged": merged, "failed": failed, "win_rate": win_rate}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _row_to_mr_position(r: dict) -> MeanReversionPosition:  # type: ignore[type-arg]
    return MeanReversionPosition(
        id=r["id"],
        market_id=r["market_id"],
        token_id=r["token_id"],
        side=r["side"],
        strategy_type=r["strategy_type"],
        entry_price=r["entry_price"],
        target_price=r["target_price"],
        stop_loss=r["stop_loss"],
        shares=r.get("shares", 0.0),
        usdc_spent=r.get("usdc_spent", 0.0),
        entry_order_id=r.get("entry_order_id"),
        exit_order_id=r.get("exit_order_id"),
        status=MeanReversionPositionStatus(r["status"]),
        realised_pnl=r.get("realised_pnl", 0.0),
        created_at=datetime.fromisoformat(r["created_at"]),
        filled_at=datetime.fromisoformat(r["filled_at"]) if r.get("filled_at") else None,
        closed_at=datetime.fromisoformat(r["closed_at"]) if r.get("closed_at") else None,
        error=r.get("error"),
    )


def _row_to_dep_pair(r: dict) -> DependencyPair:  # type: ignore[type-arg]
    return DependencyPair(
        id=r["id"],
        market_a_id=r["market_a_id"],
        market_b_id=r["market_b_id"],
        dependency_type=DependencyType(r["dependency_type"]),
        price_a=r.get("price_a", 0.0),
        price_b=r.get("price_b", 0.0),
        expected_profit=r.get("expected_profit", 0.0),
        detected_at=datetime.fromisoformat(r["detected_at"]),
        is_active=bool(r.get("is_active", 1)),
    )


def _row_to_trade(r: dict) -> Trade:  # type: ignore[type-arg]
    return Trade(
        id=r["id"],
        condition_id=r["condition_id"],
        market_question=r.get("market_question", ""),
        market_category=r.get("market_category", ""),
        yes_token_id=r["yes_token_id"],
        no_token_id=r["no_token_id"],
        yes_order_id=r.get("yes_order_id"),
        no_order_id=r.get("no_order_id"),
        yes_ask=r["yes_ask"],
        no_ask=r["no_ask"],
        fee_total=r.get("fee_total", 0.0),
        amount_usdc=r["amount_usdc"],
        shares=r["shares"],
        gross_profit=r["gross_profit"],
        net_profit=r["net_profit"],
        status=TradeStatus(r["status"]),
        tx_hash=r.get("tx_hash"),
        created_at=datetime.fromisoformat(r["created_at"]),
        filled_at=datetime.fromisoformat(r["filled_at"]) if r.get("filled_at") else None,
        merged_at=datetime.fromisoformat(r["merged_at"]) if r.get("merged_at") else None,
        error=r.get("error"),
    )
