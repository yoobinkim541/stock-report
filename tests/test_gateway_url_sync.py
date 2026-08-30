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


def test_watchdog_probes_live_tunnel_before_trusting_pid():
    """cloudflared PID가 살아 있어도 control stream이 끊긴 quick tunnel은 재기동해야 한다."""
    body = WATCHDOG.read_text(encoding="utf-8")
    assert "probe_tunnel" in body
    assert "curl -fsS" in body
    assert "probe_tunnel \"$CUR\"" in body
    assert "dig +short" in body
    assert "--resolve" in body


def test_watchdog_does_not_pass_its_lock_to_cloudflared():
    """백그라운드 cloudflared가 watchdog flock FD를 상속하면 다음 실행이 영구 차단된다."""
    body = WATCHDOG.read_text(encoding="utf-8")
    assert "cloudflared tunnel --url" in body
    assert "9>&-" in body


def test_watchdog_uses_detached_landing_worktree():
    """현재 checkout이 master여도 현관 URL 갱신용 보조 worktree를 만들 수 있어야 한다."""
    body = WATCHDOG.read_text(encoding="utf-8")
    assert "git worktree add --detach" in body


def test_watchdog_syncs_landing_when_live_url_file_is_already_current():
    """URL 파일 기록 후 현관 갱신이 실패해도 다음 실행에서 현관을 따라잡아야 한다."""
    body = WATCHDOG.read_text(encoding="utf-8")
    assert "CURRENT_GATEWAY" in body
    assert '"$CURRENT_GATEWAY" != "$NEW"' in body


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
    """현재 존재하는 모든 Next 페이지에 URL 리터럴이 남아 있으면 안 된다."""
    pages = sorted((ROOT / "src" / "app").rglob("page.tsx"))
    assert pages
    for page in pages:
        body = page.read_text(encoding="utf-8")
        rel = page.relative_to(ROOT)
        assert not TUNNEL_RE.search(body), f"{rel} 에 터널 URL 리터럴이 남아 있음"
