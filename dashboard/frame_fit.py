"""차트 iframe 높이 맞춤 공용 JS (Plotly 임베드·Canvas 공용).

tnFitFrame — 풀뷰: iframe 을 부모 창의 남은 높이에 맞춘다.

실측(2026-11-19, 1568×805): 풀뷰 차트 iframe 만 줄이고 감싼 stElementContainer 는 st.iframe
높이(1004px — 클래스 CSS 의 height·flex-basis)로 남아 페이지가 1602px 로 스크롤됐다. 그래서
컨테이너의 height·flex 도 같이 줄이고, 아래 요소 때문에 본문이 넘치면 넘친 만큼 더 줄인다.
srcdoc iframe 이라 same-origin — frameElement 를 직접 만진다.
"""

FIT_FRAME_JS = r"""
function tnFitFrame() {
  let fe, pw;
  try { fe = window.frameElement; pw = window.parent; } catch (e) { return 0; }
  if (!fe || !pw || pw === window) return 0;
  const box = fe.parentElement && fe.parentElement.dataset.testid === "stElementContainer"
    ? fe.parentElement : null;
  const main = pw.document.querySelector('[data-testid="stMain"]');
  const apply = (h) => {
    fe.style.height = h + "px";
    if (box) { box.style.height = h + "px"; box.style.flex = "0 0 " + h + "px"; }
  };
  const top = fe.getBoundingClientRect().top + (main ? main.scrollTop : 0);
  const avail = Math.floor(pw.innerHeight - top - 8);
  if (avail < Math.min(420, pw.innerHeight * 0.5)) {   // 좁은 화면 — 컨트롤이 쌓여 화면을 거의 먹음: 스크롤해서 보는 큰 차트
    const h = Math.max(320, Math.floor(pw.innerHeight * 0.75));
    apply(h);
    return h;
  }
  let h = avail;
  apply(h);
  if (main) {   // 차트 뒤 작은 꼬리(실시간 피더·여백)만 흡수 — 리플레이 터미널처럼 큰 패널은 스크롤로 본다
    const over = main.scrollHeight - main.clientHeight;
    if (over > 0 && over < 48) { h = Math.max(320, h - over); apply(h); }
  }
  return h;
}
function tnFitContent(el) {
  // 일반 화면: iframe 을 내용 높이에 맞춘다. 서버는 도구 패널이 펼쳐질 때를 대비해 높이를
  // 넉넉히(+164px) 잡아, 접힌 평소엔 차트 아래 ~140px 빈 띠가 남았다(실측 종목 분석 844 vs 708).
  let fe;
  try { fe = window.frameElement; } catch (e) { return; }
  if (!fe || !el) return;
  const box = fe.parentElement && fe.parentElement.dataset.testid === "stElementContainer"
    ? fe.parentElement : null;
  const fit = () => {
    const h = Math.ceil(el.getBoundingClientRect().height) + 4;
    if (h < 80 || Math.abs(fe.getBoundingClientRect().height - h) < 2) return;
    fe.style.height = h + "px";
    if (box) { box.style.height = h + "px"; box.style.flex = "0 0 " + h + "px"; }
  };
  fit();
  if (window.ResizeObserver) new ResizeObserver(fit).observe(el);   // 도구 패널 펼침·툴바 줄바꿈
}
"""
