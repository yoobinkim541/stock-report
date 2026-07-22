import { AppShell } from '../../components/app-shell';
import { researchQueue } from '../../lib/dashboard-data';

export default function ResearchPage() {
  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">research</div>
            <h3>리서치</h3>
            <p>뉴스와 메모를 바로 결론으로 쓰지 않고, 위키에 승격할 수 있는 후보로 먼저 정리합니다.</p>
          </div>
          <div className="report-card">
            <div className="report-status">source-aware</div>
            <h5>정리 기준</h5>
            <p>원문 보관, 사후 성과, 출처 가중치를 함께 남깁니다.</p>
          </div>
        </div>
      </section>

      <section className="main-grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">queue</div>
                <h4>승격 대기</h4>
                <p>검색보다 먼저 읽을 항목입니다.</p>
              </div>
              <span className="status-chip amber">queue</span>
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {researchQueue.map((item) => (
                <div key={item.title} className="source-item">
                  <div className="source-item-top">
                    <span className="source-name">{item.title}</span>
                    <span className="source-count">{item.tag}</span>
                  </div>
                  <p>{item.source}</p>
                  <p>{item.summary}</p>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="subgrid">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">taxonomy</div>
                <h4>분류</h4>
                <p>소스별 원문, 요약, 위키를 분리합니다.</p>
              </div>
              <span className="status-chip violet">wiki-ready</span>
            </div>
            <div className="detail-list">
              <div className="detail-item">
                <strong>News</strong>
                <span>원문과 PDF를 저장 후 요약으로 승격</span>
              </div>
              <div className="detail-item">
                <strong>Telegram</strong>
                <span>메시지 원문과 판단 메모를 분리</span>
              </div>
              <div className="detail-item">
                <strong>Market</strong>
                <span>지표와 리포트를 함께 묶어 흐름을 기록</span>
              </div>
            </div>
          </section>
        </div>
      </section>
    </AppShell>
  );
}
