import { AppShell } from '../../components/app-shell';
import { calendarEvents } from '../../lib/dashboard-data';

export default function MarketCalendarPage() {
  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">market calendar</div>
            <h3>시장-캘린더</h3>
            <p>실적, 지표, 중앙은행, 크레딧 이벤트를 날짜별로 쌓아두고, 어떤 날에 비중을 줄일지 먼저 봅니다.</p>
          </div>
          <div className="report-card">
            <div className="report-status">events loaded</div>
            <h5>캘린더 우선순위</h5>
            <p>이번 주는 FOMC, TSMC, Big Tech 실적 순으로 영향도가 큽니다.</p>
          </div>
        </div>
      </section>

      <section className="main-grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">upcoming</div>
                <h4>다가오는 이벤트</h4>
                <p>고영향 이벤트를 먼저 잡습니다.</p>
              </div>
              <span className="status-chip amber">high impact</span>
            </div>
            <div className="detail-list">
              {calendarEvents.map((event) => (
                <div key={`${event.date}-${event.label}`} className="detail-item">
                  <strong>
                    {event.date} · {event.label}
                  </strong>
                  <span>{event.note}</span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="subgrid">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">risk map</div>
                <h4>영향도</h4>
                <p>이벤트를 위험 수준으로 나눕니다.</p>
              </div>
              <span className="status-chip violet">calendar</span>
            </div>
            <div className="report-highlights">
              {[
                ['High', '포지션 크기와 레버리지를 조절'],
                ['Medium', '종목 선택은 유지, 추격은 자제'],
                ['Low', '참고용으로만 남김'],
              ].map(([title, body]) => (
                <div key={title} className="highlight">
                  <div className="highlight-top">
                    <strong>{title}</strong>
                    <span>tag</span>
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
