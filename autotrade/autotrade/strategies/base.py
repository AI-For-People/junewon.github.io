"""전략 인터페이스.

전략은 '의도(Signal)'만 만든다. 수량 확정, 리스크 심사, 실제 발주는 엔진과
RiskGuard의 몫이다. 이렇게 분리해야 전략 버그가 계좌를 태우지 못한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import AccountSnapshot, Candle, Position, Signal


@dataclass(frozen=True)
class MarketView:
    """전략이 한 종목에 대해 보는 세계."""

    symbol: str
    candles: list[Candle]      # 오래된 것 → 최신 순
    price: float               # 현재가(또는 마지막 종가)
    position: Position | None
    account: AccountSnapshot

    @property
    def closes(self) -> list[float]:
        return [c.close for c in self.candles]

    @property
    def held_qty(self) -> int:
        return self.position.qty if self.position else 0


class Strategy(ABC):
    name: str = "base"

    #: 지표 계산에 필요한 최소 봉 수. 엔진이 이만큼 못 채우면 신호를 요청하지 않는다.
    warmup_bars: int = 1

    @abstractmethod
    def on_data(self, view: MarketView) -> list[Signal]:
        """신호 목록을 반환. 아무것도 하지 않으려면 빈 리스트."""

    def describe(self) -> dict:
        return {"name": self.name, "warmup_bars": self.warmup_bars}


def sma(values: list[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window
