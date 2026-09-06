# 주간 환율 카드뉴스 자동 발송

매주 월요일 아침, 원화 환율 4종을 정리한 카드뉴스 PNG를 만들어 메일로 보낸다.
GitHub Actions에서 돌아가므로 별도 서버가 필요 없다.

## 무엇이 오는가

카드 6장이 메일 본문에 인라인으로 들어온다.

| 순서 | 카드 | 내용 |
|---|---|---|
| 1 | 커버 | 헤드라인, 4개 통화 현재가·주간 등락률·추세 |
| 2~5 | 통화별 | 현재가, 주간 등락, 60영업일 스파크라인, 이동평균·변동성·RSI·52주 고저 |
| 6 | 전망 | 요약, 다음 주 체크리스트 3개, 지표 한 줄 설명 |

추적 통화는 USD/KRW, 100JPY/KRW, EUR/KRW, CNY/KRW다.
이미지를 차단한 메일 클라이언트를 위해 같은 내용의 텍스트 본문도 함께 보낸다.

## 데이터 출처

[Frankfurter API](https://frankfurter.dev/)를 통한 **ECB(유럽중앙은행) 기준환율**이다.
API 키가 필요 없고 무료다.

ECB는 EUR 기준으로 고시하므로 EUR 기준 시계열을 한 번만 받아 KRW 대비로 환산한다.
따라서 매주 API 호출은 1회다.

알아 둘 점:

- ECB는 유럽 영업일에만, 현지 시각 오후에 고시한다. 주말·유럽 공휴일에는 새 값이 없다.
- 서울 외환시장 종가나 은행 고시 환율(매매기준율)과는 소수점 아래에서 차이가 난다.
  참고용 추세 파악에는 충분하지만, 실제 환전 금액 계산에는 쓰지 말 것.

## 설정

### 1. GitHub Secrets 등록

저장소 → Settings → Secrets and variables → Actions → New repository secret.

| 이름 | 필수 | 설명 |
|---|---|---|
| `FX_SMTP_USER` | ✅ | 보내는 Gmail 주소 (예: `you@gmail.com`) |
| `FX_SMTP_PASSWORD` | ✅ | Gmail **앱 비밀번호** 16자리 (계정 비밀번호 아님) |
| `FX_MAIL_TO` | | 받는 주소. 생략하면 `FX_SMTP_USER`로 보냄. 쉼표로 여러 명 지정 가능 |
| `ANTHROPIC_API_KEY` | | 있으면 Claude가 해설을 쓴다. 없으면 지표 기반 문장으로 자동 대체 |

### 2. Gmail 앱 비밀번호 발급

앱 비밀번호는 2단계 인증이 켜져 있어야 만들 수 있다.

1. [Google 계정 보안](https://myaccount.google.com/security)에서 2단계 인증을 켠다.
2. [앱 비밀번호](https://myaccount.google.com/apppasswords)로 이동한다.
3. 앱 이름을 아무거나 (예: `fx-newsletter`) 적고 생성한다.
4. 나온 16자리를 `FX_SMTP_PASSWORD`에 넣는다. 표시된 공백은 넣어도 되고 안 넣어도 된다
   (코드에서 공백을 제거한다).

### 3. Actions 활성화 확인

이 저장소는 포크로 시작했으므로 Actions 탭에서 워크플로 실행을 한 번 허용해 줘야 할 수 있다.

## 실행

### 자동

`.github/workflows/fx-newsletter.yml`의 cron이 **일요일 23:00 UTC = 월요일 08:00 KST**에 돈다.

시간을 바꾸려면 cron 값을 고친다. GitHub Actions cron은 항상 UTC이므로 KST에서 9시간을 뺀다.

```yaml
- cron: "0 23 * * 0"   # 월 08:00 KST
- cron: "0 22 * * 5"   # 토 07:00 KST — 금요일 ECB 고시를 가장 빨리 받아보는 쪽
```

> GitHub은 60일간 커밋이 없는 저장소의 예약 워크플로를 자동으로 멈춘다.
> 알림이 끊기면 Actions 탭에서 다시 켜면 된다. 예약 실행은 부하에 따라 몇 분에서
> 수십 분 늦게 뜰 수 있다.

### 수동

Actions 탭 → "주간 환율 카드뉴스" → Run workflow.
`dry_run`을 켜면 메일을 보내지 않고 카드만 만들어 아티팩트로 올린다.

### 로컬

```bash
cd tools
pip install -r fx_newsletter/requirements.txt
sudo apt-get install -y fonts-nanum        # 한글 폰트. macOS는 기본 폰트로 충분

# 카드만 만들어 보기 (메일 발송 없음)
python -m fx_newsletter.main --dry-run --out build/fx-cards

# 실제 발송
export FX_SMTP_USER=you@gmail.com
export FX_SMTP_PASSWORD='앱비밀번호16자리'
export ANTHROPIC_API_KEY=sk-ant-...
python -m fx_newsletter.main
```

주요 옵션:

| 옵션 | 설명 |
|---|---|
| `--dry-run` | 카드만 만들고 메일은 보내지 않는다 |
| `--no-ai` | Claude를 건너뛰고 규칙 기반 문장만 쓴다 |
| `--out DIR` | 카드 저장 위치 (기본 `build/fx-cards`) |
| `--date YYYY-MM-DD` | 발행 기준일 지정. 과거 시점 재현에 쓴다 |

## 테스트

네트워크 없이 전 구간을 검증한다. 합성 시계열로 환산·지표·렌더링·메일 조립을 확인한다.

```bash
cd tools
python -m unittest discover -s fx_newsletter/tests -t .
```

## 구조

```
tools/fx_newsletter/
├── config.py       통화 정의, 환경 변수 읽기
├── fetch.py        Frankfurter 호출, EUR 기준 → KRW 환산
├── indicators.py   이동평균 · RSI · 볼린저 · 변동성 · 추세 판정
├── commentary.py   Claude 해설 생성 + 규칙 기반 대체
├── cards.py        Pillow로 1080x1080 PNG 렌더링
├── mailer.py       인라인 이미지 메일 조립 및 SMTP 발송
└── main.py         파이프라인 진입점 (CLI)
```

설계상 두 군데가 **실패해도 멈추지 않는다**:

- Frankfurter 호스트가 죽으면 두 번째 호스트로 넘어가고, 각 요청은 4회까지
  지수 백오프로 재시도한다.
- Claude 호출이 실패하거나 키가 없으면 지표 기반 문장으로 조용히 대체된다.
  해설이 밋밋해질 뿐 뉴스레터는 나간다.

반대로 환율 데이터를 아예 못 받으면 그때는 실패로 끝낸다. 틀린 숫자를 보내느니
안 보내는 편이 낫기 때문이다.

## 통화를 바꾸려면

`config.py`의 `PAIRS`에 항목을 추가하거나 뺀다. Frankfurter가 지원하는 통화여야 한다
(ECB 고시 대상 30여 종). 카드는 통화 수에 맞춰 자동으로 늘고 준다.

```python
Pair(code="GBP", label="영국 파운드", unit=1, symbol="£"),
```

`commentary.py`의 `OUTPUT_SCHEMA` 안 `pair_comments`에도 같은 코드를 넣어야
Claude 해설이 그 통화까지 써 준다.

## 비용

- 환율 데이터: 무료
- Gmail 발송: 무료
- GitHub Actions: 퍼블릭 저장소 무료. 1회 실행 약 1~2분
- Claude API: 주 1회, 입력 2천 토큰 안팎의 짧은 호출이라 월 단위로도 소액이다

## 주의

카드와 메일에 다음 문구가 항상 들어간다.

> 본 자료는 공개 데이터를 정리한 참고 자료이며 투자 권유가 아닙니다.

해설은 주어진 지표에서 읽히는 것만 쓰도록 프롬프트에 못 박아 두었지만,
생성형 모델의 출력이므로 그대로 신뢰하지 말고 숫자를 함께 볼 것.
