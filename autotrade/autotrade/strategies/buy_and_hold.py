"""매수 후 보유 — 백테스트 비교 기준선(benchmark).

어떤 전략이든 이것보다 못하면(수수료·세금·리스크 감안) 자동화할 이유가 없다.
"""

from __future__ import annotations

from ..models import Side, Signal
from .base import MarketView, Strategy


class BuyAndHoldStrategy(Strategy):
    name = "buy_and_hold"
    warmup_bars = 1

    def __init__(self, target_notional: float = 200_000.0):
        self.target_notional = target_notional

    def on_data(self, view: MarketView) -> list[Signal]:
        if view.held_qty > 0:
            return []
        return [
            Signal(
                symbol=view.symbol,
                side=Side.BUY,
                reason="최초 진입 후 보유",
                target_notional=self.target_notional,
            )
        ]
