from reports import source_collector


def test_direct_arca_html_returns_article_text_without_page_boilerplate(monkeypatch):
    html = """<!doctype html>
    <html><body>
      <nav>전체 게시판 로그인</nav>
      <main>
        <header class="article-head"><h1 class="title">반도체 업황 분석</h1></header>
        <div class="article-content">
          <p>메모리 가격 반등으로 업황 개선 기대가 커졌다.</p>
          <p>다만 재고 수준과 수요 회복 속도를 함께 살펴야 한다.</p>
        </div>
        <div class="article-footer">추천 댓글 신고</div>
      </main>
      <footer>아카라이브 이용약관</footer>
    </body></html>"""

    class Response:
        text = html

    monkeypatch.setattr(source_collector, "_proxy_from_env", lambda: "http://proxy.example")
    monkeypatch.setattr(source_collector, "_bounded_get_via_proxy", lambda *args, **kwargs: Response())

    body = source_collector._fetch_arca_post_body("185220056")

    assert "메모리 가격 반등으로 업황 개선 기대가 커졌다." in body
    assert "재고 수준과 수요 회복 속도를 함께 살펴야 한다." in body
    assert "<html" not in body
    assert "전체 게시판 로그인" not in body
    assert "아카라이브 이용약관" not in body
