import pytest

from autotrade.models import Side
from autotrade.pricing import is_valid_tick, round_to_tick, shares_for_notional, tick_size


@pytest.mark.parametrize(
    "price,expected",
    [(1_500, 1), (2_000, 5), (4_999, 5), (5_000, 10), (19_999, 10),
     (20_000, 50), (49_999, 50), (50_000, 100), (199_999, 100),
     (200_000, 500), (499_999, 500), (500_000, 1_000), (1_200_000, 1_000)],
)
def test_tick_size_bands(price, expected):
    assert tick_size(price) == expected


def test_etf_tick_is_flat():
    assert tick_size(123_456, etf=True) == 5


def test_aggressive_rounding_is_marketable():
    """체결 우선 모드: 매수는 현재가 이상, 매도는 현재가 이하가 되어야 한다."""
    price = 71_234
    buy = round_to_tick(price, side=Side.BUY, mode="aggressive")
    sell = round_to_tick(price, side=Side.SELL, mode="aggressive")
    assert buy >= price and sell <= price
    assert buy == 71_300 and sell == 71_200


def test_passive_rounding_is_price_favourable():
    price = 71_234
    assert round_to_tick(price, side=Side.BUY, mode="passive") == 71_200
    assert round_to_tick(price, side=Side.SELL, mode="passive") == 71_300


def test_rounded_prices_are_always_valid_ticks():
    for price in range(1_000, 600_000, 977):
        for side in (Side.BUY, Side.SELL):
            for mode in ("aggressive", "passive", "nearest"):
                assert is_valid_tick(round_to_tick(price, side=side, mode=mode))


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        round_to_tick(10_000, side=Side.BUY, mode="wild-guess")


def test_shares_for_notional_floors():
    assert shares_for_notional(200_000, 71_300) == 2
    assert shares_for_notional(1_000, 71_300) == 0
    assert shares_for_notional(200_000, 0) == 0
