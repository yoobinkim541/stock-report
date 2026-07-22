import Link from 'next/link';
import { ArrowRight, ExternalLink, PlugZap, ShieldCheck } from 'lucide-react';
import { AppShell } from '../../components/app-shell';

export default function BridgePage() {
  const bridgeUrl = process.env.NEXT_PUBLIC_PYTHON_GATEWAY_URL ?? process.env.NEXT_PUBLIC_PYTHON_FRONTEND_URL ?? '';

  return (
    <AppShell>
      <section className="hero">
        <div className="hero-grid">
          <div className="hero-copy">
            <div className="panel-kicker">fixed doorway</div>
            <h3>파이썬 출입문</h3>
            <p>
              React를 메인 콘솔로 두고, 아직 무거운 Python 화면은 여기로 한 번만 연결합니다.
              Cloudflare 터널 주소가 바뀌어도 이 페이지의 링크만 갱신하면 됩니다.
            </p>
            <div className="hero-meta">
              <span className="status-chip teal">stable entry</span>
              <span className="status-chip violet">env-driven</span>
              <span className="status-chip amber">single link</span>
            </div>
          </div>

          <div className="report-card">
            <div className="report-status">gateway ready</div>
            <h5>운영 방식</h5>
            <p>프론트는 React, 스크래핑·OCR·백필·무거운 에이전트는 Python에 남겨 두는 분리 방식입니다.</p>
            <div className="report-highlights">
              <div className="highlight">
                <div className="highlight-top">
                  <strong>React</strong>
                  <span>main</span>
                </div>
                <p>대시보드와 상호작용을 담당합니다.</p>
              </div>
              <div className="highlight">
                <div className="highlight-top">
                  <strong>Python</strong>
                  <span>bridge</span>
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
                <p>환경변수 하나만 바꾸면 됩니다.</p>
              </div>
              <span className="status-chip teal">live link</span>
            </div>

            <div style={{ display: 'grid', gap: 12 }}>
              {bridgeUrl ? (
                <Link
                  href={bridgeUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="highlight"
                  style={{ display: 'block' }}
                >
                  <div className="highlight-top">
                    <strong>Python 프론트 열기</strong>
                    <ExternalLink size={14} />
                  </div>
                  <p>{bridgeUrl}</p>
                  <p>새 탭으로 열립니다.</p>
                </Link>
              ) : (
                <div className="highlight">
                  <div className="highlight-top">
                    <strong>링크 미설정</strong>
                    <span>env</span>
                  </div>
                  <p>NEXT_PUBLIC_PYTHON_GATEWAY_URL 또는 NEXT_PUBLIC_PYTHON_FRONTEND_URL 을 설정하면 이 카드가 실제 출입문이 됩니다.</p>
                </div>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">why this exists</div>
                <h4>왜 브리지가 필요한가</h4>
                <p>모든 기능을 한 번에 React로 옮기기 어렵다면, 전환 비용이 큰 부분만 우회합니다.</p>
              </div>
              <span className="status-chip amber">fallback</span>
            </div>

            <div className="detail-list">
              <div className="detail-item">
                <strong><ShieldCheck size={14} style={{ display: 'inline', marginRight: 6 }} />고정 진입점</strong>
                <span>브라우저 북마크를 하나만 유지하면 됩니다.</span>
              </div>
              <div className="detail-item">
                <strong><PlugZap size={14} style={{ display: 'inline', marginRight: 6 }} />연결 비용 절감</strong>
                <span>터널 주소가 바뀌어도 React nav는 그대로 둡니다.</span>
              </div>
              <div className="detail-item">
                <strong><ArrowRight size={14} style={{ display: 'inline', marginRight: 6 }} />점진적 이전</strong>
                <span>무거운 기능부터 하나씩 React로 옮기고, 나머지는 브리지로 둡니다.</span>
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
                <p>나중에 주소가 바뀌면 여기만 갱신합니다.</p>
              </div>
              <span className="status-chip violet">one edit</span>
            </div>
            <div className="detail-list">
              <div className="detail-item">
                <strong>1</strong>
                <span>Python 프론트 URL을 env로 관리</span>
              </div>
              <div className="detail-item">
                <strong>2</strong>
                <span>React는 고정된 메인 진입점 유지</span>
              </div>
              <div className="detail-item">
                <strong>3</strong>
                <span>무거운 워크플로우는 점진적으로 전환</span>
              </div>
            </div>
          </section>
        </div>
      </section>
    </AppShell>
  );
}
