from datetime import datetime, timedelta

from autotrade.brokers.paper import PaperBroker, PriceFeed
from autotrade.models import Candle, Order, OrderStatus, OrderType, Side


def candles(prices):
    return [
        Candle(datetime(2026, 1, 1) + timedelta(days=i), p, p, p, p, 100)
        for i, p in enumerate(prices)
    ]


def make(settings, prices=(70_000,)):
    feed = PriceFeed()
    feed.set_candles("005930", candles(list(prices)))
    feed.set_cursor("005930", 0)
    return PaperBroker(settings, feed=feed), feed


def test_buy_fills_and_charges_fee(settings):
    broker, _ = make(settings)
    order = Order("005930", Side.BUY, 10, OrderType.LIMIT, 70_000)
    filled = broker.submit_order(order)
    assert filled.status is OrderStatus.FILLED
    assert broker.positions["005930"].qty == 10
    expected_fee = 700_000 * settings.costs.fee_rate
    assert abs(filled.fee - expected_fee) < 1e-6
    assert abs(broker.cash - (10_000_000 - 700_000 - expected_fee)) < 1e-6


def test_sell_charges_fee_and_tax(settings):
    broker, _ = make(settings)
    broker.submit_order(Order("005930", Side.BUY, 10, OrderType.LIMIT, 70_000))
    sold = broker.submit_order(Order("005930", Side.SELL, 10, OrderType.LIMIT, 70_000))
    assert sold.status is OrderStatus.FILLED
    assert sold.tax > 0 and sold.fee > 0
    assert "005930" not in broker.positions
    # 같은 가격에 사고 팔면 비용만큼 손해여야 한다
    assert broker.cash < 10_000_000
    assert broker.realized_pnl < 0


def test_limit_buy_above_market_does_not_overpay(settings):
    broker, _ = make(settings)
    order = broker.submit_order(Order("005930", Side.BUY, 1, OrderType.LIMIT, 75_000))
    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == 70_000, "지정가보다 유리한 시장가에 체결돼야 한다"


def test_limit_buy_below_market_does_not_fill(settings):
    broker, _ = make(settings)
    order = broker.submit_order(Order("005930", Side.BUY, 1, OrderType.LIMIT, 60_000))
    assert order.status is OrderStatus.SUBMITTED
    assert order.filled_qty == 0
    assert broker.cash == 10_000_000


def test_insufficient_cash_rejected(settings):
    settings.paper_starting_cash = 10_000
    broker, _ = make(settings)
    order = broker.submit_order(Order("005930", Side.BUY, 10, OrderType.LIMIT, 70_000))
    assert order.status is OrderStatus.REJECTED
    assert "현금 부족" in order.note


def test_selling_more_than_held_rejected(settings):
    broker, _ = make(settings)
    order = broker.submit_order(Order("005930", Side.SELL, 5, OrderType.LIMIT, 70_000))
    assert order.status is OrderStatus.REJECTED
    assert "보유 수량 부족" in order.note


def test_market_order_pays_slippage(settings):
    settings.costs.slippage_bps = 100  # 1%
    broker, _ = make(settings)
    order = broker.submit_order(Order("005930", Side.BUY, 1, OrderType.MARKET))
    assert order.avg_fill_price == 70_000 * 1.01


def test_feed_advances_through_candles(settings):
    broker, feed = make(settings, prices=(70_000, 71_000, 72_000))
    assert broker.get_quote("005930").price == 70_000
    assert feed.advance("005930") is True
    assert broker.get_quote("005930").price == 71_000
    feed.advance("005930")
    assert feed.advance("005930") is False, "데이터가 끝나면 False"


def test_account_snapshot_is_a_copy(settings):
    broker, _ = make(settings)
    broker.submit_order(Order("005930", Side.BUY, 10, OrderType.LIMIT, 70_000))
    snap = broker.get_account()
    snap.positions["005930"].qty = 9999
    assert broker.positions["005930"].qty == 10, "스냅샷 수정이 브로커 상태를 바꾸면 안 된다"


def test_average_price_updates_on_second_buy(settings):
    broker, feed = make(settings, prices=(70_000, 80_000))
    broker.submit_order(Order("005930", Side.BUY, 1, OrderType.LIMIT, 70_000))
    feed.advance("005930")
    broker.submit_order(Order("005930", Side.BUY, 1, OrderType.LIMIT, 80_000))
    assert broker.positions["005930"].avg_price == 75_000
