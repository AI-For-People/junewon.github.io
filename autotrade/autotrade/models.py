"""거래 도메인 모델."""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

_counter = itertools.count(1)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_client_order_id(prefix: str = "AT") -> str:
    """중복 발주 추적용 클라이언트 주문 ID."""
    return f"{prefix}-{utcnow():%Y%m%d%H%M%S}-{next(_counter):04d}-{uuid.uuid4().hex[:6]}"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(str, Enum):
    LIMIT = "LIMIT"    # 지정가
    MARKET = "MARKET"  # 시장가


class OrderStatus(str, Enum):
    NEW = "NEW"              # 생성만 됨(미전송)
    BLOCKED = "BLOCKED"      # 리스크 계층이 차단
    DRY_RUN = "DRY_RUN"      # 드라이런으로 전송 생략
    SUBMITTED = "SUBMITTED"  # 증권사 접수
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"

    @property
    def is_terminal(self) -> bool:
        return self in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.BLOCKED,
            OrderStatus.DRY_RUN,
        )


@dataclass(frozen=True)
class Quote:
    """단일 시점 호가/현재가."""

    symbol: str
    price: float
    ts: datetime = field(default_factory=utcnow)
    bid: float | None = None
    ask: float | None = None

    @property
    def mid(self) -> float:
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2
        return self.price


@dataclass(frozen=True)
class Candle:
    """OHLCV 봉. ts는 봉의 시작 시각."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def as_row(self) -> list[str]:
        return [
            self.ts.strftime("%Y-%m-%d"),
            f"{self.open:g}",
            f"{self.high:g}",
            f"{self.low:g}",
            f"{self.close:g}",
            f"{self.volume:g}",
        ]


@dataclass
class Order:
    symbol: str
    side: Side
    qty: int
    order_type: OrderType = OrderType.LIMIT
    price: float | None = None            # 시장가면 None
    client_order_id: str = field(default_factory=new_client_order_id)
    broker_order_id: str | None = None
    status: OrderStatus = OrderStatus.NEW
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    fee: float = 0.0
    tax: float = 0.0
    reason: str = ""                      # 전략이 남긴 발주 근거
    note: str = ""                        # 차단/거절 사유 등
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"주문 수량은 1 이상이어야 합니다: {self.qty}")
        if self.order_type is OrderType.LIMIT and not self.price:
            raise ValueError("지정가 주문에는 price가 필요합니다.")

    @property
    def notional(self) -> float:
        """주문 명목금액. 시장가는 price가 없으므로 호출부가 참조가를 넣어준다."""
        return (self.price or 0.0) * self.qty

    @property
    def remaining_qty(self) -> int:
        return max(0, self.qty - self.filled_qty)

    def to_dict(self) -> dict:
        return {
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "qty": self.qty,
            "order_type": self.order_type.value,
            "price": self.price,
            "status": self.status.value,
            "filled_qty": self.filled_qty,
            "avg_fill_price": self.avg_fill_price,
            "fee": round(self.fee, 2),
            "tax": round(self.tax, 2),
            "reason": self.reason,
            "note": self.note,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Position:
    symbol: str
    qty: int = 0
    avg_price: float = 0.0

    def market_value(self, price: float) -> float:
        return self.qty * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.avg_price) * self.qty

    def apply_fill(self, side: Side, qty: int, price: float) -> float:
        """체결을 반영하고 실현손익을 돌려준다(매수는 0)."""
        realized = 0.0
        if side is Side.BUY:
            total_cost = self.avg_price * self.qty + price * qty
            self.qty += qty
            self.avg_price = total_cost / self.qty if self.qty else 0.0
        else:
            sell_qty = min(qty, self.qty)
            realized = (price - self.avg_price) * sell_qty
            self.qty -= sell_qty
            if self.qty == 0:
                self.avg_price = 0.0
        return realized


@dataclass
class AccountSnapshot:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    ts: datetime = field(default_factory=utcnow)

    def equity(self, prices: dict[str, float]) -> float:
        """현금 + 보유종목 평가액. 가격을 모르는 종목은 평균단가로 대체 평가한다."""
        value = self.cash
        for symbol, pos in self.positions.items():
            value += pos.market_value(prices.get(symbol, pos.avg_price))
        return value

    def exposure(self, prices: dict[str, float]) -> float:
        return sum(
            pos.market_value(prices.get(sym, pos.avg_price))
            for sym, pos in self.positions.items()
        )


@dataclass(frozen=True)
class Signal:
    """전략이 만들어내는 매매 의도. 실제 주문 여부는 리스크 계층이 결정한다."""

    symbol: str
    side: Side
    reason: str
    qty: int | None = None              # None이면 엔진이 target_notional로 산출
    target_notional: float | None = None
    limit_price: float | None = None
