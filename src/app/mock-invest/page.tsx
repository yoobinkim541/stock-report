import { AppShell } from '../../components/app-shell';
import { mockTrades } from '../../lib/dashboard-data';

export default function MockInvestPage() {
  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">mock investing</div>
            <h3>모의투자</h3>
            <p>가상 체결과 백테스트는 손실 한도를 먼저 고정한 뒤, 그 안에서만 전략을 시험합니다.</p>
          </div>
          <div className="report-card">
            <div className="report-status">paper trading</div>
            <h5>실행 원칙</h5>
            <p>거래 횟수보다 사후 성과와 회전율을 함께 기록합니다.</p>
          </div>
        </div>
      </section>

      <section className="main-grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">trade log</div>
                <h4>최근 체결</h4>
                <p>실험의 흔적을 남깁니다.</p>
              </div>
              <span className="status-chip teal">shadow</span>
            </div>
            <div className="detail-list">
              {mockTrades.map((trade) => (
                <div key={`${trade.time}-${trade.symbol}`} className="detail-item">
                  <strong>
                    {trade.time} · {trade.symbol} · {trade.action}
                  </strong>
                  <span>{trade.reason}</span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="subgrid">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">guardrails</div>
                <h4>리스크 규칙</h4>
                <p>모의투자는 캡이 아니라 손실 예산으로 다룹니다.</p>
              </div>
              <span className="status-chip rose">limits</span>
            </div>
            <div className="report-highlights">
              {[
                ['최대 손실', '1% 안에서 시나리오 조정'],
                ['레버리지', 'QLD / TQQQ / SOXL 허용'],
                ['개별주', '단기와 장기를 분리 운용'],
              ].map(([title, body]) => (
                <div key={title} className="highlight">
                  <div className="highlight-top">
                    <strong>{title}</strong>
                    <span>rule</span>
                  </div>
                  <p>{body}</p>
                </div>
              ))}
            </div>
          </section>
        </div>
      </section>
    </AppShell>
  );
}
