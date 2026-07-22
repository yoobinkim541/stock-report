export type Tone = 'teal' | 'violet' | 'amber' | 'rose' | 'slate';

export type StatCard = {
  label: string;
  value: string;
  delta: string;
  tone: Tone;
};

export type SourceCard = {
  name: string;
  count: number;
  kind: string;
  status: string;
};

export type MemoryItem = {
  title: string;
  kind: string;
  detail: string;
  tags: string[];
};

export type ChatMessage = {
  role: 'assistant' | 'user' | 'system';
  title: string;
  time: string;
  content: string;
};

export type WikiNode = {
  id: string;
  label: string;
  category: string;
  x: number;
  y: number;
  summary: string;
  evidence: string[];
  related: string[];
  tone: Tone;
};

export type ReportHighlight = {
  title: string;
  body: string;
  score: number;
};

export const statCards: StatCard[] = [
  { label: '최근 이벤트', value: '40건', delta: 'news_llm_labels · kr_intraday_outcomes', tone: 'teal' },
  { label: '누적 기억', value: '50건', delta: '월드 메모리 · 위키 승격 대기', tone: 'violet' },
  { label: '모델 파일', value: '4개', delta: '요약 · 검증 · 추천 · 리포트', tone: 'amber' },
  { label: '최신 리포트', value: '2026-07-22', delta: '거시/뉴스 이벤트 흐름', tone: 'rose' },
];

export const sourceCards: SourceCard[] = [
  { name: 'saveticker', count: 57, kind: '뉴스 · 리포트', status: '본문 수집 · PDF 보관' },
  { name: 'telegram:insidertracking', count: 3, kind: '텔레그램', status: '신호 추출 · 태그 분류' },
  { name: 'arca', count: 4, kind: '커뮤니티', status: '속보 · 참고 메타' },
  { name: 'world memory', count: 50, kind: '승격 메모리', status: '대화 맥락 · 관계 그래프' },
];

export const memoryItems: MemoryItem[] = [
  {
    title: '시장 레짐은 MIXED',
    kind: '시장 상황',
    detail: '지정학과 크레딧 압력이 함께 남아 있어, 주식 반등만으로 비중을 키우기보다 유가와 달러가 먼저 안정되는지 확인하는 쪽이 맞습니다.',
    tags: ['지정학', '크레딧', '유동성'],
  },
  {
    title: '오라클은 보유 후보',
    kind: '포트폴리오 규칙',
    detail: 'AI 인프라와 관련된 장기 구조적 수요가 남아 있어, 전체 위험 예산 안에서 핵심 보유로 유지하는 구성이 자연스럽습니다.',
    tags: ['장기보유', 'AI 인프라', '위험예산'],
  },
  {
    title: '단기 트레이딩은 캡보다 손실 한도',
    kind: '전략 메모',
    detail: '거래 횟수 제한보다 계좌 손실 상한을 먼저 정하고, 그 범위 내에서 레버리지와 개별주를 함께 운용하는 접근이 더 일관적입니다.',
    tags: ['레버리지', '손실한도', '실행규칙'],
  },
];

export const messages: ChatMessage[] = [
  {
    role: 'system',
    title: 'local context ready',
    time: '지금',
    content: '현재 시장 자료, 모의투자 원장, World Memory를 읽고 있습니다. 질문을 던지면 이 맥락 안에서 답합니다.',
  },
  {
    role: 'user',
    title: '질문',
    time: '15:32',
    content: '오라클은 들고 가고 싶은데, 현재 비중에서 먼저 줄여야 할 리스크가 뭐야?',
  },
  {
    role: 'assistant',
    title: 'AI 콘솔',
    time: '15:33',
    content: '지금은 개별 종목보다 크레딧과 지정학 쪽 방어 프리미엄을 먼저 줄이는 게 아니라, 그 축이 안정되는지 확인하면서 보유를 유지하는 편이 더 맞습니다. 오라클은 유지하고, 과도한 단기 레버리지만 손실 예산 안에서 조절하는 쪽이 자연스럽습니다.',
  },
  {
    role: 'assistant',
    title: 'AI 위키',
    time: '연결됨',
    content: '위키는 대화와 리포트에서 반복된 판단을 승격해 다시 읽는 정리층입니다. 시장 레짐, 종목 메모, 소스 신뢰도, 사후 성과를 한 화면에서 이어 볼 수 있습니다.',
  },
];

export const wikiNodes: WikiNode[] = [
  {
    id: 'regime',
    label: '시장 레짐',
    category: 'context',
    x: 16,
    y: 28,
    summary: 'MIXED → RISK-ON 전환 확인은 유가·달러·크레딧 안정이 먼저입니다.',
    evidence: ['지정학 63', '크레딧 66', '유동성 51'],
    related: ['credit', 'geopolitics', 'oil'],
    tone: 'teal',
  },
  {
    id: 'credit',
    label: '크레딧',
    category: 'risk',
    x: 32,
    y: 40,
    summary: '주식 반등이 나와도 HYG/LQD가 따라오지 않으면 신뢰도를 낮게 봅니다.',
    evidence: ['HYG/LQD 체크', '달러 강세', '대출 압력'],
    related: ['regime', 'usd', 'liquidity'],
    tone: 'violet',
  },
  {
    id: 'oil',
    label: '유가',
    category: 'risk',
    x: 48,
    y: 24,
    summary: '중동 꼬리위험이 다시 가격에 들어오는지 확인하는 핵심 관찰 지점입니다.',
    evidence: ['Brent-WTI 스프레드', '보험료', '호르무즈 통항'],
    related: ['regime', 'geopolitics'],
    tone: 'amber',
  },
  {
    id: 'ai-infra',
    label: 'AI 인프라',
    category: 'theme',
    x: 68,
    y: 38,
    summary: 'QQQ, SMH, SOXX는 수요보다 CAPEX·전력비·마진 둔화를 더 먼저 봅니다.',
    evidence: ['TSMC 매출', '전력비', '마진'],
    related: ['oracle', 'semis', 'report'],
    tone: 'rose',
  },
  {
    id: 'oracle',
    label: 'Oracle',
    category: 'holding',
    x: 79,
    y: 56,
    summary: '장기 보유 후보로 유지 가능한 AI 인프라 노출입니다.',
    evidence: ['보유 유지', '분할', '장기 수요'],
    related: ['ai-infra', 'portfolio'],
    tone: 'teal',
  },
  {
    id: 'portfolio',
    label: '포트폴리오',
    category: 'control',
    x: 62,
    y: 63,
    summary: '레버리지·현금·개별주를 손실 예산 안에서 묶어 관리합니다.',
    evidence: ['손실한도', '회전율', '현금비중'],
    related: ['oracle', 'wiki', 'sources'],
    tone: 'violet',
  },
  {
    id: 'wiki',
    label: 'AI 위키',
    category: 'memory',
    x: 52,
    y: 74,
    summary: '대화·리포트·뉴스를 승격해 재사용 가능한 기억층으로 정리합니다.',
    evidence: ['source weight', 'raw preservation', 'graph links'],
    related: ['report', 'memory', 'sources'],
    tone: 'violet',
  },
  {
    id: 'sources',
    label: '소스 가중치',
    category: 'ingest',
    x: 36,
    y: 68,
    summary: 'saveticker, telegram, arca, PDF 원문 보관 비중을 소스별로 다르게 둡니다.',
    evidence: ['본문 원본', 'PDF 아카이브', '태그 분류'],
    related: ['wiki', 'report'],
    tone: 'amber',
  },
];

export const graphEdges: Array<[string, string]> = [
  ['regime', 'credit'],
  ['regime', 'oil'],
  ['credit', 'ai-infra'],
  ['oil', 'ai-infra'],
  ['ai-infra', 'oracle'],
  ['oracle', 'portfolio'],
  ['portfolio', 'wiki'],
  ['wiki', 'sources'],
  ['sources', 'regime'],
  ['credit', 'sources'],
];

export const reportHighlights: ReportHighlight[] = [
  {
    title: '지금 우선순위',
    body: '지정학 재교전, 크레딧 확인, AI/반도체 밸류에이션 순으로 본다.',
    score: 88,
  },
  {
    title: '단기 실행 기준',
    body: '매수·매도 횟수보다 최대 손실 한도와 회전율을 먼저 고정한다.',
    score: 74,
  },
  {
    title: '학습 신호',
    body: '사후 성과가 쌓이면 추천이 왜 성공/실패했는지 위키에 승격한다.',
    score: 81,
  },
];
