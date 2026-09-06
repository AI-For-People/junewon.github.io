from .base import MarketView, Strategy
from .sma_cross import SmaCrossStrategy
from .buy_and_hold import BuyAndHoldStrategy

REGISTRY = {
    "sma_cross": SmaCrossStrategy,
    "buy_and_hold": BuyAndHoldStrategy,
}

__all__ = ["MarketView", "Strategy", "SmaCrossStrategy", "BuyAndHoldStrategy", "REGISTRY"]
