# CGV IMAX 예매 오픈 감시기

용산아이파크몰(0013)·영등포 타임스퀘어(0059) CGV에서 **오디세이 IMAX 회차가
처음 열리는 순간**을 잡아 알림을 보내는 스크립트입니다.

**새로 올라온 회차만** 알립니다. 오늘부터 N일치 시간표를 훑어서
비교하는데, 비교 단위가 날짜가 아니라 **개별 회차**입니다:

- 없던 날짜에 회차가 생기면 → `🆕 예매 오픈`
- **이미 아는 날짜에 회차가 추가되면** → `➕ 회차 추가`

CGV는 같은 날짜에 회차를 나중에 더 붙이기도 하는데, 날짜 단위로만
기억하면 그 증편을 통째로 놓칩니다. 한 번 알린 회차는 다시 울리지
않습니다.

## 빠른 시작

```bash
cd tools/cgv-imax-watch

python3 cgv_imax_watch.py --selftest   # 탐지 로직 검증 (네트워크 불필요)
python3 cgv_imax_watch.py --probe      # 어느 시간표 URL이 살아있는지 진단
python3 cgv_imax_watch.py --test-notify # 텔레그램 알림이 오는지 확인
python3 cgv_imax_watch.py --baseline   # 지금 열려 있는 회차를 기준선으로 기록
python3 cgv_imax_watch.py -v           # 이후 새로 올라오는 것만 알림
```

**처음 한 번은 `--baseline` 을 돌리세요.** 이미 예매가 열려 있는 날짜가
있으면 첫 실행에서 그게 전부 "새 회차"로 잡혀 알림이 쏟아집니다.
`--baseline` 은 지금 상태를 알림 없이 기록만 하고 끝냅니다. 그 다음부터는
정말 새로 올라온 것만 울립니다.

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

제목처럼 보이는 필드를 필드명별로 보여주고, 그 응답에서 지금 설정으로
매치가 잡히는지까지 알려줍니다. 목록에 영화가 보이는데 매치가 안 되면
거기 나온 표기를 `--title` 로 그대로 넘기면 됩니다.

## 제목 표기 주의

CGV는 같은 작품을 한글과 영어로 모두 담고, 상영관 표기를 제목 앞에 붙이기도
합니다 — 실제 응답에서 `(IMAX LASER 2D)The Odyssey` 형태를 확인했습니다.
그래서 `--title` 은 쉼표로 **표기 후보를 여러 개** 받고, 하나라도 걸리면
매치로 봅니다. 기본값이 `오디세이,The Odyssey` 인 이유입니다.

## 탐지 방식

JSON은 **구조적으로** 훑습니다. 어떤 객체의 조상 체인과 자기 자신의 값
안에 제목과 `IMAX`가 함께 있으면 매치로 봅니다. 필드 이름을 몰라도 되고,
영화 아래에 상영관이 있든 그 반대든 상관없이 잡힙니다. 그래서 "IMAX관에
다른 영화만 걸려 있는 날"을 오탐하지 않습니다 — `--selftest` 의
`API JSON — 아직 안 열림` 케이스가 그걸 검증합니다.

HTML로 폴백했을 때는 평문 근접 검색(제목 등장 위치 ±400자 안에 `IMAX`)을
씁니다. DOM 선택자를 쓰지 않으므로 마크업 개편에 잘 버팁니다.

## 텔레그램 알림 설정

예매 오픈은 대개 평일 낮에 뜹니다. 그때 터미널 앞에 앉아 있을 수는 없으니
폰으로 받는 게 사실상 필수입니다. 3분이면 됩니다.

**1. 봇 만들기** — 텔레그램에서 [@BotFather](https://t.me/BotFather) 를 찾아
`/newbot` 을 보냅니다. 이름과 아이디(`_bot` 으로 끝나야 함)를 정하면
`123456789:AAH...` 형태의 **토큰**을 줍니다.

**2. 내 Chat ID 알아내기** — [@userinfobot](https://t.me/userinfobot) 에게
아무 말이나 걸면 `Id: 12345678` 을 알려줍니다.

**3. 만든 봇에게 먼저 말 걸기** — 1번에서 만든 봇을 검색해 `/start` 를
보냅니다. **이 단계를 빠뜨리면 봇이 나에게 메시지를 못 보냅니다**
(텔레그램은 사용자가 먼저 대화를 시작한 상대에게만 봇 발신을 허용합니다).

**4. 값 넣고 확인**

```powershell
# Windows PowerShell
$env:TELEGRAM_BOT_TOKEN = "123456789:AAH..."
$env:TELEGRAM_CHAT_ID   = "12345678"
python cgv_imax_watch.py --test-notify
```

```bash
# macOS / Linux
export TELEGRAM_BOT_TOKEN='123456789:AAH...'
export TELEGRAM_CHAT_ID='12345678'
python3 cgv_imax_watch.py --test-notify
```

`[OK] 텔레그램: 전송 완료` 와 함께 폰에 메시지가 오면 끝입니다. 실패하면
원인을 짚어 줍니다 — 토큰이 틀렸는지, chat id가 틀렸는지, 3번을 안 했는지.

**5. 매번 입력하기 귀찮으면** 딸려 있는 실행 스크립트를 쓰세요.

```powershell
copy run-watch.example.ps1 run-watch.ps1   # macOS/Linux 는 run-watch.example.sh
notepad run-watch.ps1                       # 토큰 두 줄 채우기
.\run-watch.ps1
```

알림 경로를 먼저 확인하고, 정상일 때만 감시를 시작합니다. 토큰이 든
`run-watch.ps1` 은 `.gitignore` 에 있어 커밋되지 않습니다 — **이 저장소는
공개이니 토큰을 파일에 적어 올리지 마세요.**

디스코드나 슬랙을 쓰신다면 `WEBHOOK_URL` 하나만 넣으면 됩니다.

## 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--title` | `오디세이,The Odyssey` | 감시할 제목. 쉼표로 표기 후보를 여러 개 |
| `--screen` | `IMAX` | 상영관 종류 (`4DX`, `SCREENX` 등으로 바꿔도 됨) |
| `--theaters` | `0013,0059` | 극장 코드 (0013 용산, 0059 영등포) |
| `--days` | `40` | 오늘부터 며칠치를 훑을지 |
| `--interval` | `300` | 확인 주기(초). 최소 30초로 강제됩니다 |
| `--endpoint` | — | 시간표 URL 템플릿 (`{theater}`, `{date}`) |
| `--cookie` | `$CGV_COOKIE` | 403이 날 때 넘길 브라우저 쿠키 |
| `--dump` | — | 응답 원문을 파일로 저장하고 종료 |
| `--once` | — | 한 번만 확인하고 종료 (cron 용) |
| `--baseline` | — | 알림 없이 현재 상태만 기록하고 종료 |
| `--test-notify` | — | 알림 경로 점검 (테스트 메시지 발송) |
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
