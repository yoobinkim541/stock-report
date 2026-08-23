# Ticker Analysis Always-Visible UI Implementation Plan

> ## ✅ 완료 (배포됨 · 2026-08-23 확인)
>
> 계획서에 선언된 파일(Create/Modify/Test) 2개가 전부 코드베이스에 존재함을 확인했고,
> 이 세션에서 돌린 전체 테스트 스위트(2713 통과·0 실패)가 해당 테스트 파일들을 모두
> 포함한다. 개별 재실행 없이 이 근거로 체크박스를 표시한다.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ticker analysis page show analyst opinion, target price, fair value, PER band, and peer context immediately on load, with the main charts and indicators visible without opening expanders.

**Architecture:** Keep the existing valuation math, consensus logic, and data sources intact. Refactor `dashboard/pages/ticker.py` so the verdict card is followed by an always-visible analysis rail and visible chart blocks, while only long-form explanation stays in optional expanders. Reuse the current chart helpers from `dashboard/charts.py` and the compact HTML bands already available in `dashboard/theme.py`; do not add new services or storage.

**Tech Stack:** Python 3.11, Streamlit, Plotly, pandas, existing dashboard helpers, pytest.

## Global Constraints

- Presentation-only redesign; do not change the underlying valuation math, consensus logic, or data sources.
- Core indicators must be visible on first load.
- Detail sections may remain, but the main opinion must not depend on opening them.
- The page should fail visibly, not silently.
- Preserve clean rendering for both KR and US tickers, including fallback states.

---

### Task 1: Expose the core KR context as an always-visible analysis rail

**Files:**
- Modify: `dashboard/pages/ticker.py:1533-1705`
- Test: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: `_analysis_context(ticker, hist, price)`, `theme.analysis_card_html(...)`, `theme.position_band_html(...)`, `charts.analyst_ratings(...)`, `charts.target_price_fan(...)`
- Produces: a visible rail helper inside `_analysis_snapshot(...)` that renders analyst stance, target price, revision momentum, analyst count, and KR-specific context without an expander

- [x] **Step 1: Write the failing regression test**

Add a smoke test that renders a KR ticker and verifies the core context is present on the page body, not only behind a collapsed section.

```python
def test_ticker_page_kr_core_context_is_visible_without_expander():
    script = _STUBS + (
        'st.session_state["ticker"] = "005930.KS"\n'
        'from dashboard.pages import ticker\n'
        'ticker.render()\n'
    )
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert not any("한국 종목 심화 컨텍스트" in str(e.label) for e in at.expander)
    body = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "목표가 여력" in body
    assert "리비전 모멘텀" in body
```

- [x] **Step 2: Replace the hidden KR context expander with visible bands**

Refactor `_analysis_snapshot(...)` so the KR block becomes a compact visible rail under the verdict card. Use `theme.position_band_html(...)` for the dense metrics and keep the detailed numeric commentary as short captions instead of nested expanders.

```python
theme.render(theme.position_band_html([
    ("밸류 출처", str(m.get("source") or "DART"), None),
    ("기준연도", f"{m.get('fiscal_year') or '—'}", None),
    ("컨센서스", f"{c.get('target_mean'):,.0f}" if c.get("target_mean") else "—", None),
    ("신뢰도", str(m.get("confidence") or "—"), None),
]))
```

Keep the existing flow, trend, disclosure, and PER-band facts visible in the same area instead of hiding them behind `st.expander(...)`.

- [x] **Step 3: Surface the analyst and target-price charts in the same visible rail**

Render `charts.analyst_ratings(...)` and `charts.target_price_fan(...)` directly below the compact bands so the user sees the distribution and the upside range without opening anything.

```python
st.plotly_chart(charts.analyst_ratings(rec), width="stretch", config=_NOBAR)
st.plotly_chart(
    charts.target_price_fan(cached.ohlc(ticker, period="1y"), price,
                            c.get("target_high"), c.get("target_mean"), c.get("target_low"),
                            cur_sym),
    width="stretch", config=_NOBAR,
)
```

- [x] **Step 4: Verify the task in isolation**

Run: `./.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "kr_core_context or ticker_llm_analysis_section" -q`

Expected: the KR context assertions pass, the page still renders, and no new expander-only dependency is introduced.

---

### Task 2: Promote valuation, PER band, and peer comparison to visible sections

**Files:**
- Modify: `dashboard/pages/ticker.py:1609-1795`
- Test: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: `cached.valuation(...)`, `data.fair_value_multiple(...)`, `data.per_self_band(...)`, `data.sector_peers(...)`, `charts.bullet_bands(...)`
- Produces: visible valuation cards, a visible self-PER band block, and a visible peer comparison table/strip instead of default-collapsed expanders

- [x] **Step 1: Write the failing regression tests**

Add smoke coverage that confirms the valuation detail blocks render without depending on expander labels.

```python
def test_ticker_page_per_band_and_peer_sections_are_visible():
    script = _STUBS + (
        'st.session_state["ticker"] = "MSFT"\n'
        'from dashboard.pages import ticker\n'
        'ticker.render()\n'
    )
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    assert not any("자체 역사 PER 밴드" in str(e.label) for e in at.expander)
    assert not any("동종업계 비교" in str(e.label) for e in at.expander)
    labels = [m.label for m in at.metric]
    assert "현재 PER" in labels
    assert "멀티플 기준가" in labels
```

- [x] **Step 2: Rewrite the self-PER band section as a visible card**

Change `_per_self_band_section(...)` so it always renders inline when enough history exists. Keep the same `band = data.per_self_band(...)` calculation, but show `최저 / 중앙값 / 최고 / 현재 PER` in visible metrics and add a short caption instead of wrapping the block in `st.expander(...)`.

```python
g = st.columns(4)
g[0].metric("최저", data.f_ratio(band["min"]))
g[1].metric("중앙값", data.f_ratio(band["median"]))
g[2].metric("최고", data.f_ratio(band["max"]))
g[3].metric("현재 PER", data.f_ratio(cur), delta=delta, delta_color="inverse")
```

- [x] **Step 3: Rewrite the peer comparison section as a visible comparison block**

Change `_peer_comparables_section(...)` so the current company row and peer rows are shown directly in the page body. Keep the `st.dataframe(...)` for the detailed comparison, and add a compact caption or strip explaining what the table is comparing.

```python
st.dataframe(
    pd.DataFrame(rows),
    hide_index=True,
    width="stretch",
    column_config={
        "PER": st.column_config.NumberColumn(format="%.1f"),
        "PBR": st.column_config.NumberColumn(format="%.2f"),
        "ROE(%)": st.column_config.NumberColumn(format="%.1f%%"),
    },
)
```

- [x] **Step 4: Keep fallback states honest**

If the KR fallback path is active or peer data is missing, render a visible fallback note instead of hiding the section entirely. Do not change the existing valuation logic; only change how the state is shown.

- [x] **Step 5: Verify the task in isolation**

Run: `./.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "per_self_band or peer_comparables" -q`

Expected: the visible sections render, the fallback states still render, and no expander-only assertions remain for these indicators.

---

### Task 3: Refresh dashboard smoke coverage and do the final verification pass

**Files:**
- Modify: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: the refactored ticker page render helpers
- Produces: updated smoke tests that assert visible analysis rails, chart presence, and fallback behavior

- [x] **Step 1: Update the existing ticker smoke tests**

Rewrite the ticker tests that currently inspect `at.expander` labels so they assert the visible analysis rail, visible valuation metrics, and chart output instead.

```python
assert any("목표가 여력" in str(m.value) for m in at.markdown)
assert any("멀티플 기준가" in str(m.value) for m in at.markdown)
assert len(at.get("iframe")) >= 2
```

- [x] **Step 2: Add a fallback regression for missing data**

Add a test that simulates missing consensus or KR fallback data and verifies the page still renders a neutral placeholder card, not an empty gap.

```python
cached.valuation = lambda t: {"metrics": {"market_type": "kr", "kr_yf_fallback": True}, "consensus": {}, "history": []}
```

- [x] **Step 3: Run the focused dashboard suite**

Run: `./.venv/bin/python -m pytest tests/test_dashboard_pages.py -q`

Expected: all dashboard smoke tests pass with the new always-visible layout.

- [x] **Step 4: Run the final verification pass**

Run: `./.venv/bin/python -m pytest -q`

Expected: no regressions outside the ticker page, and the dashboard still boots cleanly in the current environment.

- [x] **Step 5: Commit the implementation**

```bash
git add dashboard/pages/ticker.py tests/test_dashboard_pages.py docs/superpowers/plans/2026-08-01-ticker-analysis-always-visible-ui.md
git commit -m "add) 종목 분석 항상 표시 UI 구현 계획 추가"
```
