"""KRX 호가단위(틱) 처리.

지정가 주문 가격이 호가단위에 맞지 않으면 증권사가 주문을 거부한다.
자동매매에서 가장 흔한 "왜 주문이 안 나가지?" 원인 중 하나다.

⚠️ 호가단위 체계는 개정된다(현행 표는 2023-01-25 시행 기준). ETF/ETN 등
   일부 종목군은 별도 체계를 쓰므로, 운영 전 KRX 업무규정으로 확인할 것.
"""

from __future__ import annotations

import math

from .models import Side

# (가격 상한 미만, 호가단위) — 유가증권·코스닥 공통
TICK_TABLE: tuple[tuple[float, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
)

# ETF·ETN 등은 전 구간 5원 단위(별도 체계)
ETF_TICK = 5


def tick_size(price: float, *, etf: bool = False) -> int:
    if etf:
        return ETF_TICK
    for upper, tick in TICK_TABLE:
        if price < upper:
            return tick
    return 1_000


def round_to_tick(
    price: float,
    *,
    side: Side | None = None,
    etf: bool = False,
    mode: str = "aggressive",
) -> int:
    """호가단위에 맞춰 지정가를 보정한다.

    mode="aggressive" (기본, 체결 우선)
        매수는 올림, 매도는 내림 → 현재가 기준으로 즉시 체결 가능한(marketable) 가격.
        전략이 "지금 진입"이라고 말했을 때 주문이 미체결로 떠 있으면 포지션 상태와
        전략 가정이 어긋나므로, 최대 1틱을 손해 보더라도 체결시키는 쪽을 기본으로 둔다.
    mode="passive" (가격 우선)
        매수는 내림, 매도는 올림 → 유리한 가격이지만 체결 안 될 수 있다.
        미체결 주문을 관리(정정·취소)할 준비가 된 경우에만 쓸 것.
    mode="nearest"
        단순 반올림.
    """
    tick = tick_size(price, etf=etf)
    if mode == "nearest" or side is None:
        value = round(price / tick) * tick
    elif mode == "aggressive":
        value = (
            math.ceil(price / tick) * tick if side is Side.BUY else math.floor(price / tick) * tick
        )
    elif mode == "passive":
        value = (
            math.floor(price / tick) * tick if side is Side.BUY else math.ceil(price / tick) * tick
        )
    else:
        raise ValueError(f"알 수 없는 mode: {mode!r}")
    return max(tick, int(value))


def is_valid_tick(price: float, *, etf: bool = False) -> bool:
    tick = tick_size(price, etf=etf)
    return abs(price - round(price)) < 1e-9 and int(price) % tick == 0


def shares_for_notional(notional: float, price: float) -> int:
    """국내 주식은 소수점 매수가 안 되므로 내림 정수."""
    if price <= 0:
        return 0
    return int(notional // price)
