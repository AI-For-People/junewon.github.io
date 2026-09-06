# 브로커 스펙 JSON 형식

증권사 REST API를 **코드 수정 없이** 붙이기 위한 매핑 파일 형식이다.
`autotrade/brokers/spec_rest.py` 가 이 파일을 읽어 동작한다.

## 치환 변수

요청 경로·헤더·파라미터·바디의 문자열 안에서 `${NAME}` 형태로 쓴다.

| 변수 | 값 |
|---|---|
| `${APP_KEY}` `${APP_SECRET}` | 자격증명 (환경변수에서 주입) |
| `${TOKEN}` | 발급받은 접근토큰 |
| `${ACCOUNT_NO}` `${ACCOUNT_PD}` | 계좌번호 / 상품코드 |
| `${SYMBOL}` | 종목코드 |
| `${QTY}` `${PRICE}` | 주문 수량 / 단가 (시장가는 `0`) |
| `${SIDE}` `${ORDER_TYPE}` | `codes` 매핑을 거친 증권사 코드값 |
| `${CLIENT_ORDER_ID}` | 클라이언트 주문 ID |
| `${DATE_FROM}` `${DATE_TO}` `${COUNT}` | 기간별 시세 조회용 |

## 응답 경로

`response` 값은 점 표기 경로다. 리스트 인덱스도 쓸 수 있다.

```
"cash": "output2[0].ord_psbl_cash"   →  data["output2"][0]["ord_psbl_cash"]
```

## 최상위 필드

| 키 | 설명 |
|---|---|
| `name` | 브로커 이름 |
| `base_url` | API 호스트 (끝의 `/` 제외) |
| `rate_limit_per_sec` | 초당 호출 상한. 증권사 유량 제한보다 **보수적으로** 잡을 것 |
| `success_check` | 업무 오류 판정. `{field, equals, message_field}` — HTTP 200이어도 `rt_cd != "0"` 처럼 실패인 경우를 잡는다 |
| `auth` | 토큰 발급 방식 |
| `endpoints` | `quote` / `candles` / `balance` / `order` |
| `_`로 시작하는 키 | 주석용. 검증에서 제외된다 |

## 안전장치

값 어딘가에 문자열 `TODO`가 남아 있으면 어댑터가 **기동을 거부한다.**
반쯤 채운 스펙으로 실계좌에 주문이 나가는 사고를 막기 위한 것이므로, 이 검사를 우회하지 말 것.

## 예시

완전히 채워진 실제 예시는 `autotrade/brokers/kis.py`(한국투자증권 전용 구현)와
`config/meritz.spec.example.json`(빈 템플릿)을 참고한다.
