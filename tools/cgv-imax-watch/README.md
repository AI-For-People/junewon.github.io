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

## 어디서 데이터를 가져오나

개편된 CGV 사이트가 실제로 쓰는 시간표 API를 그대로 씁니다 (브라우저
개발자도구에서 확인):

```
GET https://cgv.co.kr/api/v1/booking/searchMovScnInfo
      ?coCd=A420&siteNo={극장코드}&scnYmd={YYYYMMDD}&rtctlScopCd=08
```

JSON으로 응답합니다. 이게 막히면 구 `iframeTheater.aspx` 계열 주소로
자동 폴백하지만, 확인해 보니 그 주소들은 폐기된 뒤에도 200과 함께 시간표가
없는 빈 HTML을 돌려줍니다. 그래서 응답을 받아들이기 전에 **시간표가 실제로
담겨 있는지 검사**합니다 — 껍데기는 실패로 취급해서, 회차를 놓치고도
"없음"으로 넘어가는 일이 없게 했습니다.

`--probe` 로 지금 어느 쪽이 살아있는지 확인할 수 있습니다.

```
  [200] https://cgv.co.kr/api/v1/booking/searchMovScnInfo?...
           38,204 bytes · JSON 파싱 성공, 상영 시각 62개 발견, IMAX 문자열 있음
```

**403 / 503이 뜨면** Cloudflare 앞단에 걸린 겁니다. 브라우저 개발자도구에서
요청을 우클릭 → Copy as cURL 해서 `-b` 뒤에 붙어 있는 쿠키 문자열을 넘기세요:

```bash
export CGV_COOKIE='__cf_bm=...; _cfuvid=...'
python3 cgv_imax_watch.py --probe
```

쿠키는 30분 남짓이면 만료되므로 상시 감시에는 부적합합니다. 쿠키 없이도
되는 게 정상이고, 계속 403이 난다면 확인 주기를 늘리세요.

API 구조가 바뀌어 탐지가 안 되는 것 같으면 응답 원문을 떠서 보면 됩니다:

```bash
python3 cgv_imax_watch.py --dump today.json --dump-offset 7
```

## 탐지 방식

JSON은 **구조적으로** 훑습니다. 어떤 객체의 조상 체인과 자기 자신의 값
안에 제목과 `IMAX`가 함께 있으면 매치로 봅니다. 필드 이름을 몰라도 되고,
영화 아래에 상영관이 있든 그 반대든 상관없이 잡힙니다. 그래서 "IMAX관에
다른 영화만 걸려 있는 날"을 오탐하지 않습니다 — `--selftest` 의
`API JSON — 아직 안 열림` 케이스가 그걸 검증합니다.

HTML로 폴백했을 때는 평문 근접 검색(제목 등장 위치 ±400자 안에 `IMAX`)을
씁니다. DOM 선택자를 쓰지 않으므로 마크업 개편에 잘 버팁니다.

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
| `--cookie` | `$CGV_COOKIE` | 403이 날 때 넘길 브라우저 쿠키 |
| `--dump` | — | 응답 원문을 파일로 저장하고 종료 |
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
- CGV는 예매가 몰리는 시간에 NetFunnel(가상 대기열)을 앞단에 세웁니다.
  대기열이 걸리면 API가 평소와 다른 응답을 줄 수 있는데, 그건 이미
  오픈됐다는 신호이기도 합니다. 알림을 받으면 브라우저로 바로 넘어가세요.
- 쿠키를 쓰게 되더라도 파일에 적어 커밋하지 마세요. 이 저장소는 공개입니다.
