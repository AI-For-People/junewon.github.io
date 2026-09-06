"""지표와 해설을 1080x1080 카드뉴스 PNG로 렌더링한다."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .commentary import DISCLAIMER, Commentary
from .config import CARD_SIZE
from .indicators import PairSnapshot

log = logging.getLogger(__name__)

BG = "#0B1220"
SURFACE = "#141E33"
LINE = "#243252"
TEXT = "#F2F5FA"
MUTED = "#8A99B8"
ACCENT = "#FFC24B"
# 한국 시장 관행: 상승은 빨강, 하락은 파랑.
UP = "#FF5C5C"
DOWN = "#4D8DFF"
FLAT = "#8A99B8"

MARGIN = 72

# 앞에 있는 것부터 시도한다. CI에서는 fonts-nanum을 설치해 첫 후보를 쓰게 한다.
FONT_CANDIDATES = {
    "regular": (
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    "bold": (
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
}


def _font_path(weight: str) -> str | None:
    for path in FONT_CANDIDATES[weight]:
        if Path(path).exists():
            return path
    return None


def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    """크기·굵기별 폰트를 캐싱해 돌려준다."""
    key = (size, weight)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    path = _font_path(weight)
    if path is None:
        log.warning("한글 폰트를 찾지 못해 기본 폰트로 대체합니다. 한글이 깨질 수 있습니다.")
        loaded = ImageFont.load_default()
    else:
        loaded = ImageFont.truetype(path, size)
    _FONT_CACHE[key] = loaded
    return loaded


_FONT_CACHE: dict[tuple[int, str], ImageFont.ImageFont] = {}


def text_width(draw: ImageDraw.ImageDraw, text: str, fnt) -> int:
    return int(draw.textlength(text, font=fnt))


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_width: int) -> list[str]:
    """픽셀 폭 기준 줄바꿈. 한국어는 공백이 드물어 글자 단위로도 끊는다."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for token in paragraph.split(" "):
            candidate = f"{current} {token}".strip()
            if current and text_width(draw, candidate, fnt) > max_width:
                lines.append(current)
                current = token
            else:
                current = candidate
            # 공백 없는 긴 덩어리는 글자 단위로 쪼갠다.
            while text_width(draw, current, fnt) > max_width and len(current) > 1:
                cut = len(current)
                while cut > 1 and text_width(draw, current[:cut], fnt) > max_width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return [ln for ln in lines if ln != ""] or [""]


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fnt,
    fill: str,
    max_width: int,
    line_height: int,
    max_lines: int | None = None,
) -> int:
    """줄바꿈해 그리고, 다음 요소가 시작할 y좌표를 돌려준다."""
    x, y = xy
    lines = wrap(draw, text, fnt, max_width)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    for line in lines:
        draw.text((x, y), line, font=fnt, fill=fill)
        y += line_height
    return y


def new_card() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (CARD_SIZE, CARD_SIZE), BG)
    return image, ImageDraw.Draw(image)


def _color_for(direction: str) -> str:
    return {"up": UP, "down": DOWN}.get(direction, FLAT)


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:,.{digits}f}"


def _signed(value: float, digits: int = 2) -> str:
    return f"{'+' if value > 0 else ''}{value:,.{digits}f}"


def _footer(draw: ImageDraw.ImageDraw, note: str = "") -> None:
    draw.line(
        [(MARGIN, CARD_SIZE - 108), (CARD_SIZE - MARGIN, CARD_SIZE - 108)],
        fill=LINE,
        width=2,
    )
    left = note or "출처: ECB 기준환율 (Frankfurter API)"
    draw.text((MARGIN, CARD_SIZE - 86), left, font=font(24), fill=MUTED)
    draw.text((MARGIN, CARD_SIZE - 52), DISCLAIMER, font=font(21), fill=MUTED)


def draw_sparkline(
    draw: ImageDraw.ImageDraw,
    values: tuple[float, ...],
    box: tuple[int, int, int, int],
    color: str,
) -> None:
    """추세를 한눈에 보이게 하는 미니 라인차트."""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=20, fill=SURFACE)
    if len(values) < 2:
        return

    low, high = min(values), max(values)
    span = high - low
    pad = 26
    inner_w = x1 - x0 - pad * 2
    inner_h = y1 - y0 - pad * 2

    def point(i: int, v: float) -> tuple[float, float]:
        px = x0 + pad + inner_w * i / (len(values) - 1)
        # 값이 전부 같으면 중앙에 수평선을 그린다.
        ratio = 0.5 if span == 0 else (v - low) / span
        py = y0 + pad + inner_h * (1 - ratio)
        return px, py

    points = [point(i, v) for i, v in enumerate(values)]
    draw.polygon(
        [(points[0][0], y1 - pad)] + points + [(points[-1][0], y1 - pad)],
        fill=SURFACE,
    )
    draw.line(points, fill=color, width=5, joint="curve")
    draw.ellipse(
        [points[-1][0] - 9, points[-1][1] - 9, points[-1][0] + 9, points[-1][1] + 9],
        fill=color,
    )


def cover_card(snapshots: list[PairSnapshot], commentary: Commentary, issued: dt.date) -> Image.Image:
    image, draw = new_card()
    draw.rectangle([0, 0, CARD_SIZE, 12], fill=ACCENT)

    draw.text((MARGIN, 108), "WEEKLY FX BRIEF", font=font(30, "bold"), fill=ACCENT)
    draw.text((MARGIN, 158), "주간 환율 브리핑", font=font(76, "bold"), fill=TEXT)
    draw.text(
        (MARGIN, 254),
        f"{issued.strftime('%Y년 %m월 %d일')} 발행",
        font=font(28),
        fill=MUTED,
    )

    y = draw_wrapped(
        draw,
        (MARGIN, 330),
        commentary.headline,
        font(46, "bold"),
        ACCENT,
        CARD_SIZE - MARGIN * 2,
        62,
        max_lines=2,
    )

    y += 30
    row_h = 120
    for snapshot in snapshots:
        draw.rounded_rectangle(
            [MARGIN, y, CARD_SIZE - MARGIN, y + row_h - 16], radius=18, fill=SURFACE
        )
        draw.text((MARGIN + 28, y + 16), snapshot.display_name, font=font(30, "bold"), fill=TEXT)
        draw.text(
            (MARGIN + 28, y + 58),
            f"{snapshot.label} · {snapshot.trend}",
            font=font(23),
            fill=MUTED,
        )

        value_text = _fmt(snapshot.latest)
        value_font = font(42, "bold")
        draw.text(
            (CARD_SIZE - MARGIN - 28 - text_width(draw, value_text, value_font), y + 16),
            value_text,
            font=value_font,
            fill=TEXT,
        )
        if snapshot.week:
            change_text = f"{_signed(snapshot.week.pct, 2)}%"
            change_font = font(27, "bold")
            draw.text(
                (CARD_SIZE - MARGIN - 28 - text_width(draw, change_text, change_font), y + 66),
                change_text,
                font=change_font,
                fill=_color_for(snapshot.direction),
            )
        y += row_h

    _footer(draw)
    return image


def pair_card(snapshot: PairSnapshot, comment: str) -> Image.Image:
    image, draw = new_card()
    color = _color_for(snapshot.direction)
    draw.rectangle([0, 0, CARD_SIZE, 12], fill=color)

    draw.text((MARGIN, 96), snapshot.label, font=font(30), fill=MUTED)
    draw.text((MARGIN, 138), snapshot.display_name, font=font(58, "bold"), fill=TEXT)

    draw.text((MARGIN, 236), _fmt(snapshot.latest), font=font(112, "bold"), fill=TEXT)
    draw.text((MARGIN, 366), "원", font=font(32), fill=MUTED)

    if snapshot.week:
        badge = f"주간 {_signed(snapshot.week.delta)}원 ({_signed(snapshot.week.pct)}%)"
        badge_font = font(30, "bold")
        width = text_width(draw, badge, badge_font)
        draw.rounded_rectangle([MARGIN + 60, 362, MARGIN + 60 + width + 44, 414], radius=26, fill=color)
        draw.text((MARGIN + 82, 372), badge, font=badge_font, fill=BG)

    draw_sparkline(draw, snapshot.spark, (MARGIN, 442, CARD_SIZE - MARGIN, 690), color)
    draw.text((MARGIN + 26, 452), "최근 60영업일", font=font(22), fill=MUTED)

    rows = [
        ("20일 이동평균", _fmt(snapshot.sma20) if snapshot.sma20 else "-"),
        ("60일 이동평균", _fmt(snapshot.sma60) if snapshot.sma60 else "-"),
        ("연율 변동성", f"{snapshot.volatility:.1f}%" if snapshot.volatility else "-"),
        ("RSI(14)", f"{snapshot.rsi14:.0f}" if snapshot.rsi14 else "-"),
        ("52주 고점", _fmt(snapshot.high52) if snapshot.high52 else "-"),
        ("52주 저점", _fmt(snapshot.low52) if snapshot.low52 else "-"),
    ]
    col_w = (CARD_SIZE - MARGIN * 2) // 3
    for index, (name, value) in enumerate(rows):
        cx = MARGIN + (index % 3) * col_w
        cy = 716 + (index // 3) * 78
        draw.text((cx, cy), name, font=font(22), fill=MUTED)
        draw.text((cx, cy + 30), value, font=font(32, "bold"), fill=TEXT)

    draw_wrapped(
        draw,
        (MARGIN, 878),
        f"{snapshot.trend} · {comment}",
        font(26),
        TEXT,
        CARD_SIZE - MARGIN * 2,
        38,
        max_lines=2,
    )
    _footer(draw, f"기준일 {snapshot.latest_date.isoformat()} · ECB 고시환율")
    return image


LEGEND = (
    ("RSI(14)", "70 위는 과매수, 30 아래는 과매도로 본다"),
    ("연율 변동성", "최근 20일 흔들림을 1년치로 환산한 값"),
    ("이동평균", "20일선이 60일선 위면 상승 배열"),
)


LEGEND_BOTTOM = CARD_SIZE - 136  # 푸터 구분선 위에 남기는 여백


def _draw_legend(draw: ImageDraw.ImageDraw, min_top: int) -> None:
    """카드에 쓰인 지표를 한 줄씩 풀어 준다. 푸터 바로 위에 붙여 그린다.

    본문이 길어 자리가 안 나면 그리지 않는다. 범례는 있으면 좋은 정보이지
    본문을 밀어낼 만한 것은 아니다.
    """
    height = 56 + len(LEGEND) * 44
    top = LEGEND_BOTTOM - height
    if top < min_top:
        return
    draw.rounded_rectangle(
        [MARGIN, top, CARD_SIZE - MARGIN, top + height], radius=20, fill=SURFACE
    )
    draw.text((MARGIN + 28, top + 18), "지표 한 줄 설명", font=font(24, "bold"), fill=MUTED)
    row_y = top + 56
    for name, description in LEGEND:
        draw.text((MARGIN + 28, row_y), name, font=font(23, "bold"), fill=ACCENT)
        draw.text((MARGIN + 220, row_y), description, font=font(23), fill=MUTED)
        row_y += 44


def outlook_card(commentary: Commentary) -> Image.Image:
    image, draw = new_card()
    draw.rectangle([0, 0, CARD_SIZE, 12], fill=ACCENT)

    draw.text((MARGIN, 100), "OUTLOOK", font=font(30, "bold"), fill=ACCENT)
    draw.text((MARGIN, 148), "이번 주 정리와 관전 포인트", font=font(52, "bold"), fill=TEXT)

    y = draw_wrapped(
        draw,
        (MARGIN, 258),
        commentary.summary,
        font(32),
        TEXT,
        CARD_SIZE - MARGIN * 2,
        50,
        max_lines=6,
    )

    y += 40
    draw.text((MARGIN, y), "다음 주 체크리스트", font=font(28, "bold"), fill=ACCENT)
    y += 56
    for index, point in enumerate(commentary.watchpoints, start=1):
        draw.ellipse([MARGIN, y + 8, MARGIN + 34, y + 42], fill=SURFACE, outline=ACCENT, width=2)
        number = str(index)
        draw.text(
            (MARGIN + 17 - text_width(draw, number, font(22, "bold")) // 2, y + 14),
            number,
            font=font(22, "bold"),
            fill=ACCENT,
        )
        y = draw_wrapped(
            draw,
            (MARGIN + 54, y + 6),
            point,
            font(28),
            TEXT,
            CARD_SIZE - MARGIN * 2 - 54,
            42,
            max_lines=2,
        )
        y += 22

    _draw_legend(draw, y + 30)

    note = "해설 생성: Claude" if commentary.source == "claude" else "해설 생성: 지표 규칙 기반"
    _footer(draw, note)
    return image


def render_all(
    snapshots: list[PairSnapshot],
    commentary: Commentary,
    issued: dt.date,
    out_dir: Path,
) -> list[Path]:
    """카드 전체를 파일로 떨어뜨리고 경로 목록을 순서대로 반환한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    def save(image: Image.Image, name: str) -> None:
        path = out_dir / name
        image.save(path, "PNG", optimize=True)
        paths.append(path)

    save(cover_card(snapshots, commentary, issued), "01-cover.png")
    for index, snapshot in enumerate(snapshots, start=2):
        comment = commentary.pair_comments.get(snapshot.code, snapshot.trend)
        save(pair_card(snapshot, comment), f"{index:02d}-{snapshot.code.lower()}.png")
    save(outlook_card(commentary), f"{len(snapshots) + 2:02d}-outlook.png")
    return paths
