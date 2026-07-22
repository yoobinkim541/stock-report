import { AppShell } from '../../../components/app-shell';
import { chartSeries } from '../../../lib/dashboard-data';

function buildLinePath(values: number[], width = 100, height = 60, padding = 6) {
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

export default function ChartFullViewPage() {
  const buyHold = buildLinePath(chartSeries.map((point) => point.buyHold));
  const strategy = buildLinePath(chartSeries.map((point) => point.strategy));

  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">chart fullview</div>
            <h3>차트 풀뷰</h3>
            <p>가격, 거래량, 인디케이터, 비교선을 한 눈에 펼쳐서 보는 화면입니다. 기존 차트 느낌은 유지하고 레이아웃만 더 크게 잡았습니다.</p>
          </div>
          <div className="report-card">
            <div className="report-status">indicators on</div>
            <h5>현재 상태</h5>
            <p>1D / 6M / 전체 구간에서 추세와 과열 여부를 함께 봅니다.</p>
            <div className="hero-meta">
              {['라인', '캔들', '거래량', 'RSI', '볼린저', '비교'].map((chip) => (
                <span key={chip} className="status-chip teal">
                  {chip}
                </span>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="main-grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">compare</div>
                <h4>전략 비교</h4>
                <p>Buy & Hold와 현금화 규칙의 차이를 봅니다.</p>
              </div>
              <span className="status-chip violet">6m</span>
            </div>
            <svg viewBox="0 0 100 60" className="chart-stage">
              <path d={buyHold} fill="none" stroke="rgba(16, 185, 129, 0.95)" strokeWidth="2.2" />
              <path d={strategy} fill="none" stroke="rgba(168, 85, 247, 0.95)" strokeWidth="2.2" />
            </svg>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">legend</div>
                <h4>보조 지표</h4>
                <p>과열 구간은 가격과 함께 보정합니다.</p>
              </div>
              <span className="status-chip amber">overlay</span>
            </div>
            <div className="report-highlights">
              {[
                ['RSI 54', '중립권에서 방향 확인'],
                ['MA20 ↑', '단기 추세 유지'],
                ['MA60 ↑', '중기 구조 유지'],
                ['Volume', '거래량은 이벤트 때만 확장'],
              ].map(([title, body]) => (
                <div key={title} className="highlight">
                  <div className="highlight-top">
                    <strong>{title}</strong>
                    <span>chart</span>
                  </div>
                  <p>{body}</p>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="subgrid">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">watch</div>
                <h4>관찰 자산</h4>
                <p>차트 풀뷰에서 자주 보는 티커입니다.</p>
              </div>
              <span className="status-chip teal">watchlist</span>
            </div>
            <div className="detail-list">
              {['UNH', 'ORCL', 'CRM', 'NVDA', 'QQQ', 'SOXL'].map((ticker, index) => (
                <div key={ticker} className="detail-item">
                  <strong>{ticker}</strong>
                  <span>{index % 2 === 0 ? '추세 확인' : '리스크 점검'}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      </section>
    </AppShell>
  );
}
