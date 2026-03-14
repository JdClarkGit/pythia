"""Unit tests for the opportunity detector (mocked HTTP)."""

from __future__ import annotations

import pytest
import pytest_asyncio
import respx
import httpx

from scanner.clob_client import CLOBClient
from scanner.fee_calculator import FeeCalculator
from scanner.gamma_client import GammaClient, MarketInfo, TokenInfo
from scanner.opportunity_detector import OpportunityDetector, Opportunity


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_market() -> MarketInfo:
    return MarketInfo(
        condition_id="0xabc123",
        question="Will Congress pass the bill?",
        category="politics",
        tokens=[
            TokenInfo(token_id="111", outcome="Yes"),
            TokenInfo(token_id="222", outcome="No"),
        ],
    )


@pytest.fixture
def sample_book_response_yes() -> dict:
    return {
        "bids": [{"price": 0.47, "size": 100}],
        "asks": [{"price": 0.48, "size": 200}],
    }


@pytest.fixture
def sample_book_response_no() -> dict:
    return {
        "bids": [{"price": 0.49, "size": 100}],
        "asks": [{"price": 0.50, "size": 150}],
    }


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_finds_opportunity(
    sample_market: MarketInfo,
    sample_book_response_yes: dict,
    sample_book_response_no: dict,
):
    """YES@0.48 + NO@0.50 = 0.98 → 2% gross profit → should be detected."""
    with respx.mock(base_url="https://clob.polymarket.com") as mock_clob:
        mock_clob.get("/book", params={"token_id": "111"}).mock(
            return_value=httpx.Response(200, json=sample_book_response_yes)
        )
        mock_clob.get("/book", params={"token_id": "222"}).mock(
            return_value=httpx.Response(200, json=sample_book_response_no)
        )

        async with GammaClient() as gamma, CLOBClient() as clob:
            detector = OpportunityDetector(
                gamma,
                clob,
                FeeCalculator(),
                min_profit_pct=0.10,
                use_maker=True,
            )
            opportunities = await detector.scan(markets=[sample_market])

    assert len(opportunities) == 1
    opp = opportunities[0]
    assert abs(opp.yes_ask - 0.48) < 1e-9
    assert abs(opp.no_ask - 0.50) < 1e-9
    assert opp.gross_profit_pct > 1.9  # ≈ 2%
    assert opp.net_profit_per_share > 0.01


@pytest.mark.asyncio
async def test_scan_ignores_unprofitable(sample_market: MarketInfo):
    """YES@0.51 + NO@0.52 = 1.03 → loss → not returned."""
    with respx.mock(base_url="https://clob.polymarket.com") as mock_clob:
        mock_clob.get("/book", params={"token_id": "111"}).mock(
            return_value=httpx.Response(200, json={"asks": [{"price": 0.51, "size": 100}], "bids": []})
        )
        mock_clob.get("/book", params={"token_id": "222"}).mock(
            return_value=httpx.Response(200, json={"asks": [{"price": 0.52, "size": 100}], "bids": []})
        )

        async with GammaClient() as gamma, CLOBClient() as clob:
            detector = OpportunityDetector(
                gamma, clob, FeeCalculator(), min_profit_pct=0.10
            )
            opportunities = await detector.scan(markets=[sample_market])

    assert len(opportunities) == 0


@pytest.mark.asyncio
async def test_scan_handles_missing_book(sample_market: MarketInfo):
    """If one order book 404s, the market should be silently skipped."""
    with respx.mock(base_url="https://clob.polymarket.com") as mock_clob:
        mock_clob.get("/book", params={"token_id": "111"}).mock(
            return_value=httpx.Response(404)
        )
        mock_clob.get("/book", params={"token_id": "222"}).mock(
            return_value=httpx.Response(200, json={"asks": [{"price": 0.49, "size": 100}], "bids": []})
        )

        async with GammaClient() as gamma, CLOBClient() as clob:
            detector = OpportunityDetector(gamma, clob)
            opportunities = await detector.scan(markets=[sample_market])

    assert len(opportunities) == 0


@pytest.mark.asyncio
async def test_scan_skips_market_without_tokens():
    """Market with missing token IDs should be filtered before any API call."""
    market = MarketInfo(
        condition_id="0xdead",
        question="Incomplete market",
        tokens=[TokenInfo(token_id="999", outcome="Yes")],  # no NO token
    )
    async with GammaClient() as gamma, CLOBClient() as clob:
        detector = OpportunityDetector(gamma, clob)
        opportunities = await detector.scan(markets=[market])
    assert len(opportunities) == 0


def test_opportunity_profit_for_size():
    """profit_for_size should scale linearly."""
    from scanner.clob_client import OrderBook
    from scanner.gamma_client import MarketInfo, TokenInfo
    from scanner.fee_calculator import MarketCategory

    book = OrderBook(token_id="0", asks=[], bids=[])
    market = MarketInfo(
        condition_id="x",
        question="Test",
        tokens=[TokenInfo(token_id="0", outcome="Yes"), TokenInfo(token_id="1", outcome="No")],
    )
    opp = Opportunity(
        market=market,
        category=MarketCategory.ZERO_FEE,
        yes_ask=0.48,
        no_ask=0.49,
        yes_book=book,
        no_book=book,
        gross_profit_pct=3.0,
        net_profit_pct=3.0,
        net_profit_per_share=0.03,
        max_shares=100.0,
    )
    assert abs(opp.profit_for_size(10) - 0.30) < 1e-9
    assert abs(opp.profit_for_size(100) - 3.0) < 1e-9
