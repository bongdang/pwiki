# pwiki

> 내 Obsidian vault를 브라우저에서 읽고, 원하는 폴더만 골라서 공유하세요.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Status: v0.2.0](https://img.shields.io/badge/status-v0.2.0-blue.svg)

[English](README.md) | 한국어

Obsidian vault를 웹에서 읽고, 필요한 경우 제한적으로 수정할 수 있는 Flask 기반 Markdown wiki입니다.

pwiki는 Obsidian을 계속 개인 지식베이스(LLM wiki)의 원본으로 사용하면서, 웹 브라우저로 필요한 문서를 조회하거나 일부 폴더만 안전하게 공유하고 싶을 때를 위해 만들었습니다.

## 왜 만들었나요?

Obsidian은 로컬 PC에서 쓰기 좋은 기록 도구이지만, 다음 상황에서는 웹 인터페이스가 있으면 편합니다.

- 회사나 외부에서 내 PC의 Obsidian 기록을 간단히 확인하고 싶을 때
- Obsidian vault 전체가 아니라 특정 폴더만 가족이나 동료에게 공유하고 싶을 때
- Obsidian과 LLM 플러그인으로 개인 지식베이스(LLM wiki)를 쌓고, 그 결과를 Obsidian 없이 토큰 소모 없이 어떤 브라우저에서든 읽고 검색하고 싶을 때
- 집 공유기 뒤의 서버, NAS, Synology 같은 환경에 설치하고 reverse proxy 또는 tunnel을 붙여 외부에서 개인 wiki처럼 접근하고 싶을 때
- 기본적으로는 read-only로 안전하게 운영하되, 필요할 때는 웹에서 간단히 수정하고 Obsidian/Git 쪽으로 동기화하고 싶을 때

pwiki는 Obsidian을 대체하는 앱이 아니라, Obsidian Markdown 파일을 그대로 사용하는 가벼운 웹 레이어입니다. 별도 DB로 문서를 가져오거나 포맷을 변환하지 않고 vault의 파일 구조를 유지합니다.

기본값은 read-only이고, 필요한 경우에만 사용자별/경로별 쓰기 권한을 열 수 있습니다. 그래서 개인 기록과 공유 문서를 같은 vault에 두더라도, 특정 폴더만 가족이나 동료에게 조심스럽게 공유할 수 있습니다.

Obsidian은 원본 편집 도구로 두고, pwiki는 외부 조회, 부분 공유, 가끔 있는 웹 수정 역할만 맡는 구조입니다. 일부러 범위를 좁게 잡고 있는데, 어떤 것이 범위 밖인지는 아래 *pwiki가 안 하는 것* 섹션을 참고하세요.

## 주요 기능

**읽기 및 렌더링**

- Obsidian vault의 Markdown 파일을 직접 읽기 — 별도 DB나 포맷 변환 없음
- Obsidian vault 폴더를 그대로 가리켜도 동작합니다. `.obsidian/`, `.git/` 같은 dotfile은 자동으로 무시됩니다
- Obsidian 스타일 wikilink, 첨부, tag, callout 렌더링
- 별도 검색 서버 없이 파일 기반 검색
- 모바일 read-only 브라우징 UI

**공유 및 권한**

- Google OAuth 로그인과 사용자별/경로별 권한 관리
- CLI와 웹 admin UI에서 사용자 관리
- 권한 없는 페이지는 sidebar, 모바일 drawer, 검색 결과, 인덱스에서 모두 숨김

**편집 및 Git**

- 기본 read-only 운영, 사용자/경로 단위로 웹 편집 opt-in
- 에디터에서 파일/이미지 업로드(툴바 버튼·클립보드 붙여넣기·드래그앤드롭) — vault 안에 저장되고 `![[attachments/…]]`로 임베드되어 Obsidian으로도 그대로 동기화
- Git 관리 vault의 sync / status / commit / push; 저장·삭제·업로드를 auto-commit + push 가능(동시 편집 흡수용 rebase 옵션 포함)
- 저장 시 충돌 감지, atomic write로 기존 줄바꿈 스타일 보존

**프로그램 접근(로컬 API)**

- 같은 호스트 / 사설망에 있는 신뢰 가능한 소비자(로컬 AI 비서, RAG 파이프라인, 백업 스크립트 등)를 위한 read-only JSON API: `/api/internal/*`
- OAuth 흐름과 완전히 분리된 Bearer 토큰 + CIDR 화이트리스트 인증
- 설계상 read-only — `GET` 핸들러만 등록, 토큰을 설정하기 전까지 비활성

**운영**

- reverse proxy sub-path 배포 지원
- `install.sh` / `install.py` 기반 설치 도우미
- Docker Compose 즉시 사용 가능

## pwiki가 안 하는 것

pwiki가 일부러 안 하는 것들이 있습니다.

- Obsidian의 대체가 아닙니다. 편집은 Obsidian이 하고, pwiki는 그 위에 가볍게 얹은 웹 레이어 역할만 합니다.
- 대규모 팀 wiki가 아닙니다. 1,000개 안팎의 개인/소규모 vault를 가정하고, 검색도 별도 인덱스 없이 파일을 직접 읽습니다.
- 모바일 편집기가 아닙니다. 모바일 화면은 일부러 read-only로 두었습니다.

## 사용 시나리오

### 1. 내 Obsidian을 외부에서 보기

집 PC나 NAS에 있는 Obsidian vault를 pwiki에 연결하면, 브라우저에서 Markdown 문서를 볼 수 있습니다.

예를 들어 회사에서 과거에 정리한 개발 메모, 설정 방법, 구매 기록, 생활 정보를 빠르게 확인할 수 있습니다.

### 2. 특정 폴더만 가족이나 동료에게 공유

vault 전체를 공개하지 않고, 특정 폴더나 파일 경로에만 read 권한을 줄 수 있습니다.

예를 들어:

- `가족/여행`
- `공유/집수리`
- `업무공유/프로젝트A`
- `기록/개발메모`

같은 폴더만 허용하고, 개인 일기나 민감한 문서는 숨길 수 있습니다.

### 3. 웹에서 간단히 수정하고 Obsidian과 동기화

기본 운영은 read-only를 권장합니다.

필요한 경우 `PWIKI_READ_ONLY=0`으로 웹 쓰기를 열고, 사용자별 write 권한을 부여할 수 있습니다. Git으로 관리되는 vault를 사용하면 웹에서 저장한 변경을 commit/push하고, Obsidian 쪽에서는 Git sync를 통해 다시 받아볼 수 있습니다.

### 4. 홈서버 / NAS / Synology 배포

pwiki는 Docker Compose 기반 배포를 지원합니다.

집 공유기 뒤의 서버, NAS, Synology 같은 장비에 설치하고 nginx reverse proxy, Cloudflare Tunnel, Tailscale, ngrok 같은 외부 접속 구성을 붙이면 외부에서도 개인 wiki처럼 사용할 수 있습니다.

외부 공개 시에는 반드시 HTTPS, OAuth, 충분히 강한 `PWIKI_SECRET_KEY`를 설정하는 것을 권장합니다.

### 5. Obsidian vault 폴더를 그대로 가리키기

권한이나 접근 제어가 큰 부담이 아니라면, Obsidian이 실제로 쓰는 vault 폴더를 그대로 pwiki에 연결해도 됩니다. pwiki는 스캔할 때 `.obsidian/`, `.git/`을 비롯한 dotfile들을 알아서 빼기 때문에, Obsidian 설정과 플러그인은 웹에서 보이지 않고 `.md` 파일만 노출됩니다.

한 가지 주의할 점: Obsidian과 pwiki가 같은 파일을 동시에 쓰는 상황은 피하는 게 좋습니다. 가장 안전한 패턴은 _라이브 Obsidian vault 옆에서 pwiki를 read-only로_ 두거나, _쓰기를 허용하는 pwiki는 별도 Git 체크아웃에 두고 Obsidian의 Git plugin으로 vault와 동기화_ 하는 방식입니다.

### 6. Obsidian LLM 플러그인으로 쌓은 노트를 어디서나 보기

Obsidian과 LLM 플러그인(Smart Connections, Copilot, Text Generator 같은 것들)을 같이 써서 개인 지식베이스(LLM wiki)를 만들어 왔다면, 결과물은 결국 `.md` 파일들입니다. 같은 vault를 pwiki에 연결해 두면, Obsidian 클라이언트가 없는 환경에서도 어떤 브라우저에서든 토큰 소모 없이 그 노트를 읽고 검색할 수 있습니다. vault는 계속 Obsidian + LLM으로 채워지고, pwiki는 그 결과를 그대로 보여주는 역할만 합니다.

## 모바일 지원

모바일에서는 pwiki도 단순하게 동작합니다. 사이드바 대신 폴더 drawer가 뜨고, 작은 화면에서 본문을 읽기 좋게 레이아웃이 잡히며, 편집 UI는 일부러 노출하지 않습니다.

프로젝트는 Flask, Jinja2, 약간의 vanilla JavaScript만으로 최대한 단순하게 유지하는 것을 목표로 합니다. 모바일에서 안정적인 Markdown 편집기, preview, 충돌 처리, 파일 관리 UI까지 제공하려면 프론트엔드 복잡도가 크게 올라가기 때문에, 모바일은 조회 중심으로 두고 편집은 데스크톱 웹 또는 Obsidian/Git sync 흐름을 권장합니다.

## 요구 사항

- Python 3.11 이상 (표준 Docker 이미지는 3.13으로 동작하며, 이 버전에서 가장 많이 검증되어 있습니다).
- Docker / Docker Compose. 표준 배포 경로에서 사용합니다.
- 디스크에 있는 Obsidian vault. sync까지 매끄럽게 쓰려면 Git으로 관리하시는 게 좋습니다.
- Linux 또는 macOS 호스트. 다른 Unix 계열도 대체로 잘 돌지만 Windows는 검증하지 않았습니다.

## 빠른 시작

### 1. 저장소 받기

```bash
git clone https://github.com/bongdang/pwiki.git
cd pwiki
```

### 2. 설정

```bash
cp .env.example .env
$EDITOR .env
```

적어도 이 세 가지는 채워주세요.

- `PWIKI_SECRET_KEY` — 충분히 긴 랜덤 문자열. 생성용 one-liner는 `.env.example`에 적어 두었습니다.
- `PWIKI_GIT_HOST_DIR` — Obsidian vault의 Git working tree가 있는 호스트 경로.
- `PWIKI_MARKDOWN_SUBDIR` — 공개할 하위 폴더 이름. 비워두면 vault 전체가 보입니다.

Google 로그인을 쓰고 싶다면 `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `PWIKI_ADMIN_GOOGLE_EMAIL`도 같이 채워주세요. OAuth 없이 그냥 띄우고 싶다면 `PWIKI_ALLOW_ANONYMOUS=1`을 직접 켜야 합니다. 이 flag가 없으면 startup이 아예 거부되어, 설정이 비어 있는 `.env`만으로 vault가 그대로 노출되는 일을 막아 줍니다.

### 3. 설치 실행

```bash
./install.sh
```

installer는 `.env`를 같이 훑으면서 OAuth, read-only, Git 설정을 확인하고, Docker Compose를 띄운 다음 sync timer와 reverse proxy 안내, Google OAuth redirect URI까지 알려줍니다.

## 주요 환경 변수

전체 목록은 `.env.example`에 주석과 함께 정리해 두었습니다. 실제로 자주 손대는 변수들:

| 이름 | 설명 |
|---|---|
| `PWIKI_SECRET_KEY` | 로그인 세션 보호용 긴 랜덤 문자열 (운영 환경에서 필수) |
| `PWIKI_GIT_HOST_DIR` | Obsidian vault Git working tree의 호스트 경로 (컨테이너에 mount됨) |
| `PWIKI_MARKDOWN_SUBDIR` | 노출할 vault 하위 폴더, 빈 값이면 vault 전체 |
| `PWIKI_READ_ONLY` | `1`이면 모든 웹 쓰기 차단 (기본값) |
| `PWIKI_ALLOW_ANONYMOUS` | `1`이면 OAuth 없는 anonymous read-only 모드 허용 |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client id |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client secret |
| `PWIKI_ADMIN_GOOGLE_EMAIL` | 최초 admin으로 등록할 Google email |
| `PWIKI_PUBLIC_BASE_URL` | 외부 접속 base URL (프록시 뒤 OAuth redirect URI 생성에 사용) |
| `PWIKI_URL_PREFIX` | `/newwiki`처럼 하위 경로로 배포할 때 쓰는 접두어 |
| `PWIKI_INTERNAL_API_TOKEN` | `/api/internal/*`를 활성화하는 Bearer 토큰. 비어 있으면 로컬 API 전체 비활성. |
| `PWIKI_INTERNAL_API_ALLOWED_CIDRS` | 로컬 API의 CIDR 화이트리스트. 기본값은 loopback + RFC1918. |
| `PWIKI_INTERNAL_API_TRUSTED_PROXY_CIDRS` | 이 CIDR 안의 호스트에서 오는 `X-Forwarded-For`만 신뢰. 비어 있으면 항상 `remote_addr` 사용. |

## 권한 모델

인증은 Google OAuth만 씁니다 — 유일한 로그인 백엔드입니다. 관리자는 CLI나 웹 admin UI 중 편한 쪽에서 권한을 부여할 수 있습니다.

권한 값:

- `none`
- `read`
- `write`

권한은 사용자별 기본 권한과 경로별 예외 규칙으로 구성됩니다.

예를 들어 어떤 사용자는 기본적으로 `none`이지만, `공유/가족` 폴더에는 `read` 권한을 줄 수 있습니다.

권한 관리는 CLI와 웹 admin UI에서 모두 할 수 있습니다.

```bash
python -m cli users grant alice@example.com --default-permission read
python -m cli users path-grant alice@example.com 공유/가족 read
python -m cli users path-grant alice@example.com 개인 none
python -m cli users show alice@example.com
```

> 컨테이너 안에서 실행하세요 — 예:
> `docker compose exec -T pwiki sh -lc 'python -m cli users list'`.

웹 admin UI에서도 같은 작업을 할 수 있습니다.

권한이 없는 문서는 직접 URL 접근만 막는 것이 아니라 sidebar, 모바일 drawer, 검색 결과, 인덱스 페이지에서도 숨겨집니다. 따라서 vault 전체를 하나로 유지하면서도 특정 폴더만 가족이나 동료에게 공유하는 방식으로 사용할 수 있습니다.

## 로컬 read-only API

OAuth로 보호되는 웹 UI와는 별개로, pwiki는 *신뢰 가능한* 같은 호스트 / 사설망 소비자(로컬 AI 비서, RAG 파이프라인, 백업·인덱싱 스크립트 등)를 위한 read-only JSON API를 `/api/internal/*` 경로로 제공합니다. `PWIKI_INTERNAL_API_TOKEN`을 설정하기 전까지는 완전히 비활성 상태이므로, 설정 없이 배포한 인스턴스에서는 어떤 데이터도 노출되지 않습니다.

웹 UI를 호출하면 될 텐데 왜 별도 API를 두었나요?

- **OAuth 왕복이 필요 없습니다.** 백그라운드 프로세스나 로컬 AI 에이전트에는 브라우저도 Google 세션도 없습니다. 이 API는 Bearer 토큰 하나만 쓰므로 스크립트나 스케줄러에서 호출하기 쉽습니다.
- **설계상 read-only.** `GET` 핸들러만 등록되어 있고 저장·삭제·업로드 경로가 아예 없습니다. 토큰을 로컬 스크립트에 넘겨도 실수로 파일이 바뀔 일이 없습니다.
- **다층 방어.** 토큰을 비교하기 *전에* 먼저 CIDR 화이트리스트(기본: loopback + RFC1918)를 확인하고, `X-Forwarded-For`는 `PWIKI_INTERNAL_API_TRUSTED_PROXY_CIDRS`로 명시적으로 허용하기 전까지 무시합니다. 토큰만 유출되어도 사설망 밖에서는 읽을 수 없습니다.
- **경로 안전.** 모든 `path` 인자는 vault 루트 기준으로 `os.path.realpath` 해석을 거치며, `..` traversal과 vault 밖을 가리키는 symlink는 `403 forbidden_path`로 거절합니다.
- **항상 최신.** 매 요청마다 파일시스템을 직접 읽어서 응답합니다. 프로세스 내부 캐시가 없으므로 디스크에서 파일을 삭제·수정하면 다음 호출부터 즉시 반영되고, 서버를 다시 띄울 필요가 없습니다.
- **같은 vault, 별도 인덱스 없음.** 웹 UI가 읽는 그 Markdown 파일을 그대로 읽기 때문에, 동기화가 필요한 별도 인덱스가 없습니다.

### MCP가 있는데 왜?

자연스러운 질문입니다. Claude Desktop처럼 MCP를 지원하는 클라이언트가 이미 있다면, MCP 서버로 vault를 노출해서 모델이 직접 조회하게 두면 되지 않나요?

두 접근은 최적화하는 워크로드가 다릅니다. **MCP는 LLM을 오케스트레이터로 둡니다**: `search` → `page` → 다시 `search`로 이어지는 매 라운드의 tool result가 모델 컨텍스트에 누적되고, 시스템 프롬프트에는 도구 정의가 따라붙습니다. 자유로운 대화형 탐색("X에 대해 뭐 있는지 찾아서 요약해줘")에는 잘 맞지만, 같은 작업을 스케줄링해서 반복하거나 하루에 수십 번 돌리려고 하면 비용이 빠르게 누적됩니다.

**internal API는 *사용자*를 오케스트레이터로 둡니다.** 필터·랭킹·중복 제거·청크 분할이 LLM 컨텍스트 바깥에서 일어납니다. 로컬 RAG 파이프라인이라면 로컬 임베딩 모델로 vault 전체를 임베딩해 두고, top-k 청크를 이 API로 가져와서 관련 부분만 LLM에 한 번 던지면 보통 질문 하나당 round-trip 한 번으로 끝납니다. 백업 스크립트, 일일 다이제스트 cron, 로컬 인덱서 같은 non-LLM 소비자는 토큰을 0개 씁니다. 여기서 read-only 제약은 약점이 아닙니다 — 이런 워크로드는 대부분 vault에 쓸 필요가 없고, 쓰기 표면이 없는 만큼 위협 모델도 좁아집니다.

둘은 경쟁이 아니라 보완 관계입니다. "내 노트에 대화로 뭐든 물어볼 수 있는" 경험을 원하면 MCP가 더 매끄럽고, 같은 vault에 대해 반복 가능하고 스케줄링 가능하며 토큰 효율이 좋은 자동화를 원하면 이 API가 더 매끄럽습니다. 둘을 동시에 띄워서 같이 쓰는 데도 아무 제약이 없습니다.

엔드포인트:

| 메서드 & 경로 | 용도 |
|---|---|
| `GET /api/internal/health` | 서비스 상태 확인. API가 활성 상태이고 두 가지 가드를 통과했음을 확인합니다. |
| `GET /api/internal/search?q=<쿼리>&limit=<1..100>` | 대소문자 구분 없는 파일명 + 본문 검색 (기본 `limit=20`). 매치 주변을 발췌한 snippet을 돌려줍니다. |
| `GET /api/internal/page?path=<vault 기준 .md 경로>` | 한 페이지의 Markdown 본문, 제목, mtime을 반환. |
| `GET /api/internal/folder?path=<폴더>&recursive=<bool>&limit=<1..500>` | `.md` 파일과 하위 폴더 목록. 숨김 항목, `.git`, Markdown이 아닌 첨부는 자동 제외. |

에러 응답 형식은 모든 엔드포인트에서 동일합니다:

```json
{ "error": { "code": "unauthorized", "message": "Invalid internal API token." } }
```

빠른 동작 확인:

```bash
export PWIKI_INTERNAL_API_TOKEN='긴-랜덤-토큰'

curl -H "Authorization: Bearer $PWIKI_INTERNAL_API_TOKEN" \
  http://127.0.0.1:5000/api/internal/health

curl -H "Authorization: Bearer $PWIKI_INTERNAL_API_TOKEN" \
  "http://127.0.0.1:5000/api/internal/search?q=oauth&limit=10"
```

**중요:** `/api/internal/*`를 공개 reverse proxy를 통해 외부에 노출하지 마세요. 이 API는 `localhost` 또는 사설망에서 호출하는 것을 전제로 설계되었습니다. nginx가 `/` 전체를 그대로 공개한다면 별도로 `location /api/internal/ { return 404; }` 같은 차단 블록을 두거나, `docker-compose.yml`의 포트를 `127.0.0.1`에 바인딩하세요.

## 보안 기본값

pwiki는 개인 vault를 다루는 쪽이라 기본값을 보수적으로 잡았습니다.

- 기본값은 read-only입니다.
- OAuth가 설정되지 않은 anonymous 모드는 명시적으로 허용해야만 켜집니다.
- 쓰기 권한은 전체 read-only 설정과 사용자별 권한을 모두 통과해야 합니다.
- 웹 form 제출 보호가 적용됩니다.
- 보안상 SVG 첨부 표시는 기본적으로 허용하지 않습니다.
- OAuth와 웹 쓰기를 함께 사용할 때는 기본 개발용 키로 시작하지 않습니다.
- 외부 공개 배포에서는 HTTPS와 충분히 긴 랜덤 `PWIKI_SECRET_KEY`를 사용해야 합니다.

## Git 연동과 Obsidian sync

pwiki는 Markdown 파일을 직접 읽고 쓰기 때문에, Git으로 관리되는 vault와 자연스럽게 잘 어울립니다. 일반적으로는 Obsidian에서 Git plugin으로 vault를 원격 저장소와 동기화하고, pwiki 서버도 같은 로컬 Git 폴더를 바라보게 두는 구성을 씁니다.

Git으로 관리되는 vault에서 pwiki는 이런 일을 할 수 있습니다.

- vault status 확인
- sync helper 실행
- 웹 저장 후 commit
- 선택적 push
- Git 충돌이나 병합 중 상태에서 웹 저장 차단

자동 동기화는 운영자가 관리하는 timer 또는 별도 sync 방식과 함께 쓰는 것을 권장합니다.

참고 링크:

- Obsidian Git plugin: https://github.com/Vinzent03/obsidian-git
- Obsidian community plugins: https://help.obsidian.md/community-plugins

몇 가지 주의:

- 개인 vault를 public repository에 올리지 마세요.
- 민감한 기록이 있다면 private Git repository 또는 self-hosted Git 서버를 사용하세요.
- pwiki의 web-save auto commit/push를 켜기 전에는 Git remote 권한과 conflict 처리 방식을 한 번 더 확인하세요.

## 배포

Docker Compose, reverse proxy, OAuth 설정, Obsidian/Git vault sync까지 전체 배포·운영
절차는 [`deploy.md`](deploy.md)를 참고하세요.

## License

MIT License — [`LICENSE`](LICENSE) 참고.
