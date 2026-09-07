#!/usr/bin/env python3
"""CGV IMAX 예매 오픈 감시기.

특정 극장(기본: 용산아이파크몰 0013, 영등포 0059)의 상영 시간표를 주기적으로
확인해서, 원하는 영화(기본: 오디세이)의 IMAX 회차가 "처음 나타나는 순간"을
잡아 알림을 보낸다. 예매 오픈 = 없던 날짜에 회차가 생기는 것이므로,
오늘부터 N일치를 훑어 새로 등장한 (극장, 날짜)만 알린다.

표준 라이브러리만 사용한다. pip install 필요 없음.

  python3 cgv_imax_watch.py --probe          # 엔드포인트 생존 진단
  python3 cgv_imax_watch.py --selftest       # 네트워크 없이 탐지 로직 검증
  python3 cgv_imax_watch.py --dump out.json  # 응답 원문 저장 (구조 확인용)
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

# 개편된 CGV 사이트가 실제로 쓰는 시간표 API. 브라우저 개발자도구에서
# 확인한 것으로, GET + JSON 응답이다. {theater}=siteNo, {date}=YYYYMMDD.
CGV_API = (
    "https://cgv.co.kr/api/v1/booking/searchMovScnInfo"
    "?coCd={co}&siteNo={theater}&scnYmd={date}&rtctlScopCd={scope}"
)

# API가 막히거나 바뀌었을 때를 대비한 구 엔드포인트들.
LEGACY_ENDPOINTS = [
    "https://www.cgv.co.kr/common/showtimes/iframeTheater.aspx"
    "?areacode=01&theatercode={theater}&date={date}",
    "https://m.cgv.co.kr/WebApp/ScheduleV4/schedule.aspx?tc={theater}&date={date}",
]

BOOKING_URL = "https://cgv.co.kr/cnm/movieBook/cinema"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)

# 서버에 부담 주지 않도록 최소 간격을 둔다.
MIN_INTERVAL_SEC = 30

# 안내 문구에 쓸 실행 명령 (Windows는 python3 가 없다).
PY_CMD = "python" if sys.platform == "win32" else "python3"

TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
WS_RE = re.compile(r"\s+")
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")
HHMM_RE = re.compile(r"^([01]\d|2[0-3])[0-5]\d$")  # "1930" 같은 API 표기
SEAT_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def build_headers(cookie: str | None) -> dict:
    """브라우저가 보내는 것과 같은 헤더. Cloudflare 앞단을 통과하기 위함."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": BOOKING_URL,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def fetch(
    url: str, timeout: float = 15.0, retries: int = 3, cookie: str | None = None
) -> tuple[int, str]:
    """URL을 가져와 (status, body) 반환. 실패하면 (0, 에러문자열)."""
    last = ""
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=build_headers(cookie))
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
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            return exc.code, f"HTTP {exc.code} {exc.reason} {body}"
        except Exception as exc:  # 네트워크 계열은 재시도할 가치가 있다
            last = f"{type(exc).__name__}: {exc}"
            if attempt < retries - 1:
                time.sleep(2 ** attempt + random.random())
    return 0, last


# --------------------------------------------------------------------------
# 탐지
# --------------------------------------------------------------------------

def squash(s: str) -> str:
    """비교용 정규화: 공백 제거 + 소문자."""
    return re.sub(r"\s+", "", s).lower()


def to_text(body: str) -> str:
    """HTML이든 JSON이든 하나의 평문으로 눌러서 문자열 검색이 가능하게 만든다."""
    text = SCRIPT_RE.sub(" ", body)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return WS_RE.sub(" ", text).strip()


def parse_json(body: str):
    """JSON이면 파싱해서 돌려주고, 아니면 None."""
    stripped = body.lstrip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def _scalars(node) -> str:
    """dict/list의 '직속' 스칼라 값만 이어붙인다 (자식 컨테이너는 제외)."""
    values = node.values() if isinstance(node, dict) else node
    return " ".join(str(v) for v in values if isinstance(v, (str, int, float)))


def _collect_times(node, out: set) -> None:
    """하위 트리에서 상영 시각처럼 보이는 값을 긁어모은다."""
    if isinstance(node, dict):
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        text = str(node)
        if HHMM_RE.match(text):
            out.add(f"{text[:2]}:{text[2:]}")
        else:
            for m in TIME_RE.finditer(text):
                out.add(m.group(0))
        return
    for child in children:
        _collect_times(child, out)


def find_matches_json(data, titles: list[str], screen: str) -> list[tuple[str, list]]:
    """JSON을 구조적으로 훑는다.

    어떤 객체의 '조상 체인 + 자기 자신'의 스칼라 값들 안에 제목과 상영관
    종류가 함께 있으면 매치로 본다. 필드 이름을 몰라도 되고, 영화가 상영관
    아래에 있든 그 반대든 상관없이 잡힌다.
    """
    needles = [squash(t) for t in titles if t.strip()]
    screen_needle = squash(screen)
    hits: list[tuple[str, list]] = []
    seen: set[int] = set()

    def walk(node, ancestor_ctx: str) -> None:
        if not isinstance(node, (dict, list)):
            return
        ctx = ancestor_ctx + " " + squash(_scalars(node))
        matched = any(n in ctx for n in needles)
        if matched and screen_needle in ctx and id(node) not in seen:
            seen.add(id(node))
            times: set[str] = set()
            _collect_times(node, times)
            ordered = sorted(times)
            summary = ", ".join(ordered[:12]) if ordered else "시간 미확인"
            hits.append((summary, ordered))
            return  # 자식까지 중복해서 담지 않는다
        children = node.values() if isinstance(node, dict) else node
        for child in children:
            walk(child, ctx)

    walk(data, "")
    return hits


def find_matches_text(
    body: str, titles: list[str], screen: str, window: int = 400
) -> list[tuple[str, list]]:
    """HTML용. 제목이 등장하는 구간 주변에 상영관 표기가 같이 있는지 본다.

    페이지 어딘가에 IMAX 배너가 있다고 해서 그 영화의 IMAX 회차가 열린 건
    아니므로, 제목 근처(window 글자)로 범위를 좁혀서 판단한다.
    """
    text = to_text(body)
    flat = squash(text)
    index_map = [i for i, ch in enumerate(text) if not ch.isspace()]

    screen_needle = squash(screen)
    hits: list[tuple[str, list]] = []
    for title in titles:
        needle = squash(title)
        if not needle:
            continue
        start = 0
        while True:
            pos = flat.find(needle, start)
            if pos < 0 or pos >= len(index_map):
                break
            start = pos + len(needle)
            origin = index_map[pos]
            chunk = text[max(0, origin - window // 2): origin + window]
            if screen_needle and screen_needle not in squash(chunk):
                continue
            times = sorted({m.group(0) for m in TIME_RE.finditer(chunk)})
            summary = ", ".join(times[:12]) if times else chunk[:120]
            seats = SEAT_RE.search(chunk)
            if seats:
                summary += f"  (잔여석 표기 {seats.group(0)})"
            hits.append((summary, times))
    return hits


def find_matches(body: str, titles, screen: str, window: int = 400) -> list[tuple[str, list]]:
    """JSON이면 구조적으로, 아니면 평문 근접 검색으로 판단한다.

    (요약 문자열, 상영 시각 목록) 쌍의 리스트를 돌려준다.

    titles 는 같은 영화의 표기 후보 목록이다. CGV는 같은 작품을 한글로도
    영어로도('오디세이' / 'The Odyssey') 담고, 상영관 표기를 제목 앞에
    붙이기도 하므로('(IMAX LASER 2D)The Odyssey') 하나라도 걸리면 매치로 본다.
    """
    if isinstance(titles, str):
        titles = [titles]
    data = parse_json(body)
    if data is not None:
        hits = find_matches_json(data, titles, screen)
        if hits:
            return hits
        # 구조가 예상과 다를 수 있으니 평문으로 한 번 더 (\uXXXX 해제 후)
        return find_matches_text(
            json.dumps(data, ensure_ascii=False), titles, screen, window
        )
    return find_matches_text(body, titles, screen, window)


# --------------------------------------------------------------------------
# 상태
# --------------------------------------------------------------------------

def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
            state.setdefault("seen", {})
            return state
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
    """콘솔 + (환경변수가 있으면) 텔레그램 / 웹훅 + macOS 알림센터."""
    print(f"\n{'=' * 60}\n{subject}\n{body}\n{'=' * 60}", flush=True)
    if not quiet:
        sys.stdout.write("\a\a\a")  # 터미널 벨
        sys.stdout.flush()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        payload = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": f"{subject}\n{body}"}
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
# 조회
# --------------------------------------------------------------------------

def looks_usable(body: str) -> bool:
    """시간표가 실제로 담긴 응답인지 확인한다.

    구 .aspx 주소들은 폐기됐는데도 200과 함께 새 사이트로 리다이렉트되는 빈
    HTML 껍데기를 돌려준다. 그걸 정상 응답으로 받아들이면 "회차 없음"으로
    조용히 넘어가 버리므로, 시간표의 흔적이 없는 본문은 실패로 취급한다.
    """
    if parse_json(body) is not None:
        return True
    return bool(TIME_RE.search(to_text(body)))


def endpoints_for(args) -> list[str]:
    if args.endpoint:
        return [args.endpoint]
    primary = CGV_API.replace("{co}", args.co).replace("{scope}", args.scope)
    return [primary] + LEGACY_ENDPOINTS


def fetch_schedule(args, theater: str, datestr: str) -> tuple[str, str]:
    """(본문, 사용한 URL). 실패하면 ("", 마지막으로 시도한 URL)."""
    tried = ""
    for template in endpoints_for(args):
        url = template.format(theater=theater, date=datestr)
        tried = url
        status, payload = fetch(
            url, timeout=args.timeout, retries=2, cookie=args.cookie
        )
        if status == 200 and looks_usable(payload):
            return payload, url
        if args.verbose:
            reason = "시간표 없는 응답" if status == 200 else f"HTTP {status or 'ERR'}"
            print(f"      [{reason}] {url}", file=sys.stderr)
    return "", tried


def diff_times(entry: dict | None, current: list[str]) -> tuple[list[str], list[str]]:
    """(새로 생긴 회차, 합쳐서 기억할 회차)를 돌려준다.

    응답이 일시적으로 부실해도 아는 회차를 잃지 않도록 기억은 합집합으로
    유지한다. 알림은 '새로 생긴 것'에 대해서만 울린다.
    """
    known = set(entry.get("times", [])) if entry else set()
    added = [t for t in current if t not in known]
    return added, sorted(known | set(current))


def check_once(args, state: dict) -> list[str]:
    """전 극장 × 전 날짜를 한 바퀴 돌고, 새로 발견한 회차의 알림 문구를 반환.

    (극장, 날짜)가 처음 나타났을 때뿐 아니라, 이미 아는 날짜에 회차가
    **추가**됐을 때도 알린다. CGV는 같은 날짜에 회차를 나중에 더 붙이기도
    하므로, 날짜 단위로만 기억하면 그 증편을 통째로 놓친다.
    """
    today = dt.date.today()
    now = dt.datetime.now().isoformat(timespec="seconds")
    fresh: list[str] = []

    for theater in args.theaters:
        name = THEATERS.get(theater, f"CGV {theater}")
        for offset in range(args.days):
            date = today + dt.timedelta(days=offset)
            datestr = date.strftime("%Y%m%d")
            body, used = fetch_schedule(args, theater, datestr)
            if not body:
                if args.verbose:
                    print(f"[skip] {name} {datestr}: 응답 없음", file=sys.stderr)
                continue

            hits = find_matches(body, args.titles, args.screen)
            if not hits:
                if args.verbose:
                    print(f"[--] {name} {datestr}: 없음", file=sys.stderr)
                time.sleep(args.gap)
                continue

            key = f"{theater}|{datestr}"
            entry = state["seen"].get(key)
            current = sorted({t for _, times in hits for t in times})
            added, merged = diff_times(entry, current)
            state["seen"][key] = {
                "first_seen": (entry or {}).get("first_seen", now),
                "last_seen": now,
                "times": merged,
            }

            if args.baseline:
                if args.verbose:
                    print(f"[기준선] {name} {datestr}: {len(current)}개 회차 기록", file=sys.stderr)
            elif entry is None:
                fresh.append(
                    f"🆕 {name} · {date:%Y-%m-%d (%a)} 예매 오픈\n"
                    f"  회차: {', '.join(current) if current else hits[0][0]}\n"
                    f"  예매: {BOOKING_URL}"
                )
            elif added:
                fresh.append(
                    f"➕ {name} · {date:%Y-%m-%d (%a)} 회차 추가\n"
                    f"  추가된 회차: {', '.join(added)}\n"
                    f"  예매: {BOOKING_URL}"
                )
            elif args.verbose:
                print(f"[ok] {name} {datestr}: 변화 없음 ({len(current)}개)", file=sys.stderr)

            time.sleep(args.gap)

    return fresh


# --------------------------------------------------------------------------
# 진단 모드
# --------------------------------------------------------------------------

def run_probe(args) -> int:
    """어떤 엔드포인트가 실제로 시간표를 주는지 진단한다."""
    today = dt.date.today().strftime("%Y%m%d")
    theater = args.theaters[0]
    print(f"진단 대상: {THEATERS.get(theater, theater)} ({theater}), 날짜 {today}\n")
    ok_any = False
    for template in endpoints_for(args):
        url = template.format(theater=theater, date=today)
        status, body = fetch(url, timeout=args.timeout, retries=1, cookie=args.cookie)
        verdict = []
        if status != 200:
            verdict.append(f"응답 실패 — {body[:90]}")
        else:
            data = parse_json(body)
            if data is not None:
                verdict.append("JSON 파싱 성공")
                times: set[str] = set()
                _collect_times(data, times)
                if times:
                    verdict.append(f"상영 시각 {len(times)}개 발견")
                    ok_any = True
                else:
                    verdict.append("상영 시각 없음(휴관일이거나 구조가 다름)")
                if "imax" in squash(json.dumps(data, ensure_ascii=False)):
                    verdict.append("IMAX 문자열 있음")
            else:
                text = to_text(body)
                if TIME_RE.search(text):
                    verdict.append("상영 시각 패턴 있음")
                    ok_any = True
                else:
                    verdict.append("상영 시각 패턴 없음")
                if "__NEXT_DATA__" in body or "<html" in body[:200].lower():
                    verdict.append("HTML 페이지로 보임")
        if status == 200 and not looks_usable(body):
            verdict.append("→ 시간표 없는 껍데기라 무시됩니다")
        print(f"  [{status or 'ERR'}] {url}")
        print(f"        {len(body):>8,} bytes · {', '.join(verdict)}")
    print()
    if ok_any:
        print(f"→ 정상입니다. 그대로 감시를 시작하면 됩니다: {PY_CMD} cgv_imax_watch.py -v")
        return 0
    print(
        "→ 쓸 만한 응답이 없습니다.\n"
        "  403/503이면 --cookie 로 브라우저 쿠키를 넘겨보세요.\n"
        "  그래도 안 되면 개발자도구에서 URL을 다시 확인해 --endpoint 로 넘기세요."
    )
    return 1


def run_dump(args) -> int:
    """응답 원문을 파일로 저장한다. 구조를 눈으로 확인할 때 쓴다."""
    theater = args.theaters[0]
    datestr = (dt.date.today() + dt.timedelta(days=args.dump_offset)).strftime("%Y%m%d")
    body, used = fetch_schedule(args, theater, datestr)
    if not body:
        print(f"응답을 받지 못했습니다 (마지막 시도: {used})", file=sys.stderr)
        return 1
    data = parse_json(body)
    with open(args.dump, "w", encoding="utf-8") as fh:
        if data is not None:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        else:
            fh.write(body)
    print(f"{used}\n→ {args.dump} 에 {len(body):,} bytes 저장했습니다.")
    if data is not None:
        found: dict[str, set] = {}

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str) and re.search(r"(?i)nm$|name|titl", key):
                        text = value.strip()
                        # 이미지 경로·코드값은 제목이 아니다
                        if text and "/" not in text and not text.isdigit() and text != "-":
                            found.setdefault(key, set()).add(text)
                    walk(value)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(data)
        if found:
            print("\n제목처럼 보이는 필드들:")
            for key in sorted(found):
                values = sorted(found[key])
                print(f"  {key} ({len(values)}개)")
                for item in values[:12]:
                    print(f"    · {item}")
                if len(values) > 12:
                    print(f"    … 외 {len(values) - 12}개")

    hits = find_matches(body, args.titles, args.screen)
    target = " / ".join(args.titles)
    if hits:
        print(f"\n✅ 이 응답에서 '{target}' + {args.screen} 매치 {len(hits)}건:")
        for summary, _ in hits[:5]:
            print(f"    · {summary}")
    else:
        print(f"\n❌ 이 응답에서는 '{target}' + {args.screen} 를 찾지 못했습니다.")
        print("   위 목록에 해당 영화가 보이면 --title 로 그 표기를 그대로 넘겨주세요.")
    return 0


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
    # 실제 API 형태에 가까운 중첩 JSON: 영화 아래에 상영관, 그 아래에 회차
    api_open = json.dumps(
        {
            "code": "200",
            "data": {
                "movieList": [
                    {
                        "movNm": "오디세이",
                        "scnList": [
                            {
                                "scnsNm": "IMAX관",
                                "timeList": [
                                    {"scnSchdlStrTm": "1930", "restSeatCnt": 120},
                                    {"scnSchdlStrTm": "2240", "restSeatCnt": 301},
                                ],
                            }
                        ],
                    },
                    {"movNm": "다른영화", "scnList": [{"scnsNm": "2관"}]},
                ]
            },
        },
        ensure_ascii=False,
    )
    # 아직 안 열린 날: 다른 영화만 IMAX에 걸려 있다
    api_closed = json.dumps(
        {
            "data": {
                "movieList": [
                    {
                        "movNm": "다른영화",
                        "scnList": [{"scnsNm": "IMAX관", "timeList": [{"scnSchdlStrTm": "1000"}]}],
                    }
                ]
            }
        },
        ensure_ascii=False,
    )
    api_escaped = json.dumps({"data": [{"nm": "오디세이", "screen": "IMAX", "t": "2010"}]})
    # 실제 응답에서 확인된 표기: 상영관이 제목 앞에 붙고 제목은 영어다
    api_english = json.dumps(
        {"data": {"list": [{"movNmEn": "(IMAX LASER 2D)The Odyssey", "tm": "1400"}]}},
        ensure_ascii=False,
    )

    cases = [
        ("IMAX 회차 열림(HTML)", open_html, True),
        ("다른 영화만 있음(HTML)", closed_html, False),
        ("멀리 있는 IMAX 배너에 낚이지 않음", decoy_html, False),
        ("API JSON — 열림", api_open, True),
        ("API JSON — 아직 안 열림", api_closed, False),
        ("API JSON — 한글이 \\uXXXX 로 이스케이프된 경우", api_escaped, True),
        ("API JSON — 영어 제목 + 제목에 붙은 상영관 표기", api_english, True),
    ]
    failed = 0
    for label, payload, expected in cases:
        got = bool(find_matches(payload, ["오디세이", "The Odyssey"], "IMAX"))
        mark = "PASS" if got == expected else "FAIL"
        failed += got != expected
        print(f"  [{mark}] {label} (기대 {expected}, 실제 {got})")

    if find_matches('<div>i m a x</div><div>오디 세이</div>', ["오디세이"], "IMAX", 200):
        print("  [PASS] 공백 변형 매칭")
    else:
        print("  [FAIL] 공백 변형 매칭")
        failed += 1

    hits = find_matches(api_open, ["오디세이"], "IMAX")
    times = [t for _, ts in hits for t in ts]
    if "19:30" in times and "22:40" in times:
        print("  [PASS] API 시각 표기(1930 → 19:30) 변환")
    else:
        print(f"  [FAIL] API 시각 표기 변환 — {hits}")
        failed += 1

    # 증분 판정: 처음 발견 / 회차 추가 / 변화 없음 / 응답이 부실해진 경우
    diff_cases = [
        ("처음 발견", None, ["19:30"], ["19:30"], ["19:30"]),
        ("회차 추가", {"times": ["19:30"]}, ["19:30", "22:40"], ["22:40"], ["19:30", "22:40"]),
        ("변화 없음", {"times": ["19:30"]}, ["19:30"], [], ["19:30"]),
        ("응답이 일부만 와도 기억 유지", {"times": ["19:30", "22:40"]}, ["19:30"], [], ["19:30", "22:40"]),
    ]
    for label, entry, current, want_added, want_merged in diff_cases:
        added, merged = diff_times(entry, current)
        if added == want_added and merged == want_merged:
            print(f"  [PASS] 증분 판정 — {label}")
        else:
            print(f"  [FAIL] 증분 판정 — {label}: added={added}, merged={merged}")
            failed += 1

    print("\n셀프테스트: " + ("전부 통과" if not failed else f"{failed}건 실패"))
    return 1 if failed else 0


# --------------------------------------------------------------------------
# 진입점
# --------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="CGV IMAX 예매 오픈 감시기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--title",
        default="오디세이,The Odyssey",
        help="감시할 영화 제목. 쉼표로 표기 후보를 여러 개 줄 수 있다 "
        "(기본: '오디세이,The Odyssey')",
    )
    p.add_argument("--screen", default="IMAX", help="상영관 종류 (기본: IMAX)")
    p.add_argument(
        "--theaters",
        default="0013,0059",
        help="극장 코드(siteNo) 쉼표 구분 (기본: 0013 용산, 0059 영등포)",
    )
    p.add_argument("--days", type=int, default=40, help="오늘부터 며칠치를 볼지 (기본 40)")
    p.add_argument("--interval", type=int, default=300, help="확인 주기(초, 기본 300)")
    p.add_argument("--gap", type=float, default=0.4, help="요청 사이 간격(초, 기본 0.4)")
    p.add_argument("--timeout", type=float, default=15.0, help="요청 타임아웃(초)")
    p.add_argument("--co", default="A420", help="CGV API coCd (기본 A420)")
    p.add_argument("--scope", default="08", help="CGV API rtctlScopCd (기본 08)")
    p.add_argument("--endpoint", help="시간표 URL 템플릿. {theater}, {date} 사용")
    p.add_argument(
        "--cookie",
        default=os.environ.get("CGV_COOKIE"),
        help="브라우저 쿠키 문자열 (403이 날 때만 필요). 환경변수 CGV_COOKIE 로도 가능",
    )
    p.add_argument(
        "--state",
        default=os.path.expanduser("~/.cgv_imax_watch.json"),
        help="이미 알린 항목을 기억할 파일",
    )
    p.add_argument("--once", action="store_true", help="한 번만 확인하고 종료 (cron 용)")
    p.add_argument(
        "--baseline",
        action="store_true",
        help="지금 올라와 있는 회차를 알림 없이 기준선으로 기록하고 종료. "
        "첫 실행 때 이미 열린 날짜가 한꺼번에 알림으로 쏟아지는 걸 막는다",
    )
    p.add_argument("--probe", action="store_true", help="엔드포인트 생존 진단")
    p.add_argument("--selftest", action="store_true", help="네트워크 없이 탐지 로직 검증")
    p.add_argument("--dump", metavar="FILE", help="응답 원문을 파일로 저장하고 종료")
    p.add_argument("--dump-offset", type=int, default=0, help="--dump 할 날짜 (오늘+N일)")
    p.add_argument("--quiet", action="store_true", help="터미널 벨 끄기")
    p.add_argument("--verbose", "-v", action="store_true", help="확인 과정 출력")
    args = p.parse_args(argv)
    args.theaters = [t.strip() for t in args.theaters.split(",") if t.strip()]
    args.titles = [t.strip() for t in args.title.split(",") if t.strip()]
    args.interval = max(args.interval, MIN_INTERVAL_SEC)
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.selftest:
        return run_selftest()
    if args.probe:
        return run_probe(args)
    if args.dump:
        return run_dump(args)

    state = load_state(args.state)
    label = f"{args.titles[0]} {args.screen}"
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

        if args.baseline:
            save_state(args.state, state)
            recorded = sum(len(v.get("times", [])) for v in state["seen"].values())
            print(
                f"기준선 기록 완료: {len(state['seen'])}개 날짜, 회차 {recorded}개.\n"
                f"이제부터 새로 올라오는 것만 알립니다 → {PY_CMD} cgv_imax_watch.py -v"
            )
            return 0

        if fresh:
            save_state(args.state, state)
            notify(
                f"🎬 {label} 예매 오픈 감지 — {len(fresh)}건",
                "\n\n".join(fresh),
                quiet=args.quiet,
            )
        else:
            save_state(args.state, state)
            print(
                f"[{started:%m-%d %H:%M:%S}] {targets} · {label} 변화 없음"
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
