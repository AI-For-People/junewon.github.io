"""오프라인 시뮬레이션 브로커.

네트워크·증권사 계정 없이 엔진 전체 경로(전략→리스크→주문→체결→계좌)를
그대로 돌려보기 위한 구현. 백테스트와 페이퍼 트레이딩 양쪽에서 쓴다.

한계(반드시 인지할 것):
- 호가 잔량/부분체결/거부를 모사하지 않는다. 지정가는 종가 기준으로 즉시 체결 판정한다.
- 실제 시장충격은 슬리피지 상수로 근사할 뿐이다. 실전 성과는 여기보다 나쁘다.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from ..config import Settings
from ..models import (
    AccountSnapshot,
    Candle,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Side,
)
from .base import Broker


class PriceFeed:
    """시뮬레이션 가격 공급원."""

    def __init__(self, candles: dict[str, list[Candle]] | None = None, seed: int = 7):
        self.candles = candles or {}
        self._rng = random.Random(seed)
        self._last: dict[str, float] = {}
        self._cursor: dict[str, int] = {}

    def set_candles(self, symbol: str, candles: list[Candle]) -> None:
        self.candles[symbol] = list(candles)

    def price(self, symbol: str) -> float:
        """캔들이 있으면 커서 위치의 종가, 없으면 재현 가능한 랜덤워크."""
        series = self.candles.get(symbol)
        if series:
            idx = min(self._cursor.get(symbol, len(series) - 1), len(series) - 1)
            return series[idx].close
        last = self._last.get(symbol)
        if last is None:
            last = 50_000.0
        drift = self._rng.gauss(0, 0.004)
        last = max(100.0, round(last * (1 + drift), -1))
        self._last[symbol] = last
        return last

    def advance(self, symbol: str) -> bool:
        """캔들 커서를 한 칸 진행. 더 없으면 False."""
        series = self.candles.get(symbol)
        if not series:
            return True
        nxt = self._cursor.get(symbol, 0) + 1
        if nxt >= len(series):
            return False
        self._cursor[symbol] = nxt
        return True

    def set_cursor(self, symbol: str, idx: int) -> None:
        self._cursor[symbol] = idx

    def history(self, symbol: str, count: int) -> list[Candle]:
        series = self.candles.get(symbol)
        if not series:
            return []
        end = min(self._cursor.get(symbol, len(series) - 1) + 1, len(series))
        return series[max(0, end - count) : end]


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, settings: Settings, *, feed: PriceFeed | None = None, journal=None):
        self.settings = settings
        self.costs = settings.costs
        self.feed = feed or PriceFeed()
        self.journal = journal
        self.cash = settings.paper_starting_cash
        self.positions: dict[str, Position] = {}
        self.orders: list[Order] = []
        self.realized_pnl = 0.0
        self.total_fees = 0.0
        self.total_tax = 0.0
        self._now = datetime.now()

    # ---------------------------------------------------------------- 시세
    def get_quote(self, symbol: str) -> Quote:
        p = self.feed.price(symbol)
        spread = max(1.0, round(p * 0.0005))
        return Quote(symbol=symbol, price=p, bid=p - spread, ask=p + spread)

    def get_candles(self, symbol: str, count: int = 100) -> list[Candle]:
        hist = self.feed.history(symbol, count)
        if hist:
            return hist
        # 캔들 데이터가 없으면 랜덤워크로 합성해 준다(형식 테스트용).
        out: list[Candle] = []
        base = datetime.now() - timedelta(days=count)
        for i in range(count):
            p = self.feed.price(symbol)
            out.append(Candle(base + timedelta(days=i), p, p * 1.01, p * 0.99, p, 1000))
        return out

    # ---------------------------------------------------------------- 계좌
    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            cash=self.cash,
            positions={s: Position(p.symbol, p.qty, p.avg_price) for s, p in self.positions.items()},
        )

    def equity(self) -> float:
        prices = {s: self.feed.price(s) for s in self.positions}
        return self.cash + sum(
            p.market_value(prices.get(s, p.avg_price)) for s, p in self.positions.items()
        )

    # ---------------------------------------------------------------- 주문
    def _fill_price(self, order: Order, market_price: float) -> float | None:
        """체결가 결정. 지정가는 체결 불가면 None."""
        slip = market_price * (self.costs.slippage_bps / 10_000)
        if order.order_type is OrderType.MARKET:
            return market_price + slip if order.side is Side.BUY else market_price - slip
        assert order.price is not None
        if order.side is Side.BUY:
            return min(order.price, market_price) if market_price <= order.price else None
        return max(order.price, market_price) if market_price >= order.price else None

    def submit_order(self, order: Order) -> Order:
        market_price = self.feed.price(order.symbol)
        fill = self._fill_price(order, market_price)
        order.updated_at = datetime.now()

        if fill is None:
            order.status = OrderStatus.SUBMITTED
            order.note = f"지정가 미체결(시장가 {market_price:,.0f})"
            self.orders.append(order)
            return order

        notional = fill * order.qty
        if order.side is Side.BUY:
            fee = self.costs.buy_cost(notional)
            if notional + fee > self.cash:
                order.status = OrderStatus.REJECTED
                order.note = f"현금 부족: 필요 {notional + fee:,.0f} > 보유 {self.cash:,.0f}"
                self.orders.append(order)
                return order
            self.cash -= notional + fee
            pos = self.positions.setdefault(order.symbol, Position(order.symbol))
            pos.apply_fill(Side.BUY, order.qty, fill)
            order.fee = fee
            self.total_fees += fee
        else:
            pos = self.positions.get(order.symbol)
            if not pos or pos.qty < order.qty:
                order.status = OrderStatus.REJECTED
                order.note = f"보유 수량 부족: {pos.qty if pos else 0} < {order.qty}"
                self.orders.append(order)
                return order
            fee, tax = self.costs.sell_cost(notional)
            realized = pos.apply_fill(Side.SELL, order.qty, fill)
            self.cash += notional - fee - tax
            self.realized_pnl += realized - fee - tax
            self.total_fees += fee
            self.total_tax += tax
            order.fee, order.tax = fee, tax
            if pos.qty == 0:
                del self.positions[order.symbol]

        order.status = OrderStatus.FILLED
        order.filled_qty = order.qty
        order.avg_fill_price = fill
        order.broker_order_id = f"PAPER-{len(self.orders) + 1:06d}"
        self.orders.append(order)
        return order

    def cancel_order(self, order: Order) -> Order:
        if order.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIAL):
            order.status = OrderStatus.CANCELED
        return order
