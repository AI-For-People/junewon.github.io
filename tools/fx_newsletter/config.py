"""환경 변수와 통화 정의를 한곳에 모은 설정 모듈."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Pair:
    """KRW 대비 표시할 통화 한 종류."""

    code: str          # ISO 코드 (USD, JPY, EUR, CNY)
    label: str         # 카드에 찍을 이름
    unit: int          # 표시 단위 (엔화는 100엔 기준)
    symbol: str        # 통화 기호

    @property
    def display_name(self) -> str:
        if self.unit == 1:
            return f"{self.code}/KRW"
        return f"{self.unit}{self.code}/KRW"


PAIRS: tuple[Pair, ...] = (
    Pair(code="USD", label="미국 달러", unit=1, symbol="$"),
    Pair(code="JPY", label="일본 엔", unit=100, symbol="¥"),
    Pair(code="EUR", label="유로", unit=1, symbol="€"),
    Pair(code="CNY", label="중국 위안", unit=1, symbol="¥"),
)

# 지표 계산에 필요한 과거 구간. 52주 고저·60일 이동평균을 확보하려면 넉넉히 잡는다.
HISTORY_DAYS = 500

# 카드 규격
CARD_SIZE = 1080

CLAUDE_MODEL = os.environ.get("FX_CLAUDE_MODEL", "claude-opus-5")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class MailConfig:
    """Gmail SMTP 발송 설정."""

    host: str
    port: int
    user: str
    password: str
    sender_name: str
    recipients: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "MailConfig":
        user = _env("FX_SMTP_USER")
        raw_to = _env("FX_MAIL_TO") or user
        recipients = tuple(a.strip() for a in raw_to.split(",") if a.strip())
        return cls(
            host=_env("FX_SMTP_HOST", "smtp.gmail.com"),
            port=int(_env("FX_SMTP_PORT", "465")),
            user=user,
            # Gmail 앱 비밀번호는 표시할 때 4자씩 띄어쓰기가 들어가므로 공백을 지운다.
            password=_env("FX_SMTP_PASSWORD").replace(" ", ""),
            sender_name=_env("FX_MAIL_FROM_NAME", "주간 환율 브리핑"),
            recipients=recipients,
        )

    def validate(self) -> list[str]:
        missing = []
        if not self.user:
            missing.append("FX_SMTP_USER")
        if not self.password:
            missing.append("FX_SMTP_PASSWORD")
        if not self.recipients:
            missing.append("FX_MAIL_TO")
        return missing
