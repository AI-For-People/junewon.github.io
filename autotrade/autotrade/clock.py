"""KRX 거래시간/휴장일 판정.

주의: 휴장일은 매년 KRX가 공지한다(임시휴장·선거일·연말 휴장 포함).
data/krx_holidays.txt 를 반드시 공식 공지로 갱신할 것. 이 파일이 틀리면
휴장일에 주문을 쏘거나, 정상 개장일에 매매를 건너뛴다.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# 정규장
REGULAR_OPEN = time(9, 0)
REGULAR_CLOSE = time(15, 30)
# 장 마감 동시호가(단일가) 구간 — 지정가 체결 성격이 달라지므로 별도로 본다.
CLOSING_AUCTION_START = time(15, 20)
# 장 시작 동시호가
OPENING_AUCTION_START = time(8, 30)

DEFAULT_HOLIDAY_FILE = Path(__file__).resolve().parent.parent / "data" / "krx_holidays.txt"


def now_kst() -> datetime:
    return datetime.now(KST)


def to_kst(dt: datetime) -> datetime:
    """naive datetime은 KST로 간주한다."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def load_holidays(path: Path | str | None = None) -> set[date]:
    """YYYY-MM-DD 한 줄씩. '#' 이후는 주석."""
    p = Path(path) if path else DEFAULT_HOLIDAY_FILE
    if not p.exists():
        return set()
    days: set[date] = set()
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            days.add(date.fromisoformat(line))
        except ValueError:
            continue
    return days


class MarketCalendar:
    """KRX 국내 주식시장 달력."""

    def __init__(self, holidays: set[date] | None = None, holiday_file: Path | str | None = None):
        self.holidays = holidays if holidays is not None else load_holidays(holiday_file)

    def is_business_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def is_regular_session(self, dt: datetime | None = None) -> bool:
        """정규장(09:00~15:30) 여부."""
        dt = to_kst(dt or now_kst())
        if not self.is_business_day(dt.date()):
            return False
        return REGULAR_OPEN <= dt.time() < REGULAR_CLOSE

    def is_closing_auction(self, dt: datetime | None = None) -> bool:
        dt = to_kst(dt or now_kst())
        if not self.is_business_day(dt.date()):
            return False
        return CLOSING_AUCTION_START <= dt.time() < REGULAR_CLOSE

    def session_state(self, dt: datetime | None = None) -> str:
        """'closed' | 'pre_auction' | 'regular' | 'closing_auction' | 'after'."""
        dt = to_kst(dt or now_kst())
        if not self.is_business_day(dt.date()):
            return "closed"
        t = dt.time()
        if t < OPENING_AUCTION_START:
            return "closed"
        if t < REGULAR_OPEN:
            return "pre_auction"
        if t < CLOSING_AUCTION_START:
            return "regular"
        if t < REGULAR_CLOSE:
            return "closing_auction"
        return "after"

    def holidays_cover_year(self, year: int) -> bool:
        """해당 연도 휴장일 데이터가 채워져 있는지(= 갱신됐는지) 대략 확인."""
        return any(d.year == year for d in self.holidays)
