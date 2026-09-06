"""환경변수 기반 설정.

비밀값(앱키/시크릿)은 코드·git에 절대 넣지 않는다. .env 파일 또는 OS 환경변수만 사용한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

# ---------------------------------------------------------------- 실행 모드
# paper      : 완전 오프라인 시뮬레이션 브로커 (네트워크 불필요, 기본값)
# kis-paper  : 한국투자증권 "모의투자" 서버 (실제 API, 가상 자금)
# kis-live   : 한국투자증권 "실전투자" 서버 (실제 돈)
# meritz     : 메리츠증권 (공개 API 출시 후 config/meritz.spec.json 을 채우면 동작)
MODES = ("paper", "kis-paper", "kis-live", "meritz")

KIS_LIVE_BASE = "https://openapi.koreainvestment.com:9443"
KIS_PAPER_BASE = "https://openapivts.koreainvestment.com:29443"

# 실계좌 가동을 위한 명시적 동의 문구
LIVE_ACK_ENV = "AUTOTRADE_I_UNDERSTAND_REAL_MONEY_RISK"
LIVE_ACK_VALUE = "yes-i-accept-full-loss"


def _env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key, default)
    return v.strip() if isinstance(v, str) else v


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 는 숫자여야 합니다: {raw!r}") from exc


def _env_int(key: str, default: int) -> int:
    return int(_env_float(key, float(default)))


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw in (None, ""):
        return default
    return raw.lower() in ("1", "true", "yes", "y", "on")


def load_dotenv(path: Path | str = ".env", *, override: bool = False) -> dict[str, str]:
    """의존성 없는 최소 .env 로더. KEY=VALUE 형식만 지원한다."""
    p = Path(path)
    loaded: dict[str, str] = {}
    if not p.exists():
        return loaded
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value
        loaded[key] = value
    return loaded


@dataclass
class RiskLimits:
    """리스크 한도. 전부 '넘으면 주문을 막는' 하드 리밋이다."""

    max_order_notional: float = 300_000.0      # 1주문 최대 명목금액(원)
    max_position_notional: float = 1_000_000.0 # 종목당 최대 보유 평가액(원)
    max_gross_exposure: float = 3_000_000.0    # 전체 보유 평가액 상한(원)
    daily_loss_limit: float = 100_000.0        # 일중 손실 한도(원, 양수로 기재)
    max_orders_per_day: int = 40
    max_orders_per_minute: int = 5
    min_seconds_between_same_signal: int = 60  # 동일 종목·방향 재발주 최소 간격
    symbol_whitelist: tuple[str, ...] = ()     # 비어 있으면 '어떤 종목도 거래 불가'
    allow_short: bool = False                  # 국내주식 개인 공매도는 사실상 불가

    @classmethod
    def from_env(cls) -> "RiskLimits":
        wl = _env("AUTOTRADE_SYMBOL_WHITELIST", "") or ""
        return cls(
            max_order_notional=_env_float("AUTOTRADE_MAX_ORDER_NOTIONAL", cls.max_order_notional),
            max_position_notional=_env_float(
                "AUTOTRADE_MAX_POSITION_NOTIONAL", cls.max_position_notional
            ),
            max_gross_exposure=_env_float("AUTOTRADE_MAX_GROSS_EXPOSURE", cls.max_gross_exposure),
            daily_loss_limit=_env_float("AUTOTRADE_DAILY_LOSS_LIMIT", cls.daily_loss_limit),
            max_orders_per_day=_env_int("AUTOTRADE_MAX_ORDERS_PER_DAY", cls.max_orders_per_day),
            max_orders_per_minute=_env_int(
                "AUTOTRADE_MAX_ORDERS_PER_MINUTE", cls.max_orders_per_minute
            ),
            min_seconds_between_same_signal=_env_int(
                "AUTOTRADE_MIN_SECONDS_BETWEEN_SAME_SIGNAL", cls.min_seconds_between_same_signal
            ),
            symbol_whitelist=tuple(s.strip() for s in wl.split(",") if s.strip()),
            allow_short=_env_bool("AUTOTRADE_ALLOW_SHORT", cls.allow_short),
        )


@dataclass
class CostModel:
    """거래비용. ⚠️ 본인 증권사·계좌의 실제 요율로 반드시 바꿔 넣을 것."""

    fee_rate: float = 0.00015          # 위탁수수료(매수/매도 공통) 예시값
    sell_tax_rate: float = 0.0015      # 매도 시 증권거래세+농특세 예시값
    slippage_bps: float = 5.0          # 시뮬레이션용 슬리피지(1bp = 0.01%)

    @classmethod
    def from_env(cls) -> "CostModel":
        return cls(
            fee_rate=_env_float("AUTOTRADE_FEE_RATE", cls.fee_rate),
            sell_tax_rate=_env_float("AUTOTRADE_SELL_TAX_RATE", cls.sell_tax_rate),
            slippage_bps=_env_float("AUTOTRADE_SLIPPAGE_BPS", cls.slippage_bps),
        )

    def buy_cost(self, notional: float) -> float:
        return notional * self.fee_rate

    def sell_cost(self, notional: float) -> tuple[float, float]:
        """(수수료, 세금)."""
        return notional * self.fee_rate, notional * self.sell_tax_rate


@dataclass
class Settings:
    mode: str = "paper"
    dry_run: bool = True
    poll_interval_sec: float = 30.0
    trade_only_in_session: bool = True

    # 한국투자증권 KIS Developers
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""     # 앞 8자리 (CANO)
    kis_account_pd: str = "01"   # 뒤 2자리 상품코드 (ACNT_PRDT_CD)

    # 경로
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    journal_path: Path = field(default_factory=lambda: Path("var/journal.jsonl"))
    token_cache_path: Path = field(default_factory=lambda: Path("var/kis_token.json"))
    kill_switch_path: Path = field(default_factory=lambda: Path("var/KILL_SWITCH"))
    holiday_file: Path | None = None

    risk: RiskLimits = field(default_factory=RiskLimits)
    costs: CostModel = field(default_factory=CostModel)

    # 시뮬레이션 초기 자금
    paper_starting_cash: float = 10_000_000.0

    @property
    def is_live(self) -> bool:
        """KIS 실전투자 서버를 쓰는가(= KIS 실전 TR_ID/도메인)."""
        return self.mode == "kis-live"

    @property
    def is_real_money(self) -> bool:
        """실제 돈이 걸린 모드인가. 안전장치는 전부 이 기준으로 건다."""
        return self.mode in ("kis-live", "meritz")

    @property
    def uses_kis(self) -> bool:
        return self.mode.startswith("kis-")

    @property
    def kis_base_url(self) -> str:
        return KIS_LIVE_BASE if self.is_live else KIS_PAPER_BASE

    @classmethod
    def load(cls, *, dotenv: str | Path | None = ".env") -> "Settings":
        if dotenv:
            load_dotenv(dotenv)
        base = Path(__file__).resolve().parent.parent
        s = cls(
            mode=(_env("AUTOTRADE_MODE", "paper") or "paper").lower(),
            dry_run=_env_bool("AUTOTRADE_DRY_RUN", True),
            poll_interval_sec=_env_float("AUTOTRADE_POLL_INTERVAL_SEC", 30.0),
            trade_only_in_session=_env_bool("AUTOTRADE_TRADE_ONLY_IN_SESSION", True),
            kis_app_key=_env("KIS_APP_KEY", "") or "",
            kis_app_secret=_env("KIS_APP_SECRET", "") or "",
            kis_account_no=_env("KIS_ACCOUNT_NO", "") or "",
            kis_account_pd=_env("KIS_ACCOUNT_PD", "01") or "01",
            base_dir=base,
            journal_path=base / (_env("AUTOTRADE_JOURNAL", "var/journal.jsonl") or ""),
            token_cache_path=base / (_env("AUTOTRADE_TOKEN_CACHE", "var/kis_token.json") or ""),
            kill_switch_path=base / (_env("AUTOTRADE_KILL_SWITCH", "var/KILL_SWITCH") or ""),
            risk=RiskLimits.from_env(),
            costs=CostModel.from_env(),
            paper_starting_cash=_env_float("AUTOTRADE_PAPER_CASH", 10_000_000.0),
        )
        s.validate()
        return s

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ConfigError(f"AUTOTRADE_MODE 는 {MODES} 중 하나여야 합니다: {self.mode!r}")

        if self.uses_kis:
            missing = [
                name
                for name, value in (
                    ("KIS_APP_KEY", self.kis_app_key),
                    ("KIS_APP_SECRET", self.kis_app_secret),
                    ("KIS_ACCOUNT_NO", self.kis_account_no),
                )
                if not value
            ]
            if missing:
                raise ConfigError(
                    f"{self.mode} 모드에는 다음 환경변수가 필요합니다: {', '.join(missing)}"
                )
            if len(self.kis_account_no) != 8 or not self.kis_account_no.isdigit():
                raise ConfigError("KIS_ACCOUNT_NO 는 계좌번호 앞 8자리 숫자여야 합니다.")
            if len(self.kis_account_pd) != 2 or not self.kis_account_pd.isdigit():
                raise ConfigError("KIS_ACCOUNT_PD 는 계좌 상품코드 2자리 숫자여야 합니다.")

        if self.is_real_money:
            # 실계좌는 "설정 실수로 켜지는 일"이 없어야 한다. 두 겹으로 막는다.
            if _env(LIVE_ACK_ENV) != LIVE_ACK_VALUE:
                raise ConfigError(
                    f"실계좌({self.mode}) 모드는 명시적 동의가 필요합니다.\n"
                    f"  {LIVE_ACK_ENV}={LIVE_ACK_VALUE}\n"
                    "를 설정하십시오. 설정 전에 모의투자에서 최소 수 주간 검증하십시오."
                )
            if not self.risk.symbol_whitelist:
                raise ConfigError(
                    "실계좌 모드는 AUTOTRADE_SYMBOL_WHITELIST(거래 허용 종목)를 반드시 지정해야 합니다."
                )

        if self.risk.daily_loss_limit <= 0:
            raise ConfigError("AUTOTRADE_DAILY_LOSS_LIMIT 는 0보다 커야 합니다.")

    def describe(self) -> dict:
        """비밀값을 가린 설정 요약."""
        return {
            "mode": self.mode,
            "dry_run": self.dry_run,
            "base_url": self.kis_base_url if self.uses_kis else "(local simulator)",
            "account": (
                f"{self.kis_account_no[:4]}****-{self.kis_account_pd}"
                if self.kis_account_no
                else ""
            ),
            "app_key_set": bool(self.kis_app_key),
            "app_secret_set": bool(self.kis_app_secret),
            "poll_interval_sec": self.poll_interval_sec,
            "risk": {
                "max_order_notional": self.risk.max_order_notional,
                "max_position_notional": self.risk.max_position_notional,
                "max_gross_exposure": self.risk.max_gross_exposure,
                "daily_loss_limit": self.risk.daily_loss_limit,
                "max_orders_per_day": self.risk.max_orders_per_day,
                "max_orders_per_minute": self.risk.max_orders_per_minute,
                "symbol_whitelist": list(self.risk.symbol_whitelist),
            },
            "costs": {
                "fee_rate": self.costs.fee_rate,
                "sell_tax_rate": self.costs.sell_tax_rate,
                "slippage_bps": self.costs.slippage_bps,
            },
        }
