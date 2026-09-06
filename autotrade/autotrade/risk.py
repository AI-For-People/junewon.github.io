"""리스크 계층 — 모든 주문이 반드시 통과해야 하는 관문.

전략은 "사고 싶다"는 의도만 만든다. 실제로 주문을 내보낼지는 여기서만 결정한다.
전략 버그, 시세 이상, 무한 루프로 인한 폭주 주문을 마지막에 막는 곳이므로
이 모듈은 보수적으로(막는 쪽으로) 동작한다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .clock import MarketCalendar, now_kst, to_kst
from .config import RiskLimits
from .models import AccountSnapshot, Order, Side


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    rule: str = "ok"
    reason: str = ""

    @staticmethod
    def ok() -> "RiskDecision":
        return RiskDecision(True)

    @staticmethod
    def block(rule: str, reason: str) -> "RiskDecision":
        return RiskDecision(False, rule, reason)


@dataclass
class DayState:
    """당일 누적 상태. 날짜가 바뀌면 리셋된다."""

    day: date
    order_count: int = 0
    realized_pnl: float = 0.0
    start_equity: float | None = None
    tripped: bool = False
    trip_reason: str = ""
    recent_orders: deque = field(default_factory=lambda: deque(maxlen=200))
    last_signal_at: dict[tuple[str, str], datetime] = field(default_factory=dict)


class RiskGuard:
    def __init__(
        self,
        limits: RiskLimits,
        *,
        calendar: MarketCalendar | None = None,
        kill_switch_path: Path | str | None = None,
        trade_only_in_session: bool = True,
    ):
        self.limits = limits
        self.calendar = calendar or MarketCalendar()
        self.kill_switch_path = Path(kill_switch_path) if kill_switch_path else None
        self.trade_only_in_session = trade_only_in_session
        self.state = DayState(day=now_kst().date())

    # ------------------------------------------------------------ 상태 관리
    def _roll_day(self, when: datetime) -> None:
        today = to_kst(when).date()
        if today != self.state.day:
            self.state = DayState(day=today)

    def kill_switch_engaged(self) -> bool:
        """파일 하나 만들면 즉시 모든 주문이 멈춘다(운영 중 수동 정지 수단)."""
        return bool(self.kill_switch_path and self.kill_switch_path.exists())

    def engage_kill_switch(self, reason: str) -> None:
        self.state.tripped = True
        self.state.trip_reason = reason
        if self.kill_switch_path:
            self.kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
            self.kill_switch_path.write_text(
                f"{datetime.now().isoformat()} {reason}\n", encoding="utf-8"
            )

    def release_kill_switch(self) -> None:
        self.state.tripped = False
        self.state.trip_reason = ""
        if self.kill_switch_path and self.kill_switch_path.exists():
            self.kill_switch_path.unlink()

    # -------------------------------------------------------- 손실 한도 감시
    def observe_equity(self, equity: float, *, when: datetime | None = None) -> RiskDecision:
        """주기적으로 평가금액을 넣어주면 일중 손실 한도를 감시한다."""
        when = when or now_kst()
        self._roll_day(when)
        if self.state.start_equity is None:
            self.state.start_equity = equity
            return RiskDecision.ok()
        drawdown = self.state.start_equity - equity
        if drawdown >= self.limits.daily_loss_limit:
            reason = (
                f"일중 손실 한도 초과: 시작 {self.state.start_equity:,.0f}원 → "
                f"현재 {equity:,.0f}원 (손실 {drawdown:,.0f}원 ≥ 한도 "
                f"{self.limits.daily_loss_limit:,.0f}원)"
            )
            self.engage_kill_switch(reason)
            return RiskDecision.block("daily_loss_limit", reason)
        return RiskDecision.ok()

    # ------------------------------------------------------------ 주문 심사
    def check_order(
        self,
        order: Order,
        account: AccountSnapshot,
        prices: dict[str, float],
        *,
        when: datetime | None = None,
    ) -> RiskDecision:
        when = to_kst(when or now_kst())
        self._roll_day(when)
        lim = self.limits

        if self.state.tripped:
            return RiskDecision.block("kill_switch", f"당일 거래 중단됨: {self.state.trip_reason}")
        if self.kill_switch_engaged():
            return RiskDecision.block(
                "kill_switch", f"킬 스위치 파일 존재: {self.kill_switch_path}"
            )

        # 1) 거래 시간
        if self.trade_only_in_session and not self.calendar.is_regular_session(when):
            return RiskDecision.block(
                "market_hours",
                f"정규장 시간이 아님 (상태={self.calendar.session_state(when)}, {when:%Y-%m-%d %H:%M} KST)",
            )

        # 2) 화이트리스트 — 비어 있으면 전부 차단(기본값이 안전한 쪽)
        if not lim.symbol_whitelist:
            return RiskDecision.block(
                "whitelist", "거래 허용 종목이 지정되지 않았습니다(AUTOTRADE_SYMBOL_WHITELIST)."
            )
        if order.symbol not in lim.symbol_whitelist:
            return RiskDecision.block(
                "whitelist", f"{order.symbol} 는 허용 종목이 아닙니다."
            )

        # 3) 기본 유효성
        if order.qty <= 0:
            return RiskDecision.block("qty", "수량이 0 이하입니다.")
        ref_price = order.price or prices.get(order.symbol)
        if not ref_price or ref_price <= 0:
            return RiskDecision.block("price", f"{order.symbol} 참조가격을 알 수 없습니다.")

        notional = ref_price * order.qty
        position = account.positions.get(order.symbol)
        held = position.qty if position else 0

        # 4) 매도는 보유 수량 범위 내에서만 (개인 공매도 방지)
        if order.side is Side.SELL and not lim.allow_short:
            if order.qty > held:
                return RiskDecision.block(
                    "short_sell",
                    f"보유 {held}주를 초과한 매도({order.qty}주)는 차단됩니다.",
                )

        # 5) 주문 금액 상한
        if notional > lim.max_order_notional:
            return RiskDecision.block(
                "max_order_notional",
                f"주문금액 {notional:,.0f}원 > 한도 {lim.max_order_notional:,.0f}원",
            )

        # 6) 매수 시 현금·종목별·전체 익스포저 상한
        if order.side is Side.BUY:
            if notional > account.cash:
                return RiskDecision.block(
                    "insufficient_cash",
                    f"주문금액 {notional:,.0f}원 > 가용현금 {account.cash:,.0f}원",
                )
            projected_position = (held * ref_price) + notional
            if projected_position > lim.max_position_notional:
                return RiskDecision.block(
                    "max_position_notional",
                    f"{order.symbol} 예상 보유액 {projected_position:,.0f}원 > 한도 "
                    f"{lim.max_position_notional:,.0f}원",
                )
            projected_gross = account.exposure(prices) + notional
            if projected_gross > lim.max_gross_exposure:
                return RiskDecision.block(
                    "max_gross_exposure",
                    f"예상 총 익스포저 {projected_gross:,.0f}원 > 한도 "
                    f"{lim.max_gross_exposure:,.0f}원",
                )

        # 7) 폭주 방지 — 일일/분당 주문 건수
        if self.state.order_count >= lim.max_orders_per_day:
            reason = f"일일 주문 한도 {lim.max_orders_per_day}건 도달"
            self.engage_kill_switch(reason)
            return RiskDecision.block("max_orders_per_day", reason)

        cutoff = when - timedelta(minutes=1)
        recent = [t for t in self.state.recent_orders if t > cutoff]
        if len(recent) >= lim.max_orders_per_minute:
            return RiskDecision.block(
                "max_orders_per_minute",
                f"최근 1분간 주문 {len(recent)}건 ≥ 한도 {lim.max_orders_per_minute}건",
            )

        # 8) 동일 종목·방향 연타 방지(전략 진동/중복 신호 차단)
        key = (order.symbol, order.side.value)
        last = self.state.last_signal_at.get(key)
        if last is not None:
            elapsed = (when - last).total_seconds()
            if elapsed < lim.min_seconds_between_same_signal:
                return RiskDecision.block(
                    "signal_cooldown",
                    f"{order.symbol} {order.side.value} 재발주까지 "
                    f"{lim.min_seconds_between_same_signal - elapsed:.0f}초 남음",
                )

        return RiskDecision.ok()

    def record_submitted(self, order: Order, *, when: datetime | None = None) -> None:
        """실제로 전송된 주문만 카운트한다."""
        when = to_kst(when or now_kst())
        self._roll_day(when)
        self.state.order_count += 1
        self.state.recent_orders.append(when)
        self.state.last_signal_at[(order.symbol, order.side.value)] = when

    def record_realized_pnl(self, pnl: float) -> None:
        self.state.realized_pnl += pnl
