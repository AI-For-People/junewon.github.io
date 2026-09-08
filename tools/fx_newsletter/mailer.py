"""카드 PNG를 담은 메일을 조립하고 발송한다.

발송 수단은 두 가지다.

- Resend (권장): HTTPS API. 키가 '보내기' 권한만 가지므로 받은편지함을
  읽을 수 없다.
- Gmail SMTP: 앱 비밀번호로 로그인. 추가 가입은 없지만 그 비밀번호는
  메일 읽기 권한까지 포함한다.

둘 다 같은 본문(HTML + 텍스트)과 같은 카드 이미지를 쓴다.
"""

from __future__ import annotations

import base64
import datetime as dt
import logging
import mimetypes
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

import requests

from .commentary import DISCLAIMER, Commentary
from .config import MailConfig
from .indicators import PairSnapshot

log = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
RESEND_TIMEOUT = 60


class SendError(RuntimeError):
    """메일을 보내지 못했을 때."""


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
    image_srcs: list[str],
) -> str:
    """카드 이미지를 세로로 이어 붙인 HTML.

    image_srcs가 비어 있으면 이미지 태그 없이 표만 넣는다. 이 경우 카드는
    첨부파일로만 전달된다.
    """
    images = "".join(
        f'<img src="{src}" width="600" '
        'style="display:block;width:100%;max-width:600px;height:auto;'
        'border-radius:12px;margin:0 0 16px 0;" alt="주간 환율 카드뉴스">'
        for src in image_srcs
    )
    note = (
        ""
        if image_srcs
        else '<p style="color:#8A99B8;font-size:13px;margin:0 0 16px;">'
        "카드 이미지는 이 메일의 첨부파일로 들어 있습니다.</p>"
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
  {note}{images}
  <p style="color:#8A99B8;font-size:12px;line-height:1.6;margin-top:24px;">
    출처: ECB 기준환율 (Frankfurter API)<br>{DISCLAIMER}
  </p>
</div></body></html>"""


# --------------------------------------------------------------------------
# Resend
# --------------------------------------------------------------------------

def _cid_for(path: Path) -> str:
    return f"card-{path.stem}"


def resend_payload(
    config: MailConfig,
    snapshots: list[PairSnapshot],
    commentary: Commentary,
    issued: dt.date,
    card_paths: list[Path],
    inline: bool,
) -> dict:
    """Resend API 요청 본문.

    inline=True면 첨부에 content_id를 달고 HTML에서 cid:로 참조한다.
    inline=False면 순수 첨부로만 보낸다.
    """
    attachments = []
    for path in card_paths:
        mime, _ = mimetypes.guess_type(path.name)
        attachment = {
            "filename": path.name,
            "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "content_type": mime or "image/png",
        }
        if inline:
            attachment["content_id"] = _cid_for(path)
        attachments.append(attachment)

    srcs = [f"cid:{_cid_for(p)}" for p in card_paths] if inline else []
    return {
        "from": config.from_header,
        "to": list(config.recipients),
        "subject": subject_line(commentary, issued),
        "html": html_body(snapshots, commentary, issued, srcs),
        "text": plain_body(snapshots, commentary, issued),
        "attachments": attachments,
    }


def _post_resend(api_key: str, payload: dict) -> requests.Response:
    return requests.post(
        RESEND_ENDPOINT,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=RESEND_TIMEOUT,
    )


def send_via_resend(
    config: MailConfig,
    snapshots: list[PairSnapshot],
    commentary: Commentary,
    issued: dt.date,
    card_paths: list[Path],
    post=_post_resend,
) -> None:
    """Resend로 보낸다.

    인라인 이미지(content_id)를 먼저 시도하고, API가 요청을 거부하면
    첨부 전용으로 한 번만 다시 보낸다. 카드가 본문에 안 박히는 것보다
    메일이 아예 안 가는 쪽이 나쁘기 때문이다.
    """
    for inline in (True, False):
        payload = resend_payload(config, snapshots, commentary, issued, card_paths, inline)
        response = post(config.resend_api_key, payload)

        if response.status_code < 300:
            how = "본문 인라인" if inline else "첨부"
            log.info("Resend 발송 완료(%s) -> %s", how, ", ".join(config.recipients))
            return

        body = response.text[:500]
        # 4xx는 요청 형식 문제다. 인라인 시도였다면 첨부 방식으로 물러선다.
        if inline and 400 <= response.status_code < 500:
            log.warning(
                "Resend가 인라인 이미지 요청을 거부했습니다(%s: %s). 첨부 방식으로 재시도합니다.",
                response.status_code,
                body,
            )
            continue
        raise SendError(f"Resend 발송 실패 ({response.status_code}): {body}")


# --------------------------------------------------------------------------
# Gmail SMTP
# --------------------------------------------------------------------------

def build_message(
    config: MailConfig,
    snapshots: list[PairSnapshot],
    commentary: Commentary,
    issued: dt.date,
    card_paths: list[Path],
) -> EmailMessage:
    """SMTP용 MIME 메시지. 카드는 cid로 본문에 삽입한다."""
    message = EmailMessage()
    message["Subject"] = subject_line(commentary, issued)
    message["From"] = config.from_header
    message["To"] = ", ".join(config.recipients)

    message.set_content(plain_body(snapshots, commentary, issued))

    cids = [make_msgid(domain="fx.newsletter") for _ in card_paths]
    srcs = [f"cid:{cid[1:-1]}" for cid in cids]
    message.add_alternative(html_body(snapshots, commentary, issued, srcs), subtype="html")

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


def send_via_smtp(config: MailConfig, message: EmailMessage) -> None:
    if config.port == 587:
        with smtplib.SMTP(config.host, config.port, timeout=60) as server:
            server.starttls()
            server.login(config.user, config.password)
            server.send_message(message)
    else:
        with smtplib.SMTP_SSL(config.host, config.port, timeout=60) as server:
            server.login(config.user, config.password)
            server.send_message(message)
    log.info("SMTP 발송 완료 -> %s", ", ".join(config.recipients))


# --------------------------------------------------------------------------

def send(
    config: MailConfig,
    snapshots: list[PairSnapshot],
    commentary: Commentary,
    issued: dt.date,
    card_paths: list[Path],
) -> None:
    """설정된 수단으로 메일을 보낸다."""
    missing = config.validate()
    if missing:
        raise SendError("메일 설정이 비어 있습니다: " + ", ".join(missing))

    if config.provider == "resend":
        send_via_resend(config, snapshots, commentary, issued, card_paths)
    else:
        send_via_smtp(config, build_message(config, snapshots, commentary, issued, card_paths))
