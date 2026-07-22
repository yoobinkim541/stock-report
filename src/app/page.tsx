"use client";

import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ArrowRight,
  BarChart3,
  BookOpen,
  Bot,
  Clock3,
  Database,
  FileText,
  Filter,
  Globe,
  Layers3,
  Link2,
  MessageSquare,
  Network,
  PanelLeft,
  Play,
  Radar,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Wifi,
} from 'lucide-react';
import {
  graphEdges,
  memoryItems,
  messages,
  reportHighlights,
  sourceCards,
  statCards,
  wikiNodes,
  type Tone,
} from '../lib/site-data';

const toneClassMap: Record<Tone, string> = {
  teal: 'teal',
  violet: 'violet',
  amber: 'amber',
  rose: 'rose',
  slate: 'teal',
};

type SurfaceId = 'chat' | 'knowledge' | 'strategy' | 'connectors';

type KnowledgeEntry = {
  id: string;
  source: 'World Memory' | 'AI 위키';
  title: string;
  kind: string;
  summary: string;
  tags: string[];
  evidence: string[];
  tone: Tone;
};

type StrategyAllocation = {
  symbol: string;
  weight: number;
  memo: string;
};

type StrategyScenario = {
  id: string;
  name: string;
  status: string;
  note: string;
  allocations: StrategyAllocation[];
  metrics: Array<{ label: string; value: string; delta: string }>;
  curve: { buyHold: number[]; strategy: number[] };
  defaultBuyRsi: number;
  defaultSellRsi: number;
};

type ConnectorCard = {
  name: string;
  status: string;
  detail: string;
  action: string;
  tone: Tone;
};

const surfaceTabs: Array<{
  id: SurfaceId;
  label: string;
  description: string;
  icon: typeof Bot;
}> = [
  { id: 'chat', label: '대화', description: '맥락 자동 라우팅', icon: Bot },
  { id: 'knowledge', label: '기억·위키', description: 'World Memory + 노드 그래프', icon: BookOpen },
  { id: 'strategy', label: '전략 캔버스', description: 'RSI · 비중 · 비교', icon: Layers3 },
  { id: 'connectors', label: '로컬 커넥터', description: 'Arca · Toss · Raw Vault', icon: Database },
];

const quickPrompts = [
  '오늘 시장 변화가 어디서 시작됐는지 추적해줘',
  '내 포트폴리오에서 먼저 줄여야 할 리스크 봐줘',
  '모의투자 성과가 좋아진 이유와 나빠진 이유 나눠줘',
];

const strategyScenarios: StrategyScenario[] = [
  {
    id: 'm7-balanced',
    name: 'M7 균형 포트폴리오',
    status: '적용됨',
    note: 'Buy & Hold 대비 RSI 30/70 현금화 규칙 비교',
    allocations: [
      { symbol: 'AAPL', weight: 14.3, memo: 'core' },
      { symbol: 'MSFT', weight: 14.3, memo: 'core' },
      { symbol: 'NVDA', weight: 14.3, memo: 'growth' },
      { symbol: 'META', weight: 14.3, memo: 'platform' },
      { symbol: 'QQQ', weight: 28.8, memo: 'index' },
      { symbol: 'CASH', weight: 14.0, memo: 'buffer' },
    ],
    metrics: [
      { label: 'Cumulative Return', value: '1.31%', delta: '+0.21%' },
      { label: 'MDD', value: '-3.75%', delta: 'vs SPY -2.38%' },
      { label: 'Sharpe', value: '0.998', delta: 'vs QQQ -1.274' },
      { label: 'UPI', value: '11.58', delta: 'vs QQQ -9.68' },
    ],
    curve: {
      buyHold: [100, 101, 103, 104, 109, 112, 116, 118, 117, 121, 123, 120, 124, 126, 122],
      strategy: [100, 100.5, 101, 103, 107, 109, 110, 111, 110, 113, 116, 114, 118, 119, 117],
    },
    defaultBuyRsi: 30,
    defaultSellRsi: 70,
  },
  {
    id: 'defense',
    name: '방어형 레짐',
    status: '대기',
    note: '크레딧이 흔들릴 때 레버리지 비중을 줄이는 규칙',
    allocations: [
      { symbol: 'QQQ', weight: 40, memo: 'growth' },
      { symbol: 'TLT', weight: 20, memo: 'rates hedge' },
      { symbol: 'GLD', weight: 10, memo: 'tail risk' },
      { symbol: 'CASH', weight: 30, memo: 'optional' },
    ],
    metrics: [
      { label: 'Cumulative Return', value: '0.88%', delta: '방어 우위' },
      { label: 'MDD', value: '-2.81%', delta: '낙폭 완화' },
      { label: 'Sharpe', value: '1.217', delta: '개선' },
      { label: 'UPI', value: '13.14', delta: '현금 완충' },
    ],
    curve: {
      buyHold: [100, 100, 101, 102, 104, 105, 106, 106, 107, 108, 108, 109, 109, 110, 111],
      strategy: [100, 100.2, 100.6, 101, 102, 103, 104, 105, 105.5, 106, 106.8, 107.3, 108, 108.5, 109],
    },
    defaultBuyRsi: 28,
    defaultSellRsi: 72,
  },
];

const connectorCards: ConnectorCard[] = [
  {
    name: 'Arca SOCKS 터널',
    status: 'UP',
    detail: '127.0.0.1:1080 · 공개 글만 가져오고 Cloudflare challenge는 우회하지 않음',
    action: '프록시 수집',
    tone: 'teal',
  },
  {
    name: 'Toss 로컬 스냅샷',
    status: 'READ ONLY',
    detail: '노트북의 자산 스냅샷을 읽기 전용으로 반영하고 주문 연결은 유지하지 않음',
    action: '스냅샷 보기',
    tone: 'violet',
  },
  {
    name: 'Raw Vault',
    status: 'ARCHIVE',
    detail: '뉴스 원문 PDF · OCR 텍스트 · 텔레그램 원문 · 승격 위키 원본을 함께 보관',
    action: '보관 정책',
    tone: 'amber',
  },
];

function slugify(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9가-힣]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function formatAllocationLines(entries: StrategyAllocation[]) {
  return entries.map((entry) => `${entry.symbol} ${entry.weight.toFixed(1)} ${entry.memo}`.trim()).join('\n');
}

function parseAllocationDraft(text: string): StrategyAllocation[] {
  return String(text || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(/\s+/);
      const symbol = parts[0]?.toUpperCase().trim();
      const weight = Number.parseFloat(parts[1] || '0');
      const memo = parts.slice(2).join(' ').trim();
      return { symbol, weight, memo } satisfies StrategyAllocation;
    })
    .filter((row) => row.symbol && Number.isFinite(row.weight) && row.weight > 0);
}

function normalizeAllocations(rows: StrategyAllocation[]) {
  const total = rows.reduce((sum, row) => sum + row.weight, 0);
  if (total <= 0) return [];
  return rows.map((row) => ({ ...row, weight: row.weight / total * 100 }));
}

function makeKnowledgeEntries(): KnowledgeEntry[] {
  const memories = memoryItems.map((item, index) => ({
    id: `memory-${index}`,
    source: 'World Memory' as const,
    title: item.title,
    kind: item.kind,
    summary: item.detail,
    tags: item.tags,
    evidence: item.tags,
    tone: index === 0 ? 'rose' : index === 1 ? 'violet' : 'amber',
  }));

  const wikiDocs = wikiNodes.map((node) => ({
    id: `wiki-${node.id}`,
    source: 'AI 위키' as const,
    title: node.label,
    kind: node.category,
    summary: node.summary,
    tags: node.related,
    evidence: node.evidence,
    tone: node.tone,
  }));

  return [...wikiDocs, ...memories];
}

function buildLinePath(values: number[], width = 100, height = 60, padding = 6) {
  if (values.length === 0) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1e-6);
  const xStep = values.length === 1 ? 0 : (width - padding * 2) / (values.length - 1);
  return values
    .map((value, index) => {
      const x = padding + index * xStep;
      const normalized = (value - min) / range;
      const y = height - padding - normalized * (height - padding * 2);
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
}

export default function HomePage() {
  const [activeSurface, setActiveSurface] = useState<SurfaceId>('chat');
  const [chatFeed, setChatFeed] = useState(messages);
  const [chatDraft, setChatDraft] = useState('');
  const [knowledgeQuery, setKnowledgeQuery] = useState('');
  const [selectedKnowledgeId, setSelectedKnowledgeId] = useState('wiki-regime');
  const [selectedScenarioId, setSelectedScenarioId] = useState(strategyScenarios[0].id);
  const [allocationDraft, setAllocationDraft] = useState(formatAllocationLines(strategyScenarios[0].allocations));
  const [buyRsi, setBuyRsi] = useState(strategyScenarios[0].defaultBuyRsi);
  const [sellRsi, setSellRsi] = useState(strategyScenarios[0].defaultSellRsi);
  const [runStamp, setRunStamp] = useState('대기 중');
  const [arcaPages, setArcaPages] = useState(2);

  const knowledgeEntries = useMemo(() => makeKnowledgeEntries(), []);
  const selectedScenario = strategyScenarios.find((scenario) => scenario.id === selectedScenarioId) ?? strategyScenarios[0];
  const activeKnowledge = knowledgeEntries.find((entry) => entry.id === selectedKnowledgeId) ?? knowledgeEntries[0];
  const filteredKnowledge = knowledgeEntries.filter((entry) => {
    const haystack = [entry.title, entry.kind, entry.summary, entry.tags.join(' '), entry.source].join(' ').toLowerCase();
    return haystack.includes(knowledgeQuery.trim().toLowerCase());
  });

  useEffect(() => {
    setAllocationDraft(formatAllocationLines(selectedScenario.allocations));
    setBuyRsi(selectedScenario.defaultBuyRsi);
    setSellRsi(selectedScenario.defaultSellRsi);
    setRunStamp(selectedScenario.note);
  }, [selectedScenario.id]);

  const parsedAllocations = useMemo(() => normalizeAllocations(parseAllocationDraft(allocationDraft)), [allocationDraft]);
  const totalWeight = parsedAllocations.reduce((sum, row) => sum + row.weight, 0);
  const cashWeight = Math.max(0, 100 - totalWeight);
  const selectedCurve = selectedScenario.curve;
  const chartBuyHold = buildLinePath(selectedCurve.buyHold);
  const chartStrategy = buildLinePath(selectedCurve.strategy);

  const graphLines = useMemo(() => {
    return graphEdges
      .map(([fromId, toId]) => {
        const from = wikiNodes.find((node) => node.id === fromId);
        const to = wikiNodes.find((node) => node.id === toId);
        if (!from || !to) return null;
        return { key: `${fromId}-${toId}`, x1: from.x, y1: from.y, x2: to.x, y2: to.y };
      })
      .filter(Boolean) as Array<{ key: string; x1: number; y1: number; x2: number; y2: number }>;
  }, []);

  const currentSurfaceMeta = surfaceTabs.find((tab) => tab.id === activeSurface) ?? surfaceTabs[0];

  const sendChat = () => {
    const text = chatDraft.trim();
    if (!text) return;
    const userMessage = {
      role: 'user' as const,
      title: '질문',
      time: '지금',
      content: text,
    };

    const knowledgeLine = activeKnowledge ? `${activeKnowledge.source} · ${activeKnowledge.title}` : '기본 컨텍스트';
    const scenarioLine = `${selectedScenario.name} · RSI ${buyRsi}/${sellRsi}`;
    const assistantReply = {
      role: 'assistant' as const,
      title: 'AI 콘솔',
      time: 'preview',
      content:
        activeSurface === 'knowledge'
          ? `${knowledgeLine}를 기준으로 읽고 있습니다. ${activeKnowledge?.summary ?? ''}`
          : activeSurface === 'strategy'
            ? `${scenarioLine} 기준으로 전략 캔버스를 보고 있습니다. 총 비중 ${totalWeight.toFixed(1)}% · 현금 ${cashWeight.toFixed(1)}%.`
            : activeSurface === 'connectors'
              ? `로컬 커넥터가 연결된 상태입니다. Arca는 ${arcaPages}페이지까지 살피고, Toss는 읽기 전용 스냅샷만 반영합니다.`
              : `현재 문맥은 ${knowledgeLine} 입니다. ${selectedScenario.note}`,
    };

    setChatFeed((current) => [...current, userMessage, assistantReply]);
    setChatDraft('');
    setActiveSurface('chat');
  };

  const saveScenario = () => {
    setRunStamp(`캔버스 반영 · ${new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}`);
  };

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
            <p>챗봇처럼 묻고, 기억을 읽고, 위키를 따라가고, 전략 캔버스를 조정하는 단일 작업공간입니다.</p>
          </div>
        </div>

        <nav className="sidebar-nav" aria-label="주요 섹션">
          {surfaceTabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeSurface === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                className={`nav-item ${isActive ? 'active' : ''}`}
                onClick={() => setActiveSurface(tab.id)}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left' }}>
                  <Icon size={16} />
                  <div>
                    <strong>{tab.label}</strong>
                    <div><span>{tab.description}</span></div>
                  </div>
                </div>
                <ArrowRight size={15} />
              </button>
            );
          })}
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
          <p>화면은 React/Next가 담당하고, Python은 수집·동기화·배치 로직으로 분리하는 구조가 더 안정적입니다.</p>
        </section>
      </aside>

      <main className="content">
        <div className="topbar">
          <div>
            <div className="panel-kicker">stock-report · frontend shell</div>
            <h2>대화형 투자 콘솔</h2>
            <p>예전 Streamlit 콘솔의 기능을 React에서 다시 살렸습니다. chat-first, memory/wiki drawer, strategy canvas, local connectors 순서로 이어집니다.</p>
          </div>
          <div className="status-row">
            <span className="status-chip teal">시장 자료 연결</span>
            <span className="status-chip violet">World Memory</span>
            <span className="status-chip amber">위키 그래프</span>
            <span className="status-chip rose">전략 캔버스</span>
          </div>
        </div>

        <section className="hero">
          <div className="hero-grid">
            <div className="hero-copy">
              <div className="panel-kicker">context-first dashboard</div>
              <h3>기능은 살리고, 화면은 가볍게</h3>
              <p>
                지금 콘솔은 단순한 목록 페이지가 아니라, 대화·기억·위키·전략·커넥터를 한 곳에 묶는 운영 화면입니다.
                예전처럼 질문이 들어오면 문맥이 자동으로 잡히고, 기억은 위키로 승격되며, 전략 캔버스와 로컬 커넥터는 같은 작업공간 안에서 이어집니다.
              </p>
              <div className="hero-meta">
                <span className="status-chip teal">챗봇 스타일 응답</span>
                <span className="status-chip violet">기억·위키 통합</span>
                <span className="status-chip amber">그래프 클릭 탐색</span>
                <span className="status-chip rose">전략 캔버스</span>
              </div>
            </div>

            <div className="report-card">
              <div className="report-status">
                <ShieldCheck size={14} />
                ready · context warm
              </div>
              <h5>현재 해석</h5>
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
          {statCards.map((card) => (
            <article className={`metric-card ${toneClassMap[card.tone]}`} key={card.label}>
              <p className="label">{card.label}</p>
              <h4 className="value">{card.value}</h4>
              <p className="delta">{card.delta}</p>
            </article>
          ))}
        </section>

        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 10,
            margin: '16px 0 18px',
            padding: '12px 12px 2px',
            border: '1px solid rgba(148, 163, 184, 0.14)',
            borderRadius: 18,
            background: 'rgba(14, 20, 34, 0.52)',
          }}
        >
          {surfaceTabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeSurface === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveSurface(tab.id)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 10,
                  borderRadius: 14,
                  border: `1px solid ${isActive ? 'rgba(52, 215, 201, 0.28)' : 'rgba(148, 163, 184, 0.14)'}`,
                  background: isActive ? 'linear-gradient(135deg, rgba(20, 29, 47, 0.98), rgba(15, 23, 42, 0.98))' : 'rgba(9, 15, 28, 0.88)',
                  color: '#e5eefb',
                  padding: '11px 14px',
                  cursor: 'pointer',
                  minWidth: 180,
                }}
              >
                <Icon size={16} />
                <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2, textAlign: 'left' }}>
                  <strong style={{ fontSize: 14, fontWeight: 700 }}>{tab.label}</strong>
                  <span style={{ fontSize: 12, color: 'rgba(148, 163, 184, 0.88)' }}>{tab.description}</span>
                </span>
              </button>
            );
          })}
          <div style={{ marginLeft: 'auto', alignSelf: 'center', color: 'rgba(148, 163, 184, 0.88)', fontSize: 12 }}>
            현재 탭: <strong style={{ color: '#e5eefb' }}>{currentSurfaceMeta.label}</strong>
          </div>
        </div>

        {activeSurface === 'chat' && (
          <section className="main-grid">
            <div className="stack">
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">ai console</div>
                    <h4>대화</h4>
                    <p>프롬프트가 아니라 문맥을 읽는 챗봇형 응답 공간입니다.</p>
                  </div>
                  <span className="status-chip teal">stateful</span>
                </div>

                <div className="chat-feed">
                  {chatFeed.map((message, index) => {
                    const role = message.role === 'user' ? 'user' : message.role === 'assistant' ? 'assistant' : 'system';
                    return (
                      <article className={`message ${role}`} key={`${message.role}-${message.time}-${index}`}>
                        <div className="message-head">
                          <strong>{message.title}</strong>
                          <span>{message.time}</span>
                        </div>
                        <p>{message.content}</p>
                      </article>
                    );
                  })}
                </div>

                <div className="composer" style={{ marginTop: 14 }}>
                  <textarea
                    value={chatDraft}
                    onChange={(event) => setChatDraft(event.target.value)}
                    placeholder="무엇이든 질문하세요 — 시장·포트폴리오·종목·모의투자·전략"
                    aria-label="질문 입력"
                  />
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {quickPrompts.map((prompt) => (
                      <button
                        type="button"
                        key={prompt}
                        onClick={() => setChatDraft(prompt)}
                        className="status-chip"
                        style={{ cursor: 'pointer' }}
                      >
                        {prompt}
                      </button>
                    ))}
                  </div>
                  <div className="composer-actions">
                    <div className="helper">
                      현재 맥락: {activeKnowledge.source} · {activeKnowledge.title} · {selectedScenario.name}
                    </div>
                    <button type="button" className="primary-btn" onClick={sendChat}>
                      질문 보내기 <Send size={15} style={{ marginLeft: 6, verticalAlign: 'middle' }} />
                    </button>
                  </div>
                </div>
              </section>

              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">strategy brief</div>
                    <h4>지금 보는 축</h4>
                    <p>질문에서 바로 꺼내 읽는 핵심 신호입니다.</p>
                  </div>
                  <span className="status-chip violet">context rail</span>
                </div>
                <div className="report-highlights">
                  {reportHighlights.map((item) => (
                    <div className="highlight" key={`rail-${item.title}`}>
                      <div className="highlight-top">
                        <strong>{item.title}</strong>
                        <span>{item.score}</span>
                      </div>
                      <p>{item.body}</p>
                    </div>
                  ))}
                </div>
              </section>
            </div>

            <div className="subgrid">
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">context glance</div>
                    <h4>시장 기억</h4>
                    <p>최근 소스와 누적 메모리를 먼저 보여줍니다.</p>
                  </div>
                  <span className="status-chip amber">glance</span>
                </div>
                <div style={{ display: 'grid', gap: 10 }}>
                  {sourceCards.map((source) => (
                    <div key={source.name} className="source-item">
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

              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">prompt routing</div>
                    <h4>자동 분류</h4>
                    <p>맥락은 질문 내용에 맞춰 자동으로 잡히는 상태를 유지합니다.</p>
                  </div>
                  <span className="status-chip rose">adaptive</span>
                </div>
                <div style={{ display: 'grid', gap: 10 }}>
                  <div className="highlight">
                    <div className="highlight-top">
                      <strong>기본 스레드</strong>
                      <span>local context ready</span>
                    </div>
                    <p>질문을 받으면 시장, 포트폴리오, 종목, 모의투자, 전략 중 하나로 자동 라우팅합니다.</p>
                  </div>
                  <div className="highlight">
                    <div className="highlight-top">
                      <strong>빠른 질문</strong>
                      <span>3개만 유지</span>
                    </div>
                    <p>대화·리스크·기억 캡처만 남기고 나머지는 knowledge drawer로 보냅니다.</p>
                  </div>
                </div>
              </section>
            </div>
          </section>
        )}

        {activeSurface === 'knowledge' && (
          <section className="main-grid">
            <div className="stack">
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">knowledge drawer</div>
                    <h4>기억·위키</h4>
                    <p>World Memory와 위키를 한 덩어리로 보며, 노드를 누르면 상세 페이지를 읽습니다.</p>
                  </div>
                  <span className="status-chip violet">unified</span>
                </div>

                <div className="composer" style={{ marginBottom: 14 }}>
                  <div className="composer-actions" style={{ marginBottom: 0 }}>
                    <div className="helper">검색하거나 클릭하면 문서가 선택됩니다. 이게 바로 위키의 상위 레이어입니다.</div>
                    <button type="button" className="primary-btn" onClick={() => setSelectedKnowledgeId('wiki-wiki')}>
                      현재 대화 승격 <PlusIcon />
                    </button>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <Search size={16} color="rgba(148, 163, 184, 0.9)" />
                    <input
                      value={knowledgeQuery}
                      onChange={(event) => setKnowledgeQuery(event.target.value)}
                      placeholder="World Memory, Oracle, 크레딧, 유가..."
                      style={{
                        flex: 1,
                        borderRadius: 14,
                        border: '1px solid rgba(148, 163, 184, 0.16)',
                        background: 'rgba(10, 15, 26, 0.9)',
                        color: '#e5eefb',
                        padding: '12px 14px',
                        outline: 'none',
                      }}
                    />
                  </div>
                </div>

                <div style={{ display: 'grid', gap: 10 }}>
                  {filteredKnowledge.map((entry) => {
                    const active = entry.id === selectedKnowledgeId;
                    return (
                      <button
                        key={entry.id}
                        type="button"
                        onClick={() => {
                          setSelectedKnowledgeId(entry.id);
                          const wikiNode = entry.id.startsWith('wiki-') ? entry.id.replace('wiki-', '') : null;
                          if (wikiNode) {
                            setActiveSurface('knowledge');
                          }
                        }}
                        style={{
                          textAlign: 'left',
                          width: '100%',
                          borderRadius: 18,
                          border: `1px solid ${active ? 'rgba(52, 215, 201, 0.30)' : 'rgba(148, 163, 184, 0.12)'}`,
                          background: active ? 'linear-gradient(180deg, rgba(16, 24, 39, 0.95), rgba(11, 17, 30, 0.95))' : 'rgba(10, 15, 26, 0.88)',
                          padding: 14,
                          color: '#e5eefb',
                          cursor: 'pointer',
                        }}
                      >
                        <div className="message-head" style={{ marginBottom: 8 }}>
                          <strong>{entry.title}</strong>
                          <span>{entry.source}</span>
                        </div>
                        <p style={{ margin: 0, color: 'rgba(148, 163, 184, 0.94)', lineHeight: 1.6 }}>{entry.summary}</p>
                        <div className="detail-tags">
                          {entry.tags.slice(0, 4).map((tag) => (
                            <span className="detail-tag" key={`${entry.id}-${tag}`}>{tag}</span>
                          ))}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </section>
            </div>

            <div className="subgrid">
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">graph</div>
                    <h4>위키 관계 그래프</h4>
                    <p>노드를 클릭하면 관련 문서가 열립니다.</p>
                  </div>
                  <span className="status-chip amber">clickable</span>
                </div>

                <div className="graph-shell" style={{ gridTemplateColumns: 'minmax(0, 1.15fr) minmax(280px, 330px)' }}>
                  <div className="graph-panel" style={{ minHeight: 480 }}>
                    <svg className="graph-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                      {graphLines.map((line) => (
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
                    {wikiNodes.map((node) => {
                      const isSelected = selectedKnowledgeId === `wiki-${node.id}`;
                      return (
                        <button
                          type="button"
                          key={node.id}
                          className={`graph-node ${toneClassMap[node.tone]} ${isSelected ? 'selected' : ''}`}
                          style={{ left: `${node.x}%`, top: `${node.y}%` }}
                          onClick={() => {
                            setSelectedKnowledgeId(`wiki-${node.id}`);
                            setActiveSurface('knowledge');
                          }}
                        >
                          {node.label}
                          <small>{node.category}</small>
                        </button>
                      );
                    })}
                  </div>

                  <aside className="graph-detail">
                    <div className="panel-kicker">selected node</div>
                    <h5>{activeKnowledge.title}</h5>
                    <p>{activeKnowledge.summary}</p>
                    <div className="detail-tags">
                      {activeKnowledge.tags.map((tag) => (
                        <span className="detail-tag" key={`${activeKnowledge.id}-${tag}`}>{tag}</span>
                      ))}
                    </div>
                    <div className="detail-list">
                      {activeKnowledge.evidence.map((item) => (
                        <div className="detail-item" key={item}>
                          <strong>{activeKnowledge.source}</strong>
                          <span>{item}</span>
                        </div>
                      ))}
                    </div>
                    <p className="footer-note">
                      메모와 위키를 나눠 놓기보다, 클릭 한 번으로 같은 지식을 다른 깊이로 읽게 만드는 편이 더 덜 산만합니다.
                    </p>
                  </aside>
                </div>
              </section>
            </div>
          </section>
        )}

        {activeSurface === 'strategy' && (
          <section className="main-grid">
            <div className="stack">
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">strategy canvas</div>
                    <h4>전략 캔버스</h4>
                    <p>비중, RSI 규칙, 손실 한도를 한 화면에서 보정합니다.</p>
                  </div>
                  <span className="status-chip teal">interactive</span>
                </div>

                <div className="composer" style={{ marginBottom: 14 }}>
                  <div className="composer-actions" style={{ marginBottom: 0 }}>
                    <div className="helper">시나리오를 선택하거나 직접 비중을 편집한 뒤 캔버스를 다시 그립니다.</div>
                    <button
                      type="button"
                      className="primary-btn"
                      onClick={() => {
                        setRunStamp(`캔버스 반영 · ${new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}`);
                      }}
                    >
                      캔버스 실행 <Play size={15} style={{ marginLeft: 6, verticalAlign: 'middle' }} />
                    </button>
                  </div>

                  <div style={{ display: 'grid', gap: 10 }}>
                    <label style={{ display: 'grid', gap: 8 }}>
                      <span className="panel-kicker">시나리오</span>
                      <select
                        value={selectedScenarioId}
                        onChange={(event) => setSelectedScenarioId(event.target.value)}
                        style={{
                          borderRadius: 14,
                          border: '1px solid rgba(148, 163, 184, 0.16)',
                          background: 'rgba(10, 15, 26, 0.9)',
                          color: '#e5eefb',
                          padding: '12px 14px',
                          outline: 'none',
                        }}
                      >
                        {strategyScenarios.map((scenario) => (
                          <option key={scenario.id} value={scenario.id}>
                            {scenario.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label style={{ display: 'grid', gap: 8 }}>
                      <span className="panel-kicker">비중 편집</span>
                      <textarea
                        value={allocationDraft}
                        onChange={(event) => setAllocationDraft(event.target.value)}
                        rows={8}
                        style={{
                          width: '100%',
                          borderRadius: 14,
                          border: '1px solid rgba(148, 163, 184, 0.16)',
                          background: 'rgba(10, 15, 26, 0.9)',
                          color: '#e5eefb',
                          padding: 14,
                          lineHeight: 1.55,
                          outline: 'none',
                          resize: 'vertical',
                        }}
                      />
                    </label>
                  </div>
                </div>

                <div className="panel-header" style={{ marginTop: 6 }}>
                  <div>
                    <div className="panel-kicker">rule builder</div>
                    <h4>RSI 현금화 규칙</h4>
                  </div>
                  <span className="status-chip violet">next day</span>
                </div>

                <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', margin: 0 }}>
                  <article className="metric-card teal"><p className="label">매수 RSI</p><h4 className="value">≤ {buyRsi}</h4><p className="delta">신호가 여기에 오면 매수 대기</p></article>
                  <article className="metric-card violet"><p className="label">현금화 RSI</p><h4 className="value">≥ {sellRsi}</h4><p className="delta">과열 시 노출 축소</p></article>
                  <article className="metric-card amber"><p className="label">상태</p><h4 className="value">{runStamp}</h4><p className="delta">정보형 백테스트 · 비용 0bps</p></article>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12, marginTop: 14 }}>
                  <label style={{ display: 'grid', gap: 8 }}>
                    <span className="panel-kicker">매수 RSI</span>
                    <input
                      type="number"
                      min={1}
                      max={99}
                      value={buyRsi}
                      onChange={(event) => setBuyRsi(Number(event.target.value))}
                      style={{
                        borderRadius: 14,
                        border: '1px solid rgba(148, 163, 184, 0.16)',
                        background: 'rgba(10, 15, 26, 0.9)',
                        color: '#e5eefb',
                        padding: '12px 14px',
                      }}
                    />
                  </label>
                  <label style={{ display: 'grid', gap: 8 }}>
                    <span className="panel-kicker">현금화 RSI</span>
                    <input
                      type="number"
                      min={1}
                      max={99}
                      value={sellRsi}
                      onChange={(event) => setSellRsi(Number(event.target.value))}
                      style={{
                        borderRadius: 14,
                        border: '1px solid rgba(148, 163, 184, 0.16)',
                        background: 'rgba(10, 15, 26, 0.9)',
                        color: '#e5eefb',
                        padding: '12px 14px',
                      }}
                    />
                  </label>
                </div>

                <div style={{ marginTop: 14 }}>
                  <span className="panel-kicker">파싱된 비중</span>
                  <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
                    {parsedAllocations.map((entry) => (
                      <div key={`${entry.symbol}-${entry.memo}`} className="source-item">
                        <div className="source-item-top">
                          <span className="source-name">{entry.symbol}</span>
                          <span className="source-count">{entry.weight.toFixed(1)}%</span>
                        </div>
                        <p>{entry.memo || 'no memo'}</p>
                      </div>
                    ))}
                    <div className="source-item">
                      <div className="source-item-top">
                        <span className="source-name">CASH</span>
                        <span className="source-count">{cashWeight.toFixed(1)}%</span>
                      </div>
                      <p>손실 한도 안에서 남기는 여유 현금입니다.</p>
                    </div>
                  </div>
                </div>
              </section>
            </div>

            <div className="subgrid">
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">comparison</div>
                    <h4>Buy & Hold vs RSI 현금화</h4>
                    <p>전략이 기존 보유 대비 어디서 달라지는지 봅니다.</p>
                  </div>
                  <span className="status-chip amber">preview</span>
                </div>
                <svg viewBox="0 0 100 60" style={{ width: '100%', height: 320, display: 'block', borderRadius: 18, background: 'rgba(10, 15, 26, 0.9)', border: '1px solid rgba(148, 163, 184, 0.12)' }}>
                  {[12, 24, 36, 48].map((y) => (
                    <line key={y} x1="6" y1={y} x2="94" y2={y} stroke="rgba(148, 163, 184, 0.12)" strokeDasharray="2 2" />
                  ))}
                  <path d={buildLinePath(selectedCurve.buyHold)} fill="none" stroke="rgba(16, 185, 129, 0.98)" strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round" />
                  <path d={buildLinePath(selectedCurve.strategy)} fill="none" stroke="rgba(168, 85, 247, 0.98)" strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round" />
                  <circle cx="8" cy="54" r="1.8" fill="rgba(148, 163, 184, 0.6)" />
                  <circle cx="92" cy="12" r="1.8" fill="rgba(148, 163, 184, 0.6)" />
                </svg>
                <div className="hero-meta" style={{ marginTop: 12 }}>
                  <span className="status-chip teal">Buy & Hold</span>
                  <span className="status-chip violet">RSI 현금화</span>
                </div>
                <p className="footer-note">{selectedScenario.note}</p>
              </section>

              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">metrics</div>
                    <h4>백테스트 요약</h4>
                    <p>시나리오에 따라 리스크와 수익을 함께 읽습니다.</p>
                  </div>
                  <span className="status-chip rose">{selectedScenario.status}</span>
                </div>
                <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', margin: 0 }}>
                  {selectedScenario.metrics.map((metric) => (
                    <article className="metric-card" key={metric.label}>
                      <p className="label">{metric.label}</p>
                      <h4 className="value">{metric.value}</h4>
                      <p className="delta">{metric.delta}</p>
                    </article>
                  ))}
                </div>
                <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
                  <div className="highlight">
                    <div className="highlight-top">
                      <strong>시나리오</strong>
                      <span>{selectedScenario.name}</span>
                    </div>
                    <p>{selectedScenario.note}</p>
                  </div>
                  <div className="highlight">
                    <div className="highlight-top">
                      <strong>총 비중</strong>
                      <span>{totalWeight.toFixed(1)}%</span>
                    </div>
                    <p>정규화된 비중을 기준으로 보기 때문에 입력값이 100이 아니어도 현재 규칙을 빠르게 비교할 수 있습니다.</p>
                  </div>
                </div>
              </section>

              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">saved scenarios</div>
                    <h4>저장된 시나리오</h4>
                    <p>이전 실험을 다시 불러와 비교합니다.</p>
                  </div>
                  <span className="status-chip teal">history</span>
                </div>
                <div style={{ display: 'grid', gap: 10 }}>
                  {strategyScenarios.map((scenario) => {
                    const active = scenario.id === selectedScenarioId;
                    return (
                      <button
                        key={scenario.id}
                        type="button"
                        onClick={() => setSelectedScenarioId(scenario.id)}
                        style={{
                          width: '100%',
                          textAlign: 'left',
                          padding: 14,
                          borderRadius: 18,
                          border: `1px solid ${active ? 'rgba(52, 215, 201, 0.28)' : 'rgba(148, 163, 184, 0.12)'}`,
                          background: active ? 'linear-gradient(180deg, rgba(16, 24, 39, 0.95), rgba(11, 17, 30, 0.95))' : 'rgba(10, 15, 26, 0.9)',
                          color: '#e5eefb',
                          cursor: 'pointer',
                        }}
                      >
                        <div className="message-head" style={{ marginBottom: 6 }}>
                          <strong>{scenario.name}</strong>
                          <span>{scenario.status}</span>
                        </div>
                        <p style={{ margin: 0, color: 'rgba(148, 163, 184, 0.94)', lineHeight: 1.6 }}>{scenario.note}</p>
                      </button>
                    );
                  })}
                </div>
              </section>
            </div>
          </section>
        )}

        {activeSurface === 'connectors' && (
          <section className="main-grid">
            <div className="stack">
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">local connectors</div>
                    <h4>로컬 커넥터</h4>
                    <p>Arca, Toss, raw archive를 한곳에 모읍니다.</p>
                  </div>
                  <span className="status-chip violet">local-first</span>
                </div>
                <div style={{ display: 'grid', gap: 12 }}>
                  {connectorCards.map((connector) => (
                    <div key={connector.name} className="report-card" style={{ padding: 16 }}>
                      <div className="report-status" style={{ marginBottom: 12 }}>
                        <Wifi size={14} />
                        {connector.status}
                      </div>
                      <div className="message-head">
                        <strong>{connector.name}</strong>
                        <span>{connector.action}</span>
                      </div>
                      <p style={{ color: 'rgba(148, 163, 184, 0.94)', lineHeight: 1.65 }}>{connector.detail}</p>
                    </div>
                  ))}
                </div>
              </section>
            </div>

            <div className="subgrid">
              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">arca proxy</div>
                    <h4>공개 글 수집 상태</h4>
                    <p>Cloudflare challenge는 우회하지 않고, 성공한 공개 글만 적재합니다.</p>
                  </div>
                  <span className="status-chip amber">proxy</span>
                </div>
                <div className="metric-grid" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', margin: 0 }}>
                  <article className="metric-card teal"><p className="label">터널</p><h4 className="value">UP</h4><p className="delta">127.0.0.1:1080</p></article>
                  <article className="metric-card violet"><p className="label">조회 페이지</p><h4 className="value">{arcaPages}</h4><p className="delta">수동 조절</p></article>
                </div>
                <div style={{ display: 'grid', gap: 12, marginTop: 14 }}>
                  <label style={{ display: 'grid', gap: 8 }}>
                    <span className="panel-kicker">페이지 수</span>
                    <input
                      type="range"
                      min={1}
                      max={5}
                      value={arcaPages}
                      onChange={(event) => setArcaPages(Number(event.target.value))}
                    />
                  </label>
                  <div className="highlight">
                    <div className="highlight-top">
                      <strong>수집 정책</strong>
                      <span>read-only</span>
                    </div>
                    <p>원문, PDF, OCR 텍스트를 모두 보관하되, 위키에는 승격된 요약만 올립니다.</p>
                  </div>
                </div>
              </section>

              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">toss snapshot</div>
                    <h4>읽기 전용 자산 스냅샷</h4>
                    <p>실자산 연동 전까지는 로컬 스냅샷만 반영합니다.</p>
                  </div>
                  <span className="status-chip rose">snapshot</span>
                </div>
                <div style={{ display: 'grid', gap: 10 }}>
                  <div className="source-item">
                    <div className="source-item-top">
                      <span className="source-name">보유 현황</span>
                      <span className="source-count">read only</span>
                    </div>
                    <p>노트북에서 수집한 자산 스냅샷을 살펴보고, 주문 연결은 하지 않습니다.</p>
                  </div>
                  <div className="source-item">
                    <div className="source-item-top">
                      <span className="source-name">Raw Vault</span>
                      <span className="source-count">archive</span>
                    </div>
                    <p>뉴스 원문 PDF · 데일리 리포트 OCR · 텔레그램 원문 · 위키 원본을 같이 둡니다.</p>
                  </div>
                </div>
              </section>

              <section className="panel">
                <div className="panel-header">
                  <div>
                    <div className="panel-kicker">ingest map</div>
                    <h4>소스 분류</h4>
                    <p>각 소스별로 raw 데이터와 승격 데이터를 따로 봅니다.</p>
                  </div>
                  <span className="status-chip teal">taxonomy</span>
                </div>
                <div className="detail-list">
                  <div className="detail-item">
                    <strong>saveticker</strong>
                    <span>뉴스 원문, PDF, OCR 리포트, 요약문까지 분리해서 관리합니다.</span>
                  </div>
                  <div className="detail-item">
                    <strong>telegram</strong>
                    <span>메시지 원문을 보관하고, 승격된 판단만 World Memory에 남깁니다.</span>
                  </div>
                  <div className="detail-item">
                    <strong>arca</strong>
                    <span>공개 글만 수집하고 challenge 실패는 실패로 남겨 패턴을 봅니다.</span>
                  </div>
                </div>
              </section>
            </div>
          </section>
        )}

        <section className="panel" style={{ marginTop: 18 }}>
          <div className="panel-header">
            <div>
              <div className="panel-kicker">roadmap</div>
              <h4>다음 연결</h4>
              <p>지금은 프론트 기능과 UX를 먼저 살렸고, 다음엔 Python API를 얇게 붙이면 됩니다.</p>
            </div>
            <span className="status-chip teal">ready for api</span>
          </div>
          <div className="report-highlights">
            <div className="highlight">
              <div className="highlight-top">
                <strong>1. 채팅 연결</strong>
                <span>answer API</span>
              </div>
              <p>지금의 로컬 미리보기 응답을 실제 agent 콘솔 응답으로 교체하면 됩니다.</p>
            </div>
            <div className="highlight">
              <div className="highlight-top">
                <strong>2. 기억·위키 저장</strong>
                <span>memory store</span>
              </div>
              <p>World Memory와 위키 원본을 남겨 두는 구조는 그대로 유지하고 UI만 React로 보여줍니다.</p>
            </div>
            <div className="highlight">
              <div className="highlight-top">
                <strong>3. 전략 · 커넥터</strong>
                <span>advanced drawer</span>
              </div>
              <p>자주 안 쓰는 기능은 탭으로 정리해 첫 화면을 덜 복잡하게 만들었습니다.</p>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

function PlusIcon() {
  return <span aria-hidden="true" />;
}
