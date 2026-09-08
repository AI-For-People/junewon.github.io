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
    """메일 발송 설정.

    발송 수단은 두 가지다. RESEND_API_KEY가 있으면 Resend를 쓰고, 없으면
    Gmail SMTP로 떨어진다. Resend 키는 '보내기'만 할 수 있어 받은편지함을
    읽지 못하므로, 지메일 앱 비밀번호보다 권한이 좁다.
    """

    provider: str          # "resend" 또는 "smtp"
    sender_name: str
    sender_address: str
    recipients: tuple[str, ...]

    # Resend 경로
    resend_api_key: str = ""

    # SMTP 경로
    host: str = ""
    port: int = 465
    user: str = ""
    password: str = ""

    @classmethod
    def from_env(cls) -> "MailConfig":
        resend_key = _env("RESEND_API_KEY")
        smtp_user = _env("FX_SMTP_USER")
        provider = "resend" if resend_key else "smtp"

        # Resend는 발신 주소를 직접 정해야 한다. 도메인을 등록하지 않았다면
        # Resend가 주는 테스트 발신 주소를 쓰되, 그 경우 계정 소유자 본인
        # 주소로만 보낼 수 있다.
        default_sender = "onboarding@resend.dev" if provider == "resend" else smtp_user
        sender_address = _env("FX_MAIL_FROM", default_sender)

        raw_to = _env("FX_MAIL_TO") or smtp_user
        recipients = tuple(a.strip() for a in raw_to.split(",") if a.strip())

        return cls(
            provider=provider,
            sender_name=_env("FX_MAIL_FROM_NAME", "주간 환율 브리핑"),
            sender_address=sender_address,
            recipients=recipients,
            resend_api_key=resend_key,
            host=_env("FX_SMTP_HOST", "smtp.gmail.com"),
            port=int(_env("FX_SMTP_PORT", "465")),
            user=smtp_user,
            # Gmail 앱 비밀번호는 표시할 때 4자씩 띄어쓰기가 들어가므로 공백을 지운다.
            password=_env("FX_SMTP_PASSWORD").replace(" ", ""),
        )

    @property
    def from_header(self) -> str:
        return f"{self.sender_name} <{self.sender_address}>"

    def validate(self) -> list[str]:
        """부족한 환경 변수 이름을 돌려준다. 비어 있으면 발송 가능하다."""
        missing = []
        if not self.recipients:
            missing.append("FX_MAIL_TO")
        if not self.sender_address:
            missing.append("FX_MAIL_FROM")

        if self.provider == "resend":
            if not self.resend_api_key:
                missing.append("RESEND_API_KEY")
        else:
            if not self.user:
                missing.append("FX_SMTP_USER")
            if not self.password:
                missing.append("FX_SMTP_PASSWORD")
        return missing
