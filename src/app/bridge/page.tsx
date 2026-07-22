import { ArrowRight, ExternalLink, PlugZap, ShieldCheck } from 'lucide-react';
import { AppShell } from '../../components/app-shell';

import { gatewayUrl } from '../../lib/gateway';

export default function BridgePage() {
  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">fixed doorway</div>
            <h3>파이썬 출입문</h3>
            <p>
              React는 앞문만 맡고, 실제 앱은 이 고정 Cloudflare 주소로 들어갑니다.
              터널이 바뀌어도 React 쪽은 여기만 고치면 됩니다.
            </p>
            <div className="hero-meta">
              <span className="status-chip teal">stable entry</span>
              <span className="status-chip violet">hard-linked</span>
              <span className="status-chip amber">single link</span>
            </div>
          </div>

          <div className="report-card">
            <div className="report-status">gateway ready</div>
            <h5>운영 방식</h5>
            <p>시장 수집, OCR, 백필, 메모리 승격, 무거운 에이전트는 Python이 맡고 React는 안내만 맡습니다.</p>
            <div className="report-highlights">
              <div className="highlight">
                <div className="highlight-top">
                  <strong>React</strong>
                  <span>main</span>
                </div>
                <p>랜딩과 빠른 진입만 담당합니다.</p>
              </div>
              <div className="highlight">
                <div className="highlight-top">
                  <strong>Python</strong>
                  <span>engine</span>
                </div>
                <p>무거운 수집과 자동화는 여기서 처리합니다.</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="main-grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">connect</div>
                <h4>출입 링크</h4>
                <p>이 링크가 실제 Python 앱으로 들어가는 문입니다.</p>
              </div>
              <span className="status-chip teal">live link</span>
            </div>

            <div style={{ display: 'grid', gap: 12 }}>
              <a href={gatewayUrl} target="_blank" rel="noreferrer" className="highlight" style={{ display: 'block' }}>
                <div className="highlight-top">
                  <strong>Python 앱 열기</strong>
                  <ExternalLink size={14} />
                </div>
                <p>{gatewayUrl}</p>
                <p>새 탭으로 열립니다.</p>
              </a>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">why this exists</div>
                <h4>왜 브리지가 필요한가</h4>
                <p>모든 기능을 한 번에 React로 옮기지 않아도, 출입문은 하나로 고정할 수 있습니다.</p>
              </div>
              <span className="status-chip amber">fallback</span>
            </div>

            <div className="detail-list">
              <div className="detail-item">
                <strong><ShieldCheck size={14} style={{ display: 'inline', marginRight: 6 }} />고정 진입점</strong>
                <span>브라우저 북마크는 이 한 곳만 쓰면 됩니다.</span>
              </div>
              <div className="detail-item">
                <strong><PlugZap size={14} style={{ display: 'inline', marginRight: 6 }} />연결 비용 절감</strong>
                <span>터널이 바뀌어도 React는 한 줄만 바꾸면 됩니다.</span>
              </div>
              <div className="detail-item">
                <strong><ArrowRight size={14} style={{ display: 'inline', marginRight: 6 }} />점진적 이전</strong>
                <span>무거운 기능부터 하나씩 옮기고, 나머지는 Python에 둡니다.</span>
              </div>
            </div>
          </section>
        </div>

        <div className="subgrid">
          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">notes</div>
                <h4>운영 메모</h4>
                <p>나중에 주소가 바뀌면 이 파일만 갱신합니다.</p>
              </div>
              <span className="status-chip violet">one edit</span>
            </div>
            <div className="detail-list">
              <div className="detail-item">
                <strong>1</strong>
                <span>Python 프론트 URL은 여기서 직접 관리</span>
              </div>
              <div className="detail-item">
                <strong>2</strong>
                <span>React는 랜딩과 연결 버튼만 담당</span>
              </div>
              <div className="detail-item">
                <strong>3</strong>
                <span>무거운 워크플로우는 Python 쪽에서 유지</span>
              </div>
            </div>
          </section>
        </div>
      </section>
    </AppShell>
  );
}
