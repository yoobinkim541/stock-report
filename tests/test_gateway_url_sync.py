"""터널 URL 자동 갱신 경로 회귀 테스트.

cloudflared_watchdog.sh 는 sed 정규식으로 gateway.ts 의 URL 을 치환한다.
파일 형식이 바뀌면 치환이 조용히 실패하고 현관이 죽은 터널을 가리키게 되므로,
'워치독이 실제로 치환할 수 있는 형태인가'를 테스트로 고정한다.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWAY = ROOT / "src" / "lib" / "gateway.ts"
WATCHDOG = ROOT / "scripts" / "cloudflared_watchdog.sh"
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def test_gateway_file_has_tunnel_url():
    assert TUNNEL_RE.search(GATEWAY.read_text(encoding="utf-8"))


def test_watchdog_targets_gateway_file():
    body = WATCHDOG.read_text(encoding="utf-8")
    assert "src/lib/gateway.ts" in body
    assert "dashboard/landing/index.html" not in body


def test_sed_actually_replaces_url(tmp_path):
    """워치독과 동일한 sed 명령이 gateway.ts 를 실제로 치환하는지 확인."""
    work = tmp_path / "gateway.ts"
    work.write_text(GATEWAY.read_text(encoding="utf-8"), encoding="utf-8")
    new = "https://replaced-by-test.trycloudflare.com"
    subprocess.run(
        ["sed", "-i", "-E", f"s#https://[a-z0-9-]+\\.trycloudflare\\.com#{new}#g", str(work)],
        check=True,
    )
    after = work.read_text(encoding="utf-8")
    assert new in after
    assert TUNNEL_RE.findall(after) == [new]


def test_gateway_url_is_single_source():
    """page.tsx / bridge/page.tsx 에 URL 리터럴이 남아 있으면 안 된다."""
    for rel in ("src/app/page.tsx", "src/app/bridge/page.tsx"):
        body = (ROOT / rel).read_text(encoding="utf-8")
        assert not TUNNEL_RE.search(body), f"{rel} 에 터널 URL 리터럴이 남아 있음"
