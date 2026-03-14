"""Entry point for the Polymarket merge-arb bot.

Usage:
    python main.py scan                          # dry-run: print opportunities
    python main.py run                           # live trading
    python main.py run --dry-run                 # live scan, simulated trades
    python main.py status                        # show P&L and open positions
    python main.py merge --condition-id <id> \\
                         --amount <shares>       # manually trigger a merge
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional, Annotated

import tomli
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from capital.allocator import KellyAllocator
from capital.manager import CapitalManager
from db.store import Store
from executor.merge_trigger import MergeTrigger
from executor.order_placer import OrderPlacer
from executor.position_tracker import PositionTracker
from scanner.clob_client import CLOBClient
from scanner.fee_calculator import FeeCalculator
from scanner.gamma_client import GammaClient
from scanner.opportunity_detector import OpportunityDetector
from strategy.capital_recycler import CapitalRecycler
from strategy.maker_arb import MakerArbStrategy
from strategy.merge_arb import MergeArbStrategy
from utils.alerts import Alerter
from utils.logger import configure_root, get_logger

# ─── Setup ───────────────────────────────────────────────────────────────────

load_dotenv()

log = get_logger(__name__)
app = typer.Typer(
    name="arb-bot",
    help="Polymarket merge-arbitrage bot",
    add_completion=False,
)
console = Console()

_CONFIG_PATH = Path(__file__).parent / "config.toml"


def _load_config() -> dict:  # type: ignore[type-arg]
    """Load config.toml, merging env var overrides."""
    with open(_CONFIG_PATH, "rb") as f:
        cfg = tomli.load(f)
    # Allow env var overrides for key numeric params
    if v := os.environ.get("MAX_TRADE_SIZE_USDC"):
        cfg["scanner"]["max_trade_size_usdc"] = float(v)
    if v := os.environ.get("MIN_PROFIT_PCT"):
        cfg["scanner"]["min_profit_pct"] = float(v)
    if v := os.environ.get("TOTAL_CAPITAL_USDC"):
        cfg["capital"]["total_capital_usdc"] = float(v)
    if v := os.environ.get("WEBHOOK_URL"):
        cfg["alerts"]["webhook_url"] = v
    if v := os.environ.get("POLYGON_RPC_URL"):
        cfg["chain"]["polygon_rpc"] = v
    return cfg


def _build_clients(cfg: dict) -> tuple:  # type: ignore[type-arg]
    """Instantiate shared API clients from config + env.

    Returns:
        (gamma_client, clob_client, merger, capital_mgr, allocator, store)
    """
    pk = os.environ.get("PRIVATE_KEY", "")
    api_key = os.environ.get("POLY_API_KEY", "")
    api_secret = os.environ.get("POLY_API_SECRET", "")
    api_passphrase = os.environ.get("POLY_API_PASSPHRASE", "")
    rpc = cfg["chain"]["polygon_rpc"]

    gamma = GammaClient()
    clob = CLOBClient(
        private_key=pk or None,
        api_key=api_key or None,
        api_secret=api_secret or None,
        api_passphrase=api_passphrase or None,
    )
    merger = MergeTrigger(
        rpc_url=rpc,
        private_key=pk or None,
        ctf_address=cfg["contracts"]["ctf"],
        usdc_address=cfg["contracts"]["usdc"],
        gas_buffer_multiplier=cfg["chain"].get("gas_buffer_multiplier", 1.2),
    )
    store = Store()
    capital = CapitalManager(
        initial_usdc=cfg["capital"]["total_capital_usdc"],
        store=store,
    )
    allocator = KellyAllocator(
        kelly_fraction=cfg["capital"]["kelly_fraction"],
        max_allocation_pct=cfg["capital"]["max_allocation_pct"],
        max_trade_usdc=cfg["scanner"]["max_trade_size_usdc"],
    )
    return gamma, clob, merger, capital, allocator, store


# ─── scan command ─────────────────────────────────────────────────────────────


@app.command()
def scan(
    min_profit: Annotated[float, typer.Option(help="Minimum net profit % (e.g. 0.10)")] = 0.10,
    limit: Annotated[int, typer.Option(help="Maximum markets to scan")] = 500,
    maker: Annotated[bool, typer.Option(help="Assume maker orders (no fees)")] = True,
) -> None:
    """Scan all active markets and print merge-arb opportunities (read-only)."""
    configure_root("INFO")
    cfg = _load_config()

    async def _scan() -> None:
        async with GammaClient() as gamma, CLOBClient() as clob:
            fee_calc = FeeCalculator()
            detector = OpportunityDetector(
                gamma,
                clob,
                fee_calc,
                min_profit_pct=min_profit,
                use_maker=maker,
                concurrency=20,
            )
            markets = await gamma.get_all_active_markets(batch_size=100)
            if limit:
                markets = markets[:limit]
            opportunities = await detector.scan(markets)

        if not opportunities:
            console.print("[yellow]No profitable opportunities found.[/yellow]")
            return

        table = Table(title=f"Merge-Arb Opportunities ({len(opportunities)} found)")
        table.add_column("Question", style="cyan", max_width=50)
        table.add_column("Category", style="dim")
        table.add_column("YES Ask", justify="right")
        table.add_column("NO Ask", justify="right")
        table.add_column("Gross %", justify="right", style="green")
        table.add_column("Net %", justify="right", style="bright_green")
        table.add_column("Max $ Profit", justify="right")

        for opp in opportunities:
            max_profit = opp.net_profit_per_share * opp.max_shares
            table.add_row(
                opp.market.question[:50],
                opp.category.value,
                f"{opp.yes_ask:.4f}",
                f"{opp.no_ask:.4f}",
                f"{opp.gross_profit_pct:.3f}%",
                f"{opp.net_profit_pct:.3f}%",
                f"${max_profit:.4f}",
            )
        console.print(table)

    asyncio.run(_scan())


# ─── run command ─────────────────────────────────────────────────────────────


@app.command()
def run(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate trades without real orders")] = False,
    mode: Annotated[str, typer.Option(help="'maker' or 'taker'")] = "",
) -> None:
    """Start the live trading loop."""
    configure_root("INFO")
    cfg = _load_config()
    exec_mode = mode or cfg["execution"]["mode"]

    if not dry_run and not os.environ.get("PRIVATE_KEY"):
        console.print("[red]PRIVATE_KEY not set. Set it in .env or export it.[/red]")
        raise typer.Exit(1)

    if not dry_run and exec_mode == "taker" and not os.environ.get("POLY_API_KEY"):
        console.print("[red]POLY_API_KEY not set (required for taker mode).[/red]")
        raise typer.Exit(1)

    gamma, clob, merger, capital, allocator, store = _build_clients(cfg)
    alerter = Alerter(cfg["alerts"].get("webhook_url", ""))
    fee_calc = FeeCalculator()
    poll_interval = cfg["scanner"]["poll_interval_seconds"]
    min_profit = cfg["scanner"]["min_profit_pct"]
    spread_buf = cfg["execution"].get("maker_spread_buffer", 0.002)
    order_timeout = cfg["execution"].get("order_timeout_seconds", 300)

    async def _run() -> None:
        await store.open()
        placer = OrderPlacer(clob)

        if exec_mode == "maker":
            strategy = MakerArbStrategy(
                capital, allocator, placer, merger, store, alerter,
                spread_buffer=spread_buf,
                order_timeout=order_timeout,
                dry_run=dry_run,
            )
        else:
            strategy = MergeArbStrategy(  # type: ignore[assignment]
                capital, allocator, placer, merger, store, alerter,
                fee_calc, dry_run=dry_run,
            )

        recycler = CapitalRecycler(capital)
        recycler.register_executor(strategy.execute)

        detector = OpportunityDetector(
            gamma, clob, fee_calc,
            min_profit_pct=min_profit,
            use_maker=(exec_mode == "maker"),
            concurrency=20,
        )

        mode_label = "[DRY-RUN] " if dry_run else ""
        console.print(
            f"[bold green]{mode_label}Starting merge-arb bot[/bold green] "
            f"mode={exec_mode} capital={capital.free_usdc:.2f} USDC"
        )

        async with gamma, clob, alerter:
            await alerter.send(f"{mode_label}Bot started — capital {capital.free_usdc:.2f} USDC")
            while True:
                try:
                    opportunities = await detector.scan()
                    recycler.enqueue(opportunities)

                    # Execute top opportunities up to max concurrency
                    tasks = []
                    for opp in opportunities[:5]:  # limit concurrent trades
                        if capital.free_usdc < 1.0:
                            break
                        tasks.append(asyncio.create_task(strategy.execute(opp)))

                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                        await recycler.recycle()

                    console.print(f"[dim]{capital.status_line()}[/dim]")
                    await asyncio.sleep(poll_interval)

                except asyncio.CancelledError:
                    log.info("Shutdown signal received")
                    break
                except Exception as exc:  # noqa: BLE001
                    log.error("Main loop error: %s", exc, exc_info=True)
                    await asyncio.sleep(5)

        await store.close()
        console.print("[bold]Bot stopped.[/bold]")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


# ─── status command ───────────────────────────────────────────────────────────


@app.command()
def status() -> None:
    """Display current P&L, open positions, and trade statistics."""
    configure_root("WARNING")

    async def _status() -> None:
        store = Store()
        await store.open()

        pnl = await store.get_realised_pnl()
        stats = await store.get_trade_stats()
        positions = await store.get_open_positions()
        recent = await store.get_all_trades(limit=10)

        console.print(f"\n[bold]P&L:[/bold] {pnl:+.6f} USDC")
        console.print(
            f"[bold]Trades:[/bold] {stats['total']} total | "
            f"{stats['merged']} merged | "
            f"{stats['failed']} failed | "
            f"win-rate {stats['win_rate']:.1f}%"
        )

        if positions:
            pos_table = Table(title="Open Positions")
            pos_table.add_column("Condition ID", style="dim")
            pos_table.add_column("Shares", justify="right")
            pos_table.add_column("USDC Cost", justify="right")
            for pos in positions:
                pos_table.add_row(
                    pos.condition_id[:20] + "…",
                    f"{min(pos.yes_amount, pos.no_amount):.4f}",
                    f"${pos.usdc_cost:.4f}",
                )
            console.print(pos_table)

        if recent:
            trade_table = Table(title="Recent Trades (last 10)")
            trade_table.add_column("ID", justify="right")
            trade_table.add_column("Question", max_width=40)
            trade_table.add_column("Status")
            trade_table.add_column("Net P&L", justify="right")
            trade_table.add_column("Created")
            for t in recent:
                status_color = {
                    "merged": "green",
                    "failed": "red",
                    "cancelled": "yellow",
                    "open": "blue",
                }.get(t.status.value, "white")
                trade_table.add_row(
                    str(t.id),
                    t.market_question[:40],
                    f"[{status_color}]{t.status.value}[/{status_color}]",
                    f"{t.net_profit:+.6f}",
                    t.created_at.strftime("%m-%d %H:%M"),
                )
            console.print(trade_table)

        await store.close()

    asyncio.run(_status())


# ─── merge command ────────────────────────────────────────────────────────────


@app.command()
def merge(
    condition_id: Annotated[str, typer.Option("--condition-id", help="Market condition ID (bytes32 hex)")],
    amount: Annotated[float, typer.Option("--amount", help="Number of share-pairs to merge")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Manually trigger an on-chain mergePositions() call."""
    configure_root("INFO")
    cfg = _load_config()

    pk = os.environ.get("PRIVATE_KEY", "")
    if not pk and not dry_run:
        console.print("[red]PRIVATE_KEY not set.[/red]")
        raise typer.Exit(1)

    merger = MergeTrigger(
        rpc_url=cfg["chain"]["polygon_rpc"],
        private_key=pk or None,
        ctf_address=cfg["contracts"]["ctf"],
        usdc_address=cfg["contracts"]["usdc"],
        dry_run=dry_run,
    )

    async def _merge() -> None:
        console.print(f"Merging condition_id={condition_id} amount={amount}")
        tx_hash = await merger.merge(condition_id=condition_id, shares=amount)
        if tx_hash:
            console.print(f"[green]Success![/green] tx={tx_hash}")
        else:
            console.print("[red]Merge failed — check logs.[/red]")
            raise typer.Exit(1)

    asyncio.run(_merge())


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
