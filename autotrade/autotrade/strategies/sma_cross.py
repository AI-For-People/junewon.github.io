"""단순 이동평균 교차 전략(롱 온리).

교육·검증용 기준 전략이다. 이 전략이 돈을 벌어준다는 보장은 전혀 없다.
목적은 "엔진 배관이 제대로 도는지"를 확인할 수 있는 결정적(deterministic) 신호원이다.
"""

from __future__ import annotations

from ..models import Side, Signal
from .base import MarketView, Strategy, sma


class SmaCrossStrategy(Strategy):
    name = "sma_cross"

    def __init__(self, fast: int = 5, slow: int = 20, target_notional: float = 200_000.0):
        if fast >= slow:
            raise ValueError(f"fast({fast})는 slow({slow})보다 작아야 합니다.")
        self.fast = fast
        self.slow = slow
        self.target_notional = target_notional
        self.warmup_bars = slow + 1

    def on_data(self, view: MarketView) -> list[Signal]:
        closes = view.closes
        if len(closes) < self.slow + 1:
            return []

        fast_now, slow_now = sma(closes, self.fast), sma(closes, self.slow)
        prev = closes[:-1]
        fast_prev, slow_prev = sma(prev, self.fast), sma(prev, self.slow)
        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return []

        golden = fast_prev <= slow_prev and fast_now > slow_now
        dead = fast_prev >= slow_prev and fast_now < slow_now

        if golden and view.held_qty == 0:
            return [
                Signal(
                    symbol=view.symbol,
                    side=Side.BUY,
                    reason=f"골든크로스 SMA{self.fast}({fast_now:,.0f}) > SMA{self.slow}({slow_now:,.0f})",
                    target_notional=self.target_notional,
                )
            ]
        if dead and view.held_qty > 0:
            return [
                Signal(
                    symbol=view.symbol,
                    side=Side.SELL,
                    reason=f"데드크로스 SMA{self.fast}({fast_now:,.0f}) < SMA{self.slow}({slow_now:,.0f})",
                    qty=view.held_qty,
                )
            ]
        return []

    def describe(self) -> dict:
        return {
            "name": self.name,
            "fast": self.fast,
            "slow": self.slow,
            "target_notional": self.target_notional,
            "warmup_bars": self.warmup_bars,
        }
