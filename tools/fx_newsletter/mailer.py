"""카드 PNG를 본문에 삽입한 메일을 Gmail SMTP로 보낸다."""

from __future__ import annotations

import datetime as dt
import logging
import mimetypes
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from .commentary import DISCLAIMER, Commentary
from .config import MailConfig
from .indicators import PairSnapshot

log = logging.getLogger(__name__)


def subject_line(commentary: Commentary, issued: dt.date) -> str:
    return f"[주간 환율] {issued.strftime('%m/%d')} · {commentary.headline}"


def _sign(value: float) -> str:
    return f"{'+' if value > 0 else ''}{value:,.2f}"


def plain_body(snapshots: list[PairSnapshot], commentary: Commentary, issued: dt.date) -> str:
    """이미지를 막아 둔 클라이언트에서도 내용이 전달되도록 하는 텍스트 대체본."""
    lines = [
        f"주간 환율 브리핑 — {issued.strftime('%Y년 %m월 %d일')}",
        "",
        commentary.headline,
        "",
        commentary.summary,
        "",
        "[통화별 현황]",
    ]
    for s in snapshots:
        change = f"{_sign(s.week.delta)}원 ({_sign(s.week.pct)}%)" if s.week else "비교 불가"
        lines.append(f"- {s.display_name}: {s.latest:,.2f}원 / 주간 {change} / {s.trend}")
        comment = commentary.pair_comments.get(s.code)
        if comment:
            lines.append(f"  {comment}")

    lines += ["", "[다음 주 체크리스트]"]
    lines += [f"{i}. {w}" for i, w in enumerate(commentary.watchpoints, start=1)]
    lines += [
        "",
        f"기준일: {snapshots[0].latest_date.isoformat() if snapshots else '-'}",
        "출처: ECB 기준환율 (Frankfurter API)",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def html_body(
    snapshots: list[PairSnapshot],
    commentary: Commentary,
    issued: dt.date,
    cids: list[str],
) -> str:
    """카드 이미지를 세로로 이어 붙인 단순한 HTML. 메일 클라이언트 호환성을 위해 인라인 스타일만 쓴다."""
    images = "".join(
        f'<img src="cid:{cid[1:-1]}" width="600" '
        'style="display:block;width:100%;max-width:600px;height:auto;'
        'border-radius:12px;margin:0 0 16px 0;" alt="주간 환율 카드뉴스">'
        for cid in cids
    )
    rows = "".join(
        f'<tr>'
        f'<td style="padding:8px 0;color:#8A99B8;font-size:14px;">{s.display_name}</td>'
        f'<td style="padding:8px 0;color:#F2F5FA;font-size:14px;text-align:right;">{s.latest:,.2f}원</td>'
        f'<td style="padding:8px 0;font-size:14px;text-align:right;'
        f'color:{"#FF5C5C" if s.direction == "up" else "#4D8DFF" if s.direction == "down" else "#8A99B8"};">'
        f'{_sign(s.week.pct) + "%" if s.week else "-"}</td>'
        f'</tr>'
        for s in snapshots
    )
    return f"""<!DOCTYPE html>
<html lang="ko"><body style="margin:0;padding:24px 12px;background:#0B1220;">
<div style="max-width:600px;margin:0 auto;font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
  <p style="color:#FFC24B;font-size:13px;letter-spacing:2px;margin:0 0 4px;">WEEKLY FX BRIEF</p>
  <h1 style="color:#F2F5FA;font-size:24px;margin:0 0 6px;">주간 환율 브리핑</h1>
  <p style="color:#8A99B8;font-size:14px;margin:0 0 20px;">{issued.strftime('%Y년 %m월 %d일')} 발행</p>
  <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">{rows}</table>
  {images}
  <p style="color:#8A99B8;font-size:12px;line-height:1.6;margin-top:24px;">
    출처: ECB 기준환율 (Frankfurter API)<br>{DISCLAIMER}
  </p>
</div></body></html>"""


def build_message(
    config: MailConfig,
    snapshots: list[PairSnapshot],
    commentary: Commentary,
    issued: dt.date,
    card_paths: list[Path],
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject_line(commentary, issued)
    message["From"] = f"{config.sender_name} <{config.user}>"
    message["To"] = ", ".join(config.recipients)

    message.set_content(plain_body(snapshots, commentary, issued))

    cids = [make_msgid(domain="fx.newsletter") for _ in card_paths]
    message.add_alternative(html_body(snapshots, commentary, issued, cids), subtype="html")

    # add_alternative 뒤의 마지막 파트가 HTML 파트다. 여기에 이미지를 related로 붙인다.
    html_part = message.get_payload()[-1]
    for path, cid in zip(card_paths, cids):
        mime, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (mime or "image/png").split("/", 1)
        html_part.add_related(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            cid=cid,
            filename=path.name,
        )
    return message


def send(config: MailConfig, message: EmailMessage) -> None:
    missing = config.validate()
    if missing:
        raise RuntimeError("메일 설정이 비어 있습니다: " + ", ".join(missing))

    if config.port == 587:
        with smtplib.SMTP(config.host, config.port, timeout=60) as server:
            server.starttls()
            server.login(config.user, config.password)
            server.send_message(message)
    else:
        with smtplib.SMTP_SSL(config.host, config.port, timeout=60) as server:
            server.login(config.user, config.password)
            server.send_message(message)
    log.info("메일 발송 완료 -> %s", ", ".join(config.recipients))
