"use client";

import { useMemo, useState } from 'react';
import {
  Activity,
  ArrowRight,
  BarChart3,
  BookOpen,
  Bot,
  Clock3,
  Database,
  FileText,
  Globe,
  Layers3,
  MessageSquare,
  Network,
  PanelLeft,
  Radar,
  ShieldCheck,
  Sparkles,
  TrendingUp,
} from 'lucide-react';
import {
  graphEdges,
  memoryItems,
  messages,
  reportHighlights,
  sourceCards,
  statCards,
  wikiNodes,
} from '../lib/site-data';

const toneClassMap: Record<string, string> = {
  teal: 'teal',
  violet: 'violet',
  amber: 'amber',
  rose: 'rose',
  slate: 'teal',
};

const iconMap = {
  dashboard: Activity,
  console: Bot,
  wiki: BookOpen,
  memory: Layers3,
  report: FileText,
  sources: Database,
  graph: Network,
  safety: ShieldCheck,
  market: TrendingUp,
  timeline: Clock3,
  insight: Sparkles,
  portfolio: BarChart3,
  globe: Globe,
  message: MessageSquare,
  radar: Radar,
  panel: PanelLeft,
};

function getNodeById(id: string) {
  return wikiNodes.find((node) => node.id === id);
}

export default function HomePage() {
  const [activeNodeId, setActiveNodeId] = useState(wikiNodes[0].id);
  const activeNode = useMemo(() => getNodeById(activeNodeId) ?? wikiNodes[0], [activeNodeId]);

  const edgeLines = useMemo(() => {
    return graphEdges
      .map(([fromId, toId]) => {
        const from = getNodeById(fromId);
        const to = getNodeById(toId);
        if (!from || !to) return null;
        return {
          key: `${fromId}-${toId}`,
          x1: from.x,
          y1: from.y,
          x2: to.x,
          y2: to.y,
        };
      })
      .filter(Boolean) as Array<{ key: string; x1: number; y1: number; x2: number; y2: number }>;
  }, []);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-top">
            <div className="brand-badge">
              <span className="badge-dot" />
              stock-report agent
            </div>
            <span className="status-chip teal">ready</span>
          </div>
          <div>
            <h1>AI 콘솔</h1>
            <p>대화, 메모, 위키, 리포트를 한 화면에서 이어 읽는 개인 투자 작업공간입니다.</p>
          </div>
        </div>

        <nav className="sidebar-nav" aria-label="주요 섹션">
          <div className="nav-item active">
            <div>
              <strong>대시보드</strong>
              <div><span>요약 · 상태 · 리포트</span></div>
            </div>
            <Activity size={16} />
          </div>
          <div className="nav-item">
            <div>
              <strong>AI 콘솔</strong>
              <div><span>질문 · 응답 · 맥락</span></div>
            </div>
            <Bot size={16} />
          </div>
          <div className="nav-item">
            <div>
              <strong>AI 위키</strong>
              <div><span>관계 그래프 · 승격 메모리</span></div>
            </div>
            <BookOpen size={16} />
          </div>
          <div className="nav-item">
            <div>
              <strong>전략 캔버스</strong>
              <div><span>백테스트 · 레버리지 · 손실한도</span></div>
            </div>
            <Layers3 size={16} />
          </div>
        </nav>

        <section className="sidebar-section">
          <h2>최근 소스</h2>
          <div className="source-list">
            {sourceCards.map((source) => (
              <div className="source-item" key={source.name}>
                <div className="source-item-top">
                  <span className="source-name">{source.name}</span>
                  <span className="source-count">{source.count}</span>
                </div>
                <p>{source.kind}</p>
                <p>{source.status}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="sidebar-footer">
          <strong>배포 방향</strong>
          <p>이제부터는 Python 프론트 대신 React/Next가 화면을 담당하고, Python은 동기화·수집·배치 로직으로 분리하는 구조가 맞습니다.</p>
        </section>
      </aside>

      <main className="content">
        <div className="topbar">
          <div>
            <div className="panel-kicker">stock-report · frontend shell</div>
            <h2>대화형 투자 콘솔</h2>
            <p>지금은 하드한 분석 화면보다 먼저, 질문을 받아 문맥을 잡고 위키를 따라가며 읽을 수 있는 프론트 레이어를 세웠습니다.</p>
          </div>
          <div className="status-row">
            <span className="status-chip teal">시장 자료 연결</span>
            <span className="status-chip violet">World Memory</span>
            <span className="status-chip amber">위키 그래프</span>
            <span className="status-chip rose">모의투자</span>
          </div>
        </div>

        <section className="hero">
          <div className="hero-grid">
            <div className="hero-copy">
              <div className="panel-kicker">context-first dashboard</div>
              <h3>프론트는 지금부터 React로 간다</h3>
              <p>
                현재 레포는 Python 로직과 Streamlit 흔적이 너무 무겁게 얽혀 있어서, 화면은 React/Next로 분리하는 편이 훨씬 자연스럽습니다.
                이 뷰는 AI 콘솔, 위키, 메모리, 리포트를 한 번에 읽을 수 있게 만든 시작점입니다.
              </p>
              <div className="hero-meta">
                <span className="status-chip teal">챗봇 스타일 응답</span>
                <span className="status-chip violet">옵시디언형 위키</span>
                <span className="status-chip amber">그래프 클릭 탐색</span>
                <span className="status-chip rose">리포트 요약</span>
              </div>
            </div>

            <div className="report-card">
              <div className="report-status">
                <ShieldCheck size={14} />
                ready · context warm
              </div>
              <h5>최신 해석</h5>
              <p>
                중동 재교전, 크레딧 확인, AI/반도체 밸류에이션을 우선순위로 두고, 오라클은 장기 보유 후보로 유지하는 식의 구조가 지금 맥락에 맞습니다.
              </p>
              <div className="report-highlights">
                {reportHighlights.map((item) => (
                  <div className="highlight" key={item.title}>
                    <div className="highlight-top">
                      <strong>{item.title}</strong>
                      <span>{item.score}/100</span>
                    </div>
                    <div className="progress" aria-hidden="true">
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
          {statCards.map((card) => {
            const ToneIcon = iconMap.dashboard;
            return (
              <article className={`metric-card ${toneClassMap[card.tone]}`} key={card.label}>
                <p className="label">{card.label}</p>
                <h4 className="value">{card.value}</h4>
                <p className="delta">{card.delta}</p>
              </article>
            );
          })}
        </section>

        <section className="main-grid">
          <div className="stack">
            <section className="panel">
              <div className="panel-header">
                <div>
                  <div className="panel-kicker">ai console</div>
                  <h4>대화</h4>
                  <p>프롬프트가 아니라 문맥을 읽는 챗봇 형태의 답변 공간입니다.</p>
                </div>
                <span className="status-chip teal">stateful</span>
              </div>
              <div className="chat-feed">
                {messages.map((message) => (
                  <article className={`message ${message.role}`} key={`${message.role}-${message.time}-${message.title}`}>
                    <div className="message-head">
                      <strong>{message.title}</strong>
                      <span>{message.time}</span>
                    </div>
                    <p>{message.content}</p>
                  </article>
                ))}
              </div>
            </section>

            <section className="panel">
              <div className="panel-header">
                <div>
                  <div className="panel-kicker">strategy notes</div>
                  <h4>시장 기억</h4>
                  <p>반복된 판단을 기억 카드로 승격해 다음 질문의 기본 맥락으로 다시 읽습니다.</p>
                </div>
                <span className="status-chip violet">learning layer</span>
              </div>
              <div className="chat-feed">
                {memoryItems.map((item) => (
                  <div className="memory-card" key={item.title}>
                    <div className="message-head">
                      <strong>{item.title}</strong>
                      <span>{item.kind}</span>
                    </div>
                    <p>{item.detail}</p>
                    <div className="detail-tags">
                      {item.tags.map((tag) => (
                        <span className="detail-tag" key={tag}>
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <div className="subgrid">
            <section className="panel">
              <div className="panel-header">
                <div>
                  <div className="panel-kicker">wiki graph</div>
                  <h4>AI 위키</h4>
                  <p>노드와 선을 클릭해 대화·리포트·소스 관계를 따라갑니다.</p>
                </div>
                <span className="status-chip amber">clickable</span>
              </div>

              <div className="graph-shell">
                <div className="graph-panel">
                  <svg className="graph-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                    {edgeLines.map((line) => (
                      <line
                        key={line.key}
                        x1={line.x1}
                        y1={line.y1}
                        x2={line.x2}
                        y2={line.y2}
                        stroke="rgba(148, 163, 184, 0.26)"
                        strokeWidth="0.7"
                      />
                    ))}
                  </svg>
                  {wikiNodes.map((node) => (
                    <button
                      type="button"
                      key={node.id}
                      className={`graph-node ${toneClassMap[node.tone]} ${node.id === activeNode.id ? 'selected' : ''}`}
                      style={{ left: `${node.x}%`, top: `${node.y}%` }}
                      onClick={() => setActiveNodeId(node.id)}
                    >
                      {node.label}
                      <small>{node.category}</small>
                    </button>
                  ))}
                </div>

                <aside className="graph-detail">
                  <div className="panel-kicker">selected node</div>
                  <h5>{activeNode.label}</h5>
                  <p>{activeNode.summary}</p>
                  <div className="detail-tags">
                    {activeNode.related.map((tag) => (
                      <span className="detail-tag" key={tag}>
                        {tag}
                      </span>
                    ))}
                  </div>
                  <div className="detail-list">
                    {activeNode.evidence.map((item) => (
                      <div className="detail-item" key={item}>
                        <strong>{activeNode.label} 근거</strong>
                        <span>{item}</span>
                      </div>
                    ))}
                  </div>
                  <p className="footer-note">
                    이 패널은 나중에 위키 페이지와 연결되면, 노드를 누르는 순간 원문/대화/리포트가 바로 열리도록 확장하면 됩니다.
                  </p>
                </aside>
              </div>
            </section>

            <section className="panel">
              <div className="panel-header">
                <div>
                  <div className="panel-kicker">composer</div>
                  <h4>질문 입력</h4>
                  <p>실제 API가 붙기 전까지는 여기서 UI 흐름과 레이아웃을 먼저 다듬습니다.</p>
                </div>
                <span className="status-chip rose">demo</span>
              </div>
              <div className="composer">
                <textarea
                  readOnly
                  value={'현재 비중에서 먼저 줄여야 할 리스크를 봐줘\n오라클은 들고 가고 싶은데, 지금은 어떤 축을 먼저 볼까?'}
                  aria-label="예시 질문"
                />
                <div className="composer-actions">
                  <div className="helper">
                    입력창은 실제 연결 전 단계의 시각적 시뮬레이션입니다. 나중에는 여기서 바로 시장 맥락과 위키를 함께 조회하도록 붙이면 됩니다.
                  </div>
                  <button type="button" className="primary-btn">
                    질문 보내기
                  </button>
                </div>
              </div>
            </section>
          </div>
        </section>

        <section className="panel" style={{ marginTop: 18 }}>
          <div className="panel-header">
            <div>
              <div className="panel-kicker">roadmap</div>
              <h4>다음에 붙일 것</h4>
              <p>프론트가 자리를 잡은 뒤에는 Python 수집/동기화 API를 얇게 연결하면 됩니다.</p>
            </div>
            <span className="status-chip teal">next step</span>
          </div>
          <div className="report-highlights">
            <div className="highlight">
              <div className="highlight-top">
                <strong>1. 데이터 어댑터</strong>
                <span>API layer</span>
              </div>
              <p>시장 데이터, 모의투자 원장, 월드 메모리, 리포트 요약을 React가 받아 그리도록 API 스펙을 먼저 얇게 정리합니다.</p>
            </div>
            <div className="highlight">
              <div className="highlight-top">
                <strong>2. 위키 페이지 연결</strong>
                <span>obsidian-style</span>
              </div>
              <p>그래프 노드를 누르면 해당 위키 문서가 열리고, 원본 뉴스와 PDF 아카이브까지 바로 보이도록 연결합니다.</p>
            </div>
            <div className="highlight">
              <div className="highlight-top">
                <strong>3. 전략 캔버스 분리</strong>
                <span>actions</span>
              </div>
              <p>AI 콘솔은 질문과 문맥에 집중하고, 백테스트·손실한도·레버리지 조정은 별도 탭으로 분리하면 훨씬 직관적입니다.</p>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
