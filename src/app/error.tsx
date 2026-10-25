'use client';

export default function Error({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24, textAlign: 'center' }}>
      <div>
        <h1 style={{ fontSize: 22, marginBottom: 8 }}>페이지를 불러오지 못했습니다</h1>
        <p style={{ opacity: 0.7, marginBottom: 20 }}>일시적인 오류입니다. 다시 시도하거나 홈으로 돌아가세요.</p>
        <button type="button" className="button-primary" onClick={() => reset()}>다시 시도</button>
        {' '}
        <a className="button-secondary" href="/">홈으로</a>
      </div>
    </main>
  );
}
