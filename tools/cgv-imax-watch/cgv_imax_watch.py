#!/usr/bin/env python3
"""CGV IMAX 예매 오픈 감시기.

특정 극장(기본: 용산아이파크몰 0013, 영등포 0059)의 상영 시간표를 주기적으로
확인해서, 원하는 영화(기본: 오디세이)의 IMAX 회차가 "처음 나타나는 순간"을
잡아 알림을 보낸다. 예매 오픈 = 없던 날짜에 회차가 생기는 것이므로,
오늘부터 N일치를 훑어 새로 등장한 (극장, 날짜)만 알린다.

표준 라이브러리만 사용한다. pip install 필요 없음.

  python3 cgv_imax_watch.py --probe          # 어떤 엔드포인트가 살아있는지 진단
  python3 cgv_imax_watch.py --selftest       # 네트워크 없이 탐지 로직 검증
  python3 cgv_imax_watch.py --once           # 1회 확인 (cron/launchd 용)
  python3 cgv_imax_watch.py                  # 상주하며 주기적으로 확인
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------------

THEATERS = {
    "0013": "CGV 용산아이파크몰",
    "0059": "CGV 영등포 (타임스퀘어)",
}

# 상영시간표를 담고 있을 법한 후보 엔드포인트. {theater}=극장코드, {date}=YYYYMMDD.
# CGV가 사이트를 개편하면서 어떤 게 살아있는지 달라질 수 있어, --probe 로 먼저
# 확인한 뒤 --endpoint 로 고정해 쓰는 것을 권한다.
CANDIDATE_ENDPOINTS = [
    "https://www.cgv.co.kr/common/showtimes/iframeTheater.aspx"
    "?areacode=01&theatercode={theater}&date={date}",
    "http://www.cgv.co.kr/common/showtimes/iframeTheater.aspx"
    "?areacode=01&theatercode={theater}&date={date}",
    "https://m.cgv.co.kr/WebApp/ScheduleV4/schedule.aspx?tc={theater}&date={date}",
]

BOOKING_URLS = {
    "0013": "https://www.cgv.co.kr/ticket/?theaterCode=0013",
    "0059": "https://www.cgv.co.kr/ticket/?theaterCode=0059",
}
BOOKING_FALLBACK = "https://cgv.co.kr/cnm/movieBook/cinema"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# 서버에 부담 주지 않도록 최소 간격을 둔다.
MIN_INTERVAL_SEC = 30

TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
WS_RE = re.compile(r"\s+")
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")
SEAT_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def fetch(url: str, timeout: float = 15.0, retries: int = 3) -> tuple[int, str]:
    """URL을 가져와 (status, body) 반환. 실패하면 (0, 에러문자열)."""
    last = ""
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Referer": "https://www.cgv.co.kr/",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset()
                for enc in filter(None, [charset, "utf-8", "euc-kr", "cp949"]):
                    try:
                        return resp.status, raw.decode(enc)
                    except (UnicodeDecodeError, LookupError):
                        continue
                return resp.status, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, f"HTTP {exc.code} {exc.reason}"
        except Exception as exc:  # 네트워크 계열은 재시도할 가치가 있다
            last = f"{type(exc).__name__}: {exc}"
            if attempt < retries - 1:
                time.sleep(2 ** attempt + random.random())
    return 0, last


# --------------------------------------------------------------------------
# 탐지
# --------------------------------------------------------------------------

def to_text(body: str) -> str:
    """HTML이든 JSON이든 하나의 평문으로 눌러서 문자열 검색이 가능하게 만든다.

    DOM 선택자 대신 평문을 쓰는 이유: 마크업이 개편돼도 '오디세이'와 'IMAX'
    라는 글자는 남기 때문에, 개편에 훨씬 덜 부서진다.
    """
    text = SCRIPT_RE.sub(" ", body)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return WS_RE.sub(" ", text).strip()


def squash(s: str) -> str:
    """비교용 정규화: 공백 제거 + 소문자."""
    return re.sub(r"\s+", "", s).lower()


def find_matches(body: str, title: str, screen: str, window: int = 400) -> list[str]:
    """제목이 등장하는 구간 주변에 상영관 표기(IMAX)가 같이 있는지 본다.

    페이지 어딘가에 IMAX 배너가 있다고 해서 그 영화의 IMAX 회차가 열린 건
    아니므로, 제목 근처(window 글자)로 범위를 좁혀서 판단한다.
    반환값은 매치된 구간의 요약(상영 시각 등) 목록.
    """
    text = to_text(body)
    flat = squash(text)
    # squash 된 인덱스를 원문으로 되돌리기 위한 매핑
    index_map: list[int] = []
    for i, ch in enumerate(text):
        if not ch.isspace():
            index_map.append(i)

    needle = squash(title)
    screen_needle = squash(screen)
    if not needle:
        return []

    hits: list[str] = []
    start = 0
    while True:
        pos = flat.find(needle, start)
        if pos < 0:
            break
        start = pos + len(needle)
        if pos >= len(index_map):
            break
        origin = index_map[pos]
        chunk = text[max(0, origin - window // 2): origin + window]
        if screen_needle and screen_needle not in squash(chunk):
            continue
        times = sorted({m.group(0) for m in TIME_RE.finditer(chunk)})
        seats = SEAT_RE.search(chunk)
        summary = ", ".join(times[:12]) if times else chunk[:120]
        if seats:
            summary += f"  (잔여석 표기 {seats.group(0)})"
        hits.append(summary)
    return hits


# --------------------------------------------------------------------------
# 상태
# --------------------------------------------------------------------------

def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"seen": {}}


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 알림
# --------------------------------------------------------------------------

def notify(subject: str, body: str, quiet: bool = False) -> None:
    """콘솔 + (환경변수가 있으면) 텔레그램 / 웹훅 / macOS 알림센터."""
    line = f"\n{'=' * 60}\n{subject}\n{body}\n{'=' * 60}"
    print(line, flush=True)
    if not quiet:
        sys.stdout.write("\a\a\a")  # 터미널 벨
        sys.stdout.flush()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        payload = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": f"{subject}\n{body}", "disable_web_page_preview": "false"}
        ).encode()
        try:
            urllib.request.urlopen(
                urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage", data=payload
                ),
                timeout=10,
            ).read()
        except Exception as exc:
            print(f"[warn] 텔레그램 전송 실패: {exc}", file=sys.stderr)

    webhook = os.environ.get("WEBHOOK_URL")  # Discord / Slack 공통
    if webhook:
        content = f"**{subject}**\n{body}"
        payload = json.dumps({"content": content, "text": content}).encode()
        try:
            urllib.request.urlopen(
                urllib.request.Request(
                    webhook, data=payload, headers={"Content-Type": "application/json"}
                ),
                timeout=10,
            ).read()
        except Exception as exc:
            print(f"[warn] 웹훅 전송 실패: {exc}", file=sys.stderr)

    if sys.platform == "darwin":
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'display notification {} with title {} sound name "Glass"'.format(
                        json.dumps(body.split("\n")[0]), json.dumps(subject)
                    ),
                ],
                check=False,
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass


# --------------------------------------------------------------------------
# 확인 루프
# --------------------------------------------------------------------------

def check_once(args, state: dict) -> list[str]:
    """전 극장 × 전 날짜를 한 바퀴 돌고, 새로 발견한 항목의 알림 문구를 반환."""
    today = dt.date.today()
    endpoints = [args.endpoint] if args.endpoint else CANDIDATE_ENDPOINTS
    fresh: list[str] = []

    for theater in args.theaters:
        name = THEATERS.get(theater, f"CGV {theater}")
        for offset in range(args.days):
            date = today + dt.timedelta(days=offset)
            datestr = date.strftime("%Y%m%d")
            body = ""
            used = ""
            for template in endpoints:
                url = template.format(theater=theater, date=datestr)
                status, payload = fetch(url, timeout=args.timeout)
                if status == 200 and len(payload) > 200:
                    body, used = payload, url
                    break
            if not body:
                if args.verbose:
                    print(f"[skip] {name} {datestr}: 응답 없음", file=sys.stderr)
                continue

            hits = find_matches(body, args.title, args.screen)
            key = f"{theater}|{datestr}"
            if hits:
                if not state["seen"].get(key):
                    state["seen"][key] = {
                        "found_at": dt.datetime.now().isoformat(timespec="seconds"),
                        "times": hits,
                    }
                    booking = BOOKING_URLS.get(theater, BOOKING_FALLBACK)
                    fresh.append(
                        f"{name} · {date:%Y-%m-%d (%a)}\n"
                        f"  회차: {' / '.join(hits[:3])}\n"
                        f"  예매: {booking}"
                    )
                elif args.verbose:
                    print(f"[ok] {name} {datestr}: 이미 알림 완료", file=sys.stderr)
            elif args.verbose:
                print(f"[--] {name} {datestr}: 없음 ({used})", file=sys.stderr)

            time.sleep(args.gap)

    return fresh


def run_probe(args) -> int:
    """어떤 후보 엔드포인트가 실제로 시간표를 주는지 진단한다."""
    today = dt.date.today().strftime("%Y%m%d")
    theater = args.theaters[0]
    print(f"진단 대상: {THEATERS.get(theater, theater)} ({theater}), 날짜 {today}\n")
    ok_any = False
    for template in CANDIDATE_ENDPOINTS:
        url = template.format(theater=theater, date=today)
        status, body = fetch(url, timeout=args.timeout, retries=1)
        verdict = []
        if status != 200:
            verdict.append("응답 실패")
        else:
            text = to_text(body)
            if len(body) < 500:
                verdict.append("본문이 너무 짧음(빈 껍데기 의심)")
            if TIME_RE.search(text):
                verdict.append("상영 시각 패턴 있음")
                ok_any = True
            else:
                verdict.append("상영 시각 패턴 없음")
            if "imax" in squash(text):
                verdict.append("IMAX 문자열 있음")
            if squash("자바스크립트") in squash(text) or "__NEXT_DATA__" in body:
                verdict.append("JS 렌더링 페이지로 보임")
        print(f"  [{status or 'ERR'}] {url}")
        print(f"        {len(body):>8,} bytes · {', '.join(verdict)}")
        if status == 200 and args.verbose:
            print(f"        미리보기: {to_text(body)[:200]}")
    print()
    if ok_any:
        print("→ '상영 시각 패턴 있음'이 뜬 URL을 --endpoint 로 고정해 쓰면 가장 안정적입니다.")
        return 0
    print(
        "→ 살아있는 엔드포인트가 없습니다. 브라우저에서 CGV 예매 페이지를 열고\n"
        "  개발자도구 Network 탭에서 시간표를 담아오는 요청 URL을 찾아\n"
        "  --endpoint 'https://.../{theater}/{date}' 형태로 넘겨주세요."
    )
    return 1


def run_selftest() -> int:
    """네트워크 없이 탐지 로직만 검증한다."""
    open_html = """
    <div class="theater"><div class="type">IMAX 2D</div>
      <div class="info-movie"><strong>오디세이</strong></div>
      <div class="time"><a>09:30</a><a>13:20</a><a>19:40</a></div>
      <span class="seat">88 / 403</span></div>
    """
    closed_html = """
    <div class="banner">IMAX 관에서 만나보세요</div>
    <div class="theater"><div class="type">2D</div>
      <div class="info-movie"><strong>다른영화</strong></div>
      <div class="time"><a>11:00</a></div></div>
    """
    decoy_html = """
    <div class="banner">IMAX 특별관 안내</div>
    """ + ("<p>메뉴</p>" * 200) + """
    <div class="theater"><div class="type">2D</div>
      <div class="info-movie"><strong>오디세이</strong></div>
      <div class="time"><a>15:00</a></div></div>
    """
    json_payload = json.dumps(
        {"screens": [{"screenType": "IMAX", "movieNm": "오디세이", "startTime": "20:10"}]},
        ensure_ascii=False,
    )

    cases = [
        ("IMAX 회차 열림(HTML)", open_html, True),
        ("다른 영화만 있음", closed_html, False),
        ("멀리 있는 IMAX 배너에 낚이지 않음", decoy_html, False),
        ("JSON 응답", json_payload, True),
    ]
    failed = 0
    for label, payload, expected in cases:
        got = bool(find_matches(payload, "오디세이", "IMAX"))
        mark = "PASS" if got == expected else "FAIL"
        if got != expected:
            failed += 1
        print(f"  [{mark}] {label} (기대 {expected}, 실제 {got})")
    # 공백/대소문자 변형도 잡아야 한다
    variant = '<div>i m a x</div><div>오디 세이</div>'
    if not find_matches(variant, "오디세이", "IMAX", window=200):
        print("  [FAIL] 공백 변형 매칭")
        failed += 1
    else:
        print("  [PASS] 공백 변형 매칭")
    print("\n셀프테스트: " + ("전부 통과" if not failed else f"{failed}건 실패"))
    return 1 if failed else 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="CGV IMAX 예매 오픈 감시기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--title", default="오디세이", help="감시할 영화 제목 (기본: 오디세이)")
    p.add_argument("--screen", default="IMAX", help="상영관 종류 (기본: IMAX)")
    p.add_argument(
        "--theaters",
        default="0013,0059",
        help="극장 코드 쉼표 구분 (기본: 0013 용산, 0059 영등포)",
    )
    p.add_argument("--days", type=int, default=30, help="오늘부터 며칠치를 볼지 (기본 30)")
    p.add_argument("--interval", type=int, default=300, help="확인 주기(초, 기본 300)")
    p.add_argument("--gap", type=float, default=0.4, help="요청 사이 간격(초, 기본 0.4)")
    p.add_argument("--timeout", type=float, default=15.0, help="요청 타임아웃(초)")
    p.add_argument("--endpoint", help="시간표 URL 템플릿. {theater}, {date} 사용")
    p.add_argument(
        "--state",
        default=os.path.expanduser("~/.cgv_imax_watch.json"),
        help="이미 알린 항목을 기억할 파일",
    )
    p.add_argument("--once", action="store_true", help="한 번만 확인하고 종료 (cron 용)")
    p.add_argument("--probe", action="store_true", help="엔드포인트 생존 진단")
    p.add_argument("--selftest", action="store_true", help="네트워크 없이 탐지 로직 검증")
    p.add_argument("--quiet", action="store_true", help="터미널 벨 끄기")
    p.add_argument("--verbose", "-v", action="store_true", help="확인 과정 출력")
    args = p.parse_args(argv)
    args.theaters = [t.strip() for t in args.theaters.split(",") if t.strip()]
    args.interval = max(args.interval, MIN_INTERVAL_SEC)
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.selftest:
        return run_selftest()
    if args.probe:
        return run_probe(args)

    state = load_state(args.state)
    label = f"{args.title} {args.screen}"
    targets = ", ".join(THEATERS.get(t, t) for t in args.theaters)

    while True:
        started = dt.datetime.now()
        try:
            fresh = check_once(args, state)
        except KeyboardInterrupt:
            print("\n중단합니다.")
            return 0
        except Exception as exc:
            print(f"[error] 확인 중 오류: {exc}", file=sys.stderr)
            fresh = []

        if fresh:
            save_state(args.state, state)
            notify(
                f"🎬 {label} 예매 오픈 감지 — {len(fresh)}건",
                "\n\n".join(fresh),
                quiet=args.quiet,
            )
        else:
            print(
                f"[{started:%m-%d %H:%M:%S}] {targets} · {label} 아직 없음"
                f" (다음 확인 {args.interval}초 후)",
                flush=True,
            )

        if args.once:
            return 0
        try:
            time.sleep(args.interval + random.uniform(0, args.interval * 0.1))
        except KeyboardInterrupt:
            print("\n중단합니다.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
