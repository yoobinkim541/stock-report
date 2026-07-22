import Link from 'next/link';
import {
  Activity,
  ArrowRight,
  BarChart3,
  BookOpen,
  Clock3,
  FileText,
  Sparkles,
  TrendingUp,
} from 'lucide-react';
import { AppShell } from '../components/app-shell';
import { homeMetrics, researchQueue } from '../lib/dashboard-data';
import { messages, reportHighlights } from '../lib/site-data';

const quickActions = [
  { href: '/portfolio', title: '포트폴리오', note: '비중, 손실, 현금' },
  { href: '/analysis', title: '종목 분석', note: '오라클, CRM, 반도체' },
  { href: '/ai-console', title: 'AI 콘솔', note: '대화형 문맥 응답' },
  { href: '/bridge', title: '파이썬 출입문', note: '무거운 화면 우회' },
];

const focusCards = [
  { title: '지정학', body: '중동 재교전과 호르무즈 통항이 흔들리면 가장 먼저 비중을 줄입니다.' },
  { title: '크레딧', body: 'HYG/LQD가 따라오지 않으면 반등을 신뢰하지 않습니다.' },
  { title: 'AI/반도체', body: 'CAPEX, 전력비, 마진 둔화가 수요 스토리보다 먼저입니다.' },
];

export default function HomePage() {
  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">control room</div>
            <h3>시장과 포트폴리오를 같은 규칙으로 읽는 메인 워크벤치</h3>
            <p>
              이 화면은 요약만 보여주는 곳이 아니라, 다음 행동을 고르는 곳입니다. 시장 레짐, 보유 비중, 위키 기억,
              고정 브리지까지 한 흐름으로 이어지게 했습니다.
            </p>
            <div className="hero-meta">
              <span className="status-chip teal">context ready</span>
              <span className="status-chip violet">wiki-linked</span>
              <span className="status-chip amber">risk-first</span>
              <span className="status-chip rose">react shell</span>
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                gap: 10,
                marginTop: 18,
              }}
            >
              {[
                ['최근 이벤트', '40건'],
                ['누적 기억', '50건'],
                ['모델 파일', '4개'],
              ].map(([label, value]) => (
                <div
                  key={label}
                  style={{
                    padding: 14,
                    borderRadius: 16,
                    border: '1px solid rgba(148, 163, 184, 0.12)',
                    background: 'rgba(10, 15, 26, 0.62)',
                  }}
                >
                  <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>{label}</div>
                  <strong style={{ fontSize: 18 }}>{value}</strong>
                </div>
              ))}
            </div>
          </div>

          <div className="report-card">
            <div className="report-status">
              <Sparkles size={14} />
              live overview
            </div>
            <h5>현재 판단 레이어</h5>
            <p>
              지금은 거시 위험과 성장 모멘텀을 같이 보는 구간입니다. 오라클은 유지 후보로 두고, 과도한 단기 레버리지는
              먼저 정리하는 구성이 자연스럽습니다.
            </p>
            <div className="report-highlights">
              {reportHighlights.map((item) => (
                <div key={item.title} className="highlight">
                  <div className="highlight-top">
                    <strong>{item.title}</strong>
                    <span>{item.score}/100</span>
                  </div>
                  <div className="progress">
                    <span style={{ width: `${item.score}%` }} />
                  </div>
                  <p>{item.body}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="metric-grid" aria-label="핵심 지표">
        {homeMetrics.map((card) => (
          <article key={card.label} className={`metric-card ${card.tone}`}>
            <p className="label">{card.label}</p>
            <h4 className="value">{card.value}</h4>
            <p className="delta">{card.delta}</p>
          </article>
        ))}
      </section>

      <section className="main-grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">market pulse</div>
                <h4>지금 보는 축</h4>
                <p>지정학, 크레딧, AI/반도체를 먼저 읽고 나머지를 뒤에 둡니다.</p>
              </div>
              <span className="status-chip teal">priority</span>
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {focusCards.map((card) => (
                <div key={card.title} className="highlight">
                  <div className="highlight-top">
                    <strong>{card.title}</strong>
                    <span>watch</span>
                  </div>
                  <p>{card.body}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">recent memory</div>
                <h4>최근 대화</h4>
                <p>앱이 읽고 있는 문맥을 실제 대화 흐름처럼 보여줍니다.</p>
              </div>
              <span className="status-chip violet">context</span>
            </div>
            <div className="chat-feed">
              {messages.map((message) => (
                <article key={`${message.role}-${message.time}`} className={`message ${message.role}`}>
                  <div className="message-head">
                    <strong>{message.title}</strong>
                    <span>{message.time}</span>
                  </div>
                  <p>{message.content}</p>
                </article>
              ))}
            </div>
          </section>
        </div>

        <div className="subgrid">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">shortcuts</div>
                <h4>바로 가기</h4>
                <p>자주 쓰는 작업면만 남겼습니다.</p>
              </div>
              <span className="status-chip amber">nav</span>
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {quickActions.map((item) => (
                <Link key={item.href} href={item.href} className="highlight" style={{ display: 'block' }}>
                  <div className="highlight-top">
                    <strong>{item.title}</strong>
                    <ArrowRight size={14} />
                  </div>
                  <p>{item.note}</p>
                </Link>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">research queue</div>
                <h4>리서치 대기열</h4>
                <p>위키로 승격할 후보를 먼저 모아둡니다.</p>
              </div>
              <span className="status-chip rose">queue</span>
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {researchQueue.map((item) => (
                <div key={item.title} className="source-item">
                  <div className="source-item-top">
                    <span className="source-name">{item.title}</span>
                    <span className="source-count">{item.tag}</span>
                  </div>
                  <p>{item.source}</p>
                  <p>{item.summary}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">activity</div>
                <h4>최근 동작</h4>
                <p>시간순으로 보이는 경량 로그입니다.</p>
              </div>
              <span className="status-chip teal">live</span>
            </div>
            <div className="detail-list">
              <div className="detail-item">
                <strong><Clock3 size={14} style={{ display: 'inline', marginRight: 6 }} />09:31</strong>
                <span>QQQ, AMZN, ORCL 관련 노트 업데이트</span>
              </div>
              <div className="detail-item">
                <strong><Activity size={14} style={{ display: 'inline', marginRight: 6 }} />11:12</strong>
                <span>시장-캘린더 이벤트 3건 추가</span>
              </div>
              <div className="detail-item">
                <strong><BookOpen size={14} style={{ display: 'inline', marginRight: 6 }} />15:02</strong>
                <span>AI 위키에 레짐 메모 승격</span>
              </div>
              <div className="detail-item">
                <strong><TrendingUp size={14} style={{ display: 'inline', marginRight: 6 }} />16:40</strong>
                <span>모의투자 전략 캔버스 재실행</span>
              </div>
              <div className="detail-item">
                <strong><BarChart3 size={14} style={{ display: 'inline', marginRight: 6 }} />17:00</strong>
                <span>차트 풀뷰에서 UNH, ORCL 비교</span>
              </div>
              <div className="detail-item">
                <strong><FileText size={14} style={{ display: 'inline', marginRight: 6 }} />18:21</strong>
                <span>데일리 리포트 OCR 보관 완료</span>
              </div>
            </div>
          </section>
        </div>
      </section>
    </AppShell>
  );
}
