"""주간 전망 코멘트를 만든다.

기본은 Claude(Anthropic Messages API)로 한국어 해설을 생성하되,
API 키가 없거나 호출이 실패하면 지표 기반 규칙 문장으로 대체한다.
어느 쪽이든 카드 렌더러가 기대하는 동일한 구조를 돌려준다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict

from .config import CLAUDE_MODEL
from .indicators import PairSnapshot

log = logging.getLogger(__name__)

DISCLAIMER = "본 자료는 공개 데이터를 정리한 참고 자료이며 투자 권유가 아닙니다."

SYSTEM_PROMPT = """당신은 한국 독자를 위한 외환시장 카드뉴스를 쓰는 애널리스트다.
주어진 것은 ECB 고시 기준 원화 환율의 계산된 지표뿐이다. 다음을 지켜라.

- 주어진 수치에서 곧바로 읽히는 사실만 쓴다. 뉴스·정책·발언을 지어내지 않는다.
- 수치가 없는 인과("연준 발언으로 인해" 등)를 만들지 않는다. 지표가 말하는 것만 말한다.
- 단정적 가격 예측을 하지 않는다. 방향성과 조건을 함께 쓴다.
- 카드뉴스 문체: 짧고 평이한 한국어 문장. 한 문장 40자 안팎.
- 전문 용어를 쓰면 바로 뒤에 쉬운 말로 풀어 준다."""


@dataclass
class Commentary:
    """카드에 얹을 해설 묶음."""

    headline: str
    summary: str
    pair_comments: dict[str, str]
    watchpoints: list[str]
    source: str  # "claude" 또는 "rules"

    def to_dict(self) -> dict:
        return asdict(self)


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "이번 주 환율을 한 줄로 요약한 제목. 25자 이내.",
        },
        "summary": {
            "type": "string",
            "description": "전체 흐름 요약. 2~3문장, 160자 이내.",
        },
        "pair_comments": {
            "type": "object",
            "description": "통화 코드별 한 줄 해설. 각 60자 이내.",
            "properties": {
                "USD": {"type": "string"},
                "JPY": {"type": "string"},
                "EUR": {"type": "string"},
                "CNY": {"type": "string"},
            },
            "required": ["USD", "JPY", "EUR", "CNY"],
            "additionalProperties": False,
        },
        "watchpoints": {
            "type": "array",
            "description": "다음 주 확인할 관전 포인트 3개. 각 40자 이내.",
            "items": {"type": "string"},
        },
    },
    "required": ["headline", "summary", "pair_comments", "watchpoints"],
    "additionalProperties": False,
}


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def snapshots_to_payload(snapshots: list[PairSnapshot]) -> list[dict]:
    """모델에 넘길 최소한의 숫자 묶음. 시계열 원본은 보내지 않는다."""
    payload = []
    for s in snapshots:
        payload.append(
            {
                "통화쌍": s.display_name,
                "코드": s.code,
                "기준일": s.latest_date.isoformat(),
                "현재가": _round(s.latest),
                "주간등락": _round(s.week.delta) if s.week else None,
                "주간등락률%": _round(s.week.pct) if s.week else None,
                "월간등락률%": _round(s.month.pct) if s.month else None,
                "20일이동평균": _round(s.sma20),
                "60일이동평균": _round(s.sma60),
                "연율변동성%": _round(s.volatility, 1),
                "RSI14": _round(s.rsi14, 1),
                "볼린저위치%": _round(s.bollinger_pct, 1),
                "52주고점": _round(s.high52),
                "52주저점": _round(s.low52),
                "추세": s.trend,
            }
        )
    return payload


def _describe(snapshot: PairSnapshot) -> str:
    """지표만으로 만드는 통화별 한 줄 해설."""
    week = snapshot.week
    if week is None:
        return f"{snapshot.trend}. 비교할 지난주 고시가 없습니다."

    move = "올랐" if week.delta > 0 else ("내렸" if week.delta < 0 else "보합이었")
    parts = [f"한 주간 {abs(week.pct):.1f}% {move}습니다"]

    if snapshot.rsi14 is not None:
        if snapshot.rsi14 >= 70:
            parts.append("RSI가 과매수 구간입니다")
        elif snapshot.rsi14 <= 30:
            parts.append("RSI가 과매도 구간입니다")

    if snapshot.bollinger_pct is not None:
        if snapshot.bollinger_pct >= 100:
            parts.append("볼린저 상단을 넘었습니다")
        elif snapshot.bollinger_pct <= 0:
            parts.append("볼린저 하단을 밑돌았습니다")

    if len(parts) == 1:
        # 덧붙일 특이사항이 없을 때만 추세를 문장으로 쓴다. 카드에는 이미 추세가 찍힌다.
        parts.append(snapshot.trend + "가 이어지고 있습니다")
    return ". ".join(parts[:3]) + "."


def rule_based(snapshots: list[PairSnapshot]) -> Commentary:
    """API 없이 지표만으로 만드는 대체 해설."""
    usd = next((s for s in snapshots if s.code == "USD"), None)
    if usd and usd.week:
        arrow = "상승" if usd.week.delta > 0 else ("하락" if usd.week.delta < 0 else "보합")
        headline = f"원/달러 {arrow}, 주간 {abs(usd.week.pct):.1f}%"
    else:
        headline = "주간 원화 환율 동향"

    rising = [s.display_name for s in snapshots if s.direction == "up"]
    falling = [s.display_name for s in snapshots if s.direction == "down"]
    lines = []
    if rising:
        lines.append(f"{', '.join(rising)}이(가) 올라 원화가 그만큼 약해졌습니다.")
    if falling:
        lines.append(f"{', '.join(falling)}이(가) 내려 원화가 그만큼 강해졌습니다.")
    if not lines:
        lines.append("네 통화 모두 뚜렷한 방향 없이 좁은 범위에 머물렀습니다.")

    most_volatile = max(
        (s for s in snapshots if s.volatility is not None),
        key=lambda s: s.volatility,
        default=None,
    )
    if most_volatile:
        lines.append(
            f"변동성은 {most_volatile.display_name}이(가) 연율 "
            f"{most_volatile.volatility:.1f}%로 가장 컸습니다."
        )

    watchpoints = []
    for s in snapshots:
        if s.rsi14 is not None and (s.rsi14 >= 70 or s.rsi14 <= 30):
            zone = "과매수" if s.rsi14 >= 70 else "과매도"
            watchpoints.append(f"{s.display_name} RSI {s.rsi14:.0f} — {zone} 되돌림 여부")
        if s.high52 and s.latest >= s.high52 * 0.995:
            watchpoints.append(f"{s.display_name} 52주 고점권 돌파 시도")
        if s.low52 and s.latest <= s.low52 * 1.005:
            watchpoints.append(f"{s.display_name} 52주 저점권 지지 여부")
    if not watchpoints:
        watchpoints.append("20일·60일 이동평균 교차 여부")
    watchpoints.append("주요국 금리 결정 일정 확인")

    return Commentary(
        headline=headline,
        summary=" ".join(lines),
        pair_comments={s.code: _describe(s) for s in snapshots},
        watchpoints=watchpoints[:3],
        source="rules",
    )


def _call_claude(snapshots: list[PairSnapshot]) -> Commentary:
    import anthropic

    client = anthropic.Anthropic()
    payload = snapshots_to_payload(snapshots)

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        # 요약 성격의 정형 작업이라 effort를 medium으로 낮춰 비용을 줄인다.
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
        messages=[
            {
                "role": "user",
                "content": (
                    "아래는 ECB 고시 기준으로 계산한 원화 환율 지표다. "
                    "이 숫자만 근거로 주간 카드뉴스용 해설을 작성하라.\n\n"
                    + json.dumps(payload, ensure_ascii=False, indent=2)
                ),
            }
        ],
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"모델이 요청을 거절했습니다: {response.stop_details}")

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)
    return Commentary(
        headline=data["headline"].strip(),
        summary=data["summary"].strip(),
        pair_comments={k: v.strip() for k, v in data["pair_comments"].items()},
        watchpoints=[w.strip() for w in data["watchpoints"]][:3],
        source="claude",
    )


def build(snapshots: list[PairSnapshot], use_ai: bool = True) -> Commentary:
    """AI 해설을 시도하고, 불가능하면 규칙 기반으로 조용히 내려앉는다."""
    if not use_ai:
        return rule_based(snapshots)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY가 없어 규칙 기반 해설로 대체합니다.")
        return rule_based(snapshots)

    try:
        return _call_claude(snapshots)
    except ImportError:
        log.warning("anthropic 패키지가 없어 규칙 기반 해설로 대체합니다.")
    except Exception as exc:
        # 뉴스레터는 해설이 다소 밋밋해도 나가는 편이 낫다. 실패해도 멈추지 않는다.
        log.warning("Claude 호출 실패(%s: %s). 규칙 기반 해설로 대체합니다.", type(exc).__name__, exc)
    return rule_based(snapshots)
