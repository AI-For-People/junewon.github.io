from datetime import date, datetime

from autotrade.clock import KST, MarketCalendar, load_holidays


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def test_weekend_is_closed():
    cal = MarketCalendar(holidays=set())
    assert cal.session_state(at(2026, 9, 5, 10)) == "closed"   # 토
    assert cal.session_state(at(2026, 9, 6, 10)) == "closed"   # 일


def test_session_states_through_a_trading_day():
    cal = MarketCalendar(holidays=set())
    assert cal.session_state(at(2026, 9, 7, 8, 0)) == "closed"
    assert cal.session_state(at(2026, 9, 7, 8, 45)) == "pre_auction"
    assert cal.session_state(at(2026, 9, 7, 10, 0)) == "regular"
    assert cal.session_state(at(2026, 9, 7, 15, 25)) == "closing_auction"
    assert cal.session_state(at(2026, 9, 7, 16, 0)) == "after"


def test_regular_session_boundaries_are_inclusive_open_exclusive_close():
    cal = MarketCalendar(holidays=set())
    assert cal.is_regular_session(at(2026, 9, 7, 9, 0))
    assert not cal.is_regular_session(at(2026, 9, 7, 8, 59))
    assert not cal.is_regular_session(at(2026, 9, 7, 15, 30))


def test_holiday_blocks_the_day():
    cal = MarketCalendar(holidays={date(2026, 9, 7)})
    assert not cal.is_regular_session(at(2026, 9, 7, 10))
    assert cal.session_state(at(2026, 9, 7, 10)) == "closed"


def test_naive_datetime_is_treated_as_kst():
    cal = MarketCalendar(holidays=set())
    assert cal.is_regular_session(datetime(2026, 9, 7, 10, 0))


def test_holiday_file_parsing(tmp_path):
    f = tmp_path / "h.txt"
    f.write_text("# 주석\n2026-01-01  # 신정\n\n엉터리줄\n2026-03-02\n", encoding="utf-8")
    assert load_holidays(f) == {date(2026, 1, 1), date(2026, 3, 2)}


def test_missing_holiday_file_is_empty(tmp_path):
    assert load_holidays(tmp_path / "nope.txt") == set()


def test_shipped_holiday_file_loads():
    cal = MarketCalendar()
    assert cal.holidays_cover_year(2026)
    assert date(2026, 1, 1) in cal.holidays
