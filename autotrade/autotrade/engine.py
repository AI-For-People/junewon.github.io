"""매매 엔진 — 전략 신호를 주문으로 바꾸는 단 하나의 경로.

흐름
----
계좌/시세 조회 → 손실한도 감시 → 전략 신호 → 수량·가격 확정
→ RiskGuard 심사 → (드라이런이면 여기서 멈춤) → 주문 전송 → 감사 로그

이 경로 밖에서 주문이 나가는 코드는 존재해선 안 된다.
"""

from __future__ import annotations

import signal as signal_module
import time
from dataclasses import dataclass, field
from datetime import datetime

from .brokers.base import Broker
from .clock import MarketCalendar, now_kst
from .config import Settings
from .errors import BrokerError
from .journal import Journal
from .models import (
    AccountSnapshot,
    Order,
    OrderStatus,
    OrderType,
    Side,
    Signal,
)
from .pricing import round_to_tick, shares_for_notional
from .risk import RiskGuard
from .strategies.base import MarketView, Strategy


@dataclass
class CycleResult:
    ts: datetime
    orders: list[Order] = field(default_factory=list)
    blocked: list[tuple[Order, str]] = field(default_factory=list)
    equity: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def submitted(self) -> list[Order]:
        return [o for o in self.orders if o.status is OrderStatus.SUBMITTED or o.status is OrderStatus.FILLED]


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        broker: Broker,
        strategy: Strategy,
        risk: RiskGuard,
        journal: Journal,
        *,
        calendar: MarketCalendar | None = None,
        use_market_order: bool = False,
        limit_mode: str = "aggressive",
    ):
        self.settings = settings
        self.broker = broker
        self.strategy = strategy
        self.risk = risk
        self.journal = journal
        self.calendar = calendar or MarketCalendar()
        self.use_market_order = use_market_order
        self.limit_mode = limit_mode
        self._stop = False

    # ------------------------------------------------------------ 라이프사이클
    def request_stop(self, *_args) -> None:
        self._stop = True

    def install_signal_handlers(self) -> None:
        """Ctrl+C / SIGTERM 에서 주문 도중에 죽지 않고 사이클 끝에서 멈춘다."""
        for sig in (signal_module.SIGINT, signal_module.SIGTERM):
            try:
                signal_module.signal(sig, self.request_stop)
            except (ValueError, OSError):
                pass  # 메인 스레드가 아니면 무시

    # ------------------------------------------------------------ 주문 생성
    def _build_order(self, sig: Signal, price: float, account: AccountSnapshot) -> Order | None:
        if price <= 0:
            return None

        if self.use_market_order and self.broker.supports_market_order:
            order_type, limit = OrderType.MARKET, None
        else:
            raw = sig.limit_price or price
            order_type, limit = OrderType.LIMIT, float(round_to_tick(raw, side=sig.side))

        ref_price = limit or price
        if sig.qty is not None:
            qty = int(sig.qty)
        else:
            budget = min(
                sig.target_notional or self.settings.risk.max_order_notional,
                self.settings.risk.max_order_notional,
            )
            if sig.side is Side.BUY:
                budget = min(budget, account.cash)
            qty = shares_for_notional(budget, ref_price)

        if sig.side is Side.SELL:
            held = account.positions[sig.symbol].qty if sig.symbol in account.positions else 0
            qty = min(qty, held)

        if qty <= 0:
            return None

        return Order(
            symbol=sig.symbol,
            side=sig.side,
            qty=qty,
            order_type=order_type,
            price=limit,
            reason=sig.reason,
        )

    # ------------------------------------------------------------ 1 사이클
    def run_once(self, symbols: list[str], *, when: datetime | None = None) -> CycleResult:
        when = when or now_kst()
        result = CycleResult(ts=when)

        account = self.broker.get_account()
        prices: dict[str, float] = {}
        views: list[MarketView] = []

        need = max(self.strategy.warmup_bars + 2, 5)
        for symbol in symbols:
            try:
                candles = self.broker.get_candles(symbol, need)
            except BrokerError as exc:
                self.journal.write("data.error", symbol=symbol, error=str(exc))
                result.notes.append(f"{symbol} 시세 조회 실패: {exc}")
                continue
            if not candles:
                result.notes.append(f"{symbol} 캔들 없음")
                continue
            price = candles[-1].close
            prices[symbol] = price
            views.append(
                MarketView(
                    symbol=symbol,
                    candles=candles,
                    price=price,
                    position=account.positions.get(symbol),
                    account=account,
                )
            )

        result.equity = account.equity(prices)

        # 손실 한도 감시 — 넘으면 킬 스위치가 올라가고 이후 주문은 전부 차단된다.
        equity_check = self.risk.observe_equity(result.equity, when=when)
        if not equity_check.allowed:
            self.journal.write(
                "risk.kill_switch", rule=equity_check.rule, reason=equity_check.reason,
                equity=result.equity,
            )
            result.notes.append(equity_check.reason)
            return result

        for view in views:
            if len(view.candles) < self.strategy.warmup_bars:
                continue
            for sig in self.strategy.on_data(view):
                order = self._build_order(sig, view.price, account)
                if order is None:
                    self.journal.write(
                        "signal.skipped", symbol=sig.symbol, side=sig.side.value,
                        reason=sig.reason, note="수량 0 (예산·보유 부족)",
                    )
                    continue

                decision = self.risk.check_order(order, account, prices, when=when)
                if not decision.allowed:
                    order.status = OrderStatus.BLOCKED
                    order.note = f"[{decision.rule}] {decision.reason}"
                    result.blocked.append((order, decision.reason))
                    self.journal.write(
                        "order.blocked", rule=decision.rule, reason=decision.reason,
                        order=order.to_dict(),
                    )
                    continue

                if self.settings.dry_run:
                    order.status = OrderStatus.DRY_RUN
                    order.note = "DRY_RUN — 실제 주문을 전송하지 않음"
                    result.orders.append(order)
                    self.journal.write("order.dry_run", order=order.to_dict())
                    continue

                try:
                    order = self.broker.submit_order(order)
                except BrokerError as exc:
                    order.status = OrderStatus.REJECTED
                    order.note = str(exc)
                    self.journal.write("order.error", order=order.to_dict(), error=str(exc))
                    result.orders.append(order)
                    continue

                if order.status in (OrderStatus.SUBMITTED, OrderStatus.FILLED, OrderStatus.PARTIAL):
                    self.risk.record_submitted(order, when=when)
                    # 같은 사이클 내 후속 신호가 이미 쓴 현금을 다시 쓰지 않도록 반영
                    account = self._apply_local_effect(account, order)
                result.orders.append(order)
                self.journal.write("order.result", order=order.to_dict())

        return result

    @staticmethod
    def _apply_local_effect(account: AccountSnapshot, order: Order) -> AccountSnapshot:
        """브로커 재조회 없이 사이클 내 예산 소진을 근사 반영한다."""
        price = order.avg_fill_price or order.price or 0.0
        if order.side is Side.BUY:
            account.cash = max(0.0, account.cash - price * order.qty)
        return account

    # ------------------------------------------------------------ 루프
    def run_forever(self, symbols: list[str], *, max_cycles: int | None = None) -> int:
        self.install_signal_handlers()
        self.journal.write(
            "engine.start",
            mode=self.settings.mode,
            dry_run=self.settings.dry_run,
            broker=self.broker.name,
            strategy=self.strategy.describe(),
            symbols=symbols,
            risk=self.settings.describe()["risk"],
        )
        cycles = 0
        try:
            while not self._stop and (max_cycles is None or cycles < max_cycles):
                cycles += 1
                state = self.calendar.session_state()
                if self.settings.trade_only_in_session and state not in ("regular", "closing_auction"):
                    self.journal.write("engine.idle", session_state=state)
                else:
                    try:
                        result = self.run_once(symbols)
                        self.journal.write(
                            "engine.cycle",
                            equity=result.equity,
                            orders=len(result.orders),
                            blocked=len(result.blocked),
                            notes=result.notes,
                        )
                    except Exception as exc:  # 루프는 어떤 예외에도 죽지 않는다
                        self.journal.write("engine.error", error=repr(exc))
                if self._stop:
                    break
                time.sleep(self.settings.poll_interval_sec)
        finally:
            self.journal.write("engine.stop", cycles=cycles)
            self.broker.close()
        return cycles
