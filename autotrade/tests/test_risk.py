"""리스크 계층 테스트 — 여기가 뚫리면 계좌가 뚫린다. 가장 촘촘하게 본다."""

from datetime import datetime, timedelta

import pytest

from autotrade.clock import KST, MarketCalendar
from autotrade.models import AccountSnapshot, Order, OrderType, Position, Side
from autotrade.risk import RiskGuard

from .conftest import TRADING_TIME


def make_guard(limits, tmp_path, **kw):
    return RiskGuard(
        limits,
        calendar=MarketCalendar(holidays=set()),
        kill_switch_path=tmp_path / "KILL_SWITCH",
        **kw,
    )


def buy(symbol="005930", qty=2, price=70_000):
    return Order(symbol=symbol, side=Side.BUY, qty=qty, order_type=OrderType.LIMIT, price=price)


def sell(symbol="005930", qty=2, price=70_000):
    return Order(symbol=symbol, side=Side.SELL, qty=qty, order_type=OrderType.LIMIT, price=price)


def account(cash=10_000_000, **positions):
    return AccountSnapshot(
        cash=cash,
        positions={s: Position(s, q, p) for s, (q, p) in positions.items()},
    )


def test_allows_a_normal_order(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    assert g.check_order(buy(), account(), {"005930": 70_000}, when=TRADING_TIME).allowed


def test_empty_whitelist_blocks_everything(limits, tmp_path):
    limits.symbol_whitelist = ()
    g = make_guard(limits, tmp_path)
    d = g.check_order(buy(), account(), {"005930": 70_000}, when=TRADING_TIME)
    assert not d.allowed and d.rule == "whitelist"


def test_symbol_outside_whitelist_blocked(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    d = g.check_order(buy("000660"), account(), {"000660": 70_000}, when=TRADING_TIME)
    assert not d.allowed and d.rule == "whitelist"


def test_order_notional_cap(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    d = g.check_order(buy(qty=10), account(), {"005930": 70_000}, when=TRADING_TIME)
    assert not d.allowed and d.rule == "max_order_notional"


def test_insufficient_cash_blocked(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    d = g.check_order(buy(qty=2), account(cash=1_000), {"005930": 70_000}, when=TRADING_TIME)
    assert not d.allowed and d.rule == "insufficient_cash"


def test_position_cap(limits, tmp_path):
    limits.max_position_notional = 200_000
    g = make_guard(limits, tmp_path)
    acct = account(**{"005930": (2, 70_000)})
    d = g.check_order(buy(qty=2), acct, {"005930": 70_000}, when=TRADING_TIME)
    assert not d.allowed and d.rule == "max_position_notional"


def test_gross_exposure_cap(limits, tmp_path):
    limits.max_gross_exposure = 100_000
    limits.max_position_notional = 10_000_000
    g = make_guard(limits, tmp_path)
    acct = account(**{"005930": (1, 70_000)})
    d = g.check_order(buy(qty=2), acct, {"005930": 70_000}, when=TRADING_TIME)
    assert not d.allowed and d.rule == "max_gross_exposure"


def test_cannot_sell_more_than_held(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    acct = account(**{"005930": (1, 70_000)})
    d = g.check_order(sell(qty=3), acct, {"005930": 70_000}, when=TRADING_TIME)
    assert not d.allowed and d.rule == "short_sell"


def test_sell_within_holding_allowed(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    acct = account(**{"005930": (3, 70_000)})
    assert g.check_order(sell(qty=3), acct, {"005930": 70_000}, when=TRADING_TIME).allowed


def test_blocked_outside_market_hours(limits, tmp_path):
    g = RiskGuard(limits, calendar=MarketCalendar(holidays=set()),
                  kill_switch_path=tmp_path / "KS", trade_only_in_session=True)
    night = datetime(2026, 9, 7, 23, 0, tzinfo=KST)
    d = g.check_order(buy(), account(), {"005930": 70_000}, when=night)
    assert not d.allowed and d.rule == "market_hours"


def test_blocked_on_holiday(limits, tmp_path):
    holiday = datetime(2026, 9, 25, 10, 30, tzinfo=KST)
    g = RiskGuard(limits, calendar=MarketCalendar(holidays={holiday.date()}),
                  kill_switch_path=tmp_path / "KS")
    d = g.check_order(buy(), account(), {"005930": 70_000}, when=holiday)
    assert not d.allowed and d.rule == "market_hours"


def test_per_minute_throttle(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    acct, prices = account(), {"005930": 70_000}
    for i in range(limits.max_orders_per_minute):
        o = buy()
        assert g.check_order(o, acct, prices, when=TRADING_TIME + timedelta(seconds=i)).allowed
        g.record_submitted(o, when=TRADING_TIME + timedelta(seconds=i))
        # 쿨다운 회피를 위해 방향 키를 비운다(분당 한도만 보기 위함)
        g.state.last_signal_at.clear()
    d = g.check_order(buy(), acct, prices, when=TRADING_TIME + timedelta(seconds=10))
    assert not d.allowed and d.rule == "max_orders_per_minute"


def test_daily_order_cap_trips_kill_switch(limits, tmp_path):
    limits.max_orders_per_day = 2
    limits.max_orders_per_minute = 100
    g = make_guard(limits, tmp_path)
    acct, prices = account(), {"005930": 70_000}
    for i in range(2):
        o = buy()
        when = TRADING_TIME + timedelta(minutes=i * 5)
        assert g.check_order(o, acct, prices, when=when).allowed
        g.record_submitted(o, when=when)
    d = g.check_order(buy(), acct, prices, when=TRADING_TIME + timedelta(minutes=30))
    assert not d.allowed and d.rule == "max_orders_per_day"
    assert g.kill_switch_engaged(), "일일 한도 도달 시 킬 스위치가 올라가야 한다"


def test_same_signal_cooldown(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    acct, prices = account(), {"005930": 70_000}
    o = buy()
    assert g.check_order(o, acct, prices, when=TRADING_TIME).allowed
    g.record_submitted(o, when=TRADING_TIME)
    d = g.check_order(buy(), acct, prices, when=TRADING_TIME + timedelta(seconds=30))
    assert not d.allowed and d.rule == "signal_cooldown"
    later = TRADING_TIME + timedelta(seconds=61)
    assert g.check_order(buy(), acct, prices, when=later).allowed


def test_daily_loss_limit_trips_and_blocks(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    assert g.observe_equity(10_000_000, when=TRADING_TIME).allowed
    d = g.observe_equity(9_890_000, when=TRADING_TIME + timedelta(minutes=1))
    assert not d.allowed and d.rule == "daily_loss_limit"
    blocked = g.check_order(buy(), account(), {"005930": 70_000},
                            when=TRADING_TIME + timedelta(minutes=2))
    assert not blocked.allowed and blocked.rule == "kill_switch"


def test_loss_within_limit_is_fine(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    g.observe_equity(10_000_000, when=TRADING_TIME)
    assert g.observe_equity(9_950_000, when=TRADING_TIME + timedelta(minutes=1)).allowed


def test_kill_switch_file_blocks_orders(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    (tmp_path / "KILL_SWITCH").write_text("stop")
    d = g.check_order(buy(), account(), {"005930": 70_000}, when=TRADING_TIME)
    assert not d.allowed and d.rule == "kill_switch"
    g.release_kill_switch()
    assert g.check_order(buy(), account(), {"005930": 70_000}, when=TRADING_TIME).allowed


def test_counters_reset_next_day(limits, tmp_path):
    limits.max_orders_per_day = 1
    g = make_guard(limits, tmp_path)
    o = buy()
    g.check_order(o, account(), {"005930": 70_000}, when=TRADING_TIME)
    g.record_submitted(o, when=TRADING_TIME)
    assert g.state.order_count == 1
    tomorrow = TRADING_TIME + timedelta(days=1)
    g.check_order(buy(), account(), {"005930": 70_000}, when=tomorrow)
    assert g.state.order_count == 0, "날짜가 바뀌면 당일 카운터가 리셋돼야 한다"


def test_missing_reference_price_blocked(limits, tmp_path):
    g = make_guard(limits, tmp_path)
    o = Order(symbol="005930", side=Side.BUY, qty=1, order_type=OrderType.MARKET)
    d = g.check_order(o, account(), {}, when=TRADING_TIME)
    assert not d.allowed and d.rule == "price"
