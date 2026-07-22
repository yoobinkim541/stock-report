# 현관 랜딩 재설계 + 터널 URL 단일화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vercel 현관(`src/app/page.tsx`)을 포트폴리오용 랜딩 페이지로 재작성하고, 터널 URL을 단일 진실원으로 만들어 워치독 자동 갱신에 연결한다.

**Architecture:** 터널 URL을 `src/lib/gateway.ts` 한 곳에 두고 `cloudflared_watchdog.sh`가 그 파일을 `sed`로 갱신하게 바꾼다. 랜딩은 인라인 스타일을 걷어내고 `globals.css`에 `.lp-*` 프리픽스 클래스로 옮긴다. 죽은 정적 랜딩 `dashboard/landing/`은 워치독이 참조를 끊은 뒤 삭제한다.

**Tech Stack:** Next.js 14.2.31 (App Router), React 18.3.1, TypeScript 5.6, lucide-react, bash, pytest

## Global Constraints

- 터널 URL 리터럴은 정규식 `https://[a-z0-9-]+\.trycloudflare\.com` 에 매칭되어야 한다. `gateway.ts`의 URL은 **한 줄·작은따옴표 리터럴** 형태를 유지한다.
- 새 CSS 클래스는 모두 `.lp-` 프리픽스를 쓴다. `globals.css`의 기존 클래스(`.hero`, `.metric-card`, `.panel`, `.status-chip` 등)는 다른 라우트가 사용 중이므로 **수정하지 않는다**.
- 기존 CSS 변수만 사용한다: `--bg`, `--bg-elevated`, `--bg-soft`, `--border`, `--text`, `--muted`, `--teal`, `--violet`, `--amber`, `--rose`, `--shadow`, `--radius-xl`, `--radius-lg`, `--radius-md`, `--radius-sm`.
- 외부 폰트·CDN·이미지를 추가하지 않는다. 아이콘은 `lucide-react`만 쓴다.
- import는 상대 경로를 쓴다 (`tsconfig.json`에 path alias 없음).
- 검증된 수치만 표기하고 라벨을 정확히 단다: **테스트 파일 135 · 크론 스크립트 41 · 데이터 프로바이더 24 · 대시보드 화면 9**. 구현 시점에 재확인하고, 다르면 실제 값을 쓴다.
- UI 목업에 구체적 티커·보유금액·수익률을 넣지 않는다. 실적·수익률 주장을 하지 않는다.
- `dashboard/landing/` 삭제는 **워치독 수정·검증 이후**에만 한다.

---

### Task 1: 터널 URL 단일 진실원 + 워치독 연결

**Files:**
- Create: `src/lib/gateway.ts`
- Modify: `src/app/page.tsx:3` (하드코딩 상수 → import)
- Modify: `src/app/bridge/page.tsx:4` (하드코딩 상수 → import)
- Modify: `scripts/cloudflared_watchdog.sh:5-6,17,65-67`
- Test: `tests/test_gateway_url_sync.py`

**Interfaces:**
- Consumes: 없음
- Produces: `src/lib/gateway.ts` 에서 `export const gatewayUrl: string`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_gateway_url_sync.py`:

```python
"""터널 URL 자동 갱신 경로 회귀 테스트.

cloudflared_watchdog.sh 는 sed 정규식으로 gateway.ts 의 URL 을 치환한다.
파일 형식이 바뀌면 치환이 조용히 실패하고 현관이 죽은 터널을 가리키게 되므로,
'워치독이 실제로 치환할 수 있는 형태인가'를 테스트로 고정한다.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWAY = ROOT / "src" / "lib" / "gateway.ts"
WATCHDOG = ROOT / "scripts" / "cloudflared_watchdog.sh"
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def test_gateway_file_has_tunnel_url():
    assert TUNNEL_RE.search(GATEWAY.read_text(encoding="utf-8"))


def test_watchdog_targets_gateway_file():
    body = WATCHDOG.read_text(encoding="utf-8")
    assert "src/lib/gateway.ts" in body
    assert "dashboard/landing/index.html" not in body


def test_sed_actually_replaces_url(tmp_path):
    """워치독과 동일한 sed 명령이 gateway.ts 를 실제로 치환하는지 확인."""
    work = tmp_path / "gateway.ts"
    work.write_text(GATEWAY.read_text(encoding="utf-8"), encoding="utf-8")
    new = "https://replaced-by-test.trycloudflare.com"
    subprocess.run(
        ["sed", "-i", "-E", f"s#https://[a-z0-9-]+\\.trycloudflare\\.com#{new}#g", str(work)],
        check=True,
    )
    after = work.read_text(encoding="utf-8")
    assert new in after
    assert TUNNEL_RE.findall(after) == [new]


def test_gateway_url_is_single_source():
    """page.tsx / bridge/page.tsx 에 URL 리터럴이 남아 있으면 안 된다."""
    for rel in ("src/app/page.tsx", "src/app/bridge/page.tsx"):
        body = (ROOT / rel).read_text(encoding="utf-8")
        assert not TUNNEL_RE.search(body), f"{rel} 에 터널 URL 리터럴이 남아 있음"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `./.venv/bin/pytest tests/test_gateway_url_sync.py -q`
Expected: FAIL — `gateway.ts` 없음(FileNotFoundError), 워치독에 `src/lib/gateway.ts` 없음.

- [ ] **Step 3: `src/lib/gateway.ts` 생성**

```ts
// 터널 주소 단일 진실원.
// scripts/cloudflared_watchdog.sh 가 아래 URL 을 sed 정규식으로 자동 치환한다:
//   s#https://[a-z0-9-]+\.trycloudflare\.com#<새 URL>#g
// 한 줄·작은따옴표 리터럴 형태를 유지할 것. 형식을 바꾸면 자동 갱신이 조용히 깨지고
// 터널 재기동 시 현관이 죽은 주소를 가리키게 된다. (tests/test_gateway_url_sync.py 가 감시)
export const gatewayUrl = 'https://growing-chester-concepts-cow.trycloudflare.com/';
```

- [ ] **Step 4: `src/app/page.tsx` 수정**

3행 `const gatewayUrl = '...';` 를 삭제하고 1행 import 아래에 추가:

```ts
import { gatewayUrl } from '../lib/gateway';
```

- [ ] **Step 5: `src/app/bridge/page.tsx` 수정**

4행 `const bridgeUrl = '...';` 를 삭제하고 import 추가, 파일 내 `bridgeUrl` 참조를 `gatewayUrl` 로 치환:

```ts
import { gatewayUrl } from '../../lib/gateway';
```

- [ ] **Step 6: 워치독 수정**

`scripts/cloudflared_watchdog.sh` 17행:

```bash
LANDING="$PROJECT_DIR/src/lib/gateway.ts"
```

65-67행:

```bash
    sed -i -E "s#https://[a-z0-9-]+\.trycloudflare\.com#${NEW}#g" src/lib/gateway.ts
    if ! git diff --quiet -- src/lib/gateway.ts 2>/dev/null; then
        git add src/lib/gateway.ts
```

5-6행 주석도 사실에 맞게 교체:

```bash
# 죽으면 재시작 → 새 trycloudflare URL 확보 → src/lib/gateway.ts 의 상수를 교체하고
# git push → Vercel(Next.js 앱)이 자동 재배포.
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `./.venv/bin/pytest tests/test_gateway_url_sync.py -q`
Expected: PASS (4 passed)

- [ ] **Step 8: 워치독 문법 검사**

Run: `bash -n scripts/cloudflared_watchdog.sh`
Expected: 출력 없음 (문법 정상)

- [ ] **Step 9: 커밋**

```bash
git add src/lib/gateway.ts src/app/page.tsx src/app/bridge/page.tsx scripts/cloudflared_watchdog.sh tests/test_gateway_url_sync.py
git commit -m "fix) 터널 URL 을 단일 진실원으로 모으고 워치독이 현관을 갱신하도록 고침"
```

---

### Task 2: 죽은 정적 랜딩 삭제

**Files:**
- Delete: `dashboard/landing/index.html`, `dashboard/landing/vercel.json`

**Interfaces:**
- Consumes: Task 1 (워치독이 더 이상 `dashboard/landing` 을 참조하지 않음)
- Produces: 없음

- [ ] **Step 1: 잔여 참조 확인**

Run:
```bash
grep -rn "dashboard/landing" --include="*.py" --include="*.sh" --include="*.ts" --include="*.tsx" --include="*.json" . | grep -v node_modules | grep -v docs/superpowers
```
Expected: 출력 없음. 출력이 있으면 멈추고 그 참조를 먼저 정리한다.

- [ ] **Step 2: 삭제**

```bash
git rm dashboard/landing/index.html dashboard/landing/vercel.json
```

- [ ] **Step 3: 테스트 재확인**

Run: `./.venv/bin/pytest tests/test_gateway_url_sync.py -q`
Expected: PASS (4 passed)

- [ ] **Step 4: 커밋**

```bash
git commit -m "chore) 배포되지 않는 정적 랜딩 제거"
```

---

### Task 3: 랜딩 전용 CSS 추가

**Files:**
- Modify: `src/app/globals.css` (파일 끝에 `.lp-*` 블록 추가)

**Interfaces:**
- Consumes: 없음
- Produces: 클래스 `.lp-page .lp-wrap .lp-nav .lp-hero .lp-hero-copy .lp-kicker .lp-title .lp-lead .lp-cta-row .lp-cta .lp-cta-ghost .lp-chips .lp-chip .lp-mock .lp-mock-label .lp-mock-body .lp-mock-side .lp-mock-line .lp-mock-main .lp-mock-card .lp-spark .lp-section .lp-section-head .lp-grid-3 .lp-card .lp-card-title .lp-card-body .lp-flow .lp-flow-step .lp-flow-arrow .lp-stack .lp-stat-row .lp-stat .lp-stat-num .lp-stat-label .lp-foot`

- [ ] **Step 1: `globals.css` 끝에 추가**

```css
/* ── 랜딩 페이지 (src/app/page.tsx) 전용. 다른 라우트와 충돌하지 않도록 lp- 프리픽스 ── */
.lp-page { min-height: 100vh; padding: 24px 20px 56px; }
.lp-wrap { max-width: 1120px; margin: 0 auto; display: grid; gap: 40px; }
.lp-nav { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.lp-hero { display: grid; gap: 22px; padding: 8px 0 4px; }
.lp-hero-copy { display: grid; gap: 14px; max-width: 780px; }
.lp-kicker { font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); }
.lp-title { margin: 0; font-size: 46px; line-height: 1.12; letter-spacing: -0.01em; }
.lp-lead { margin: 0; font-size: 17px; line-height: 1.75; color: var(--muted); }
.lp-cta-row { display: flex; flex-wrap: wrap; gap: 10px; }
.lp-cta {
  display: inline-flex; align-items: center; gap: 8px; padding: 13px 20px;
  border-radius: var(--radius-md); font-size: 15px; font-weight: 700; text-decoration: none;
  background: var(--teal); color: #04211f; border: 1px solid transparent;
}
.lp-cta:hover { filter: brightness(1.08); }
.lp-cta-ghost {
  display: inline-flex; align-items: center; gap: 8px; padding: 13px 20px;
  border-radius: var(--radius-md); font-size: 15px; font-weight: 600; text-decoration: none;
  background: transparent; color: var(--text); border: 1px solid var(--border);
}
.lp-cta-ghost:hover { border-color: var(--teal); }
.lp-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.lp-chip {
  font-size: 12px; color: var(--muted); background: var(--bg-soft);
  border: 1px solid var(--border); border-radius: 999px; padding: 6px 12px;
}
.lp-mock {
  border: 1px solid var(--border); border-radius: var(--radius-xl);
  background: var(--bg-elevated); box-shadow: var(--shadow); overflow: hidden;
}
.lp-mock-label {
  font-size: 11px; letter-spacing: 0.08em; color: var(--muted);
  padding: 10px 16px; border-bottom: 1px solid var(--border); background: var(--bg-soft);
}
.lp-mock-body { display: grid; grid-template-columns: 168px minmax(0, 1fr); min-height: 232px; }
.lp-mock-side { border-right: 1px solid var(--border); padding: 16px; display: grid; gap: 10px; align-content: start; }
.lp-mock-line { height: 9px; border-radius: 999px; background: rgba(148, 163, 184, 0.16); }
.lp-mock-line.short { width: 56%; }
.lp-mock-line.accent { background: rgba(52, 215, 201, 0.32); width: 72%; }
.lp-mock-main { padding: 16px; display: grid; gap: 12px; align-content: start; }
.lp-mock-card {
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: rgba(7, 11, 20, 0.6); padding: 14px; display: grid; gap: 9px;
}
.lp-spark { width: 100%; height: 56px; display: block; }
.lp-section { display: grid; gap: 18px; }
.lp-section-head { display: grid; gap: 6px; }
.lp-section-head h2 { margin: 0; font-size: 24px; }
.lp-section-head p { margin: 0; color: var(--muted); line-height: 1.7; }
.lp-grid-3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.lp-card {
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  background: var(--bg-soft); padding: 18px; display: grid; gap: 8px; align-content: start;
}
.lp-card-title { display: flex; align-items: center; gap: 9px; font-size: 15px; font-weight: 700; }
.lp-card-body { margin: 0; color: var(--muted); font-size: 14px; line-height: 1.65; }
.lp-flow { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr; gap: 12px; align-items: stretch; }
.lp-flow-step {
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  background: var(--bg-soft); padding: 18px; display: grid; gap: 7px; align-content: start;
}
.lp-flow-arrow { display: grid; place-items: center; color: var(--muted); }
.lp-stack { display: flex; flex-wrap: wrap; gap: 8px; }
.lp-stat-row { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.lp-stat {
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  background: rgba(7, 11, 20, 0.6); padding: 16px; display: grid; gap: 5px;
}
.lp-stat-num { font-size: 26px; font-weight: 700; }
.lp-stat-label { font-size: 12px; color: var(--muted); }
.lp-foot {
  border-top: 1px solid var(--border); padding-top: 20px;
  color: var(--muted); font-size: 13px; line-height: 1.8;
}

@media (max-width: 900px) {
  .lp-grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .lp-stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .lp-flow { grid-template-columns: 1fr; }
  .lp-flow-arrow { transform: rotate(90deg); }
}

@media (max-width: 560px) {
  .lp-page { padding: 18px 14px 40px; }
  .lp-title { font-size: 32px; }
  .lp-lead { font-size: 15px; }
  .lp-grid-3 { grid-template-columns: 1fr; }
  .lp-stat-row { grid-template-columns: 1fr; }
  .lp-mock-body { grid-template-columns: 1fr; }
  .lp-mock-side { border-right: none; border-bottom: 1px solid var(--border); }
  .lp-cta, .lp-cta-ghost { width: 100%; justify-content: center; }
}
```

- [ ] **Step 2: 기존 클래스 미수정 확인**

Run: `git diff --unified=0 src/app/globals.css | grep '^-' | grep -v '^---'`
Expected: 출력 없음 (순수 추가만).

- [ ] **Step 3: 커밋**

```bash
git add src/app/globals.css
git commit -m "add) 랜딩 전용 lp- 스타일을 추가"
```

---

### Task 4: 랜딩 페이지 재작성

**Files:**
- Modify: `src/app/page.tsx` (전면 재작성)

**Interfaces:**
- Consumes: Task 1 `gatewayUrl`, Task 3 `.lp-*` 클래스
- Produces: 기본 export `HomePage()`

- [ ] **Step 1: 수치 재확인**

Run:
```bash
echo "테스트 파일: $(ls tests/test_*.py | wc -l)"; echo "크론: $(ls crons/*.py | grep -v __init__ | wc -l)"; echo "프로바이더: $(ls providers/*.py | grep -v __init__ | wc -l)"; echo "화면: $(ls dashboard/pages/*.py | grep -v __init__ | wc -l)"
```
Expected: 135 / 41 / 24 / 9. 다르면 **실제 출력값을 코드에 반영한다**.

- [ ] **Step 2: `src/app/page.tsx` 전체 교체**

```tsx
import {
  ArrowRight,
  BarChart3,
  BookOpen,
  Bot,
  Building2,
  CalendarDays,
  ExternalLink,
  FlaskConical,
  Home,
  LineChart,
  Lock,
  PieChart,
  ServerCog,
  ShieldCheck,
  Wallet,
} from 'lucide-react';

import { gatewayUrl } from '../lib/gateway';

const screens = [
  { icon: Home, title: '홈', body: '오늘의 시장 요약과 먼저 확인할 항목을 모아 봅니다.' },
  { icon: Wallet, title: '포트폴리오', body: '보유 구성과 비중, 리스크 노출을 한 화면에서 점검합니다.' },
  { icon: Building2, title: '종목 분석', body: '가치평가·재무·수급 지표를 종목 단위로 묶어 보여줍니다.' },
  { icon: LineChart, title: '차트', body: '추세, 이동평균, 지지·저항 구간을 확인합니다.' },
  { icon: BarChart3, title: '시장', body: '지수와 섹터 흐름, 주요 일정을 추적합니다.' },
  { icon: FlaskConical, title: '모의투자', body: '실제 주문 없이 전략을 시뮬레이션합니다.' },
  { icon: PieChart, title: '리서치', body: '리포트와 수집한 원문을 아카이브해 다시 찾습니다.' },
  { icon: Bot, title: 'AI 콘솔', body: '대화로 분석을 요청하고 결과를 바로 확인합니다.' },
  { icon: BookOpen, title: 'AI 위키', body: '대화에서 얻은 판단을 지식 카드로 승격해 재사용합니다.' },
];

const highlights = [
  {
    title: '자동매매 0 · 하드블록',
    body: '주문 실행 경로를 아예 두지 않았습니다. 연동은 조회 전용이고, 자동 집행은 설계 단계에서 막혀 있습니다.',
  },
  {
    title: '동시성 · 데이터 정합성',
    body: '상태 파일은 원자적 쓰기와 락으로 다루고, 기록은 정해진 단일 writer 를 통해서만 남깁니다.',
  },
  {
    title: '백테스트 OOS 게이트',
    body: '표본 외 검증을 통과하지 못한 전략은 반영하지 않습니다. 결과가 나쁘면 그대로 NO-GO 로 남깁니다.',
  },
  {
    title: '운영 신뢰성',
    body: '워치독이 프로세스 생존뿐 아니라 코드 변경까지 감지해, 낡은 프로세스가 조용히 남는 상황을 막습니다.',
  },
];

const stats = [
  { num: '135', label: '테스트 파일' },
  { num: '41', label: '크론 스크립트' },
  { num: '24', label: '데이터 프로바이더' },
  { num: '9', label: '대시보드 화면' },
];

const stack = ['Python 3.11', 'Streamlit', 'Next.js 14', 'TypeScript', 'Cloudflare Tunnel', 'Vercel', 'Oracle Cloud'];

export default function HomePage() {
  return (
    <main className="lp-page">
      <div className="lp-wrap">
        <nav className="lp-nav">
          <div className="lp-card-title">
            <ServerCog size={18} color="var(--teal)" />
            Stock Report
          </div>
          <a href={gatewayUrl} target="_blank" rel="noreferrer" className="status-chip teal">
            대시보드 열기
          </a>
        </nav>

        <header className="lp-hero">
          <div className="lp-hero-copy">
            <span className="lp-kicker">개인 투자 인텔리전스 터미널</span>
            <h1 className="lp-title">
              시장 수집부터 리스크 점검까지,
              <br />
              개인 서버에서 도는 투자 분석 터미널
            </h1>
            <p className="lp-lead">
              데이터 수집·정제, 지표 계산, 백테스트, 리포트 생성까지 직접 만들어 한 대의 서버에서 운영합니다.
              이 페이지는 그 터미널로 들어가는 입구이고, 실제 화면은 비밀번호 뒤에 있습니다.
            </p>
          </div>

          <div className="lp-cta-row">
            <a href={gatewayUrl} target="_blank" rel="noreferrer" className="lp-cta">
              대시보드 열기
              <ExternalLink size={16} />
            </a>
            <a href="/bridge" className="lp-cta-ghost">
              출입문 설정
              <ArrowRight size={16} />
            </a>
          </div>

          <div className="lp-chips">
            <span className="lp-chip">
              <Lock size={11} /> 비밀번호 필요
            </span>
            <span className="lp-chip">개인 서버 구동</span>
            <span className="lp-chip">표시 전용 · 주문 0</span>
          </div>
        </header>

        <section className="lp-section">
          <div className="lp-mock">
            <div className="lp-mock-label">UI 예시 — 실제 데이터가 아닌 레이아웃 표현입니다</div>
            <div className="lp-mock-body">
              <div className="lp-mock-side">
                <div className="lp-mock-line accent" />
                <div className="lp-mock-line short" />
                <div className="lp-mock-line" />
                <div className="lp-mock-line short" />
                <div className="lp-mock-line" />
              </div>
              <div className="lp-mock-main">
                <div className="lp-mock-card">
                  <div className="lp-mock-line short" />
                  <svg className="lp-spark" viewBox="0 0 320 56" preserveAspectRatio="none" aria-hidden="true">
                    <polyline
                      points="0,44 40,38 80,41 120,26 160,30 200,18 240,23 280,12 320,16"
                      fill="none"
                      stroke="var(--teal)"
                      strokeWidth="2"
                    />
                  </svg>
                </div>
                <div className="lp-mock-card">
                  <div className="lp-mock-line" />
                  <div className="lp-mock-line short" />
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-section-head">
            <h2>무엇을 볼 수 있나</h2>
            <p>대시보드는 9개 화면으로 나뉘어 있습니다.</p>
          </div>
          <div className="lp-grid-3">
            {screens.map(({ icon: Icon, title, body }) => (
              <article key={title} className="lp-card">
                <div className="lp-card-title">
                  <Icon size={16} color="var(--teal)" />
                  {title}
                </div>
                <p className="lp-card-body">{body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-section-head">
            <h2>어떻게 돌아가나</h2>
            <p>무거운 처리는 개인 서버가 맡고, 바깥에는 입구만 노출합니다.</p>
          </div>
          <div className="lp-flow">
            <div className="lp-flow-step">
              <div className="lp-card-title">Oracle Cloud VM</div>
              <p className="lp-card-body">Streamlit 대시보드, ML 파이프라인, 크론 작업이 상주합니다.</p>
            </div>
            <div className="lp-flow-arrow">
              <ArrowRight size={18} />
            </div>
            <div className="lp-flow-step">
              <div className="lp-card-title">Cloudflare Tunnel</div>
              <p className="lp-card-body">서버를 직접 열지 않고 HTTPS 만 중계합니다.</p>
            </div>
            <div className="lp-flow-arrow">
              <ArrowRight size={18} />
            </div>
            <div className="lp-flow-step">
              <div className="lp-card-title">Vercel 현관</div>
              <p className="lp-card-body">지금 보고 있는 이 페이지. 소개와 진입만 담당합니다.</p>
            </div>
          </div>
          <div className="lp-stack">
            {stack.map((item) => (
              <span key={item} className="lp-chip">
                {item}
              </span>
            ))}
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-section-head">
            <h2>설계에서 신경 쓴 것</h2>
            <p>편의보다 안전과 재현성을 우선했습니다.</p>
          </div>
          <div className="lp-grid-3">
            {highlights.map(({ title, body }) => (
              <article key={title} className="lp-card">
                <div className="lp-card-title">
                  <ShieldCheck size={16} color="var(--violet)" />
                  {title}
                </div>
                <p className="lp-card-body">{body}</p>
              </article>
            ))}
          </div>
          <div className="lp-stat-row">
            {stats.map(({ num, label }) => (
              <div key={label} className="lp-stat">
                <span className="lp-stat-num">{num}</span>
                <span className="lp-stat-label">{label}</span>
              </div>
            ))}
          </div>
        </section>

        <footer className="lp-foot">
          <CalendarDays size={13} /> 데이터와 모델은 개인 서버에서 구동되며 Cloudflare 는 HTTPS 만 중계합니다.
          <br />
          표시·정보용이며 매매 신호가 아닙니다. 투자 판단과 책임은 이용자 본인에게 있습니다.
        </footer>
      </div>
    </main>
  );
}
```

- [ ] **Step 3: 가짜 지표가 사라졌는지 확인**

Run: `grep -nE "최근 이벤트|누적 기억|모델 파일" src/app/page.tsx`
Expected: 출력 없음.

- [ ] **Step 4: 커밋**

```bash
git add src/app/page.tsx
git commit -m "fix) 현관을 프로젝트 소개 랜딩으로 재작성하고 하드코딩 지표를 제거함"
```

---

### Task 5: 빌드 검증

**Files:** 없음 (검증 전용)

**Interfaces:**
- Consumes: Task 1·3·4 전체
- Produces: 없음

- [ ] **Step 1: 의존성 설치**

Run: `npm install`
Expected: 성공 종료. `node_modules/` 생성.

- [ ] **Step 2: 타입 검사**

Run: `npm run typecheck`
Expected: 오류 없이 종료(코드 0).

- [ ] **Step 3: 프로덕션 빌드**

Run: `npm run build`
Expected: `Compiled successfully`, 라우트 목록에 `/` 포함, 오류 0.

- [ ] **Step 4: 전체 관련 테스트**

Run: `./.venv/bin/pytest tests/test_gateway_url_sync.py tests/test_dashboard_pages.py -q`
Expected: 전부 PASS.

- [ ] **Step 5: 최종 sed 회귀 재확인**

Run:
```bash
cp src/lib/gateway.ts /tmp/gw.bak && sed -i -E "s#https://[a-z0-9-]+\.trycloudflare\.com#https://zzz-test.trycloudflare.com#g" src/lib/gateway.ts && grep -c "zzz-test" src/lib/gateway.ts && cp /tmp/gw.bak src/lib/gateway.ts && git diff --quiet src/lib/gateway.ts && echo "RESTORED CLEAN"
```
Expected: `1` 그리고 `RESTORED CLEAN`.

---

### Task 6: 배포

**Files:** 없음

**Interfaces:**
- Consumes: Task 5 전량 통과
- Produces: 없음

- [ ] **Step 1: master 기준 최신화 확인**

Run: `git fetch -q origin && git rev-list --left-right --count origin/master...HEAD`
Expected: 왼쪽 `0` (master 에 새 커밋 없음). 왼쪽이 0 이 아니면 rebase 후 Task 5 를 다시 돌린다.

- [ ] **Step 2: 푸시**

Run: `git push origin HEAD:master`
Expected: 성공. Vercel 이 자동 재배포.

- [ ] **Step 3: 배포 확인**

Run: `sleep 90 && curl -s -o /dev/null -w "%{http_code}\n" https://stock-report-bice.vercel.app && curl -s https://stock-report-bice.vercel.app | grep -c "개인 서버에서 도는 투자 분석 터미널"`
Expected: `200` 그리고 `1` 이상.

---

## Self-Review

**1. 스펙 커버리지**
- 목표 1(포트폴리오 랜딩) → Task 3·4
- 목표 2(터널 URL 단일화 + 워치독) → Task 1
- 목표 3(죽은 정적 랜딩 제거) → Task 2
- 정직성 규칙(가짜 지표 삭제, 검증 수치, 목업 라벨) → Task 4 Step 1·2·3
- 스타일/반응형 → Task 3 (미디어쿼리 900px·560px)
- 검증 항목 1~7 → Task 1 Step 7·8, Task 5 전체
- 작업 순서 리스크(워치독 먼저, 삭제 나중) → Task 1 → Task 2 순서로 고정

**2. 플레이스홀더** 없음. 모든 코드 블록이 실제 내용.

**3. 타입 일관성** `gatewayUrl` 이름이 Task 1(정의)·Task 4(사용)에서 동일. `.lp-*` 클래스는 Task 3 Produces 목록과 Task 4 사용처가 일치.
