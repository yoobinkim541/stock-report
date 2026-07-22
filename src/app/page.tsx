import { ArrowRight, ExternalLink, Layers3, PlugZap, ShieldCheck, Sparkles } from 'lucide-react';

const gatewayUrl = 'https://growing-chester-concepts-cow.trycloudflare.com/';

const cards = [
  {
    title: 'React',
    body: '가벼운 랜딩, 상태 요약, 진입 버튼만 담당합니다.',
  },
  {
    title: 'Python',
    body: '시장 수집, OCR, 메모리 승격, 무거운 워크플로우를 처리합니다.',
  },
  {
    title: 'Bridge',
    body: '터널 주소가 바뀌어도 이 한 군데만 바꾸면 됩니다.',
  },
];

export default function HomePage() {
  return (
    <main
      style={{
        minHeight: '100vh',
        padding: '28px',
        background:
          'radial-gradient(circle at top left, rgba(52, 215, 201, 0.12), transparent 24%), radial-gradient(circle at 85% 10%, rgba(167, 139, 250, 0.12), transparent 20%), linear-gradient(180deg, #050810 0%, #070b14 52%, #090d18 100%)',
      }}
    >
      <div style={{ maxWidth: 1240, margin: '0 auto', display: 'grid', gap: 18 }}>
        <header
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
            padding: '10px 4px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div
              style={{
                width: 38,
                height: 38,
                borderRadius: 12,
                display: 'grid',
                placeItems: 'center',
                background: 'rgba(52, 215, 201, 0.12)',
                border: '1px solid rgba(52, 215, 201, 0.18)',
              }}
            >
              <Sparkles size={18} color="var(--teal)" />
            </div>
            <div>
              <div style={{ fontSize: 12, color: 'var(--muted)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                stock-report
              </div>
              <div style={{ fontSize: 14, color: 'var(--text)' }}>React landing gateway</div>
            </div>
          </div>
          <span className="status-chip teal">gateway ready</span>
        </header>

        <section
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1.15fr) minmax(320px, 0.85fr)',
            gap: 18,
            alignItems: 'stretch',
          }}
        >
          <div
            style={{
              padding: 28,
              borderRadius: 28,
              border: '1px solid rgba(148, 163, 184, 0.14)',
              background: 'linear-gradient(180deg, rgba(14, 20, 34, 0.92), rgba(10, 15, 26, 0.84))',
              boxShadow: '0 30px 80px rgba(0, 0, 0, 0.32)',
              display: 'grid',
              gap: 18,
            }}
          >
            <div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
                <span className="status-chip teal">React only front door</span>
                <span className="status-chip violet">Cloudflare tunnel</span>
                <span className="status-chip amber">Python app</span>
              </div>
              <h1 style={{ margin: 0, fontSize: 52, lineHeight: 1.02, letterSpacing: 0 }}>
                React는 입구만 맡고,
                <br />
                본체는 터널로 바로 엽니다.
              </h1>
              <p style={{ margin: '16px 0 0', maxWidth: 760, fontSize: 17, lineHeight: 1.7, color: 'var(--muted)' }}>
                지금 이 프론트는 무거운 대시보드가 아니라 고정 랜딩 페이지예요. 시장 자료, 포트폴리오, 위키,
                수집과 백필은 Python 앱이 처리하고, React는 가장 안정적인 출입문만 제공합니다.
              </p>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
              <a
                href={gatewayUrl}
                target="_blank"
                rel="noreferrer"
                className="status-chip teal"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '14px 18px',
                  fontSize: 15,
                  fontWeight: 700,
                }}
              >
                Python 앱 열기
                <ExternalLink size={15} />
              </a>
              <a
                href="/bridge"
                className="status-chip violet"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '14px 18px',
                  fontSize: 15,
                  fontWeight: 700,
                }}
              >
                출입문 설정
                <ArrowRight size={15} />
              </a>
            </div>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                gap: 12,
                marginTop: 4,
              }}
            >
              {[
                ['최근 이벤트', '40건'],
                ['누적 기억', '50건'],
                ['모델 파일', '4개'],
              ].map(([label, value]) => (
                <div
                  key={label}
                  style={{
                    padding: 16,
                    borderRadius: 18,
                    border: '1px solid rgba(148, 163, 184, 0.12)',
                    background: 'rgba(7, 11, 20, 0.66)',
                  }}
                >
                  <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>{label}</div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div>
                </div>
              ))}
            </div>
          </div>

          <div
            style={{
              padding: 24,
              borderRadius: 28,
              border: '1px solid rgba(148, 163, 184, 0.14)',
              background: 'rgba(14, 20, 34, 0.76)',
              boxShadow: '0 24px 72px rgba(0, 0, 0, 0.26)',
              display: 'grid',
              gap: 16,
            }}
          >
            <div className="report-status">
              <PlugZap size={14} />
              fixed doorway
            </div>
            <div>
              <h2 style={{ margin: '6px 0 8px', fontSize: 26 }}>한 번만 연결하면 됩니다</h2>
              <p style={{ margin: 0, color: 'var(--muted)', lineHeight: 1.7 }}>
                주소가 바뀌는 Cloudflare 터널은 React 쪽에서 한 줄만 바꾸면 되고, 나머지는 그대로 둡니다.
                그래서 이 페이지는 메뉴가 아니라 실제 출입문 역할만 합니다.
              </p>
            </div>

            <div style={{ display: 'grid', gap: 10 }}>
              <div
                style={{
                  padding: 14,
                  borderRadius: 18,
                  background: 'rgba(7, 11, 20, 0.7)',
                  border: '1px solid rgba(52, 215, 201, 0.14)',
                }}
              >
                <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>gateway url</div>
                <div style={{ fontSize: 14, wordBreak: 'break-all' }}>{gatewayUrl}</div>
              </div>

              <div
                style={{
                  padding: 14,
                  borderRadius: 18,
                  background: 'rgba(7, 11, 20, 0.7)',
                  border: '1px solid rgba(167, 139, 250, 0.14)',
                }}
              >
                <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>role</div>
                <div style={{ fontSize: 14 }}>React = 안내 / Python = 실행</div>
              </div>
            </div>
          </div>
        </section>

        <section
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
            gap: 14,
          }}
        >
          {cards.map((card) => (
            <article
              key={card.title}
              style={{
                padding: 18,
                borderRadius: 22,
                border: '1px solid rgba(148, 163, 184, 0.12)',
                background: 'rgba(14, 20, 34, 0.72)',
                minHeight: 132,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                <div
                  style={{
                    width: 30,
                    height: 30,
                    borderRadius: 10,
                    display: 'grid',
                    placeItems: 'center',
                    background: 'rgba(52, 215, 201, 0.08)',
                    border: '1px solid rgba(52, 215, 201, 0.12)',
                  }}
                >
                  <Layers3 size={15} color="var(--teal)" />
                </div>
                <strong style={{ fontSize: 16 }}>{card.title}</strong>
              </div>
              <p style={{ margin: 0, color: 'var(--muted)', lineHeight: 1.65 }}>{card.body}</p>
            </article>
          ))}
        </section>

        <section
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 10,
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '16px 18px',
            borderRadius: 20,
            border: '1px solid rgba(148, 163, 184, 0.12)',
            background: 'rgba(7, 11, 20, 0.68)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--muted)' }}>
            <ShieldCheck size={15} />
            <span>React 쪽은 가볍게 유지하고, 무거운 기능은 Python 앱에서 계속 돌립니다.</span>
          </div>
          <a href={gatewayUrl} target="_blank" rel="noreferrer" className="status-chip teal">
            바로 열기
          </a>
        </section>
      </div>
    </main>
  );
}
