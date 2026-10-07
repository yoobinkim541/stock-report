import type { ReactNode } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight, AudioLines, BookOpen, Check, ChevronRight, FlaskConical, Globe2, Layers3, Radar, ScanLine, ShieldCheck, Sparkles, Waypoints } from 'lucide-react';
import { LandingExperience } from '../components/landing-experience';
import { LandingScene } from '../components/landing-scene';

function Brand({ footer = false }: { footer?: boolean }) {
  return <a className="brand" href="/" aria-label="Stock Report 홈"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span><strong>Stock<span>Report</span></strong>{footer ? null : <span className="brand-divider" />}</a>;
}

function AppLink({ children = '워크스페이스 열기', className = 'button-primary' }: { children?: ReactNode; className?: string }) {
  return <a href="/bridge" data-app-link className={className} target="_blank" rel="noopener noreferrer">{children}<ArrowUpRight size={17} /></a>;
}

const capabilities = [
  { icon: Globe2, label: 'US & KR MARKETS', text: '미국·한국 시장' },
  { icon: ScanLine, label: 'ADVANCED CHARTING', text: '정교한 차트 분석' },
  { icon: Sparkles, label: 'CONTEXTUAL AI', text: '맥락을 읽는 AI' },
  { icon: ShieldCheck, label: 'RISK FIRST', text: '리스크 우선 설계' },
];

const workflow = [
  { icon: Radar, label: 'DISCOVER', title: '시장을 발견하다', body: '시세, 뉴스, 경제 일정에서\n시장 변화의 단서를 포착합니다.' },
  { icon: Waypoints, label: 'UNDERSTAND', title: '맥락을 연결하다', body: 'AI 리서치와 위키로\n움직임 뒤의 이유를 탐색합니다.' },
  { icon: FlaskConical, label: 'VALIDATE', title: '전략을 검증하다', body: '백테스트와 모의투자로\n아이디어의 가능성을 점검합니다.' },
  { icon: ShieldCheck, label: 'MANAGE', title: '리스크를 관리하다', body: '성과와 손실 예산을 확인하고\n더 나은 판단을 쌓아갑니다.' },
];

export default function HomePage() {
  return <LandingExperience>
    <a className="skip-link" href="#main">본문으로 건너뛰기</a>
    <header className="site-header"><div className="header-inner"><Brand /><nav aria-label="메인 메뉴"><a href="#platform">플랫폼</a><a href="#intelligence">인텔리전스</a><a href="#workflow">워크플로우</a></nav><AppLink className="nav-cta">앱 시작하기</AppLink></div></header>
    <main id="main">
      <section className="hero section-shell" aria-labelledby="hero-title">
        <div className="hero-grid-background" aria-hidden="true" /><div className="hero-orbit orbit-one" aria-hidden="true" /><div className="hero-orbit orbit-two" aria-hidden="true" />
        <div className="hero-copy">
          <span className="eyebrow hero-eyebrow"><span className="status-dot" />YOUR EDGE, CONNECTED.<span className="eyebrow-rule" />TRADING INTELLIGENCE</span>
          <h1 id="hero-title">시장을 읽는 시야.<br /><span>판단을 바꾸는 차이.</span></h1>
          <p>흩어진 시장 데이터에서 당신만의 투자 관점으로.<br />차트, AI 리서치, 전략 검증을 하나의 워크스페이스에서 연결하세요.</p>
          <div className="hero-actions"><AppLink /><a className="button-secondary" href="#platform">플랫폼 살펴보기<ArrowDown size={15} /></a></div>
          <div className="hero-footnote"><span><Check size={12} />미국 · 한국 주식</span><span><Check size={12} />출처 기반 AI 분석</span><span><Check size={12} />모의투자로 전략 검증</span></div>
        </div>
        <LandingScene />
      </section>
      <div className="capability-strip section-shell" aria-label="플랫폼 핵심 기능">{capabilities.map(({ icon: Icon, label, text }) => <div key={label}><Icon size={21} strokeWidth={1.4} /><span><small>{label}</small><strong>{text}</strong></span></div>)}</div>

      <section id="platform" className="platform-section section-shell">
        <div className="section-heading" data-reveal><div><span className="eyebrow"><span className="section-index">01 /</span> THE PLATFORM</span><h2>더 넓게 보고.<br /><span className="text-muted">더 깊게 이해하세요.</span></h2></div><p>좋은 판단에는 연결된 정보가 필요합니다.<br />시장의 움직임부터 그 뒤의 이유까지,<br />투자의 모든 맥락을 한곳에 담았습니다.</p></div>
        <div className="feature-grid">
          <article className="feature-card feature-research" data-reveal>
            <div className="card-label"><Sparkles size={17} /><span>AI RESEARCH</span><span className="card-number">01</span></div>
            <h3>답변에 근거를.<br />정보에 맥락을.</h3><p>뉴스와 기업 리포트를 AI 위키로 연결하고,<br />대화로 탐색하세요. 모든 인사이트는 출처와 함께.</p>
            <div className="knowledge-visual" aria-hidden="true">
              <svg viewBox="0 0 500 210"><path d="M70 45 Q165 30 250 105 M60 163 Q159 161 250 105 M420 42 Q352 39 250 105 M425 167 Q344 163 250 105" /><path className="knowledge-flow" d="M70 45 Q165 30 250 105 M60 163 Q159 161 250 105 M420 42 Q352 39 250 105 M425 167 Q344 163 250 105" /></svg>
              <div className="knowledge-node node-news"><AudioLines size={15} /><span>시장 뉴스</span></div><div className="knowledge-node node-report"><BookOpen size={15} /><span>기업 리포트</span></div><div className="knowledge-core"><Sparkles size={28} /><span>AI WIKI</span></div><div className="knowledge-node node-market"><Globe2 size={15} /><span>시장 맥락</span></div><div className="knowledge-node node-memory"><Layers3 size={15} /><span>투자 메모리</span></div>
            </div>
            <div className="card-footer"><span><span className="status-dot" />SOURCE-LINKED INTELLIGENCE</span><Waypoints size={19} /></div>
          </article>
          <article className="feature-card feature-chart" data-reveal>
            <div className="card-label"><ScanLine size={17} /><span>MARKET WORKSPACE</span><span className="card-number">02</span></div>
            <h3>움직임을 포착하는<br />더 정교한 시야.</h3><p>실시간 시세와 멀티 타임프레임 차트.<br />추세와 지표를 겹쳐 시장의 흐름을 읽으세요.</p>
            <div className="analysis-visual" aria-hidden="true"><div className="analysis-tabs"><span>PRICE ACTION</span><span>TECHNICALS</span><span>CONTEXT</span></div><svg viewBox="0 0 440 155"><path d="M0 130 H440 M0 85 H440 M0 40 H440" stroke="#ffffff" strokeOpacity=".06" /><path d="M0 127 L22 117 L36 129 L57 94 L78 100 L93 84 L118 104 L139 77 L158 88 L181 53 L205 68 L220 46 L243 62 L265 36 L292 49 L313 22 L338 31 L360 9 L389 26 L410 10 L440 17" fill="none" stroke="#c5f66b" strokeWidth="2" className="analysis-line" pathLength="1" /><path d="M0 143 L440 45 M0 98 L440 0" stroke="#c5f66b" strokeOpacity=".2" strokeDasharray="5 5" /><circle cx="313" cy="22" r="11" fill="#c5f66b" fillOpacity=".12" /><circle cx="313" cy="22" r="3" fill="#c5f66b" /></svg><div className="analysis-tag"><Radar size={12} />추세 · 모멘텀 · 가격</div></div>
            <div className="card-footer"><span>ONE MARKET. MULTIPLE PERSPECTIVES.</span><ArrowUpRight size={19} /></div>
          </article>
          <article className="feature-card feature-test" data-reveal>
            <div className="card-label"><FlaskConical size={17} /><span>STRATEGY LAB</span><span className="card-number">03</span></div>
            <h3>아이디어는 자유롭게.<br />검증은 엄격하게.</h3><p>백테스트와 모의투자로 전략을 점검하세요.<br />비용과 손실을 반영하고, 결과를 다시 학습합니다.</p>
            <div className="backtest-visual" aria-hidden="true"><div className="backtest-title"><span>WALK-FORWARD VALIDATION</span><span className="tiny-pill">OOS</span></div><div className="backtest-bars">{[32, 46, 39, 59, 48, 63, 72, 57, 78, 66, 86, 71, 91, 82, 96, 87, 100, 92].map((height, index) => <i key={index} style={{ height: `${height}%`, animationDelay: `${index * 45}ms` }} />)}</div><div className="backtest-axis"><span>RESEARCH</span><span>TEST</span><span>VALIDATE<Check size={11} /></span></div></div>
            <div className="card-footer"><span>TEST THE IDEA. UNDERSTAND THE RISK.</span><FlaskConical size={18} /></div>
          </article>
          <article className="feature-card feature-risk" data-reveal>
            <div className="card-label"><ShieldCheck size={17} /><span>PORTFOLIO & RISK</span><span className="card-number">04</span></div>
            <h3>수익의 가능성과<br />손실의 한계를 함께.</h3><p>자산 비중, 벤치마크 대비 성과, 손실 예산.<br />포트폴리오를 리스크의 관점에서 관리하세요.</p>
            <div className="risk-visual" aria-hidden="true"><div className="risk-ring"><svg viewBox="0 0 150 150"><circle cx="75" cy="75" r="58" fill="none" stroke="#202922" strokeWidth="13" /><circle className="risk-ring-progress" cx="75" cy="75" r="58" fill="none" stroke="#c5f66b" strokeWidth="13" strokeDasharray="235 365" transform="rotate(-90 75 75)" /><circle cx="75" cy="75" r="58" fill="none" stroke="#6b7c71" strokeWidth="13" strokeDasharray="60 365" strokeDashoffset="-244" transform="rotate(-90 75 75)" /></svg><div><ShieldCheck size={22} /><small>RISK FIRST</small></div></div><div className="risk-legend"><span><i />자산 배분<b>ALLOCATION</b></span><span><i />현금 비중<b>CASH BUFFER</b></span><span><i />손실 예산<b>RISK BUDGET</b></span></div></div>
            <div className="card-footer"><span>YOUR PORTFOLIO. A CLEARER PICTURE.</span><ShieldCheck size={19} /></div>
          </article>
        </div>
      </section>

      <section id="intelligence" className="intelligence-section"><div className="section-shell intelligence-grid">
        <div className="intelligence-copy" data-reveal><span className="eyebrow"><span className="section-index">02 /</span> CONNECTED INTELLIGENCE</span><h2>하나의 뉴스가<br />더 큰 그림이 될 때.</h2><p>오늘의 뉴스는 어제의 리서치와 연결됩니다.<br />AI 위키는 흩어진 정보를 맥락으로 쌓고,<br />당신의 다음 질문에 필요한 근거를 찾아냅니다.</p><div className="intelligence-points"><span><Check size={15} />원문을 보존하는 리서치 메모리</span><span><Check size={15} />관계를 발견하는 지식 그래프</span><span><Check size={15} />참고한 출처까지 이어지는 AI 답변</span></div><a href="#workflow" className="text-link">인사이트가 만들어지는 과정<ArrowRight size={16} /></a></div>
        <div className="research-demo" data-reveal>
          <div className="research-top"><div className="research-icon"><Sparkles size={19} /></div><div><strong>Research, connected.</strong><span>AI CONSOLE + KNOWLEDGE WIKI</span></div><span className="tiny-pill">예시</span></div>
          <div className="research-question">반도체 업황을 보려면 무엇을 연결해야 할까?<span>YOU</span></div>
          <div className="research-answer"><span className="answer-label"><Sparkles size={13} />STOCK REPORT AI</span><p>수요, 공급, 기업 실적을 함께 살펴보세요.<br />서로 연결된 자료에서 맥락을 찾을 수 있습니다.</p><div className="research-map"><div><span>01</span><strong>AI 인프라 수요</strong><small>투자 계획 · 데이터센터</small></div><div><span>02</span><strong>반도체 공급망</strong><small>메모리 · 파운드리 · 장비</small></div><div><span>03</span><strong>기업 펀더멘털</strong><small>실적 · 가이던스 · 밸류에이션</small></div></div><div className="research-sources"><BookOpen size={13} /><span>뉴스 원문</span><span>기업 리포트</span><span>관련 위키</span><ArrowUpRight size={13} /></div></div>
          <div className="research-input"><span>더 깊이 이해하고 싶은 시장을 질문하세요</span><span className="research-send"><ArrowUpRight size={16} /></span></div>
        </div>
      </div></section>

      <section id="workflow" className="workflow-section section-shell">
        <div className="section-heading" data-reveal><div><span className="eyebrow"><span className="section-index">03 /</span> THE WORKFLOW</span><h2>데이터에서 판단까지.<br /><span className="text-muted">끊김 없는 하나의 흐름.</span></h2></div><p>발견하고, 이해하고, 검증하고, 관리하세요.<br />각 단계의 기록이 다음 판단의 근거가 됩니다.</p></div>
        <div className="workflow-track" data-reveal>{workflow.map(({ icon: Icon, label, title, body }, index) => <article key={label}><div className="workflow-step-top"><span className="workflow-icon"><Icon size={22} strokeWidth={1.5} /></span><span className="workflow-number">0{index + 1}</span></div><small>{label}</small><h3>{title}</h3><p>{body}</p>{index < 3 ? <ChevronRight className="step-arrow" size={17} /> : null}</article>)}</div>
        <div className="feedback-loop" aria-hidden="true"><span className="feedback-line" /><Layers3 size={13} /><span>모든 기록은 다음 인사이트로</span><span className="feedback-line" /></div>
      </section>

      <section className="final-section section-shell" data-reveal><div className="final-grid" aria-hidden="true" /><div className="final-glow" aria-hidden="true" /><span className="eyebrow"><span className="status-dot" />BUILD YOUR PERSPECTIVE.</span><h2>당신의 다음 판단에,<br /><span>더 선명한 근거를.</span></h2><p>시장을 보는 새로운 워크스페이스, Stock Report.</p><AppLink>워크스페이스 시작하기</AppLink><small>개인 투자 리서치 플랫폼 · 실제 주문은 사용자가 직접 판단하고 실행합니다.</small></section>
    </main>
    <footer className="site-footer section-shell"><div className="footer-top"><Brand footer /><span>SEE THE MARKET. CONNECT THE CONTEXT.</span><a href="#main">맨 위로<ArrowUpRight size={14} /></a></div><div className="footer-bottom"><span>© {new Date().getFullYear()} Stock Report</span><p>화면의 시세와 분석은 기능 설명을 위한 예시입니다. 투자 판단과 책임은 사용자에게 있습니다.</p><span>DESIGNED FOR CLARITY.</span></div></footer>
  </LandingExperience>;
}
