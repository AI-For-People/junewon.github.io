"""메리츠 어댑터 & 스펙 기반 REST 브로커 테스트.

메리츠는 아직 공개 API가 확인되지 않았으므로, 검증 대상은
"출시되면 스펙 JSON만 채워서 붙을 수 있는가"와 "미완성 스펙으로는 절대 기동하지 않는가"다.
"""

import json
from pathlib import Path

import pytest

from autotrade.brokers.meritz import MeritzBroker
from autotrade.brokers.spec_rest import BrokerSpec, SpecRestBroker, dig, render
from autotrade.config import Settings
from autotrade.errors import BrokerError, ConfigError
from autotrade.models import Order, OrderStatus, OrderType, Side

from .fakes import FakeResponse, FakeSession

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "config" / "meritz.spec.example.json"


def working_spec() -> dict:
    return {
        "name": "meritz",
        "base_url": "https://example.invalid",
        "rate_limit_per_sec": 10000,
        "success_check": {"field": "rt_cd", "equals": "0", "message_field": "msg1"},
        "auth": {
            "path": "/oauth2/token",
            "method": "POST",
            "body": {"appkey": "${APP_KEY}", "appsecret": "${APP_SECRET}"},
            "token_field": "access_token",
            "expires_field": "expires_in",
            "header_template": {"authorization": "Bearer ${TOKEN}", "appkey": "${APP_KEY}"},
        },
        "endpoints": {
            "quote": {
                "path": "/quote", "method": "GET",
                "params": {"code": "${SYMBOL}"},
                "response": {"price": "output.price"},
            },
            "candles": {
                "path": "/daily", "method": "GET",
                "params": {"code": "${SYMBOL}"},
                "response": {"list": "output", "date": "d", "date_format": "%Y%m%d",
                             "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v"},
            },
            "balance": {
                "path": "/balance", "method": "GET",
                "response": {"cash": "output2.cash", "positions": "output1",
                             "position_symbol": "code", "position_qty": "qty",
                             "position_avg_price": "avg"},
            },
            "order": {
                "path": "/order", "method": "POST",
                "codes": {"buy": "02", "sell": "01", "limit": "00", "market": "01"},
                "body": {"acct": "${ACCOUNT_NO}", "code": "${SYMBOL}", "side": "${SIDE}",
                         "type": "${ORDER_TYPE}", "qty": "${QTY}", "price": "${PRICE}"},
                "response": {"order_id": "output.ordno", "message": "msg1"},
            },
        },
    }


@pytest.fixture
def settings(tmp_path):
    return Settings(mode="meritz", dry_run=False, base_dir=tmp_path,
                    journal_path=tmp_path / "j.jsonl")


def make(settings, routes, spec=None):
    session = FakeSession({
        "/oauth2/token": FakeResponse({"access_token": "T", "expires_in": 3600}),
        **routes,
    })
    broker = SpecRestBroker(
        settings, BrokerSpec(spec or working_spec()), session=session,
        credentials={"app_key": "K", "app_secret": "S", "account_no": "1234", "account_pd": "01"},
    )
    return broker, session


# ------------------------------------------------------------------ 안전장치
def test_shipped_example_spec_is_valid_json():
    json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_spec_still_has_todos():
    assert BrokerSpec.load(EXAMPLE).unresolved_todos(), "예시 스펙은 채워지지 않은 상태여야 한다"


def test_incomplete_spec_refuses_to_start(settings):
    with pytest.raises(ConfigError, match="완성되지 않았"):
        SpecRestBroker(settings, BrokerSpec.load(EXAMPLE), session=FakeSession())


def test_meritz_without_spec_file_explains_the_situation(settings, tmp_path):
    with pytest.raises(ConfigError) as exc:
        MeritzBroker(settings, spec_path=tmp_path / "missing.json")
    assert "메리츠" in str(exc.value)
    assert "kis-paper" in str(exc.value), "대안 경로를 안내해야 한다"


def test_meritz_uses_filled_spec_when_present(settings, tmp_path, monkeypatch):
    spec_file = tmp_path / "meritz.spec.json"
    spec_file.write_text(json.dumps(working_spec()), encoding="utf-8")
    monkeypatch.setenv("MERITZ_APP_KEY", "K")
    monkeypatch.setenv("MERITZ_ACCOUNT_NO", "1234")
    broker = MeritzBroker(settings, spec_path=spec_file, session=FakeSession())
    assert broker.name == "meritz"
    assert broker.credentials["app_key"] == "K"


# ------------------------------------------------------------------ 템플릿
def test_render_substitutes_placeholders():
    assert render({"a": "${TOKEN}", "b": "Bearer ${TOKEN}"}, {"TOKEN": "T"}) == {
        "a": "T", "b": "Bearer T"
    }


def test_render_leaves_unknown_keys_empty():
    assert render("${NOPE}", {}) == ""


def test_dig_walks_nested_paths():
    data = {"output": {"rows": [{"price": 100}, {"price": 200}]}}
    assert dig(data, "output.rows[1].price") == 200
    assert dig(data, "output.missing", "fallback") == "fallback"
    assert dig(data, "output.rows[9].price") is None


# ------------------------------------------------------------------ 동작
def test_quote_flow(settings):
    broker, session = make(settings, {"/quote": FakeResponse({"rt_cd": "0",
                                                              "output": {"price": "71,300"}})})
    assert broker.get_quote("005930").price == 71_300
    assert session.call_for("/quote")["params"]["code"] == "005930"
    assert session.call_for("/quote")["headers"]["authorization"] == "Bearer T"


def test_candles_flow_sorts_and_parses(settings):
    payload = {"rt_cd": "0", "output": [
        {"d": "20260904", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
        {"d": "20260903", "o": 1, "h": 2, "l": 0.5, "c": 1.2, "v": 10},
    ]}
    broker, _ = make(settings, {"/daily": FakeResponse(payload)})
    assert [c.close for c in broker.get_candles("005930")] == [1.2, 1.5]


def test_balance_flow(settings):
    payload = {"rt_cd": "0",
               "output1": [{"code": "005930", "qty": "5", "avg": "70000"},
                           {"code": "000660", "qty": "0", "avg": "0"}],
               "output2": {"cash": "1,000,000"}}
    broker, _ = make(settings, {"/balance": FakeResponse(payload)})
    account = broker.get_account()
    assert account.cash == 1_000_000
    assert list(account.positions) == ["005930"]


def test_order_maps_side_and_type_codes(settings):
    broker, session = make(settings, {"/order": FakeResponse({"rt_cd": "0", "msg1": "OK",
                                                              "output": {"ordno": "A1"}})})
    order = broker.submit_order(Order("005930", Side.SELL, 4, OrderType.MARKET))
    assert order.status is OrderStatus.SUBMITTED and order.broker_order_id == "A1"
    body = session.call_for("/order")["json"]
    assert body["side"] == "01" and body["type"] == "01" and body["price"] == "0"
    assert body["qty"] == "4" and body["acct"] == "1234"


def test_business_error_marks_order_rejected(settings):
    broker, _ = make(settings, {"/order": FakeResponse({"rt_cd": "9", "msg1": "한도초과"})})
    order = broker.submit_order(Order("005930", Side.BUY, 1, OrderType.LIMIT, 70_000))
    assert order.status is OrderStatus.REJECTED and "한도초과" in order.note


def test_http_error_raises(settings):
    broker, _ = make(settings, {"/quote": FakeResponse({"msg": "boom"}, 500)})
    with pytest.raises(BrokerError, match="HTTP 500"):
        broker.get_quote("005930")


def test_missing_endpoint_is_reported(settings):
    spec = working_spec()
    del spec["endpoints"]["quote"]
    broker, _ = make(settings, {}, spec=spec)
    with pytest.raises(BrokerError, match="quote"):
        broker.get_quote("005930")
