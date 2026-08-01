## Task 1 Report

### What I implemented
- Reworked the KR branch inside `dashboard/pages/ticker.py::_analysis_snapshot(...)` so the old `🇰🇷 한국 종목 심화 컨텍스트` expander is gone and the core KR analysis context is always visible directly under the verdict card.
- Replaced the hidden `st.metric(...)` rows with visible compact rails using `theme.position_band_html(...)`.
- Surfaced the requested KR context inline: analyst stance, consensus target price, target upside, revision momentum, analyst count, valuation source/year/confidence, recent earnings surprise, disclosure count, valuation multiples, KR flow data, and PER-band context.
- Rendered the analyst ratings chart and target price fan chart directly in the visible KR rail.
- Added explicit `key=` values to the new charts so they can coexist with the existing valuation-section charts without Streamlit duplicate-element errors.

### What I tested and results
- Red step: `./.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "kr_core_context or ticker_llm_analysis_section" -q`
  - Failed first as expected because the KR context still existed behind the expander.
- Green step: `./.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "kr_core_context or ticker_llm_analysis_section" -q`
  - Passed: `2 passed, 74 deselected`
- Extra confidence: `./.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "ticker_analysis_context_collects_kr_deep_signals or kr_core_context or ticker_llm_analysis_section" -q`
  - Passed: `3 passed, 73 deselected`

### Files changed
- `dashboard/pages/ticker.py`
- `tests/test_dashboard_pages.py`

### Self-review findings or concerns
- The change stays within Task 1 scope and preserves the existing KR data sources and valuation math.
- US ticker behavior is untouched because the new rail only runs inside `if ctx["is_kr"]`.
- Missing/fallback KR data still degrades to `—` labels and skips charts when consensus inputs are absent.
- The only notable implementation wrinkle was Streamlit duplicate chart IDs after making the KR charts visible earlier on the page; explicit chart keys resolved that without changing the downstream valuation section.

### Unexpected items
- None beyond the Streamlit duplicate-element ID issue noted above.

### Round 1 Fix Notes
- Added explicit fallback `st.info(...)` states inside the KR rail for two empty paths: no analyst consensus and missing target-price inputs.
- Added a regression test that renders a KR ticker with empty consensus data and no usable price data, then asserts both fallback messages appear in the page info blocks.
- Focused verification after the fix:
  - `./.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "kr_core_context or ticker_llm_analysis_section" -q`
  - `./.venv/bin/python -m pytest tests/test_dashboard_pages.py -k "ticker_analysis_context_collects_kr_deep_signals or kr_core_context or ticker_llm_analysis_section" -q`
