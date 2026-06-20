"""tax_tracker.py — 양도세 계산 회귀 테스트 (네트워크 불필요).

머니 계산(실현손익·양도세)에 단위 테스트 공백이 있어 신규 추가.
검증 범위:
- add_sell: gain_usd/gain_krw 산식 (음수 차익 포함)
- get_yearly_summary: 연 기본공제(EXEMPTION_KRW) 적용 · 음수 합산 0 처리
  · 세율(TAX_RATE) 적용 · 공제 경계값 · 연도 필터
- simulate_sell: 기존 누적 + 신규 차익 합산 후 공제·세율 적용 (비저장)

격리: STOCK_REPORT_DB tmp 리다이렉트는 conftest.py 가 import 시점에 처리.
각 테스트는 store 컬렉션을 비워 상호 오염을 막는다.
"""
import pytest

import store
import tax_tracker as tt


# ── 픽스처: 각 테스트마다 tax_records 컬렉션 비우기 ──────────────────────
@pytest.fixture(autouse=True)
def _clean_tax_collection():
    """매 테스트 전후로 격리 DB의 tax_records 를 비워 누적 오염 방지."""
    store.replace_all(tt._COLLECTION, [])
    yield
    store.replace_all(tt._COLLECTION, [])


# ══════════════════════════════════════════════════════════════════════
#  add_sell — 차익 산식
# ══════════════════════════════════════════════════════════════════════

def test_add_sell_positive_gain():
    """양의 차익: gain_usd = (매도-매수)*수량, gain_krw = gain_usd*fx."""
    rec = tt.add_sell("nvda", qty=10, buy_price_usd=100.0,
                      sell_price_usd=150.0, fx=1300.0)
    assert rec["ticker"] == "NVDA"            # 대문자 정규화
    assert rec["gain_usd"] == 500.0           # (150-100)*10
    assert rec["gain_krw"] == 650_000.0       # 500*1300, round 0
    assert rec["fx"] == 1300.0


def test_add_sell_negative_gain_recorded_raw():
    """손실(음수 차익)도 그대로 기록 — 0 처리는 연 합산 단계에서만."""
    rec = tt.add_sell("MSFT", qty=5, buy_price_usd=400.0,
                      sell_price_usd=350.0, fx=1400.0)
    assert rec["gain_usd"] == -250.0          # (350-400)*5
    assert rec["gain_krw"] == -350_000.0      # -250*1400


def test_add_sell_persists_to_store():
    """기록이 store 컬렉션에 적재되는지 확인."""
    tt.add_sell("GOOGL", 2, 100.0, 120.0, 1300.0)
    all_recs = tt.get_all_records()
    assert len(all_recs) == 1
    assert all_recs[0]["ticker"] == "GOOGL"


# ══════════════════════════════════════════════════════════════════════
#  get_yearly_summary — 공제 · 세율 · 0 처리
# ══════════════════════════════════════════════════════════════════════

def test_yearly_summary_applies_exemption_and_rate():
    """공제 초과분에만 세율 적용: tax = (합산 - 공제) * TAX_RATE."""
    # gain_krw = 10,000,000 (공제 250만 초과)
    tt.add_sell("NVDA", qty=10, buy_price_usd=0.0,
                sell_price_usd=1000.0, fx=1000.0)   # 10,000 USD * 1000 = 10,000,000 KRW
    s = tt.get_yearly_summary()
    assert s["total_gain_krw"] == 10_000_000.0
    expected_taxable = 10_000_000.0 - tt.EXEMPTION_KRW   # 7,500,000
    assert s["taxable_krw"] == expected_taxable
    assert s["tax_krw"] == round(expected_taxable * tt.TAX_RATE, 0)  # 1,650,000
    assert s["count"] == 1


def test_yearly_summary_below_exemption_no_tax():
    """공제 이하 차익이면 과세표준·세금 모두 0."""
    # gain_krw = 2,000,000 < EXEMPTION_KRW(2,500,000)
    tt.add_sell("MSFT", qty=1, buy_price_usd=0.0,
                sell_price_usd=2000.0, fx=1000.0)    # 2,000 USD * 1000 = 2,000,000
    s = tt.get_yearly_summary()
    assert s["total_gain_krw"] == 2_000_000.0
    assert s["taxable_krw"] == 0.0
    assert s["tax_krw"] == 0.0


def test_yearly_summary_exemption_boundary_exact():
    """경계값: 합산 == 공제 → 과세표준 0 (max(0, 0))."""
    tt.add_sell("ORCL", qty=1, buy_price_usd=0.0,
                sell_price_usd=2500.0, fx=1000.0)    # 정확히 2,500,000 = EXEMPTION
    s = tt.get_yearly_summary()
    assert s["total_gain_krw"] == float(tt.EXEMPTION_KRW)
    assert s["taxable_krw"] == 0.0
    assert s["tax_krw"] == 0.0


def test_yearly_summary_one_krw_above_exemption():
    """경계값 +1: 공제 초과 1원에도 세금이 비례 발생."""
    tt.add_sell("SAP", qty=1, buy_price_usd=0.0,
                sell_price_usd=2_500_001.0, fx=1.0)  # 2,500,001 KRW
    s = tt.get_yearly_summary()
    assert s["taxable_krw"] == 1.0
    assert s["tax_krw"] == round(1.0 * tt.TAX_RATE, 0)   # round(0.22) = 0


def test_yearly_summary_negative_net_clamped_to_zero():
    """순손실(음수 합산)이면 과세표준·세금 0 — 음수 세금 금지."""
    tt.add_sell("UNH", qty=10, buy_price_usd=500.0,
                sell_price_usd=400.0, fx=1300.0)     # -1000 USD → 음수 KRW
    s = tt.get_yearly_summary()
    assert s["total_gain_krw"] < 0
    assert s["taxable_krw"] == 0.0
    assert s["tax_krw"] == 0.0


def test_yearly_summary_nets_gains_and_losses():
    """이익·손실 상계 후 합산에 공제·세율 적용."""
    # 이익 8,000,000 + 손실 -1,000,000 = 순 7,000,000
    tt.add_sell("NVDA", qty=8, buy_price_usd=0.0,
                sell_price_usd=1000.0, fx=1000.0)    # +8,000,000
    tt.add_sell("MSFT", qty=1, buy_price_usd=1000.0,
                sell_price_usd=0.0, fx=1000.0)       # -1,000,000
    s = tt.get_yearly_summary()
    assert s["total_gain_krw"] == 7_000_000.0
    expected_taxable = 7_000_000.0 - tt.EXEMPTION_KRW   # 4,500,000
    assert s["taxable_krw"] == expected_taxable
    assert s["tax_krw"] == round(expected_taxable * tt.TAX_RATE, 0)
    assert s["count"] == 2


def test_yearly_summary_filters_by_year():
    """다른 연도 기록은 합산에서 제외 (date prefix 필터)."""
    tt.add_sell("NVDA", 10, 0.0, 1000.0, 1000.0)     # 올해 기록
    # 과거 연도 기록을 직접 store 에 주입 (add_sell 은 today 만 기록)
    recs = store.load_collection(tt._COLLECTION, tt.TAX_FILE)
    recs.append({
        "date": "1999-01-01", "ticker": "OLD", "qty": 1,
        "buy_price_usd": 0.0, "sell_price_usd": 1.0,
        "gain_usd": 1.0, "gain_krw": 99_000_000.0, "fx": 1000.0,
    })
    store.replace_all(tt._COLLECTION, recs)

    from datetime import datetime
    this_year = datetime.now().year
    s = tt.get_yearly_summary(this_year)
    assert s["count"] == 1                            # 1999 기록 제외
    assert s["total_gain_krw"] == 10_000_000.0

    s_old = tt.get_yearly_summary(1999)
    assert s_old["count"] == 1
    assert s_old["total_gain_krw"] == 99_000_000.0


def test_yearly_summary_empty_is_zero():
    """기록 없으면 모든 합산·세금 0, count 0."""
    s = tt.get_yearly_summary()
    assert s["total_gain_usd"] == 0
    assert s["total_gain_krw"] == 0
    assert s["taxable_krw"] == 0.0
    assert s["tax_krw"] == 0.0
    assert s["count"] == 0


# ══════════════════════════════════════════════════════════════════════
#  simulate_sell — 누적 + 신규 차익 합산 (비저장)
# ══════════════════════════════════════════════════════════════════════

def test_simulate_sell_does_not_persist():
    """시뮬레이션은 기록을 남기지 않는다."""
    before = len(tt.get_all_records())
    tt.simulate_sell("NVDA", qty=10, buy_price_usd=100.0,
                     sell_price_usd=200.0, fx=1300.0)
    assert len(tt.get_all_records()) == before


def test_simulate_sell_combines_with_existing():
    """기존 누적 + 신규 차익을 합산해 공제·세율 적용."""
    # 기존: 3,000,000 KRW 실현
    tt.add_sell("MSFT", qty=3, buy_price_usd=0.0,
                sell_price_usd=1000.0, fx=1000.0)    # +3,000,000

    # 신규(시뮬): +5,000,000 KRW
    sim = tt.simulate_sell("NVDA", qty=5, buy_price_usd=0.0,
                           sell_price_usd=1000.0, fx=1000.0)
    assert sim["gain_krw"] == 5_000_000.0
    assert sim["existing_gain_krw"] == 3_000_000.0
    assert sim["combined_gain_krw"] == 8_000_000.0
    expected_taxable = 8_000_000.0 - tt.EXEMPTION_KRW   # 5,500,000
    assert sim["taxable_krw"] == expected_taxable
    assert sim["tax_krw"] == round(expected_taxable * tt.TAX_RATE, 0)
    assert sim["existing_count"] == 1


def test_simulate_sell_loss_offsets_existing_gain():
    """신규 손실이 기존 이익을 상계 → 공제 이하로 떨어지면 세금 0."""
    tt.add_sell("MSFT", qty=1, buy_price_usd=0.0,
                sell_price_usd=3_000_000.0, fx=1.0)  # +3,000,000

    sim = tt.simulate_sell("NVDA", qty=1, buy_price_usd=2_000_000.0,
                           sell_price_usd=0.0, fx=1.0)  # -2,000,000
    assert sim["combined_gain_krw"] == 1_000_000.0    # 공제(250만) 이하
    assert sim["taxable_krw"] == 0.0
    assert sim["tax_krw"] == 0.0
