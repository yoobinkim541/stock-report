export type RouteKey =
  | 'home'
  | 'portfolio'
  | 'analysis'
  | 'charts'
  | 'market-calendar'
  | 'mock-invest'
  | 'research'
  | 'ai-console'
  | 'ai-wiki'
  | 'bridge';

export type TickerRow = {
  symbol: string;
  name: string;
  price: string;
  delta: string;
  tone: 'teal' | 'violet' | 'amber' | 'rose';
};

export type MetricCard = {
  label: string;
  value: string;
  delta: string;
  tone: 'teal' | 'violet' | 'amber' | 'rose';
};

export type HoldingRow = {
  symbol: string;
  name: string;
  value: string;
  delta: string;
  memo: string;
};

export type CalendarEvent = {
  date: string;
  label: string;
  impact: string;
  note: string;
};

export type ResearchItem = {
  title: string;
  source: string;
  summary: string;
  tag: string;
};

export type TradeRow = {
  time: string;
  symbol: string;
  action: string;
  reason: string;
};

export type ChartPoint = {
  label: string;
  buyHold: number;
  strategy: number;
};

export const pageMeta: Record<RouteKey, { kicker: string; title: string; subtitle: string }> = {
  home: {
    kicker: 'control room',
    title: '홈 대시보드',
    subtitle: '시장, 포트폴리오, 위키, 전략, 리서치를 하나의 콘솔로 묶은 시작 화면입니다.',
  },
  portfolio: {
    kicker: 'portfolio',
    title: '포트폴리오',
    subtitle: '보유, 현금, 손익, 레버리지를 같은 기준으로 읽는 요약 화면입니다.',
  },
  analysis: {
    kicker: 'analysis',
    title: '종목 분석',
    subtitle: '개별 종목의 논리와 리스크를 핵심 신호 위주로 정리하는 화면입니다.',
  },
  charts: {
    kicker: 'chart room',
    title: '차트 풀뷰',
    subtitle: '가격과 보조지표를 크게 펼쳐 추세와 과열을 함께 보는 화면입니다.',
  },
  'market-calendar': {
    kicker: 'calendar',
    title: '시장-캘린더',
    subtitle: '실적, 지표, 중앙은행 일정과 크레딧 이벤트를 먼저 잡는 화면입니다.',
  },
  'mock-invest': {
    kicker: 'paper trading',
    title: '모의투자',
    subtitle: '가상 체결과 백테스트를 손실 한도 안에서 시험하는 화면입니다.',
  },
  research: {
    kicker: 'research',
    title: '리서치',
    subtitle: '뉴스, 텔레그램, 리포트 원문을 승격 대기열로 모아두는 화면입니다.',
  },
  'ai-console': {
    kicker: 'ai console',
    title: 'AI 콘솔',
    subtitle: '질문을 넣으면 문맥과 위키를 읽고 자동으로 답을 조립하는 대화 공간입니다.',
  },
  'ai-wiki': {
    kicker: 'ai wiki',
    title: 'AI 위키',
    subtitle: 'World Memory와 승격된 판단을 그래프로 다시 읽는 정리층입니다.',
  },
  bridge: {
    kicker: 'bridge',
    title: '파이썬 출입문',
    subtitle: '터널 주소가 바뀌어도 여기 한 곳만 바꾸면 되는 고정 진입점입니다.',
  },
};

export const navItems: Array<{ key: RouteKey; label: string; href: string }> = [
  { key: 'home', label: '홈', href: '/' },
  { key: 'portfolio', label: '포트폴리오', href: '/portfolio' },
  { key: 'analysis', label: '종목 분석', href: '/analysis' },
  { key: 'charts', label: '차트 풀뷰', href: '/charts/fullview' },
  { key: 'market-calendar', label: '시장-캘린더', href: '/market-calendar' },
  { key: 'mock-invest', label: '모의투자', href: '/mock-invest' },
  { key: 'research', label: '리서치', href: '/research' },
  { key: 'ai-console', label: 'AI 콘솔', href: '/ai-console' },
  { key: 'ai-wiki', label: 'AI 위키', href: '/ai-wiki' },
  { key: 'bridge', label: '파이썬 출입문', href: '/bridge' },
];

export const sidebarStats = [
  { label: '최근 이벤트', value: '40건', detail: 'news_llm_labels · kr_intraday_outcomes' },
  { label: '누적 기억', value: '50건', detail: 'World Memory · 위키 승격' },
  { label: '모델 파일', value: '4개', detail: '요약 · 검증 · 추천 · 리포트' },
  { label: '최신 리포트', value: '2026-07-22', detail: '거시/뉴스 이벤트 흐름' },
];

export const sidebarWatchlist: TickerRow[] = [
  { symbol: 'QQQI', name: 'QQQ 인덱스', price: '2,054.22', delta: '+3.11%', tone: 'teal' },
  { symbol: 'UNH', name: 'UnitedHealth', price: '1,536.61', delta: '+36.63%', tone: 'teal' },
  { symbol: 'SGOV', name: '초단기 국채', price: '1,509.00', delta: '+0.15%', tone: 'teal' },
  { symbol: 'MSFT', name: 'Microsoft', price: '1,292.13', delta: '-0.92%', tone: 'rose' },
  { symbol: 'ORCL', name: 'Oracle', price: '979.40', delta: '-27.44%', tone: 'rose' },
  { symbol: 'NVDA', name: 'NVIDIA', price: '707.54', delta: '+7.76%', tone: 'teal' },
  { symbol: 'GOOGL', name: 'Alphabet', price: '671.39', delta: '+4.09%', tone: 'teal' },
  { symbol: 'SPMO', name: 'Momentum', price: '393.83', delta: '+3.83%', tone: 'teal' },
  { symbol: 'CRM', name: 'Salesforce', price: '307.76', delta: '-3.90%', tone: 'rose' },
];

export const homeMetrics: MetricCard[] = [
  { label: '포트폴리오', value: '$9,941.12', delta: '+146.31 · 1.49%', tone: 'teal' },
  { label: 'Phase', value: '1 조정', delta: '리스크 체크 중', tone: 'violet' },
  { label: 'QQQ 낙폭', value: '-5.7%', delta: '기준선 대비', tone: 'amber' },
  { label: 'DCA 배율', value: '1.5x', delta: '분할 매수 유지', tone: 'rose' },
];

export const portfolioHoldings: HoldingRow[] = [
  { symbol: 'CRM', name: 'Salesforce', value: '$48,975', delta: '+4.0%', memo: '주요 성장주' },
  { symbol: 'META', name: 'Meta Platforms', value: '$45,837', delta: '+2.3%', memo: '광고·AI' },
  { symbol: 'INTC', name: 'Intel', value: '$36,379', delta: '-10.8%', memo: '반도체 턴어라운드' },
  { symbol: 'ADBE', name: 'Adobe', value: '$30,048', delta: '-0.3%', memo: '소프트웨어' },
  { symbol: 'AMZN', name: 'Amazon', value: '$15,104', delta: '+1.1%', memo: '클라우드/소비' },
  { symbol: 'QCOM', name: 'Qualcomm', value: '$560', delta: '+3.5%', memo: '소형 포지션' },
];

export const portfolioSignals = [
  { label: '평가손익', value: '-0.8%', note: '-$1,399' },
  { label: '현금 비중', value: '28.7%', note: '여유 증거금 $71,170' },
  { label: '회전율', value: '102%', note: '비용 부담 보통' },
  { label: 'MDD', value: '3.9%', note: '지수보다 깊음' },
];

export const analysisSnapshot = {
  symbol: 'ORCL',
  name: 'Oracle',
  price: '$138.44',
  delta: '+1.18%',
  thesis:
    'AI 인프라와 데이터베이스 수요를 함께 보는 장기 후보로 유지하되, 단기에는 크레딧과 유가가 먼저 안정되는지 확인하는 구성이 맞습니다.',
  bullets: [
    '장기 수요는 남아 있고, 단기 요인은 레짐 전환 확인이 우선',
    '비중은 손실 한도 안에서 유지하고, 과열 구간에서는 현금 비중을 먼저 확보',
    '뉴스가 아니라 체결과 가이던스, CAPEX를 같이 읽어야 함',
  ],
  gauges: [
    { label: '기술 점수', value: '74', tone: 'teal' },
    { label: '가치 점수', value: '63', tone: 'violet' },
    { label: '리스크 점수', value: '51', tone: 'amber' },
  ],
};

export const chartSeries: ChartPoint[] = [
  { label: 'D1', buyHold: 100, strategy: 100 },
  { label: 'D2', buyHold: 101, strategy: 100.6 },
  { label: 'D3', buyHold: 103, strategy: 101.2 },
  { label: 'D4', buyHold: 104, strategy: 103 },
  { label: 'D5', buyHold: 109, strategy: 105.4 },
  { label: 'D6', buyHold: 112, strategy: 108 },
  { label: 'D7', buyHold: 116, strategy: 109 },
  { label: 'D8', buyHold: 118, strategy: 110 },
  { label: 'D9', buyHold: 117, strategy: 110.5 },
  { label: 'D10', buyHold: 121, strategy: 113 },
  { label: 'D11', buyHold: 123, strategy: 115.7 },
  { label: 'D12', buyHold: 120, strategy: 114 },
  { label: 'D13', buyHold: 124, strategy: 118.1 },
  { label: 'D14', buyHold: 126, strategy: 119 },
  { label: 'D15', buyHold: 122, strategy: 117.2 },
];

export const calendarEvents: CalendarEvent[] = [
  { date: '07/22', label: 'FOMC minutes', impact: 'High', note: '금리 경로와 유동성 재평가' },
  { date: '07/23', label: 'TSMC earnings call', impact: 'High', note: 'AI CAPEX·HBM 수요 체크' },
  { date: '07/24', label: 'US PMI', impact: 'Medium', note: '경기 둔화/회복 신호 확인' },
  { date: '07/25', label: 'Big Tech results', impact: 'High', note: 'QQQ 모멘텀과 밸류에이션 점검' },
  { date: '07/26', label: 'Credit spread review', impact: 'Medium', note: 'HYG/LQD·DXY 동시 체크' },
];

export const researchQueue: ResearchItem[] = [
  { title: 'Oracle 장기 보유 근거', source: 'AI 위키', summary: 'AI 인프라·DB 매출과 수요 재조정의 균형을 본다.', tag: 'holding' },
  { title: '레버리지 허용 범위', source: '모의투자', summary: '거래 횟수보다 최대 손실 예산으로 관리하는 방식.', tag: 'risk' },
  { title: '데일리 리포트 OCR', source: 'SaveTicker', summary: 'PDF 원문에서 본문을 추출해 위키 승격 기준으로 사용.', tag: 'ingest' },
  { title: '텔레그램 신호 분류', source: 'insidertracking', summary: '원문과 승격 판단을 분리해 raw vault를 유지.', tag: 'signal' },
];

export const mockTrades: TradeRow[] = [
  { time: '09:31', symbol: 'AMZN', action: 'Buy', reason: '모멘텀 확인' },
  { time: '10:14', symbol: 'QQQ', action: 'Add', reason: '리스크 안정' },
  { time: '13:28', symbol: 'ORCL', action: 'Hold', reason: '장기 보유 유지' },
  { time: '15:02', symbol: 'SOXL', action: 'Trim', reason: '손실 한도 관리' },
];
