import os

import pytest

from autotrade.config import LIVE_ACK_ENV, LIVE_ACK_VALUE, Settings, load_dotenv
from autotrade.errors import ConfigError


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("AUTOTRADE_", "KIS_", "MERITZ_")):
            monkeypatch.delenv(key, raising=False)


def test_defaults_are_safe():
    s = Settings.load(dotenv=None)
    assert s.mode == "paper"
    assert s.dry_run is True, "기본값은 반드시 드라이런이어야 한다"
    assert s.risk.symbol_whitelist == (), "기본 화이트리스트는 비어 있어야 한다(=전부 차단)"
    assert s.is_real_money is False


def test_unknown_mode_rejected(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_MODE", "야수의심장")
    with pytest.raises(ConfigError):
        Settings.load(dotenv=None)


def test_kis_mode_requires_credentials(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_MODE", "kis-paper")
    with pytest.raises(ConfigError, match="KIS_APP_KEY"):
        Settings.load(dotenv=None)


def test_account_number_format_validated(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_MODE", "kis-paper")
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "123")
    with pytest.raises(ConfigError, match="8자리"):
        Settings.load(dotenv=None)


def _kis_creds(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PD", "01")


def test_live_mode_requires_explicit_acknowledgement(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_MODE", "kis-live")
    _kis_creds(monkeypatch)
    with pytest.raises(ConfigError, match="명시적 동의"):
        Settings.load(dotenv=None)


def test_live_mode_requires_whitelist(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_MODE", "kis-live")
    _kis_creds(monkeypatch)
    monkeypatch.setenv(LIVE_ACK_ENV, LIVE_ACK_VALUE)
    with pytest.raises(ConfigError, match="WHITELIST"):
        Settings.load(dotenv=None)


def test_live_mode_ok_with_all_guards(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_MODE", "kis-live")
    _kis_creds(monkeypatch)
    monkeypatch.setenv(LIVE_ACK_ENV, LIVE_ACK_VALUE)
    monkeypatch.setenv("AUTOTRADE_SYMBOL_WHITELIST", "005930, 000660")
    s = Settings.load(dotenv=None)
    assert s.risk.symbol_whitelist == ("005930", "000660")
    assert s.is_real_money and s.is_live
    assert "openapi.koreainvestment.com" in s.kis_base_url


def test_paper_mode_uses_virtual_server(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_MODE", "kis-paper")
    _kis_creds(monkeypatch)
    s = Settings.load(dotenv=None)
    assert "openapivts" in s.kis_base_url
    assert s.is_real_money is False


def test_meritz_is_treated_as_real_money(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_MODE", "meritz")
    with pytest.raises(ConfigError, match="명시적 동의"):
        Settings.load(dotenv=None)


def test_describe_never_leaks_secrets(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_MODE", "kis-paper")
    _kis_creds(monkeypatch)
    monkeypatch.setenv("KIS_APP_SECRET", "super-secret-value")
    described = repr(Settings.load(dotenv=None).describe())
    assert "super-secret-value" not in described
    assert "12345678" not in described


def test_dotenv_does_not_override_real_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AUTOTRADE_MODE=kis-live\nAUTOTRADE_MAX_ORDER_NOTIONAL=999\n", encoding="utf-8")
    monkeypatch.setenv("AUTOTRADE_MODE", "paper")
    load_dotenv(env)
    assert os.environ["AUTOTRADE_MODE"] == "paper"
    assert os.environ["AUTOTRADE_MAX_ORDER_NOTIONAL"] == "999"


def test_bad_numeric_env_rejected(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_MAX_ORDER_NOTIONAL", "많이")
    with pytest.raises(ConfigError):
        Settings.load(dotenv=None)


def test_zero_loss_limit_rejected(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_DAILY_LOSS_LIMIT", "0")
    with pytest.raises(ConfigError):
        Settings.load(dotenv=None)
