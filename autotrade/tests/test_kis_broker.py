"""KIS 어댑터 — 네트워크 없이 요청 형식과 응답 파싱을 검증한다."""

import json

import pytest

from autotrade.brokers.kis import KisBroker, TokenStore
from autotrade.config import Settings
from autotrade.errors import AuthError, BrokerError
from autotrade.models import Order, OrderStatus, OrderType, Side

from .fakes import FakeResponse, FakeSession

TOKEN_OK = FakeResponse({"access_token": "tok-123", "expires_in": 3600})
HASH_OK = FakeResponse({"HASH": "hash-abc"})


@pytest.fixture
def kis_settings(tmp_path):
    return Settings(
        mode="kis-paper",
        dry_run=False,
        kis_app_key="APPKEY",
        kis_app_secret="APPSECRET",
        kis_account_no="12345678",
        kis_account_pd="01",
        base_dir=tmp_path,
        journal_path=tmp_path / "j.jsonl",
        token_cache_path=tmp_path / "token.json",
        kill_switch_path=tmp_path / "KS",
    )


def broker_with(kis_settings, routes):
    session = FakeSession({"/oauth2/tokenP": TOKEN_OK, "/uapi/hashkey": HASH_OK, **routes})
    return KisBroker(kis_settings, session=session), session


def test_paper_mode_targets_virtual_host(kis_settings):
    assert "openapivts" in kis_settings.kis_base_url


def test_token_is_cached_on_disk_and_reused(kis_settings):
    broker, session = broker_with(kis_settings, {})
    assert broker.access_token() == "tok-123"
    assert broker.access_token() == "tok-123"
    token_calls = [c for c in session.calls if "tokenP" in c["url"]]
    assert len(token_calls) == 1, "토큰은 한 번만 발급해야 한다(발급 횟수 제한)"

    # 새 인스턴스도 파일 캐시를 재사용해야 한다
    broker2, session2 = broker_with(kis_settings, {})
    assert broker2.access_token() == "tok-123"
    assert not [c for c in session2.calls if "tokenP" in c["url"]]


def test_token_cache_file_is_owner_only(kis_settings):
    broker, _ = broker_with(kis_settings, {})
    broker.access_token()
    mode = kis_settings.token_cache_path.stat().st_mode & 0o777
    assert mode == 0o600, f"토큰 파일 권한이 {oct(mode)} 입니다"


def test_token_cache_ignored_for_different_credentials(kis_settings, tmp_path):
    broker, _ = broker_with(kis_settings, {})
    broker.access_token()
    other = Settings(**{**kis_settings.__dict__, "kis_app_key": "OTHERKEY"})
    broker2, session2 = broker_with(other, {})
    broker2.access_token()
    assert [c for c in session2.calls if "tokenP" in c["url"]], "앱키가 바뀌면 재발급해야 한다"


def test_token_failure_raises_auth_error(kis_settings):
    session = FakeSession({"/oauth2/tokenP": FakeResponse({"error": "invalid"}, 403)})
    with pytest.raises(AuthError):
        KisBroker(kis_settings, session=session).access_token()


def test_get_quote_parses_price(kis_settings):
    broker, session = broker_with(
        kis_settings,
        {"inquire-price": FakeResponse({"rt_cd": "0", "output": {"stck_prpr": "71,300"}})},
    )
    quote = broker.get_quote("005930")
    assert quote.price == 71_300
    call = session.call_for("inquire-price")
    assert call["params"]["FID_INPUT_ISCD"] == "005930"
    assert call["headers"]["tr_id"] == "FHKST01010100"


def test_business_error_code_raises(kis_settings):
    broker, _ = broker_with(
        kis_settings,
        {"inquire-price": FakeResponse({"rt_cd": "1", "msg_cd": "EGW00123",
                                        "msg1": "잘못된 종목코드"})},
    )
    with pytest.raises(BrokerError, match="잘못된 종목코드"):
        broker.get_quote("999999")


def test_candles_are_sorted_oldest_first(kis_settings):
    payload = {
        "rt_cd": "0",
        "output2": [
            {"stck_bsop_date": "20260904", "stck_clpr": "72000", "stck_oprc": "71000",
             "stck_hgpr": "72500", "stck_lwpr": "70900", "acml_vol": "1000"},
            {"stck_bsop_date": "20260903", "stck_clpr": "71000", "stck_oprc": "70500",
             "stck_hgpr": "71200", "stck_lwpr": "70000", "acml_vol": "900"},
        ],
    }
    broker, _ = broker_with(kis_settings, {"inquire-daily-itemchartprice": FakeResponse(payload)})
    candles = broker.get_candles("005930", 10)
    assert [c.close for c in candles] == [71_000, 72_000]


def test_balance_parses_positions_and_cash(kis_settings):
    payload = {
        "rt_cd": "0",
        "output1": [
            {"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "70,000"},
            {"pdno": "000660", "hldg_qty": "0", "pchs_avg_pric": "0"},
        ],
        "output2": [{"dnca_tot_amt": "5,000,000", "ord_psbl_cash": "4,800,000"}],
    }
    broker, _ = broker_with(kis_settings, {"inquire-balance": FakeResponse(payload)})
    account = broker.get_account()
    assert account.cash == 4_800_000
    assert set(account.positions) == {"005930"}, "수량 0 종목은 제외돼야 한다"
    assert account.positions["005930"].avg_price == 70_000


def test_buy_order_body_and_trid(kis_settings):
    broker, session = broker_with(
        kis_settings,
        {"order-cash": FakeResponse({"rt_cd": "0", "msg1": "정상처리",
                                     "output": {"ODNO": "0000123456"}})},
    )
    order = broker.submit_order(Order("005930", Side.BUY, 3, OrderType.LIMIT, 71_300))
    assert order.status is OrderStatus.SUBMITTED
    assert order.broker_order_id == "0000123456"

    call = session.call_for("order-cash")
    body = json.loads(call["data"])
    assert call["headers"]["tr_id"] == "VTTC0802U", "모의투자 매수 TR_ID"
    assert call["headers"]["hashkey"] == "hash-abc"
    assert body["ORD_DVSN"] == "00" and body["ORD_QTY"] == "3" and body["ORD_UNPR"] == "71300"
    assert body["CANO"] == "12345678"


def test_sell_uses_sell_trid(kis_settings):
    broker, session = broker_with(
        kis_settings, {"order-cash": FakeResponse({"rt_cd": "0", "output": {"ODNO": "1"}})}
    )
    broker.submit_order(Order("005930", Side.SELL, 1, OrderType.LIMIT, 71_300))
    assert session.call_for("order-cash")["headers"]["tr_id"] == "VTTC0801U"


def test_market_order_sends_zero_price(kis_settings):
    broker, session = broker_with(
        kis_settings, {"order-cash": FakeResponse({"rt_cd": "0", "output": {"ODNO": "1"}})}
    )
    broker.submit_order(Order("005930", Side.BUY, 1, OrderType.MARKET))
    body = json.loads(session.call_for("order-cash")["data"])
    assert body["ORD_DVSN"] == "01" and body["ORD_UNPR"] == "0"


def test_rejected_order_is_marked_not_raised(kis_settings):
    broker, _ = broker_with(
        kis_settings,
        {"order-cash": FakeResponse({"rt_cd": "1", "msg_cd": "40240000",
                                     "msg1": "주문가능금액을 초과하였습니다"})},
    )
    order = broker.submit_order(Order("005930", Side.BUY, 1, OrderType.LIMIT, 71_300))
    assert order.status is OrderStatus.REJECTED
    assert "주문가능금액" in order.note


def test_trid_can_be_overridden_by_env(kis_settings, monkeypatch):
    monkeypatch.setenv("KIS_TRID_BUY", "TTTC0011U")
    broker, session = broker_with(
        kis_settings, {"order-cash": FakeResponse({"rt_cd": "0", "output": {"ODNO": "1"}})}
    )
    broker.submit_order(Order("005930", Side.BUY, 1, OrderType.LIMIT, 71_300))
    assert session.call_for("order-cash")["headers"]["tr_id"] == "TTTC0011U"


def test_expired_cached_token_is_refreshed(kis_settings):
    from datetime import datetime, timedelta, timezone

    store = TokenStore(kis_settings.token_cache_path)
    store.save("stale", datetime.now(timezone.utc) - timedelta(hours=1), "whatever")
    broker, session = broker_with(kis_settings, {})
    assert broker.access_token() == "tok-123"
    assert [c for c in session.calls if "tokenP" in c["url"]]


def test_live_mode_uses_live_trid_and_host(tmp_path):
    settings = Settings(
        mode="kis-live", kis_app_key="K", kis_app_secret="S",
        kis_account_no="12345678", kis_account_pd="01",
        base_dir=tmp_path, token_cache_path=tmp_path / "t.json",
    )
    session = FakeSession({"/oauth2/tokenP": TOKEN_OK, "/uapi/hashkey": HASH_OK,
                           "order-cash": FakeResponse({"rt_cd": "0", "output": {"ODNO": "1"}})})
    broker = KisBroker(settings, session=session)
    broker.submit_order(Order("005930", Side.BUY, 1, OrderType.LIMIT, 71_300))
    call = session.call_for("order-cash")
    assert "openapi.koreainvestment.com" in call["url"]
    assert call["headers"]["tr_id"] == "TTTC0802U"
