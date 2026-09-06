# CGV IMAX 예매 오픈 감시기

용산아이파크몰(0013)·영등포 타임스퀘어(0059) CGV에서 **오디세이 IMAX 회차가
처음 열리는 순간**을 잡아 알림을 보내는 스크립트입니다.

예매 오픈은 "없던 날짜에 회차가 갑자기 생기는 것"이므로, 오늘부터 N일치
시간표를 훑어 **새로 등장한 (극장, 날짜)** 만 알립니다. 이미 알린 건은
상태 파일에 기록해 두 번 울리지 않습니다.

## 빠른 시작

```bash
cd tools/cgv-imax-watch

python3 cgv_imax_watch.py --selftest   # 탐지 로직 검증 (네트워크 불필요)
python3 cgv_imax_watch.py --probe      # 어느 시간표 URL이 살아있는지 진단
python3 cgv_imax_watch.py -v           # 상주하며 5분마다 확인
```

파이썬 3.9+ 표준 라이브러리만 씁니다. `pip install` 필요 없습니다.

## ⚠️ 먼저 `--probe` 를 돌려야 하는 이유

CGV가 홈페이지를 개편해 `cgv.co.kr/cnm/...` 구조로 옮겨갔습니다. 스크립트에
들어 있는 후보 URL(구 `iframeTheater.aspx` 등)이 아직 살아있는지는 **직접
확인해야** 합니다. `--probe` 가 "상영 시각 패턴 있음"을 출력하면 그 URL을
그대로 쓰면 됩니다.

전부 실패하면 직접 찾아서 넘기세요:

1. 브라우저에서 CGV 극장별 예매 페이지를 엽니다.
2. 개발자도구 → Network → Fetch/XHR, 날짜를 한 번 바꿔봅니다.
3. 시간표 데이터가 실린 요청의 URL을 복사합니다.
4. 극장코드 자리를 `{theater}`, 날짜 자리를 `{date}` (YYYYMMDD)로 바꿔 넘깁니다.

```bash
python3 cgv_imax_watch.py --endpoint 'https://cgv.co.kr/.../{theater}/{date}' -v
```

탐지는 DOM 선택자가 아니라 **평문 검색**으로 합니다(제목 등장 위치 ±400자
안에 `IMAX`가 있는지). 마크업이 바뀌어도 잘 버티고, 페이지 구석의 IMAX
배너에는 낚이지 않습니다 — `--selftest` 의 세 번째 케이스가 그걸 검증합니다.

## 알림 받기

환경변수만 넣으면 켜집니다. 아무것도 없으면 콘솔 출력 + 터미널 벨,
macOS면 알림센터까지 자동으로 씁니다.

```bash
# 텔레그램 (@BotFather 로 봇 생성, @userinfobot 으로 chat id 확인)
export TELEGRAM_BOT_TOKEN=123456:AA...
export TELEGRAM_CHAT_ID=12345678

# 또는 디스코드/슬랙 웹훅
export WEBHOOK_URL=https://discord.com/api/webhooks/...
```

**폰으로 받으려면 텔레그램을 권합니다.** 예매 오픈은 보통 새벽이나 업무
시간에 뜨는데, 터미널 벨은 그때 못 듣습니다.

## 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--title` | `오디세이` | 감시할 영화 제목 |
| `--screen` | `IMAX` | 상영관 종류 (`4DX`, `SCREENX` 등으로 바꿔도 됨) |
| `--theaters` | `0013,0059` | 극장 코드 (0013 용산, 0059 영등포) |
| `--days` | `30` | 오늘부터 며칠치를 훑을지 |
| `--interval` | `300` | 확인 주기(초). 최소 30초로 강제됩니다 |
| `--endpoint` | — | 시간표 URL 템플릿 (`{theater}`, `{date}`) |
| `--once` | — | 한 번만 확인하고 종료 (cron 용) |
| `--state` | `~/.cgv_imax_watch.json` | 이미 알린 항목 기록 |

## 백그라운드로 계속 돌리기

**cron (Linux/macOS)** — 10분마다:

```cron
*/10 * * * * cd ~/junewon.github.io/tools/cgv-imax-watch && \
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
  /usr/bin/python3 cgv_imax_watch.py --once >> ~/cgv-watch.log 2>&1
```

**launchd (macOS, 노트북 잠자기 후에도 복귀)** —
`~/Library/LaunchAgents/com.local.cgv-imax-watch.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.local.cgv-imax-watch</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string>
    <string>/Users/YOU/junewon.github.io/tools/cgv-imax-watch/cgv_imax_watch.py</string>
    <string>--once</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>TELEGRAM_BOT_TOKEN</key><string>...</string>
    <key>TELEGRAM_CHAT_ID</key><string>...</string>
  </dict>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/cgv-imax-watch.log</string>
  <key>StandardErrorPath</key><string>/tmp/cgv-imax-watch.err</string>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.local.cgv-imax-watch.plist
```

## 범위와 한계

- 하는 일: **감지와 알림**까지입니다. 좌석 선택·결제 자동화는 들어 있지
  않습니다. CGV 약관은 자동화 프로그램을 통한 예매를 금지하고 있고, 캡차와
  이상거래 탐지에 걸리면 계정이 정지될 수 있습니다.
- 알림이 오면 스크립트가 함께 출력하는 예매 링크로 직접 들어가면 됩니다.
  30초 주기로 돌려도 사람이 수동으로 새로고침하는 것보다 훨씬 빠릅니다.
- 서버에 부담이 가지 않도록 요청 간 간격(`--gap`)과 최소 확인 주기(30초)를
  두었습니다. 이보다 짧게 때리지 마세요.
