"""차트 풀뷰 iframe 높이 맞춤(frame_fit.tnFitFrame) 런타임 — node 로 가짜 DOM 에서 실행."""
import json
import shutil
import subprocess

import pytest

from dashboard import frame_fit

_HARNESS = r"""
__SCRIPT__
function run(c) {
  const box = { dataset: { testid: "stElementContainer" }, style: {} };
  const fe = { parentElement: box, style: {}, getBoundingClientRect: () => ({ top: c.top }) };
  const main = {
    scrollTop: 0, clientHeight: c.vh,
    get scrollHeight() { return c.top + parseInt(fe.style.height || "0", 10) + c.below; },
  };
  const pw = { innerHeight: c.vh, document: { querySelector: () => main } };
  globalThis.window = { frameElement: fe, parent: pw };
  const h = tnFitFrame();
  return { h, fe: fe.style.height, box: box.style.height, flex: box.style.flex };
}
console.log(JSON.stringify(__CASES__.map(run)));
"""


def _run(cases, tmp_path):
    js = _HARNESS.replace("__SCRIPT__", frame_fit.FIT_FRAME_JS).replace("__CASES__", json.dumps(cases))
    path = tmp_path / "fit.js"
    path.write_text(js, encoding="utf-8")
    out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치 — 런타임 JS 검증 스킵")
def test_fit_fills_viewport_and_shrinks_streamlit_container(tmp_path):
    desk, trailer, terminal, narrow = _run([
        {"vh": 805, "top": 105, "below": 0},     # 차트만 — 창 바닥 8px 위까지
        {"vh": 805, "top": 105, "below": 24},    # 실시간 피더 같은 작은 꼬리 — 흡수
        {"vh": 805, "top": 150, "below": 600},   # 리플레이 터미널 — 차트는 유지, 페이지 스크롤
        {"vh": 844, "top": 511, "below": 0},     # 모바일: 컨트롤이 화면을 거의 먹음 — 큰 차트
    ], tmp_path)
    assert desk == {"h": 692, "fe": "692px", "box": "692px", "flex": "0 0 692px"}
    assert trailer["h"] == 692 - (105 + 692 + 24 - 805)
    assert terminal["h"] == 805 - 150 - 8
    assert narrow["h"] == int(844 * 0.75)
