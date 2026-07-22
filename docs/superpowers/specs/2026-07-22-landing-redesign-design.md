# 현관 랜딩 페이지 재설계 + 터널 URL 단일화

작성일: 2026-07-22

## 배경

`stock-report-bice.vercel.app`(Vercel 프로젝트 `stock-report`)이 실제 현관이고, 소스는 master의
`src/app/page.tsx`(Next.js 14 App Router)다. 정적 `dashboard/landing/index.html`이 속했던 Vercel
프로젝트 `stock-dashboard`(`prj_kDkA7…`)는 계정 프로젝트 목록에 더 이상 존재하지 않는다.

현재 랜딩에는 세 가지 문제가 있다.

1. **성격 불일치** — "React는 입구만 맡고, 본체는 터널로 바로 엽니다", 카드 3개가 `React / Python /
   Bridge`. 개발자가 자기 아키텍처를 설명하는 페이지라, 남에게 프로젝트를 보여주는 용도로 맞지 않는다.
2. **하드코딩된 가짜 지표** — `page.tsx:143-147`의 `최근 이벤트 40건 / 누적 기억 50건 / 모델 파일 4개`는
   고정 문자열인데 실시간 지표처럼 보인다.
3. **터널 URL 자동 갱신이 현관을 비켜간다** — URL이 `src/app/page.tsx:3`과 `src/app/bridge/page.tsx:4`에
   각각 하드코딩돼 있는데, `scripts/cloudflared_watchdog.sh:65`는 배포되지 않는
   `dashboard/landing/index.html`만 `sed`로 갱신한다. 터널이 재기동되면 현관 링크가 죽은 주소를 가리킨다.
   현재 무사한 이유는 cloudflared 프로세스가 23일째 안 죽었기 때문일 뿐이다.

## 목표

1. 현관을 포트폴리오용 랜딩 페이지로 재작성한다.
2. 터널 URL을 단일 진실원으로 만들고 워치독 자동 갱신에 연결한다.
3. 죽은 정적 랜딩(`dashboard/landing/`)을 제거한다.

## 비목표

- 다른 라우트(`portfolio`, `charts`, `ai-console` 등 10개)는 건드리지 않는다.
- `src/lib/site-data.ts`의 목업 데이터 정리는 이번 범위가 아니다.
- Streamlit 대시보드 UI는 변경하지 않는다.
- `codex/react-frontend-shell` 원격 브랜치는 삭제하지 않는다.

## 설계

### 1. 터널 URL 단일 진실원

새 파일 `src/lib/gateway.ts`:

```ts
// scripts/cloudflared_watchdog.sh 가 이 파일의 URL 을 정규식으로 자동 치환한다.
// 한 줄·작은따옴표·리터럴 형태를 유지할 것. 형식이 바뀌면 자동 갱신이 조용히 깨진다.
export const gatewayUrl = 'https://growing-chester-concepts-cow.trycloudflare.com/';
```

- `src/app/page.tsx` → `import { gatewayUrl } from '../lib/gateway';`
- `src/app/bridge/page.tsx` → `import { gatewayUrl } from '../../lib/gateway';`
  (`tsconfig.json`에 path alias가 없으므로 기존 코드와 같이 상대 경로를 쓴다.)
- 두 파일의 기존 하드코딩 상수(`gatewayUrl`, `bridgeUrl`)는 제거한다.

### 2. 워치독 연결

`scripts/cloudflared_watchdog.sh`:

- `LANDING` 변수와 `dashboard/landing/index.html` 참조를 `src/lib/gateway.ts`로 교체한다.
- `sed` 정규식 `https://[a-z0-9-]+\.trycloudflare\.com`은 그대로 쓴다 — `gateway.ts`의 URL에도 그대로 맞는다.
- `git diff --quiet` 검사 대상과 `git add` 대상도 함께 교체한다.
- master 전용 워크트리에서 커밋·푸시하는 기존 구조는 유지한다. 푸시가 곧 Vercel 재배포다.

### 3. 죽은 정적 랜딩 제거

`dashboard/landing/index.html`과 `dashboard/landing/vercel.json`을 삭제한다. 워치독이 더 이상 이 경로를
참조하지 않게 된 뒤에 지운다(순서 중요 — 먼저 지우면 다음 틱에서 워치독이 실패한다).

### 4. 랜딩 구조

위에서 아래로:

| 섹션 | 내용 |
| --- | --- |
| 히어로 | 프로젝트 정체성 한 줄 + `대시보드 열기`(gatewayUrl) + 태그(🔒 비밀번호 필요 · 개인 서버 구동 · 표시 전용·주문 0) |
| UI 미리보기 | CSS로 만든 추상 목업(사이드바 + 지표 카드 + 스파크라인). **"UI 예시" 라벨을 화면에 명시** |
| 기능 | 9개 화면 카드 — 홈, 포트폴리오, 종목 분석, 차트, 시장, 모의투자, 리서치, AI 콘솔, AI 위키 |
| 아키텍처 | Oracle VM(Streamlit · ML · 크론) → Cloudflare Tunnel(HTTPS 중계) → Vercel 현관 흐름 + 스택 칩 |
| 엔지니어링 | 자동매매 0·하드블록 / 동시성·정합성 / 백테스트 OOS 게이트 / 정직한 NO-GO / 운영 신뢰성 |
| 푸터 | 면책·보안 문구 (기존 정적 랜딩 문구를 계승) |

### 5. 정직성 규칙

이 페이지는 남에게 보여주는 용도이므로 다음을 지킨다.

- `page.tsx:143-147`의 하드코딩 지표 3개는 **삭제한다**. 실시간처럼 보이는 고정값을 남기지 않는다.
- 수치는 검증된 정적 사실만 쓰고 라벨을 정확히 단다:
  테스트 파일 135 · 크론 스크립트 41 · 데이터 프로바이더 24 · 대시보드 화면 9.
  ("테스트 135개"가 아니라 "테스트 파일 135개")
- 구현 시점에 이 수치를 재확인한다. 값이 바뀌었으면 문서가 아니라 실제 값을 따른다.
- UI 목업에 구체적인 티커·보유금액·수익률을 넣지 않는다. 실적·수익률 주장을 하지 않는다.
- 보유 종목 등 개인 정보는 노출하지 않는다. 대시보드는 비밀번호 뒤에 그대로 둔다.

### 6. 스타일 · 코드 품질

- 현재 278줄이 거의 전부 인라인 `style={{}}`이다. 반복되는 카드·칩·그리드 스타일은
  `src/app/globals.css`(839줄, 이미 `.status-chip`·`.report-status` 등 보유)로 옮기고 클래스로 쓴다.
- 다크 단일 테마 유지. 기존 팔레트(`--teal`, `--violet`, `--amber`, `--muted`)와 배경 그라디언트를 계승한다.
- 반응형: 현재 `gridTemplateColumns: repeat(3, …)`와 `minmax(320px, …)`가 고정이라 좁은 화면에서 깨진다.
  미디어쿼리를 넣어 375px에서 세로 1열로 접히게 한다.
- 외부 폰트·CDN을 추가하지 않는다. 아이콘은 이미 의존성에 있는 `lucide-react`만 쓴다.

### 7. 검증

1. `npm install` (현재 `node_modules` 없음)
2. `npm run typecheck` — 통과
3. `npm run build` — 통과
4. 375px / 768px / 1280px 폭에서 레이아웃 확인
5. **sed 회귀 테스트**: `gateway.ts` 사본에 워치독과 동일한 정규식을 실행해 치환되는지 확인한 뒤 되돌린다.
   이번 변경의 핵심 리스크이므로 반드시 실행한다.
6. 워치독 변경 후 `bash -n scripts/cloudflared_watchdog.sh` 문법 검사
7. 기존 파이썬 테스트에 영향이 없는지 확인 (`dashboard/landing` 삭제가 참조를 깨지 않는지 grep)

### 8. 리스크

| 리스크 | 대응 |
| --- | --- |
| sed 형식 불일치로 URL 자동 갱신이 조용히 깨짐 | 검증 5번을 필수로 실행. `gateway.ts`에 형식 유지 주석을 남김 |
| `dashboard/landing` 삭제 순서가 뒤바뀌어 워치독 실패 | 워치독 수정 → 검증 → 삭제 순서를 지킴 |
| master push가 곧 배포 | 커밋·푸시 전에 사용자 승인을 받는다 |
| 다른 세션과 충돌 | 작업 직전 `git fetch` 후 master 기준 확인 |

## 열린 질문

없음.
