"""Unit tests for the fee calculator."""

import pytest
from scanner.fee_calculator import FeeCalculator, MarketCategory, classify_market


@pytest.fixture
def calc() -> FeeCalculator:
    return FeeCalculator()


# ─── classify_market ─────────────────────────────────────────────────────────


def test_classify_crypto_btc():
    cat = classify_market("Will BTC hit $100k in 2025?")
    assert cat == MarketCategory.CRYPTO


def test_classify_crypto_ethereum():
    cat = classify_market("Will Ethereum ETF be approved?", category="crypto")
    assert cat == MarketCategory.CRYPTO


def test_classify_ncaab():
    cat = classify_market("Will Duke win the NCAAB championship?")
    assert cat == MarketCategory.SPORTS_NCAAB


def test_classify_serie_a():
    cat = classify_market("Will Inter Milan win Serie A?")
    assert cat == MarketCategory.SPORTS_NCAAB


def test_classify_zero_fee_default():
    cat = classify_market("Will the US economy enter recession in 2025?")
    assert cat == MarketCategory.ZERO_FEE


def test_classify_tags_override():
    cat = classify_market("Something", tags=["crypto", "defi"])
    assert cat == MarketCategory.CRYPTO


# ─── taker_fee_rate ──────────────────────────────────────────────────────────


def test_zero_fee_market(calc: FeeCalculator):
    rate = calc.taker_fee_rate(0.5, MarketCategory.ZERO_FEE)
    assert rate == 0.0


def test_crypto_fee_at_midpoint(calc: FeeCalculator):
    """Crypto fee at p=0.5 should be ≈ 1.5625 %."""
    rate = calc.taker_fee_rate(0.5, MarketCategory.CRYPTO)
    assert abs(rate - 0.015625) < 1e-8


def test_ncaab_fee_at_midpoint(calc: FeeCalculator):
    """NCAAB fee at p=0.5 should be ≈ 0.4375 %."""
    rate = calc.taker_fee_rate(0.5, MarketCategory.SPORTS_NCAAB)
    assert abs(rate - 0.004375) < 1e-8


def test_fee_zero_at_boundary(calc: FeeCalculator):
    """Fee should be 0 at price = 0 or 1."""
    for cat in MarketCategory:
        assert calc.taker_fee_rate(0.0, cat) == 0.0
        assert calc.taker_fee_rate(1.0, cat) == 0.0


def test_fee_increases_toward_midpoint(calc: FeeCalculator):
    """Crypto fee should be higher at p=0.5 than at the tails.

    The formula 2·p·0.25·(p·(1−p))² is NOT symmetric in p because of the
    leading p factor — it increases more steeply above 0.5 than below.
    """
    rate_mid = calc.taker_fee_rate(0.5, MarketCategory.CRYPTO)
    rate_tail = calc.taker_fee_rate(0.1, MarketCategory.CRYPTO)
    assert rate_mid > rate_tail  # highest fee near midpoint


# ─── net_profit_per_share ────────────────────────────────────────────────────


def test_net_profit_positive_zero_fee(calc: FeeCalculator):
    """Sum 0.48 + 0.49 = 0.97 → profit 0.03 for zero-fee market."""
    profit = calc.net_profit_per_share(0.48, 0.49, MarketCategory.ZERO_FEE)
    assert abs(profit - 0.03) < 1e-9


def test_net_profit_maker_no_fee(calc: FeeCalculator):
    """Maker order should have same profit as zero-fee even in crypto market."""
    profit_maker = calc.net_profit_per_share(0.48, 0.49, MarketCategory.CRYPTO, use_maker=True)
    profit_zero = calc.net_profit_per_share(0.48, 0.49, MarketCategory.ZERO_FEE)
    assert abs(profit_maker - profit_zero) < 1e-9


def test_net_profit_taker_crypto_reduced(calc: FeeCalculator):
    """Taker in crypto should earn less than maker due to fees."""
    profit_maker = calc.net_profit_per_share(0.48, 0.49, MarketCategory.CRYPTO, use_maker=True)
    profit_taker = calc.net_profit_per_share(0.48, 0.49, MarketCategory.CRYPTO, use_maker=False)
    assert profit_taker < profit_maker


def test_net_profit_unprofitable_returns_negative(calc: FeeCalculator):
    """Sum > 1.00 should yield negative profit."""
    profit = calc.net_profit_per_share(0.51, 0.52, MarketCategory.ZERO_FEE)
    assert profit < 0


# ─── is_profitable ───────────────────────────────────────────────────────────


def test_is_profitable_above_threshold(calc: FeeCalculator):
    assert calc.is_profitable(0.48, 0.49, MarketCategory.ZERO_FEE, min_profit_pct=0.10)


def test_is_profitable_below_threshold(calc: FeeCalculator):
    """Sum > 1.00 is unprofitable regardless of threshold."""
    # YES@0.50 + NO@0.51 = 1.01 → loss
    assert not calc.is_profitable(0.50, 0.51, MarketCategory.ZERO_FEE, min_profit_pct=0.10)
    # Sum = 0.9999 → profit 0.0001 = 0.01% < 0.10% threshold
    assert not calc.is_profitable(0.4999, 0.5, MarketCategory.ZERO_FEE, min_profit_pct=0.10)


def test_is_profitable_maker_vs_taker_crypto(calc: FeeCalculator):
    """Same prices: maker profitable, taker not (due to high crypto fee)."""
    yes_ask, no_ask = 0.489, 0.5
    # Maker: profit = 1 - 0.489 - 0.5 = 0.011 = 1.1% > 0.10%
    assert calc.is_profitable(yes_ask, no_ask, MarketCategory.CRYPTO, use_maker=True)
    # Taker: profit = 0.011 - fees; fees at 0.5 are ~1.5% so net < 0
    assert not calc.is_profitable(yes_ask, no_ask, MarketCategory.CRYPTO, use_maker=False)
