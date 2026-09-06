# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 저장소 현황 (먼저 읽을 것)

이 저장소는 **`mmistakes/minimal-mistakes` v4.19.2를 그대로 포크한 Jekyll *테마 젬* 소스**다.
저장소 이름(`AI-For-People/junewon.github.io`)은 GitHub Pages 사용자 사이트 형식이지만, 내용물은 아직 개인 블로그가 아니다:

- 루트 `_config.yml`이 아직 플레이스홀더다 — `title: "Site Title"`, `name: "Your Name"`, `url:` 비어 있음
- 루트에 `_posts/`가 없다. 글은 `docs/_posts/`(업스트림 데모 사이트)와 `test/_posts/`(픽스처)에만 있다
- `git log`가 업스트림 커밋 그대로다 (개인 커밋 없음)

**개인 블로그용 콘텐츠를 추가하기 전에 사용자에게 방향을 확인할 것.** 두 갈래가 있고 파일 배치가 완전히 달라진다:

1. **테마 저장소로 유지** — 테마를 직접 수정/배포. 지금 구조 그대로.
2. **개인 사이트로 전환** — 보통은 포크가 아니라 빈 저장소에 `remote_theme: mmistakes/minimal-mistakes`(또는 `theme:` 젬)만 지정하는 방식을 쓴다. 이 경우 `_layouts/`, `_includes/`, `_sass/`, `docs/`, `test/`, `*.gemspec`, `Rakefile`은 대부분 불필요해진다.

임의로 판단해서 루트에 `_posts/`를 만들거나 `_config.yml`을 개인 정보로 덮어쓰지 말 것.

## 명령어

### 환경 주의사항 (Claude Code 웹 세션)

`/opt/rbenv/versions/3.3.6/bin`이 PATH에 없어서 `bundle exec jekyll`도 `bundle exec rake`도 `command not found`로 실패한다. 젬은 정상 설치되어 있으므로 PATH만 붙이면 된다. **아래 명령들을 실행하기 전에 매번 이걸 먼저 export할 것:**

```bash
export PATH="/opt/rbenv/versions/3.3.6/bin:$PATH"
```

(`/opt/rbenv/shims`를 붙이는 건 소용없다 — rbenv global 버전이 설정되어 있지 않아 shim이 거부한다.)

### 테마 개발 (루트 Gemfile = gemspec)

```bash
bundle install

# test/ 픽스처 사이트를 띄우고 _data, _includes, _layouts, _sass, assets 변경을 감시
bundle exec rake preview          # http://localhost:4000/test

# 1회 빌드 (검증됨)
export PATH="/opt/rbenv/versions/3.3.6/bin:$PATH"
bundle exec jekyll build --source test --destination test/_site --config test/_config.yml
```

Sass deprecation 경고(susy/breakpoint vendor 코드의 `/` 나눗셈)가 수백 줄 나오는 건 정상이다. 빌드 실패가 아니다.

### 문서/데모 사이트 (`docs/` — 별도 Gemfile)

`docs/Gemfile`은 `github-pages` 젬을 쓰므로 루트와 젬 버전이 다르다. 반드시 `docs/`에서 따로 설치할 것.

```bash
cd docs && bundle install
bundle exec jekyll serve --config _config.yml,_config.dev.yml
```

`_config.dev.yml`은 오버라이드 전용이다 (url을 localhost로, analytics 끄기, sass expanded).

### JavaScript 번들

```bash
npm install
npm run build:js     # uglify → assets/js/main.min.js, 그 뒤 banner.js가 라이선스 배너 삽입
npm run watch:js
```

`assets/js/main.min.js`는 **생성물이다. 직접 편집하지 말 것.** `assets/js/_main.js` 또는 `assets/js/plugins/*`를 고치고 `npm run build:js`를 돌린다. 새 플러그인 파일을 추가했다면 `package.json`의 `uglify` 스크립트 파일 목록에도 넣어야 번들에 포함된다.

### 테스트

**테스트 스위트가 없다.** `test/`는 러너가 아니라 테마를 눈으로 확인하는 픽스처 사이트다. `.travis.yml`이 하는 일은 docs 사이트의 Algolia 인덱스 빌드뿐이다.

검증 = `rake preview`로 `test/` 사이트를 띄워 레이아웃이 깨지지 않았는지 확인하는 것.

## 아키텍처

### 한 저장소 안의 세 사이트

| 위치 | 정체 | 설정 |
|---|---|---|
| 루트 | 테마 젬 소스 + 플레이스홀더 사이트 | `_config.yml` (테마 자체를 쓰므로 `theme:` 지정 없음) |
| `test/` | 개발용 픽스처 사이트 | `theme: minimal-mistakes-jekyll`, `baseurl: /test` |
| `docs/` | 공개 데모·문서 사이트 | github-pages 젬, 컬렉션 `docs`/`recipes`/`pets`/`portfolio` |

`test/`와 `docs/`는 루트의 `_layouts`, `_includes`, `_sass`, `assets`를 테마로 끌어다 쓴다. 테마 파일을 고치면 두 사이트에 동시에 반영된다.

### 젬에 실제로 포함되는 것

`minimal-mistakes-jekyll.gemspec`이 `git ls-files`를 정규식으로 걸러 **`assets/`, `_data/`, `_includes/`, `_layouts/`, `_sass/`, LICENSE/README/CHANGELOG만** 젬에 담는다. 그 밖의 파일(`docs/`, `test/`, `Rakefile`, `index.html` 등)은 배포물에 들어가지 않는다 — 테마 사용자에게 필요한 것을 추가할 땐 반드시 저 다섯 디렉터리 안에 둘 것.

### 레이아웃 계층

`_layouts/default.html`이 껍데기(head → masthead → sidebar → content → footer → scripts)이고 나머지가 그 위에 얹힌다:

- `single` (글/페이지), `home`(페이지네이션), `splash`(전폭 랜딩), `collection`, `search`
- `archive` → `archive-taxonomy`, `categories`/`category`, `tags`/`tag`, `posts`
- `compress.html`은 화면 레이아웃이 아니라 HTML 압축 래퍼다

### 확장 지점

- **`_includes/`의 하위 디렉터리가 곧 플러그인 슬롯이다.** `comments-providers/`, `analytics-providers/`, `search/`, `head/`, `footer/`, `toc/`, `gallery/`, `nav_list`, `feature_row`, `video/`. 새 댓글/애널리틱스 제공자를 붙이려면 ① 해당 디렉터리에 파일 추가 ② `comments.html`/`analytics.html`의 분기 추가 ③ `_config.yml`에 설정 키 추가 — 세 곳을 모두 건드려야 한다.
- **스킨**은 `_sass/minimal-mistakes/skins/_<이름>.scss`. `_config.yml`의 `minimal_mistakes_skin` 값으로 선택된다. 파일을 추가하면 그 이름이 곧 선택 가능한 값이 된다.
- **SCSS 진입점**은 `assets/css/main.scss` → `_sass/minimal-mistakes.scss` → 변수 → 스킨 → 컴포넌트 순. 컴포넌트는 `_sass/minimal-mistakes/_*.scss`.

### 데이터 파일

- `_data/navigation.yml` — 마스트헤드 및 사이드바 네비게이션 목록
- `_data/ui-text.yml` — **모든 UI 문자열이 로케일별로 여기 있다(30개 이상 언어).** 템플릿에 사용자 노출 문자열을 하드코딩하지 말고 이 파일에 키를 추가하고 `site.data.ui-text[site.locale]`로 참조할 것.

### 필수 플러그인

템플릿이 `include_cached`를 쓰므로 `jekyll-include-cache`가 반드시 있어야 한다. 없으면 빌드가 `Unknown tag 'include_cached'`로 죽는다. `_config.yml`의 `plugins`와 `whitelist`(GitHub Pages `--safe` 모드 흉내) 양쪽에 등록되어 있다.

## 컨벤션

- `.editorconfig`: 스페이스 2칸, LF, UTF-8, 후행 공백 제거(단 `*.md` 제외), **파일 끝 개행 없음**(`insert_final_newline = false`)
- 브랜치는 `master`에서 따고 의미 있는 이름을 붙인다 (`.github/CONTRIBUTING.md`)
- `.github/PULL_REQUEST_TEMPLATE.md`는 Summary / Context 두 섹션과 변경 유형 주석을 요구한다
