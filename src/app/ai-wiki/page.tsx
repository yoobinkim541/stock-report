import { AppShell } from '../../components/app-shell';
import { graphEdges, memoryItems, wikiNodes } from '../../lib/site-data';

export default function AiWikiPage() {
  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">ai wiki</div>
            <h3>AI 위키</h3>
            <p>World Memory와 승격된 판단을 하나의 그래프에서 다시 읽는 정리층입니다. 노드를 누르면 관련 기억으로 이동합니다.</p>
          </div>
          <div className="report-card">
            <div className="report-status">linked memory</div>
            <h5>그래프 요약</h5>
            <p>{graphEdges.length}개의 연결이 현재 레짐, 크레딧, 유가, AI 인프라를 묶고 있습니다.</p>
          </div>
        </div>
      </section>

      <section className="main-grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">memory</div>
                <h4>승격 메모</h4>
                <p>대화와 리포트에서 반복된 판단을 다시 읽습니다.</p>
              </div>
              <span className="status-chip teal">memory</span>
            </div>
            <div className="detail-list">
              {memoryItems.map((item) => (
                <div key={item.title} className="detail-item">
                  <strong>{item.title}</strong>
                  <span>{item.kind}</span>
                  <span>{item.detail}</span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="subgrid">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">graph</div>
                <h4>관계 노드</h4>
                <p>핵심 노드만 먼저 보여줍니다.</p>
              </div>
              <span className="status-chip violet">connected</span>
            </div>
            <div className="detail-list">
              {wikiNodes.map((node) => (
                <div key={node.id} className="detail-item">
                  <strong>{node.label}</strong>
                  <span>{node.summary}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      </section>
    </AppShell>
  );
}
