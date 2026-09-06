"""Frankfurter(ECB 기준환율) API에서 원화 환율 시계열을 가져온다.

ECB는 EUR을 기준으로 환율을 고시하므로 EUR 기준 시계열을 한 번만 받아
KRW 대비 환율로 환산한다. 요청이 한 번뿐이라 API 부담이 작고, 모든 통화가
동일한 고시일 집합을 공유하므로 날짜 정렬 문제도 생기지 않는다.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

import requests

from .config import PAIRS, Pair

# Frankfurter는 도메인을 .dev로 옮겼으나 구 도메인도 살아 있다. 순서대로 시도한다.
API_HOSTS = (
    "https://api.frankfurter.dev/v1",
    "https://api.frankfurter.app",
)

BASE = "EUR"
TIMEOUT = 30
MAX_ATTEMPTS = 4


class FetchError(RuntimeError):
    """모든 API 호스트에서 시계열을 받지 못했을 때."""


@dataclass(frozen=True)
class Series:
    """한 통화쌍의 날짜 오름차순 시계열."""

    pair: Pair
    dates: tuple[dt.date, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.dates) != len(self.values):
            raise ValueError("dates와 values 길이가 다릅니다")

    @property
    def latest_date(self) -> dt.date:
        return self.dates[-1]

    @property
    def latest(self) -> float:
        return self.values[-1]

    def value_on_or_before(self, target: dt.date) -> tuple[dt.date, float] | None:
        """target 이하의 가장 최근 고시값. 주말·공휴일 공백을 흡수한다."""
        for i in range(len(self.dates) - 1, -1, -1):
            if self.dates[i] <= target:
                return self.dates[i], self.values[i]
        return None


def _get_json(url: str) -> dict:
    """지수 백오프로 재시도하며 JSON을 받아온다."""
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = requests.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # 네트워크·HTTP·JSON 오류를 모두 재시도 대상으로 본다
            last_error = exc
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** (attempt + 1))
    raise FetchError(f"{url} 요청 실패: {last_error}")


def fetch_raw(start: dt.date, end: dt.date, session_get=_get_json) -> dict:
    """EUR 기준 시계열 원본을 받아온다. 호스트를 순서대로 시도한다."""
    # 기준통화(EUR)는 symbols에 넣으면 API가 거부하므로 반드시 빼둔다.
    symbols = ",".join(sorted(({p.code for p in PAIRS} | {"KRW"}) - {BASE}))
    errors: list[str] = []
    for host in API_HOSTS:
        url = f"{host}/{start.isoformat()}..{end.isoformat()}?base={BASE}&symbols={symbols}"
        try:
            payload = session_get(url)
        except Exception as exc:
            errors.append(f"{host}: {exc}")
            continue
        if payload.get("rates"):
            return payload
        errors.append(f"{host}: 응답에 rates가 비어 있음")
    raise FetchError("환율 시계열을 가져오지 못했습니다 -> " + " | ".join(errors))


def to_series(payload: dict) -> dict[str, Series]:
    """EUR 기준 원본을 통화쌍별 KRW 환산 시계열로 바꾼다."""
    rates: dict[str, dict[str, float]] = payload.get("rates") or {}
    if not rates:
        raise FetchError("응답에 rates가 없습니다")

    result: dict[str, Series] = {}
    for pair in PAIRS:
        dates: list[dt.date] = []
        values: list[float] = []
        for day in sorted(rates):
            row = rates[day]
            krw_per_eur = row.get("KRW")
            # EUR은 기준 통화라 응답에 자기 자신이 없다.
            unit_per_eur = 1.0 if pair.code == BASE else row.get(pair.code)
            if not krw_per_eur or not unit_per_eur:
                continue  # 일부 통화만 빠진 날은 건너뛴다
            dates.append(dt.date.fromisoformat(day))
            values.append(pair.unit * krw_per_eur / unit_per_eur)
        if len(values) < 2:
            raise FetchError(f"{pair.display_name} 시계열이 너무 짧습니다 ({len(values)}건)")
        result[pair.code] = Series(pair=pair, dates=tuple(dates), values=tuple(values))
    return result


def load_series(today: dt.date, history_days: int, session_get=_get_json) -> dict[str, Series]:
    """오늘 기준 history_days 만큼의 통화쌍 시계열을 반환한다."""
    payload = fetch_raw(today - dt.timedelta(days=history_days), today, session_get=session_get)
    return to_series(payload)
