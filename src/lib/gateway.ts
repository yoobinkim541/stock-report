// 터널 주소 단일 진실원.
// scripts/cloudflared_watchdog.sh 가 아래 URL 을 sed 정규식으로 자동 치환한다:
//   s#https://[a-z0-9-]+\.trycloudflare\.com#<새 URL>#g
// 한 줄·작은따옴표 리터럴 형태를 유지할 것. 형식을 바꾸면 자동 갱신이 조용히 깨지고
// 터널 재기동 시 현관이 죽은 주소를 가리키게 된다. (tests/test_gateway_url_sync.py 가 감시)
export const gatewayUrl = 'https://growing-chester-concepts-cow.trycloudflare.com/';
