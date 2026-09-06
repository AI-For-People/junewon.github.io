"""프레임워크 공통 예외."""

from __future__ import annotations


class AutoTradeError(Exception):
    """모든 자동매매 예외의 최상위 타입."""


class ConfigError(AutoTradeError):
    """설정값이 없거나 안전하지 않을 때."""


class BrokerError(AutoTradeError):
    """증권사 API 호출 실패."""

    def __init__(self, message: str, *, code: str | None = None, payload: object | None = None):
        super().__init__(message)
        self.code = code
        self.payload = payload


class AuthError(BrokerError):
    """토큰 발급/인증 실패."""


class RiskViolation(AutoTradeError):
    """리스크 한도 위반으로 주문이 차단됨."""

    def __init__(self, reason: str, *, rule: str = "unknown"):
        super().__init__(reason)
        self.rule = rule


class KillSwitchEngaged(RiskViolation):
    """킬 스위치가 올라가 모든 거래가 중단된 상태."""

    def __init__(self, reason: str):
        super().__init__(reason, rule="kill_switch")
