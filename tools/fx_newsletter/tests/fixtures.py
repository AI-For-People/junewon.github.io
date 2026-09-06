"""테스트와 오프라인 미리보기가 공유하는 합성 데이터."""

from __future__ import annotations

import datetime as dt
import math


def synthetic_payload(end: dt.date, days: int = 320, seed: float = 0.0) -> dict:
    """Frankfurter의 EUR 기준 시계열 응답을 흉내 낸다.

    실제 응답처럼 주말은 비워 두고, 통화마다 다른 주기의 사인파를 겹쳐
    상승·하락·횡보가 모두 나오도록 만든다.
    """
    rates: dict[str, dict[str, float]] = {}
    day = end - dt.timedelta(days=days)
    step = 0
    while day <= end:
        if day.weekday() < 5:  # 주말 고시 없음
            t = step + seed
            rates[day.isoformat()] = {
                "KRW": 1450 + 60 * math.sin(t / 47) + 8 * math.sin(t / 5),
                "USD": 1.08 + 0.05 * math.sin(t / 61),
                "JPY": 168 + 9 * math.sin(t / 39 + 1.2),
                "CNY": 7.8 + 0.25 * math.sin(t / 55 + 2.4),
            }
            step += 1
        day += dt.timedelta(days=1)
    return {
        "amount": 1.0,
        "base": "EUR",
        "start_date": (end - dt.timedelta(days=days)).isoformat(),
        "end_date": end.isoformat(),
        "rates": rates,
    }
