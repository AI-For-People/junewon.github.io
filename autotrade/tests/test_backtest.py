from datetime import datetime, timedelta

import pytest

from autotrade.backtest import load_candles_csv, run_backtest
from autotrade.config import CostModel, RiskLimits, Settings
from autotrade.models import Candle, Side
from autotrade.strategies import BuyAndHoldStrategy, SmaCrossStrategy


def ramp(prices):
    return [
        Candle(datetime(2024, 1, 1) + timedelta(days=i), p, p, p, p, 1000)
        for i, p in enumerate(prices)
    ]


def test_needs_at_least_two_bars():
    with pytest.raises(ValueError):
        run_backtest(ramp([100]), BuyAndHoldStrategy())


def test_buy_and_hold_profits_on_a_rising_market():
    prices = [10_000 + i * 100 for i in range(100)]
    r = run_backtest(ramp(prices), BuyAndHoldStrategy(target_notional=1_000_000),
                     starting_cash=2_000_000)
    assert r.total_return > 0
    assert len(r.trades) == 1 and r.trades[0].side is Side.BUY


def test_costs_are_charged():
    prices = [10_000] * 60
    strat = SmaCrossStrategy(fast=2, slow=5, target_notional=500_000)
    r = run_backtest(ramp(prices), strat, starting_cash=1_000_000,
                     costs=CostModel(fee_rate=0.001, sell_tax_rate=0.002, slippage_bps=0))
    # 가격이 평평하면 신호가 없거나, 있어도 비용만큼 손해다.
    assert r.final_equity <= 1_000_000


def test_zero_cost_flat_market_is_break_even():
    prices = [10_000] * 60
    r = run_backtest(ramp(prices), BuyAndHoldStrategy(target_notional=500_000),
                     starting_cash=1_000_000,
                     costs=CostModel(fee_rate=0, sell_tax_rate=0, slippage_bps=0))
    assert r.final_equity == pytest.approx(1_000_000)


def test_max_drawdown_is_negative_on_a_crash():
    # 1주문 한도를 풀어야 자금 대부분이 투입돼 낙폭이 드러난다.
    settings = Settings(risk=RiskLimits(max_order_notional=5_000_000))
    prices = [10_000 + i * 100 for i in range(50)] + [15_000 - i * 200 for i in range(50)]
    r = run_backtest(ramp(prices), BuyAndHoldStrategy(target_notional=5_000_000),
                     starting_cash=5_000_000, settings=settings)
    assert r.max_drawdown < -0.1


def test_never_spends_more_cash_than_available():
    prices = [10_000] * 40
    strat = BuyAndHoldStrategy(target_notional=10_000_000)
    r = run_backtest(ramp(prices), strat, starting_cash=100_000)
    assert all(equity >= 0 for _, equity in r.equity_curve)
    assert r.final_equity <= 100_000


def test_benchmark_is_reported():
    prices = [10_000 + i * 50 for i in range(80)]
    r = run_backtest(ramp(prices), SmaCrossStrategy(fast=3, slow=10), starting_cash=1_000_000)
    assert r.benchmark_return > 0
    assert "매수후보유" in r.summary()


def test_result_is_deterministic():
    prices = [10_000 + (i % 17) * 120 for i in range(120)]
    strat = lambda: SmaCrossStrategy(fast=3, slow=10, target_notional=300_000)
    a = run_backtest(ramp(prices), strat(), starting_cash=1_000_000)
    b = run_backtest(ramp(prices), strat(), starting_cash=1_000_000)
    assert a.final_equity == b.final_equity and len(a.trades) == len(b.trades)


def test_load_candles_csv_roundtrip(tmp_path):
    csv_path = tmp_path / "c.csv"
    csv_path.write_text(
        "date,open,high,low,close,volume\n"
        "2024-01-03,100,110,90,105,10\n"
        "2024-01-02,100,110,90,101,10\n",
        encoding="utf-8",
    )
    rows = load_candles_csv(csv_path)
    assert [c.close for c in rows] == [101, 105], "날짜 오름차순으로 정렬돼야 한다"


def test_shipped_sample_data_runs():
    from pathlib import Path

    sample = Path(__file__).resolve().parent.parent / "data" / "sample_synthetic_daily.csv"
    r = run_backtest(load_candles_csv(sample), SmaCrossStrategy(), symbol="SAMPLE")
    assert len(r.equity_curve) == 500
    assert r.final_equity > 0
