'use client';

import { useState, type CSSProperties } from 'react';
import { Activity, ArrowUpRight, BookOpen, ChartNoAxesCombined, ChevronDown, CircleDot, Layers3, Maximize2, ScanLine, ShieldCheck, Sparkles, Wallet } from 'lucide-react';

const markets = {
  us: { name: 'NVIDIA', ticker: 'NVDA', exchange: 'NASDAQ', price: '142.87', delta: '+2.34%', currency: 'USD', watch: [['NVDA', '142.87', '+2.34%'], ['AAPL', '228.26', '+0.82%'], ['MSFT', '428.76', '+1.16%'], ['QQQ', '498.32', '+0.94%']], offset: 0 },
  kr: { name: '삼성전자', ticker: '005930', exchange: 'KRX', price: '72,400', delta: '+1.26%', currency: 'KRW', watch: [['005930', '72,400', '+1.26%'], ['000660', '198,500', '+2.06%'], ['035420', '182,000', '+0.55%'], ['KOSPI', '2,685.42', '+0.72%']], offset: 24 },
} as const;

const periods = ['1일', '1주', '1개월'] as const;
const chartPrices = [216, 220, 199, 206, 196, 183, 192, 171, 177, 164, 173, 183, 167, 148, 158, 139, 150, 126, 135, 118, 129, 111, 116, 93, 102, 86, 98, 109, 92, 81, 90, 72, 84, 69, 79, 62, 74, 53, 65, 48];

function PriceChart({ market, period }: { market: keyof typeof markets; period: number }) {
  const points = chartPrices.map((price, index) => ({ x: 20 + index * 14, y: price + Math.sin(index * (period + 1) * 0.6) * (period * 12 + markets[market].offset) }));
  const path = points.map((point, i) => `${i === 0 ? 'M' : 'L'}${point.x},${point.y + 14}`).join(' ');
  return (
    <svg className="price-chart" viewBox="0 0 620 280" role="img" aria-label={`${markets[market].name} ${periods[period]} 예시 가격 차트`}>
      <defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#c5f66b" stopOpacity="0.13" /><stop offset="100%" stopColor="#c5f66b" stopOpacity="0" /></linearGradient></defs>
      {[45, 95, 145, 195, 245].map(y => <line key={`h${y}`} x1="0" y1={y} x2="620" y2={y} stroke="#ffffff" strokeOpacity="0.055" strokeDasharray="3 5" />)}
      {[60, 160, 260, 360, 460, 560].map(x => <line key={`v${x}`} x1={x} y1="0" x2={x} y2="270" stroke="#ffffff" strokeOpacity="0.035" />)}
      <path d={`${path} L566,270 L20,270 Z`} fill="url(#chart-fill)" />
      <path className="chart-trace" d={path} pathLength="1" fill="none" stroke="#c5f66b" strokeWidth="1.5" strokeOpacity="0.7" />
      {points.map(({ x, y }, index) => {
        const up = index === 0 || y < points[index - 1].y;
        return <g key={index} className="chart-candle" style={{ '--candle-delay': `${index * 22}ms` } as CSSProperties}>
          <line x1={x} y1={y - 9} x2={x} y2={y + 27} stroke={up ? '#c5f66b' : '#72877c'} strokeWidth="1" />
          <rect x={x - 3.5} y={y} width="7" height={up ? 17 : 21} rx="1" fill={up ? '#c5f66b' : '#72877c'} />
          <rect x={x - 4} y={268 - (index % 7 + 1) * 3} width="8" height={(index % 7 + 1) * 3} fill={up ? '#c5f66b' : '#72877c'} opacity="0.14" />
        </g>;
      })}
      <path d="M20 233 C110 229 134 199 204 192 S338 145 398 128 S479 102 566 83" fill="none" stroke="#899ba5" strokeWidth="1.5" strokeDasharray="5 5" opacity="0.6" />
      <line x1="0" y1={points[39].y + 8} x2="580" y2={points[39].y + 8} stroke="#c5f66b" strokeOpacity="0.4" strokeDasharray="3 4" />
      <circle className="chart-endpoint" cx="566" cy={points[39].y + 8} r="4" fill="#c5f66b" />
    </svg>
  );
}

export function LandingScene() {
  const [market, setMarket] = useState<keyof typeof markets>('us');
  const [period, setPeriod] = useState(0);
  const data = markets[market];

  return (
    <div className="terminal-wrap">
      <div className="terminal-halo" aria-hidden="true" />
      <div className="terminal">
        <div className="terminal-topbar">
          <div className="terminal-brand"><span className="brand-mark small-mark" aria-hidden="true"><i /><i /><i /></span><strong>Stock Report</strong><span className="terminal-tag">WORKSPACE</span></div>
          <span className="terminal-example"><span className="status-dot" />인터랙티브 데모 · 예시 데이터</span>
        </div>
        <div className="terminal-content">
          <aside className="terminal-rail" aria-label="워크스페이스 미리보기"><span className="rail-active"><ChartNoAxesCombined size={18} /></span><Activity size={18} /><Wallet size={18} /><BookOpen size={18} /><Layers3 size={18} /><span className="rail-bottom"><Sparkles size={18} /></span></aside>
          <div className="terminal-main">
            <div className="terminal-toolbar">
              <div className="terminal-instrument"><span className="instrument-logo">{market === 'us' ? 'N' : 'S'}</span><div><strong data-testid="demo-symbol">{data.name}</strong><span>{data.ticker} <b>·</b> {data.exchange}</span></div><ChevronDown size={14} /></div>
              <div className="segmented" aria-label="데모 시장 선택"><button type="button" aria-label="미국 시장" aria-pressed={market === 'us'} onClick={() => setMarket('us')}>US</button><button type="button" aria-label="한국 시장" aria-pressed={market === 'kr'} onClick={() => setMarket('kr')}>KR</button></div>
            </div>
            <div className="terminal-price"><strong>{data.price}</strong><span>{data.currency}</span><em><ArrowUpRight size={14} />{data.delta}</em></div>
            <div className="chart-controls"><span><span className="legend-dot" />Price<span className="legend-dot muted-dot" />MA 20</span><div className="period-buttons">{periods.map((label, index) => <button key={label} type="button" aria-pressed={period === index} onClick={() => setPeriod(index)}>{label}</button>)}</div><Maximize2 size={13} aria-hidden="true" /></div>
            <div className="chart-region"><PriceChart market={market} period={period} /><div className="chart-axis" aria-hidden="true"><span>HIGH</span><span>PRICE</span><span>MA 20</span><span>LOW</span></div></div>
            <div className="chart-time" aria-hidden="true">{(period === 0 ? ['09:30', '10:30', '11:30', '12:30', '13:30', '14:30'] : period === 1 ? ['MON', 'TUE', 'WED', 'THU', 'FRI'] : ['W1', 'W2', 'W3', 'W4']).map(time => <span key={time}>{time}</span>)}</div>
            <div className="terminal-bottom"><span><CircleDot size={12} />멀티 타임프레임</span><span><ScanLine size={12} />차트 분석</span><span><ShieldCheck size={12} />리스크 모니터</span></div>
          </div>
          <aside className="terminal-insights">
            <div className="insights-title">WATCHLIST <span>04</span></div>
            {data.watch.map(([symbol, price, delta], index) => <div className={`watch-row${index === 0 ? ' watch-selected' : ''}`} key={symbol}><span><b>{symbol}</b><small>{price}</small></span><svg viewBox="0 0 60 24" aria-hidden="true"><path d={`M0 20 L8 ${14 + index} L16 18 L24 ${7 + index} L32 11 L40 5 L48 8 L60 2`} fill="none" stroke="currentColor" strokeWidth="1.4" /></svg><em>{delta}</em></div>)}
            <div className="ai-insight"><div><Sparkles size={14} /><strong>AI 인사이트</strong><span>DEMO</span></div><p>가격 너머의 맥락을 읽다.</p><small>시장 흐름, 기업 리포트, 관련 뉴스를<br />출처와 함께 연결합니다.</small><div className="source-tags"><span>시장</span><span>리포트</span><span>뉴스</span></div></div>
          </aside>
        </div>
      </div>
      <div className="floating-signal"><span className="signal-icon"><Sparkles size={16} /></span><div><small>CONNECTED INTELLIGENCE</small><strong>데이터가 인사이트가 되는 순간</strong></div><span className="signal-orbit" aria-hidden="true" /></div>
      <div className="terminal-caption"><span className="caption-line" />REAL-TIME CONTEXT. INFORMED DECISIONS.<span className="caption-line" /></div>
    </div>
  );
}
