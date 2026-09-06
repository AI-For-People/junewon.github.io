"""엔진 테스트 — 특히 '드라이런이면 절대 주문이 나가지 않는다'를 못 박는다."""

from datetime import timedelta

import pytest

from autotrade.brokers.paper import PaperBroker, PriceFeed
from autotrade.clock import MarketCalendar
from autotrade.engine import TradingEngine
from autotrade.journal import Journal
from autotrade.models import Candle, OrderStatus, Side, Signal
from autotrade.risk import RiskGuard
from autotrade.strategies.base import MarketView, Strategy

from .conftest import TRADING_TIME


class AlwaysBuy(Strategy):
    name = "always_buy"
    warmup_bars = 1

    def __init__(self, notional=200_000):
        self.notional = notional

    def on_data(self, view: MarketView):
        return [Signal(view.symbol, Side.BUY, "테스트", target_notional=self.notional)]


class SellEverything(Strategy):
    name = "sell_all"
    warmup_bars = 1

    def on_data(self, view: MarketView):
        if view.held_qty <= 0:
            return []
        return [Signal(view.symbol, Side.SELL, "청산", qty=view.held_qty)]


def flat_candles(price=70_000, n=30):
    from datetime import datetime

    return [
        Candle(datetime(2026, 1, 1) + timedelta(days=i), price, price, price, price, 1000)
        for i in range(n)
    ]


@pytest.fixture
def rig(settings, tmp_path):
    feed = PriceFeed()
    feed.set_candles("005930", flat_candles())
    feed.set_cursor("005930", 29)
    broker = PaperBroker(settings, feed=feed)
    journal = Journal(tmp_path / "j.jsonl", echo=False)
    risk = RiskGuard(settings.risk, calendar=MarketCalendar(holidays=set()),
                     kill_switch_path=tmp_path / "KS")
    return settings, broker, journal, risk


def build(rig, strategy, **kw):
    settings, broker, journal, risk = rig
    return TradingEngine(settings, broker, strategy, risk, journal,
                         calendar=MarketCalendar(holidays=set()), **kw), broker


def test_dry_run_never_submits(rig):
    settings, broker, journal, risk = rig
    settings.dry_run = True
    engine, broker = build(rig, AlwaysBuy())
    result = engine.run_once(["005930"], when=TRADING_TIME)
    assert len(result.orders) == 1
    assert result.orders[0].status is OrderStatus.DRY_RUN
    assert broker.orders == [], "드라이런에서는 브로커에 주문이 도달하면 안 된다"
    assert broker.cash == settings.paper_starting_cash


def test_live_mode_submits_and_fills(rig):
    engine, broker = build(rig, AlwaysBuy())
    result = engine.run_once(["005930"], when=TRADING_TIME)
    assert result.orders[0].status is OrderStatus.FILLED
    assert broker.positions["005930"].qty == 2  # 200,000 / 70,000 = 2주
    assert broker.cash < 10_000_000


def test_blocked_order_never_reaches_broker(rig):
    settings, broker, journal, risk = rig
    settings.risk.symbol_whitelist = ()          # 전부 차단
    engine, broker = build(rig, AlwaysBuy())
    result = engine.run_once(["005930"], when=TRADING_TIME)
    assert result.orders == []
    assert len(result.blocked) == 1
    assert broker.orders == []


def test_sizing_respects_max_order_notional(rig):
    settings, *_ = rig
    settings.risk.max_order_notional = 100_000
    engine, broker = build(rig, AlwaysBuy(notional=5_000_000))
    result = engine.run_once(["005930"], when=TRADING_TIME)
    assert result.orders[0].qty == 1  # 100,000 한도 → 70,000짜리 1주


def test_sell_is_capped_at_holdings(rig):
    settings, broker, journal, risk = rig
    engine, broker = build(rig, AlwaysBuy())
    engine.run_once(["005930"], when=TRADING_TIME)
    held = broker.positions["005930"].qty

    engine.strategy = SellEverything()
    result = engine.run_once(["005930"], when=TRADING_TIME + timedelta(minutes=5))
    assert result.orders[0].qty == held
    assert "005930" not in broker.positions


def test_zero_quantity_signal_is_skipped(rig):
    engine, broker = build(rig, AlwaysBuy(notional=100))  # 1주도 못 산다
    result = engine.run_once(["005930"], when=TRADING_TIME)
    assert result.orders == [] and result.blocked == []
    assert broker.orders == []


def test_kill_switch_stops_the_cycle(rig):
    settings, broker, journal, risk = rig
    engine, broker = build(rig, AlwaysBuy())
    # 장 시작 평가액을 1,010만으로 잡아두면 현재 1,000만은 10만원 손실이다.
    risk.observe_equity(10_100_000, when=TRADING_TIME)
    settings.risk.daily_loss_limit = 50_000
    result = engine.run_once(["005930"], when=TRADING_TIME + timedelta(minutes=1))
    assert result.orders == []
    assert any("손실 한도" in n for n in result.notes)


def test_journal_records_every_order(rig, tmp_path):
    engine, broker = build(rig, AlwaysBuy())
    engine.run_once(["005930"], when=TRADING_TIME)
    events = [e["event"] for e in engine.journal.read_all()]
    assert "order.result" in events


def test_limit_price_is_on_a_valid_tick(rig):
    from autotrade.pricing import is_valid_tick

    engine, broker = build(rig, AlwaysBuy())
    result = engine.run_once(["005930"], when=TRADING_TIME)
    assert is_valid_tick(result.orders[0].price)


def test_run_forever_stops_after_max_cycles(rig):
    settings, *_ = rig
    settings.poll_interval_sec = 0
    settings.trade_only_in_session = False
    engine, broker = build(rig, AlwaysBuy())
    assert engine.run_forever(["005930"], max_cycles=3) == 3
