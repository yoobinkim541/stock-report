import { AppShell } from '../../components/app-shell';
import { memoryItems, messages, reportHighlights, sourceCards } from '../../lib/site-data';

export default function AiConsolePage() {
  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">ai console</div>
            <h3>대화형 투자 콘솔</h3>
            <p>질문을 던지면 시장, 포트폴리오, 종목, 모의투자, 전략 중 하나로 자동으로 이어지는 작업공간입니다.</p>
          </div>
          <div className="report-card">
            <div className="report-status">chat-first</div>
            <h5>현재 문맥</h5>
            <p>World Memory, 위키, 리포트, 커넥터를 함께 읽으면서 답을 만들게 되어 있습니다.</p>
          </div>
        </div>
      </section>

      <section className="main-grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">conversation</div>
                <h4>최근 대화</h4>
                <p>스트림릿에서 쓰던 흐름을 그대로 옮겼습니다.</p>
              </div>
              <span className="status-chip teal">stateful</span>
            </div>
            <div className="chat-feed">
              {messages.map((message) => (
                <article key={`${message.role}-${message.time}`} className={`message ${message.role}`}>
                  <div className="message-head">
                    <strong>{message.title}</strong>
                    <span>{message.time}</span>
                  </div>
                  <p>{message.content}</p>
                </article>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">signals</div>
                <h4>리스크 레일</h4>
                <p>사실상 답변의 배경이 되는 우선순위입니다.</p>
              </div>
              <span className="status-chip violet">context rail</span>
            </div>
            <div className="report-highlights">
              {reportHighlights.map((item) => (
                <div key={item.title} className="highlight">
                  <div className="highlight-top">
                    <strong>{item.title}</strong>
                    <span>{item.score}</span>
                  </div>
                  <p>{item.body}</p>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="subgrid">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">memory</div>
                <h4>World Memory</h4>
                <p>반복된 판단은 더 높은 레이어로 승격됩니다.</p>
              </div>
              <span className="status-chip amber">linked</span>
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

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">sources</div>
                <h4>연결된 소스</h4>
                <p>원문과 승격본을 함께 봅니다.</p>
              </div>
              <span className="status-chip rose">live</span>
            </div>
            <div className="detail-list">
              {sourceCards.map((source) => (
                <div key={source.name} className="detail-item">
                  <strong>{source.name}</strong>
                  <span>{source.kind}</span>
                  <span>{source.status}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      </section>
    </AppShell>
  );
}
