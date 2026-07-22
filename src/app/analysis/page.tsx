import { AppShell } from '../../components/app-shell';
import { analysisSnapshot } from '../../lib/dashboard-data';

export default function AnalysisPage() {
  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">stock analysis</div>
            <h3>{analysisSnapshot.name}</h3>
            <p>
              {analysisSnapshot.symbol} · {analysisSnapshot.price} · {analysisSnapshot.delta}
            </p>
            <p>{analysisSnapshot.thesis}</p>
          </div>
          <div className="report-card">
            <div className="report-status">analysis ready</div>
            <h5>핵심 포인트</h5>
            <div className="report-highlights">
              {analysisSnapshot.gauges.map((item) => (
                <div key={item.label} className="highlight">
                  <div className="highlight-top">
                    <strong>{item.label}</strong>
                    <span>{item.value}</span>
                  </div>
                  <div className="progress">
                    <span style={{ width: `${Number(item.value)}%` }} />
                  </div>
                </div>
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
                <div className="panel-kicker">thesis</div>
                <h4>판단 메모</h4>
                <p>장기 보유는 유지하되, 단기는 리스크를 먼저 확인합니다.</p>
              </div>
              <span className="status-chip teal">holding</span>
            </div>
            <div className="detail-list">
              {analysisSnapshot.bullets.map((bullet) => (
                <div key={bullet} className="detail-item">
                  <strong>note</strong>
                  <span>{bullet}</span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="subgrid">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">signals</div>
                <h4>참조 신호</h4>
                <p>시장과 함께 읽는 보조 체크리스트입니다.</p>
              </div>
              <span className="status-chip amber">context</span>
            </div>
            <div className="report-highlights">
              {[
                ['CAPEX', '클라우드와 AI 인프라 지출 방향'],
                ['크레딧', '반등이 넓게 퍼지는지 확인'],
                ['유가', '방어 프리미엄이 남아 있는지 확인'],
              ].map(([title, body]) => (
                <div key={title} className="highlight">
                  <div className="highlight-top">
                    <strong>{title}</strong>
                    <span>watch</span>
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
