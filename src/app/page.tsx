import {
  ArrowRight,
  BarChart3,
  BookOpen,
  Bot,
  Building2,
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
  { num: '136', label: '테스트 파일' },
  { num: '41', label: '크론 스크립트' },
  { num: '24', label: '데이터 프로바이더' },
  { num: '9', label: '대시보드 화면' },
];

const stack = [
  'Python 3.11',
  'Streamlit',
  'Next.js 14',
  'TypeScript',
  'Cloudflare Tunnel',
  'Vercel',
  'Oracle Cloud',
];

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
          데이터와 모델은 개인 서버에서 구동되며 Cloudflare 는 HTTPS 만 중계합니다.
          <br />
          표시·정보용이며 매매 신호가 아닙니다. 투자 판단과 책임은 이용자 본인에게 있습니다.
        </footer>
      </div>
    </main>
  );
}
