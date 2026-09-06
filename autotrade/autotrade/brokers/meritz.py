"""메리츠증권 어댑터.

⚠️ 현황 (2026-09 확인 기준)
--------------------------------
메리츠증권은 개인 개발자에게 **공개된 오픈 API 개발자센터/문서를 아직 운영하지 않는다.**
2026년 5월 언론 보도 기준으로 "오픈 API 개발을 사실상 마무리하고 내부 테스트 중,
무수수료 Super365 계좌와 결합해 연내 출시 예상" 단계다. 즉 지금 시점에서는
메리츠 계좌를 코드로 직접 연동할 공식 경로가 없다.

따라서 이 어댑터는 "출시되면 즉시 붙일 수 있도록" 준비만 해 둔 것이다.
동작 방식은 SpecRestBroker와 같다. 메리츠가 API 문서를 공개하면
config/meritz.spec.example.json 을 config/meritz.spec.json 으로 복사해
엔드포인트 경로와 응답 필드명만 채우면 엔진 전체가 그대로 돌아간다.

❌ 절대 하지 말 것
------------------
공식 API가 없다고 해서 HTS/MTS 화면을 매크로로 자동 클릭하거나,
내부 웹 요청을 리버스 엔지니어링해 호출하는 방식은 쓰지 않는다.
- 대부분의 증권사 전자금융거래 약관에서 금지행위로 규정한다(계좌 이용 제한·거래 정지 사유).
- 화면/내부 API는 사전 고지 없이 바뀌므로 주문이 조용히 실패하거나 잘못 나갈 수 있다.
- 사고가 나도 증권사 보상·분쟁조정 대상이 되기 어렵다.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import Settings
from ..errors import ConfigError
from .spec_rest import BrokerSpec, SpecRestBroker

DEFAULT_SPEC_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "meritz.spec.json"
EXAMPLE_SPEC_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "meritz.spec.example.json"
)

STATUS_NOTE = """\
메리츠증권 공개 오픈 API는 아직 확인되지 않았습니다 (2026-09 기준: 내부 테스트/출시 예정 단계).

지금 할 수 있는 것:
  1) 메리츠증권 고객센터(1588-3400) 또는 지점에 '개인용 오픈 API(REST) 제공 계획과
     신청 절차'를 문의하고, 베타 참여가 가능한지 확인한다.
  2) 그 사이 검증은 mode=paper(오프라인 시뮬레이터)와
     mode=kis-paper(한국투자증권 모의투자)로 진행한다. 전략·리스크·운영 코드는
     브로커와 분리돼 있으므로 메리츠 API가 나오면 어댑터만 갈아끼우면 된다.
  3) 메리츠 API 문서를 받으면:
       cp config/meritz.spec.example.json config/meritz.spec.json
     으로 복사한 뒤 TODO 항목(엔드포인트 경로·필드명·주문코드)을 채운다.
"""


class MeritzBroker(SpecRestBroker):
    name = "meritz"

    def __init__(self, settings: Settings, *, journal=None, spec_path: Path | str | None = None, **kw):
        path = Path(spec_path or os.environ.get("MERITZ_SPEC_PATH") or DEFAULT_SPEC_PATH)
        if not path.exists():
            raise ConfigError(
                f"메리츠 API 스펙 파일이 없습니다: {path}\n\n{STATUS_NOTE}"
            )
        spec = BrokerSpec.load(path)
        credentials = {
            "app_key": os.environ.get("MERITZ_APP_KEY", ""),
            "app_secret": os.environ.get("MERITZ_APP_SECRET", ""),
            "account_no": os.environ.get("MERITZ_ACCOUNT_NO", ""),
            "account_pd": os.environ.get("MERITZ_ACCOUNT_PD", ""),
        }
        super().__init__(settings, spec, journal=journal, credentials=credentials, **kw)


def status() -> str:
    return STATUS_NOTE
