"""Browser checks for the public landing. Requires Python Playwright + Chromium.

Usage: python scripts/verify_landing.py http://127.0.0.1:3107 /tmp/landing-check
"""

import json
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright


def verify(url: str, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("requestfailed", lambda request: errors.append(f"Request failed: {request.url}"))
        page.goto(url, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.get_by_role("button", name="한국 시장").click()
        assert page.get_by_test_id("demo-symbol").inner_text() == "삼성전자"
        page.get_by_role("button", name="미국 시장").click()
        assert page.get_by_test_id("demo-symbol").inner_text() == "NVIDIA"
        previous_chart = page.locator(".price-chart").get_attribute("aria-label")
        page.get_by_role("button", name="1주", exact=True).click()
        assert page.locator(".price-chart").get_attribute("aria-label") != previous_chart
        page.get_by_role("button", name="모션 일시정지").click()
        assert page.locator(".landing").get_attribute("data-motion") == "paused"
        assert page.locator(".chart-trace").evaluate("el => getComputedStyle(el).animationPlayState") == "paused"
        page.get_by_role("button", name="모션 재생").click()
        assert page.locator(".landing").get_attribute("data-motion") == "running"
        for anchor in page.locator('a[href^="#"]').all():
            target = anchor.get_attribute("href")
            assert target and page.locator(target).count() == 1, target
        app_links = page.locator('a[data-app-link]')
        assert app_links.count() >= 3
        for anchor in app_links.all():
            assert anchor.get_attribute("href") == "/bridge"
        for section in page.locator('[data-reveal]').all():
            section.scroll_into_view_if_needed()
            page.wait_for_timeout(800)
            assert section.evaluate("el => getComputedStyle(el).opacity") == "1"
        page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
        page.screenshot(path=str(output / "desktop.png"), full_page=True, animations="disabled")
        for width in [360, 390, 768, 1440, 1920]:
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
            if width == 390:
                page.screenshot(path=str(output / "mobile.png"), full_page=True, animations="disabled")
        page.emulate_media(reduced_motion="reduce")
        page.reload(wait_until="networkidle")
        assert page.locator(".chart-trace").evaluate("el => getComputedStyle(el).animationName") == "none"
        assert page.get_by_role("button", name="시스템 모션 감소 설정 적용됨").is_disabled()
        page.get_by_role("button", name="한국 시장").click()
        assert page.get_by_test_id("demo-symbol").inner_text() == "삼성전자"
        page.emulate_media(reduced_motion="no-preference")
        page.reload(wait_until="networkidle")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.textContent") == "본문으로 건너뛰기"
        response = page.request.get(url.rstrip("/") + "/bridge", max_redirects=0)
        assert response.status == 307
        assert response.headers["location"].startswith("https://")
        static_page = browser.new_page(java_script_enabled=False)
        static_page.goto(url, wait_until="networkidle")
        assert static_page.get_by_role("heading", level=1).is_visible()
        assert static_page.get_by_text("답변에 근거를.", exact=False).is_visible()
        assert static_page.locator('a[data-app-link]').count() >= 3
        static_page.close()
        assert not errors, errors
        browser.close()
    print(json.dumps({"status": "passed", "widths": [360, 390, 768, 1440, 1920], "browser_errors": errors, "screenshots": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    verify(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3107", Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/landing-check"))
