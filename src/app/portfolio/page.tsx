import { AppShell } from '../../components/app-shell';
import { portfolioHoldings, portfolioSignals } from '../../lib/dashboard-data';

export default function PortfolioPage() {
  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">portfolio</div>
            <h3>내 포트폴리오</h3>
            <p>비중, 현금, 손익, 레버리지를 한 화면에서 봅니다. 과열 종목보다 손실 한도가 먼저 보이도록 구성했습니다.</p>
          </div>
          <div className="report-card">
            <div className="report-status">phase 1 · 조정</div>
            <h5>리스크 해석</h5>
            <p>지금은 QQQ 추종보다 현금과 방어자산을 같이 두는 편이 더 자연스럽습니다.</p>
            <div className="report-highlights">
              {portfolioSignals.map((signal) => (
                <div key={signal.label} className="highlight">
                  <div className="highlight-top">
                    <strong>{signal.label}</strong>
                    <span>{signal.value}</span>
                  </div>
                  <p>{signal.note}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="metric-grid">
        {portfolioSignals.map((signal) => (
          <article key={signal.label} className="metric-card teal">
            <p className="label">{signal.label}</p>
            <h4 className="value">{signal.value}</h4>
            <p className="delta">{signal.note}</p>
          </article>
        ))}
      </section>

      <section className="main-grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">holdings</div>
                <h4>보유 종목</h4>
                <p>실제 보유와 평가액을 우선 보여줍니다.</p>
              </div>
              <span className="status-chip teal">live</span>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>종목</th>
                    <th>평가액</th>
                    <th>수익률</th>
                    <th>메모</th>
                  </tr>
                </thead>
                <tbody>
                  {portfolioHoldings.map((row) => (
                    <tr key={row.symbol}>
                      <td>
                        <strong>{row.symbol}</strong>
                        <div className="muted-cell">{row.name}</div>
                      </td>
                      <td>{row.value}</td>
                      <td className={row.delta.startsWith('+') ? 'pos' : 'neg'}>{row.delta}</td>
                      <td>{row.memo}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">cash rule</div>
                <h4>현금과 방어 비중</h4>
                <p>단기 트레이딩은 횟수보다 손실 예산으로 관리합니다.</p>
              </div>
              <span className="status-chip violet">guardrail</span>
            </div>
            <div className="report-highlights">
              {[
                ['현금 28.7%', '신규 편입 여력을 남겨 둔 상태'],
                ['손실 한도', '매수·매도 횟수보다 우선 고정'],
                ['레버리지', 'QLD/TQQQ/SOXL은 조건부 사용'],
              ].map(([title, body]) => (
                <div key={title} className="highlight">
                  <div className="highlight-top">
                    <strong>{title}</strong>
                    <span>policy</span>
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
                <div className="panel-kicker">allocation</div>
                <h4>비중 해석</h4>
                <p>빨강은 줄이고, 초록은 유지합니다.</p>
              </div>
              <span className="status-chip amber">summary</span>
            </div>
            <div className="detail-list">
              <div className="detail-item">
                <strong>성장주</strong>
                <span>CRM, META, AMZN은 유지 쪽</span>
              </div>
              <div className="detail-item">
                <strong>변동성</strong>
                <span>INTC, ADBE는 손실 추적 필요</span>
              </div>
              <div className="detail-item">
                <strong>현금</strong>
                <span>레짐이 불명확할수록 상승시 추격보다 대기</span>
              </div>
            </div>
          </section>
        </div>
      </section>
    </AppShell>
  );
}
