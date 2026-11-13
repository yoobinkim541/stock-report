"""주문 화면 위 한 줄 티커. 네트워크 없음."""
from __future__ import annotations


def ticker_strip_text(symbol: str, last: float) -> str:
    """이름 · 티커 · 통화가 붙은 가격. 주문 탭을 열어도 종목이 남는다."""
    import ticker_names

    raw = str(symbol or "").strip()
    sym = ticker_names.normalize_input(raw) or raw.upper()
    name = ticker_names.display_name(sym, allow_net=False) or sym
    unit = ticker_names.macro_unit(sym) if ticker_names.is_macro(sym) else (
        "₩" if ticker_names.is_kr_listing(sym) else "$"
    )
    if unit == "₩":
        price = f"₩{last:,.0f}"
    elif unit == "$":
        price = f"${last:,.2f}"
    elif unit == "%":
        price = f"{last:,.2f}%"
    else:
        price = f"{last:,.2f}" + (f" {unit}" if unit else "")
    return f"{name} · {sym} · {price}"
