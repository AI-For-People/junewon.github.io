# korean-law MCP 플러그인

이 저장소를 Claude Code로 열면 [korean-law-mcp](https://github.com/chrisryugj/korean-law-mcp)
플러그인(법제처 Open API 기반 법령·판례 조회 MCP 서버)이 자동으로 활성화됩니다.

설정 위치: `.claude/settings.json`의 `extraKnownMarketplaces` + `enabledPlugins`

## 최초 1회 필요한 작업

1. **법제처 Open API 인증키(OC) 발급** (무료)
   - https://open.law.go.kr/LSO/openApi/guideList.do 에서 신청
   - `honggildong` 형태의 인증키가 발급됩니다.
2. Claude Code가 플러그인을 설치할 때 이 인증키를 물어봅니다.
   민감정보로 로컬에 저장되며 저장소에는 커밋되지 않습니다.

수동으로 설치하려면 Claude Code에서:

```
/plugin marketplace add chrisryugj/korean-law-mcp
/plugin install korean-law@korean-law-marketplace
```

## 업데이트

```
/plugin marketplace update korean-law-marketplace
```

## 제공 도구

`search_law`, `get_law_text`, `search_decisions`, `get_decision_text`,
`get_annexes`, `legal_analysis`, `legal_research`, `ordinance_radar` 등
법제처 42개 API를 묶은 통합 도구. 자연어로 질문하면 자동 호출됩니다.

예: "근로기준법 제74조 알려줘", "민법 제750조 판례 검증해줘"

## 참고

- 설치 없이 쓰려면 claude.ai → 설정 → 커넥터에서 커스텀 커넥터로
  `https://mcp.gomdori.app/law` 를 추가하는 방법도 있습니다.
- 라이선스: MIT (chrisryugj/korean-law-mcp)
