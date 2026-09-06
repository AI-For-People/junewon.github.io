"""브로커 어댑터. 엔진은 Broker 인터페이스만 알고 증권사 구현은 모른다."""

from .base import Broker
from .paper import PaperBroker

__all__ = ["Broker", "PaperBroker", "get_broker"]


def get_broker(settings, journal=None):
    """설정 모드에 맞는 브로커를 만든다. KIS 구현은 지연 임포트(requests 의존)."""
    if settings.mode == "paper":
        return PaperBroker(settings, journal=journal)
    if settings.mode in ("kis-paper", "kis-live"):
        from .kis import KisBroker

        return KisBroker(settings, journal=journal)
    if settings.mode == "meritz":
        from .meritz import MeritzBroker

        return MeritzBroker(settings, journal=journal)
    raise ValueError(f"알 수 없는 모드: {settings.mode}")
