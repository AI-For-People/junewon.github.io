"""환율 시계열에서 카드에 실을 지표를 계산한다.

ECB 고시는 영업일 기준이라 관측치가 주 5회다. 따라서 '지난주 대비' 같은
비교는 관측치 개수가 아니라 달력 날짜로 되짚어 찾는다.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from .fetch import Series

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class Change:
    """기준 시점 대비 변화량."""

    ref_date: dt.date
    ref_value: float
    delta: float
    pct: float


@dataclass(frozen=True)
class PairSnapshot:
    """카드 한 장을 채우는 데 필요한 통화쌍 지표 묶음."""

    code: str
    display_name: str
    label: str
    latest_date: dt.date
    latest: float
    week: Change | None
    month: Change | None
    sma20: float | None
    sma60: float | None
    volatility: float | None
    rsi14: float | None
    bollinger_pct: float | None
    high52: float | None
    low52: float | None
    trend: str
    spark: tuple[float, ...] = field(default=())

    @property
    def direction(self) -> str:
        """주간 등락 방향. 카드 색상과 문구를 고르는 데 쓴다."""
        if self.week is None or abs(self.week.pct) < 0.05:
            return "flat"
        return "up" if self.week.delta > 0 else "down"


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values)


def _stdev(values) -> float | None:
    values = list(values)
    if len(values) < 2:
        return None
    avg = _mean(values)
    return math.sqrt(sum((v - avg) ** 2 for v in values) / (len(values) - 1))


def sma(values: tuple[float, ...], window: int) -> float | None:
    if len(values) < window:
        return None
    return _mean(values[-window:])


def annualized_volatility(values: tuple[float, ...], window: int = 20) -> float | None:
    """일간 로그수익률의 표준편차를 연율화한 값(%)."""
    if len(values) < window + 1:
        return None
    recent = values[-(window + 1):]
    returns = [
        math.log(recent[i] / recent[i - 1])
        for i in range(1, len(recent))
        if recent[i] > 0 and recent[i - 1] > 0
    ]
    deviation = _stdev(returns)
    if deviation is None:
        return None
    return deviation * math.sqrt(TRADING_DAYS_PER_YEAR) * 100


def rsi(values: tuple[float, ...], window: int = 14) -> float | None:
    """Wilder 방식 RSI. 과매수/과매도 판단에 쓴다."""
    if len(values) < window + 1:
        return None
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = _mean(gains[:window])
    avg_loss = _mean(losses[:window])
    for i in range(window, len(deltas)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bollinger_pct(values: tuple[float, ...], window: int = 20, mult: float = 2.0) -> float | None:
    """볼린저 밴드 내 위치(%B). 0이면 하단, 100이면 상단."""
    if len(values) < window:
        return None
    recent = values[-window:]
    middle = _mean(recent)
    deviation = _stdev(recent)
    if not deviation:
        return None
    lower = middle - mult * deviation
    upper = middle + mult * deviation
    return (values[-1] - lower) / (upper - lower) * 100


def _change(series: Series, days: int) -> Change | None:
    found = series.value_on_or_before(series.latest_date - dt.timedelta(days=days))
    if found is None:
        return None
    ref_date, ref_value = found
    if ref_date == series.latest_date or ref_value == 0:
        return None
    delta = series.latest - ref_value
    return Change(
        ref_date=ref_date,
        ref_value=ref_value,
        delta=delta,
        pct=delta / ref_value * 100,
    )


def _trend_label(latest: float, sma20: float | None, sma60: float | None) -> str:
    """이동평균 배열로 추세를 한 단어로 요약한다."""
    if sma20 is None or sma60 is None:
        return "판단 보류"
    if latest > sma20 > sma60:
        return "상승 추세"
    if latest < sma20 < sma60:
        return "하락 추세"
    if sma20 > sma60:
        return "상승 속 조정"
    return "하락 속 반등"


def build_snapshot(series: Series, spark_points: int = 60) -> PairSnapshot:
    """시계열 하나를 카드용 스냅샷으로 압축한다."""
    values = series.values
    window52 = values[-TRADING_DAYS_PER_YEAR:] if len(values) >= 2 else values
    sma20 = sma(values, 20)
    sma60 = sma(values, 60)
    return PairSnapshot(
        code=series.pair.code,
        display_name=series.pair.display_name,
        label=series.pair.label,
        latest_date=series.latest_date,
        latest=series.latest,
        week=_change(series, 7),
        month=_change(series, 30),
        sma20=sma20,
        sma60=sma60,
        volatility=annualized_volatility(values),
        rsi14=rsi(values),
        bollinger_pct=bollinger_pct(values),
        high52=max(window52),
        low52=min(window52),
        trend=_trend_label(series.latest, sma20, sma60),
        spark=values[-spark_points:],
    )


def build_snapshots(series_map: dict[str, Series]) -> list[PairSnapshot]:
    """설정에 정의된 통화 순서를 유지한 채 스냅샷 목록을 만든다."""
    from .config import PAIRS

    return [build_snapshot(series_map[p.code]) for p in PAIRS if p.code in series_map]
