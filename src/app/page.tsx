import {
  BarChart3,
  BookOpen,
  Bot,
  CalendarDays,
  CheckCircle2,
  Database,
  ExternalLink,
  FlaskConical,
  LineChart,
  Lock,
  Network,
  Radio,
  Search,
  ServerCog,
  ShieldCheck,
  Sparkles,
  Wallet,
} from 'lucide-react';
import dynamic from 'next/dynamic';

import { gatewayUrl } from '../lib/gateway';

const LandingScene = dynamic(
  () => import('../components/landing-scene').then((mod) => mod.LandingScene),
  { ssr: false }
);

const featureBullets = [
  '라이트/다크 테마',
  'Cloudflare 고정 입구',
  'Python 대시보드 연결',
  'AI 위키·시장 메모리',
  '반응형 원페이지',
];

const productCards = [
  { label: 'Portfolio NAV', value: '$248,073', delta: '+2.0%p vs QQQ' },
  { label: 'Market Pulse', value: 'MIXED', delta: 'credit watch' },
  { label: 'AI Wiki', value: 'live', delta: 'source linked' },
  { label: 'Risk Budget', value: '1.0%', delta: 'loss capped' },
];

const capabilityRows = [
  {
    icon: Wallet,
    title: '포트폴리오 관제',
    body: '보유 비중, 현금, 벤치마크 대비 성과, 최대손실 예산을 한 번에 봅니다.',
  },
  {
    icon: LineChart,
    title: '종목 분석과 차트',
    body: 'KIS 실시간 가격, 이동평균, 추세선, 가치평가, 연관 종목을 같은 흐름에서 확인합니다.',
  },
  {
    icon: Bot,
    title: '대화형 AI 콘솔',
    body: '질문 의도를 시장·포트폴리오·위키 맥락으로 라우팅하고, 참고한 출처를 따라갈 수 있게 합니다.',
  },
  {
    icon: Database,
    title: '원문 기반 기억',
    body: '뉴스, 리포트, 텔레그램, 모의투자 로그를 원본과 함께 쌓아 나중에 다시 검증합니다.',
  },
];

const dashboardModules = [
  {
    icon: BarChart3,
    title: '홈',
    eyebrow: 'Market cockpit',
    body: '오늘의 지수, 공포·탐욕, 밸류에이션, AI 브리핑을 가장 먼저 보여주는 시작 화면입니다.',
    detail: '시장 온도와 포트폴리오 상태를 짧게 스캔한 뒤, 더 깊게 볼 화면으로 이동하는 허브 역할을 합니다.',
  },
  {
    icon: Wallet,
    title: '포트폴리오',
    eyebrow: 'Risk and allocation',
    body: '보유 종목, 평가손익, 현금 비중, QQQ 대비 성과, 최대손실 한도를 같이 봅니다.',
    detail: '“무엇을 더 살까”보다 “지금 먼저 줄일 리스크가 무엇인가”를 보게 만드는 화면입니다.',
  },
  {
    icon: Search,
    title: '종목 분석',
    eyebrow: 'Single-name research',
    body: 'KIS 가격, 재무·가치평가, 기술등급, 연관 종목 추천, LLM 해설을 종목 단위로 묶습니다.',
    detail: '주가와 차트 가격이 어긋나는 문제처럼 데이터 소스 차이도 추적할 수 있게 정리합니다.',
  },
  {
    icon: LineChart,
    title: '차트 풀뷰',
    eyebrow: 'Full chart workspace',
    body: '캔들·라인·HA, 이동평균, 자동 추세선, 채널, 피보나치, 피치포크를 넓은 화면에서 봅니다.',
    detail: '차트 위 글씨와 레벨이 겹치지 않도록 가독성을 우선한 분석용 작업 공간입니다.',
  },
  {
    icon: CalendarDays,
    title: '시장·캘린더',
    eyebrow: 'Events and schedule',
    body: '거시 일정, 실적, 정책 이벤트, 뉴스 흐름을 시장 맥락과 함께 배치합니다.',
    detail: '단발 뉴스가 아니라 “무슨 일이 어디서 시작해 여기까지 왔는지”를 따라가기 위한 시간축입니다.',
  },
  {
    icon: FlaskConical,
    title: '모의투자',
    eyebrow: 'Paper trading lab',
    body: '미국·한국 모의투자, 단기 트레이딩, 레버리지 후보, 손실한도 기반 실행 로그를 검증합니다.',
    detail: '거래 횟수 제한보다 계좌 손실 한도를 먼저 고정하고, 사후 성과로 추천 품질을 학습합니다.',
  },
  {
    icon: BookOpen,
    title: '리서치',
    eyebrow: 'Raw source archive',
    body: 'SaveTicker 뉴스 본문, 데일리 리포트 PDF/OCR, 텔레그램·커뮤니티 원문을 보관합니다.',
    detail: '요약만 남기지 않고 원본을 같이 들고 있어야 위키와 답변의 왜곡을 줄일 수 있습니다.',
  },
  {
    icon: Bot,
    title: 'AI 콘솔',
    eyebrow: 'Chat and source tracing',
    body: '시장·포트폴리오·종목·전략 질문을 챗봇처럼 받고, 참고한 위키와 출처를 하단에 붙입니다.',
    detail: '미리 만든 답변처럼 보이는 흐름을 줄이고, 질문 문맥에 맞는 ReAct/피드백 루프를 적용합니다.',
  },
  {
    icon: Database,
    title: 'AI 위키',
    eyebrow: 'World memory layer',
    body: '대화, 뉴스, 원문, 모의투자 결과를 승격해 연결 그래프와 노트 형태로 다시 읽습니다.',
    detail: '월드 메모리의 상위 정리층으로, LLM 이 스스로 노트를 정리하고 관계를 시각화하도록 설계했습니다.',
  },
];

const workflow = [
  { title: 'Collect', body: '뉴스·가격·리포트 원문을 수집' },
  { title: 'Structure', body: '위키와 원장에 출처를 연결' },
  { title: 'Ask', body: '챗봇이 필요한 맥락만 꺼내 답변' },
  { title: 'Enter', body: '실제 대시보드는 터널로 진입' },
];

export default function HomePage() {
  return (
    <main className="lp-page lp-page-fresh">
      <section className="lp-kit-hero">
        <nav className="lp-kit-nav">
          <a href="/" className="lp-kit-brand">
            <span className="lp-kit-logo">
              <ServerCog size={18} />
            </span>
            <span>
              <strong>Stock Report</strong>
              <small>personal trading intelligence</small>
            </span>
          </a>
          <div className="lp-kit-nav-actions">
            <span className="lp-kit-pill">
              <Radio size={13} />
              tunnel live
            </span>
            <a href={gatewayUrl} target="_blank" rel="noreferrer" className="lp-kit-nav-link">
              Open App
              <ExternalLink size={14} />
            </a>
          </div>
        </nav>

        <div className="lp-kit-hero-grid">
          <div className="lp-kit-copy">
            <span className="lp-kit-kicker">Trading Dashboard Gateway</span>
            <h1>Stock Report Dashboard</h1>
            <p>
              Vercel 에는 빠르고 가벼운 랜딩만 올리고, 실제 Python 대시보드는 개인 서버의 Cloudflare 터널로
              연결합니다. 시장 수집, AI 위키, 모의투자, 포트폴리오 점검까지 한 입구에서 시작합니다.
            </p>

            <div className="lp-kit-checks">
              {featureBullets.map((item) => (
                <span key={item}>
                  <CheckCircle2 size={16} />
                  {item}
                </span>
              ))}
            </div>

            <div className="lp-kit-actions">
              <a href={gatewayUrl} target="_blank" rel="noreferrer" className="lp-kit-primary">
                대시보드 열기
                <ExternalLink size={17} />
              </a>
            </div>
          </div>

          <div className="lp-kit-stage" aria-label="스크롤에 반응하는 3D 대시보드 목업">
            <LandingScene />
            <div className="lp-device-tablet">
              <div className="lp-device-top">
                <span>Trade</span>
                <span>Dashboard</span>
                <span>Market</span>
                <span>AI Wiki</span>
              </div>
              <div className="lp-device-grid">
                {productCards.map((card) => (
                  <article key={card.label} className="lp-device-card">
                    <small>{card.label}</small>
                    <strong>{card.value}</strong>
                    <span>{card.delta}</span>
                  </article>
                ))}
              </div>
              <div className="lp-device-chart">
                <span style={{ height: '36%' }} />
                <span style={{ height: '58%' }} />
                <span style={{ height: '48%' }} />
                <span style={{ height: '72%' }} />
                <span style={{ height: '64%' }} />
                <span style={{ height: '82%' }} />
                <span style={{ height: '68%' }} />
              </div>
            </div>
            <div className="lp-device-phone">
              <div className="lp-phone-notch" />
              <strong>AI Brief</strong>
              <span>risk-on, but credit first</span>
              <div className="lp-phone-meter">
                <i />
              </div>
              <a href={gatewayUrl} target="_blank" rel="noreferrer">
                Enter
              </a>
            </div>
          </div>
        </div>
      </section>

      <section className="lp-explain-band">
        <div className="lp-section-head lp-section-head-light">
          <span>What this landing does</span>
          <h2>React 는 현관, Python 은 엔진</h2>
          <p>
            Vercel 번들 제한 때문에 Streamlit 전체를 올리는 대신, 이 랜딩은 빠른 안내와 고정 진입 역할만
            맡습니다. 클릭하면 현재 터널로 바로 이동합니다.
          </p>
        </div>
        <div className="lp-capability-grid">
          {capabilityRows.map(({ icon: Icon, title, body }) => (
            <article key={title} className="lp-capability-card">
              <Icon size={21} />
              <h3>{title}</h3>
              <p>{body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="lp-module-band">
        <div className="lp-section-head lp-section-head-light">
          <span>Inside the dashboard</span>
          <h2>삭제한 페이지 설명은 원페이지 안에 모았습니다.</h2>
          <p>
            React 에 개별 페이지를 두지 않고, 실제 기능은 개인 서버의 Python 대시보드에서 실행합니다. 이 랜딩은
            각 기능이 무엇을 하는지 설명하고, 하나의 진입 버튼으로만 연결합니다.
          </p>
        </div>
        <div className="lp-module-grid">
          {dashboardModules.map(({ icon: Icon, title, eyebrow, body, detail }) => (
            <article key={title} className="lp-module-card">
              <div className="lp-module-top">
                <Icon size={20} />
                <span>{eyebrow}</span>
              </div>
              <h3>{title}</h3>
              <p>{body}</p>
              <small>{detail}</small>
            </article>
          ))}
        </div>
      </section>

      <section className="lp-dark-showcase">
        <div className="lp-dark-copy">
          <span>Animated system map</span>
          <h2>스크롤하면 목업이 움직이고, 설명은 실제 구조를 따라갑니다.</h2>
          <p>
            3D 캔버스는 제품이 살아있는 느낌을 주고, 아래 흐름은 데이터가 어디서 들어와 어떻게 답변과
            대시보드로 이어지는지 보여줍니다.
          </p>
        </div>
        <div className="lp-workflow">
          {workflow.map((step, index) => (
            <article key={step.title} className="lp-workflow-card">
              <span>{String(index + 1).padStart(2, '0')}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="lp-final-cta">
        <div>
          <span className="lp-kit-kicker">Ready to inspect</span>
          <h2>실제 대시보드로 들어가서 확인하세요.</h2>
          <p>이 페이지는 공개 가능한 가벼운 입구이고, 내부 기능은 비밀번호 뒤의 개인 서버에서 계속 돌아갑니다.</p>
        </div>
        <a href={gatewayUrl} target="_blank" rel="noreferrer" className="lp-kit-primary">
          라이브 터미널 열기
          <ExternalLink size={17} />
        </a>
      </section>

      <footer className="lp-foot lp-foot-fresh">
        <span>
          <ShieldCheck size={14} />
          표시·정보용이며 매매 신호가 아닙니다.
        </span>
        <span>
          <Lock size={14} />
          실제 대시보드는 개인 서버와 Cloudflare 터널에서 실행됩니다.
        </span>
        <span>
          <Network size={14} />
          Vercel 은 고정 랜딩과 출입문 역할만 담당합니다.
        </span>
        <span>
          <BarChart3 size={14} />
          시장·포트폴리오·AI 위키 맥락을 한곳으로 연결합니다.
        </span>
        <span>
          <Sparkles size={14} />
          스크롤 기반 3D 제품 목업 적용.
        </span>
      </footer>
    </main>
  );
}
