# data/

| 파일 | 내용 |
|---|---|
| `krx_holidays.txt` | KRX 휴장일 목록. **KRX 공식 공지로 검증·갱신 필요.** |
| `sample_synthetic_daily.csv` | 백테스트 배관 검증용 **합성(가짜) 일봉**. 실제 종목 시세가 아니다. |

## 실제 시세로 백테스트하려면

`sample_synthetic_daily.csv` 는 난수로 만든 데이터라 여기서 나온 수익률은
아무 의미가 없다. 실제 검증은 아래 중 하나로 받은 데이터를 같은 CSV 형식
(`date,open,high,low,close,volume`, 날짜는 `YYYY-MM-DD`)으로 저장해 사용한다.

- 증권사 API의 기간별 시세 조회 (예: `python -m autotrade.cli fetch --symbol 005930 --out data/005930.csv`)
- KRX 정보데이터시스템(data.krx.co.kr)의 시세 다운로드

⚠️ 시세 데이터는 대부분 **재배포·제3자 제공이 금지**된다. 받은 데이터를 공개
저장소에 커밋하거나 남에게 넘기지 말 것. (그래서 이 디렉터리의 `*.csv` 중
샘플을 제외한 파일은 `.gitignore` 로 막아 두었다.)
