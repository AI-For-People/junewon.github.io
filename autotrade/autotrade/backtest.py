"""일봉 백테스트.

목적은 '수익률 자랑'이 아니라 **전략을 실계좌에 붙이기 전에 걸러내는 것**이다.
그래서 수수료·세금·슬리피지를 반드시 반영하고, 매수후보유(benchmark)와 함께 보여준다.

구조적 한계(결과를 믿기 전에 반드시 읽을 것)
--------------------------------------------
- 일봉 종가 체결 가정이다. 실제로는 그 가격에 못 산다.
- 생존 편향: 상장폐지·거래정지 종목이 데이터에 없으면 성과가 부풀려진다.
- 수정주가를 쓰지 않으면 배당락·액면분할이 가짜 신호를 만든다.
- 파라미터를 과거에 맞춰 고르면(과최적화) 미래에는 그대로 무너진다.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import CostModel, Settings
from .models import Candle, Order, OrderType, Position, Side
from .pricing import round_to_tick, shares_for_notional
from .strategies.base import MarketView, Strategy
from .models import AccountSnapshot


def load_candles_csv(path: Path | str, *, date_format: str = "%Y-%m-%d") -> list[Candle]:
    """date,open,high,low,close,volume 헤더를 가진 CSV를 읽는다."""
    rows: list[Candle] = []
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("date"):
                continue
            rows.append(
                Candle(
                    ts=datetime.strptime(row["date"].strip(), date_format),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0),
                )
            )
    rows.sort(key=lambda c: c.ts)
    return rows


@dataclass
class Trade:
    ts: datetime
    side: Side
    qty: int
    price: float
    cost: float
    reason: str
    realized: float = 0.0


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    start: datetime
    end: datetime
    starting_cash: float
    final_equity: float
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    total_fees: float = 0.0
    total_tax: float = 0.0
    benchmark_equity: float = 0.0

    @property
    def total_return(self) -> float:
        return self.final_equity / self.starting_cash - 1 if self.starting_cash else 0.0

    @property
    def benchmark_return(self) -> float:
        return self.benchmark_equity / self.starting_cash - 1 if self.starting_cash else 0.0

    @property
    def years(self) -> float:
        return max((self.end - self.start).days / 365.25, 1e-9)

    @property
    def cagr(self) -> float:
        if self.starting_cash <= 0 or self.final_equity <= 0:
            return 0.0
        return (self.final_equity / self.starting_cash) ** (1 / self.years) - 1

    @property
    def max_drawdown(self) -> float:
        peak, mdd = -math.inf, 0.0
        for _, value in self.equity_curve:
            peak = max(peak, value)
            if peak > 0:
                mdd = min(mdd, value / peak - 1)
        return mdd

    @property
    def sharpe(self) -> float:
        """무위험수익률 0 가정, 일간 기준 연율화. 표본이 적으면 무의미하다."""
        curve = [v for _, v in self.equity_curve]
        rets = [
            curve[i] / curve[i - 1] - 1
            for i in range(1, len(curve))
            if curve[i - 1] > 0
        ]
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        return 0.0 if sd == 0 else (mean / sd) * math.sqrt(252)

    @property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.side is Side.SELL]

    @property
    def win_rate(self) -> float:
        closed = self.closed_trades
        if not closed:
            return 0.0
        return sum(1 for t in closed if t.realized > 0) / len(closed)

    def summary(self) -> str:
        lines = [
            f"종목            : {self.symbol}",
            f"전략            : {self.strategy}",
            f"기간            : {self.start:%Y-%m-%d} ~ {self.end:%Y-%m-%d} ({self.years:.2f}년, "
            f"{len(self.equity_curve)}봉)",
            f"초기자금        : {self.starting_cash:>15,.0f} 원",
            f"최종평가        : {self.final_equity:>15,.0f} 원",
            f"총수익률        : {self.total_return:>15.2%}",
            f"CAGR            : {self.cagr:>15.2%}",
            f"최대낙폭(MDD)   : {self.max_drawdown:>15.2%}",
            f"샤프(연율)      : {self.sharpe:>15.2f}",
            f"매매횟수        : {len(self.trades):>15,d} (청산 {len(self.closed_trades):,d})",
            f"승률            : {self.win_rate:>15.2%}",
            f"수수료+세금     : {self.total_fees + self.total_tax:>15,.0f} 원",
            "-" * 46,
            f"매수후보유 수익률: {self.benchmark_return:>14.2%}  ← 이보다 못하면 자동화할 이유가 없다",
        ]
        return "\n".join(lines)


def run_backtest(
    candles: list[Candle],
    strategy: Strategy,
    *,
    symbol: str = "TEST",
    starting_cash: float = 10_000_000.0,
    costs: CostModel | None = None,
    settings: Settings | None = None,
) -> BacktestResult:
    if len(candles) < 2:
        raise ValueError("백테스트에는 최소 2개 이상의 봉이 필요합니다.")
    costs = costs or (settings.costs if settings else CostModel())
    max_order_notional = settings.risk.max_order_notional if settings else 300_000.0

    cash = starting_cash
    position = Position(symbol)
    result = BacktestResult(
        symbol=symbol,
        strategy=strategy.name,
        start=candles[0].ts,
        end=candles[-1].ts,
        starting_cash=starting_cash,
        final_equity=starting_cash,
    )

    slip = costs.slippage_bps / 10_000

    for i in range(1, len(candles) + 1):
        window = candles[:i]
        bar = window[-1]
        account = AccountSnapshot(
            cash=cash,
            positions={symbol: position} if position.qty else {},
        )
        view = MarketView(
            symbol=symbol,
            candles=window,
            price=bar.close,
            position=position if position.qty else None,
            account=account,
        )

        if len(window) >= strategy.warmup_bars:
            for sig in strategy.on_data(view):
                limit = float(round_to_tick(sig.limit_price or bar.close, side=sig.side, mode="aggressive"))
                if sig.side is Side.BUY:
                    budget = min(sig.target_notional or max_order_notional, max_order_notional, cash)
                    fill = limit * (1 + slip)
                    qty = shares_for_notional(budget, fill)
                    if qty <= 0:
                        continue
                    notional = fill * qty
                    fee = costs.buy_cost(notional)
                    if notional + fee > cash:
                        qty = shares_for_notional(cash / (1 + costs.fee_rate), fill)
                        if qty <= 0:
                            continue
                        notional = fill * qty
                        fee = costs.buy_cost(notional)
                    cash -= notional + fee
                    position.apply_fill(Side.BUY, qty, fill)
                    result.total_fees += fee
                    result.trades.append(
                        Trade(bar.ts, Side.BUY, qty, fill, fee, sig.reason)
                    )
                else:
                    qty = min(int(sig.qty or position.qty), position.qty)
                    if qty <= 0:
                        continue
                    fill = limit * (1 - slip)
                    notional = fill * qty
                    fee, tax = costs.sell_cost(notional)
                    realized = position.apply_fill(Side.SELL, qty, fill)
                    cash += notional - fee - tax
                    result.total_fees += fee
                    result.total_tax += tax
                    result.trades.append(
                        Trade(bar.ts, Side.SELL, qty, fill, fee + tax, sig.reason, realized - fee - tax)
                    )

        equity = cash + position.market_value(bar.close)
        result.equity_curve.append((bar.ts, equity))

    result.final_equity = result.equity_curve[-1][1]

    # 벤치마크: 첫 봉 종가에 전액 매수 후 보유(수수료 반영)
    first, last = candles[0].close, candles[-1].close
    bench_qty = shares_for_notional(starting_cash / (1 + costs.fee_rate), first)
    bench_cost = first * bench_qty
    bench_cash = starting_cash - bench_cost - costs.buy_cost(bench_cost)
    result.benchmark_equity = bench_cash + bench_qty * last

    return result
