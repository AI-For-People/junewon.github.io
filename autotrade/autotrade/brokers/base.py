"""브로커 공통 인터페이스."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AccountSnapshot, Candle, Order, Quote


class Broker(ABC):
    """증권사 어댑터가 구현해야 하는 최소 계약.

    구현체는 절대로 스스로 리스크 판단을 하지 않는다. 시키는 대로 보내고,
    실패는 BrokerError로 올린다. 판단은 RiskGuard와 Engine의 몫이다.
    """

    name: str = "base"
    supports_market_order: bool = True

    # ---------------------------------------------------------------- 시세
    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """현재가 조회."""

    @abstractmethod
    def get_candles(self, symbol: str, count: int = 100) -> list[Candle]:
        """최근 일봉을 오래된 것부터 정렬해 반환."""

    # ---------------------------------------------------------------- 계좌
    @abstractmethod
    def get_account(self) -> AccountSnapshot:
        """예수금과 보유 종목."""

    # ---------------------------------------------------------------- 주문
    @abstractmethod
    def submit_order(self, order: Order) -> Order:
        """주문 전송. 전송 결과를 반영한 order를 돌려준다."""

    def cancel_order(self, order: Order) -> Order:
        raise NotImplementedError(f"{self.name} 는 주문 취소를 지원하지 않습니다.")

    def close(self) -> None:
        """리소스 정리(HTTP 세션 등)."""

    def __enter__(self) -> "Broker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
