export default function NotFound() {
  return (
    <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24, textAlign: 'center' }}>
      <div>
        <h1 style={{ fontSize: 22, marginBottom: 8 }}>페이지가 없습니다</h1>
        <p style={{ opacity: 0.7, marginBottom: 20 }}>주소를 확인하거나 워크스페이스로 이동하세요. 워크스페이스는 서버 터널이 꺼져 있으면 열리지 않을 수 있습니다.</p>
        <a className="button-primary" href="/">홈으로</a>
        {' '}
        <a className="button-secondary" href="/bridge" target="_blank" rel="noopener noreferrer">워크스페이스 열기</a>
      </div>
    </main>
  );
}
