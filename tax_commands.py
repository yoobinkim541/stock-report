"""Tax-related Telegram bot commands."""
import logging
from datetime import datetime

from barbell_strategy import fetch_exchange_rate, fetch_portfolio_value
from attachment_parser import load_pending_sells, clear_pending_sells, build_pending_sells_summary

logger = logging.getLogger(__name__)


def _tax_import_apply(chat_id: str, send_fn):
    from tax_tracker import add_sell
    pending = load_pending_sells()
    if not pending:
        send_fn(chat_id,
                "❌ 적용할 매도내역 없음\n"
                "PDF 또는 스크린샷을 전송하면 자동 파싱 후 대기 파일이 생성됩니다.")
        return
    sells = pending.get("sells", [])
    if not sells:
        send_fn(chat_id, "❌ 파싱된 매도내역이 없습니다.")
        return
    fx = fetch_exchange_rate()
    applied: list[str] = []
    errors: list[str] = []
    for s in sells:
        try:
            rec = add_sell(
                s["ticker"], s["qty"],
                s["buy_price_usd"], s["sell_price_usd"], fx,
            )
            gu = rec["gain_usd"]
            sg = "▲" if gu >= 0 else "▼"
            applied.append(
                f"  {s['ticker']} ({s['name']})  {s['qty']}주"
                f"  {sg}${abs(gu):,.2f}"
            )
        except Exception as e:
            errors.append(f"  {s['ticker']}: {e}")
    clear_pending_sells()
    lines = ["✅ 매도내역 세금 기록 반영 완료", "━━━━━━━━━━━━━━━━━━━━━━━"]
    lines += applied
    if errors:
        lines += ["", "❌ 오류:"] + errors
    lines += ["", f"환율: {fx:,.0f}원/USD  ·  /tax 로 확인"]
    send_fn(chat_id, "\n".join(lines))


def _tax_delete(chat_id: str, args: list, send_fn):
    from tax_tracker import delete_record, get_all_records
    if len(args) < 2:
        send_fn(chat_id, "❌ 형식: /tax delete N  (N = /tax history 의 번호)")
        return
    try:
        n = int(args[1])
    except ValueError:
        send_fn(chat_id, "❌ 숫자를 입력하세요.  예) /tax delete 3")
        return
    removed = delete_record(n)
    if removed is None:
        records = get_all_records()
        send_fn(chat_id, f"❌ #{n} 번 기록 없음 (전체 {len(records)}건)")
    else:
        gu = removed.get("gain_usd", 0)
        sg = "▲" if gu >= 0 else "▼"
        send_fn(chat_id,
                f"🗑 #{n} 삭제 완료\n"
                f"  {removed['date']}  {removed['ticker']}  {removed['qty']}주\n"
                f"  @${removed['buy_price_usd']:.2f} → @${removed['sell_price_usd']:.2f}"
                f"  {sg}${abs(gu):,.2f}")


def _tax_sim(chat_id: str, args: list, send_fn):
    from tax_tracker import simulate_sell, EXEMPTION_KRW
    if len(args) < 2:
        send_fn(chat_id,
                "❌ 형식: /tax sim TICKER [수량] [매수단가]\n"
                "예)  /tax sim NVDA\n"
                "     /tax sim NVDA 2\n"
                "     /tax sim NVDA 2 184.14")
        return

    ticker = args[1].upper()

    snap_holdings: list[dict] = []
    try:
        from barbell_strategy import PORTFOLIO_PATH
        import json
        with open(PORTFOLIO_PATH, encoding="utf-8") as _f:
            snap = json.load(_f)
        for section in ("overseas_general", "overseas_fractional"):
            snap_holdings += snap.get(section, {}).get("holdings_usd", []) + \
                              snap.get(section, {}).get("holdings", [])
    except Exception as _e:
        logger.debug("포트폴리오 스냅샷 로드 실패 (평단가 표시 생략): %s", _e)

    snap_entry = next((h for h in snap_holdings if h.get("ticker") == ticker), None)

    try:
        qty = float(args[2]) if len(args) >= 3 else None
    except ValueError:
        send_fn(chat_id, "❌ 수량은 숫자여야 합니다.  예) /tax sim NVDA 2")
        return
    if qty is None:
        if snap_entry:
            qty = snap_entry.get("shares", 0)
        else:
            send_fn(chat_id,
                    f"❌ 포트폴리오에 {ticker} 없음\n"
                    f"수량을 직접 입력하세요: /tax sim {ticker} [수량] [매수단가]")
            return

    try:
        buy_price = float(args[3]) if len(args) >= 4 else None
    except ValueError:
        send_fn(chat_id, "❌ 매수단가는 숫자여야 합니다.")
        return
    if buy_price is None:
        if snap_entry and snap_entry.get("avg_price_usd"):
            buy_price = snap_entry["avg_price_usd"]
        elif snap_entry and snap_entry.get("cost_usd") and qty:
            buy_price = snap_entry["cost_usd"] / qty
        else:
            send_fn(chat_id,
                    f"❌ 매수단가를 찾을 수 없음\n"
                    f"/tax sim {ticker} {qty} [매수단가]")
            return

    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="2d")
        if h.empty:
            raise ValueError("데이터 없음")
        sell_price = float(h["Close"].iloc[-1])
    except Exception as e:
        send_fn(chat_id, f"❌ {ticker} 현재가 조회 실패: {e}")
        return

    try:
        fx = fetch_exchange_rate()
        res = simulate_sell(ticker, qty, buy_price, sell_price, fx)
    except Exception as e:
        send_fn(chat_id, f"❌ 시뮬레이션 오류: {e}")
        return

    gu = res["gain_usd"]
    gk = res["gain_krw"]
    sg = "▲" if gu >= 0 else "▼"
    cg = res["combined_gain_krw"]
    csg = "▲" if cg >= 0 else "▼"
    tx = res["tax_krw"]
    txu = tx / fx if fx > 0 else 0
    tk = res["taxable_krw"]
    ei = res["existing_gain_krw"]
    esg = "▲" if ei >= 0 else "▼"
    exem = min(EXEMPTION_KRW, max(0, int(cg))) if cg > 0 else 0

    SEP = "─" * 44
    company = snap_entry.get("name", ticker) if snap_entry else ticker
    lines = [
        f"🔮 매도 시뮬레이션 (실제 반영 안됨)",
        SEP,
        f"종목  {ticker} — {company}",
        f"수량  {qty}주   @${sell_price:.2f} (현재가)",
        f"매수단가  ${buy_price:.2f}",
        f"예상 손익  {sg}${abs(gu):,.2f}  ({sg}{abs(gk):,.0f}원)",
        SEP,
        f"기존 실현손익  {esg}{abs(ei):,.0f}원",
        f"합산 총손익    {csg}{abs(cg):,.0f}원",
        f"기본공제 차감  -{exem:,.0f}원",
        f"과세표준       {tk:,.0f}원",
    ]
    if tk <= 0:
        lines.append(f"예상 세금      0원  (공제 이내)")
    else:
        lines.append(f"예상 세금(22%) {tx:,.0f}원  (${txu:,.2f})")
    lines += [
        SEP,
        f"※ 실제 반영: /tax sell {ticker} {qty} {buy_price:.2f} {sell_price:.2f}",
    ]
    send_fn(chat_id, "\n".join(lines))


def _tax_sell(chat_id: str, args: list, send_fn):
    from tax_tracker import add_sell
    if len(args) < 5:
        send_fn(chat_id,
                "❌ 형식: /tax sell TICKER 수량 매수단가 매도단가\n"
                "예)  /tax sell NVDA 10 400.00 520.00")
        return
    try:
        ticker = args[1].upper()
        qty = float(args[2])
        buy_price = float(args[3])
        sell_price = float(args[4])
    except (ValueError, IndexError):
        send_fn(chat_id, "❌ 숫자 형식 오류. 예) /tax sell NVDA 10 400.00 520.00")
        return
    try:
        fx = fetch_exchange_rate()
        rec = add_sell(ticker, qty, buy_price, sell_price, fx)
        gu = rec["gain_usd"]
        gk = rec["gain_krw"]
        sg = "+" if gu >= 0 else ""
        send_fn(chat_id, (
            f"✅ 매도 기록 저장\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  종목     {ticker}\n"
            f"  수량     {qty}주\n"
            f"  매수단가 ${buy_price:.2f}\n"
            f"  매도단가 ${sell_price:.2f}\n"
            f"  실현손익 {sg}${gu:,.2f}  ({sg}{gk:,.0f}원)\n"
            f"  환율     {fx:,.0f}원/USD\n"
            f"  날짜     {rec['date']}"
        ))
    except Exception as e:
        send_fn(chat_id, f"❌ 매도 기록 오류: {e}")


def _tax_history(chat_id: str, send_fn):
    from tax_tracker import get_all_records
    records = get_all_records()
    if not records:
        send_fn(chat_id,
                "📭 매도 기록 없음\n"
                "/tax sell TICKER 수량 매수단가 매도단가  로 기록")
        return
    SEP = "─" * 50
    lines = ["📋 전체 매도 기록", SEP]
    for r in records:
        gu = r.get("gain_usd", 0)
        gk = r.get("gain_krw", 0)
        sg = "▲" if gu >= 0 else "▼"
        lines.append(
            f"{r['date']}  {r['ticker']:<6}  {r['qty']}주\n"
            f"  @${r['buy_price_usd']:.2f} → @${r['sell_price_usd']:.2f}"
            f"  {sg}${abs(gu):,.2f}  ({sg}{abs(gk):,.0f}원)"
        )
    lines += [SEP, f"총 {len(records)}건"]
    send_fn(chat_id, "\n".join(lines))


def _tax_summary(chat_id: str, send_fn):
    from tax_tracker import get_yearly_summary, EXEMPTION_KRW
    try:
        year = datetime.now().year
        summary = get_yearly_summary(year)
        fx = fetch_exchange_rate()

        total_usd = summary["total_gain_usd"]
        total_krw = summary["total_gain_krw"]
        taxable_krw = summary["taxable_krw"]
        tax_krw = summary["tax_krw"]
        tax_usd = tax_krw / fx if fx > 0 else 0
        count = summary["count"]

        SEP = "─" * 52
        sign = "+" if total_krw >= 0 else ""
        lines = [
            f"💸 {year}년 실현손익 & 양도소득세 추산",
            f"📅 {datetime.now().strftime('%Y-%m-%d')}  (@{fx:,.0f}원/USD)",
            SEP,
        ]

        if count == 0:
            lines += [
                "올해 매도 기록 없음",
                "",
                "/tax sell TICKER 수량 매수단가 매도단가  — 매도 기록",
                "/tax history  — 전체 매도 기록",
            ]
        else:
            by_usd: dict[str, float] = {}
            by_krw: dict[str, float] = {}
            for r in summary["records"]:
                t = r["ticker"]
                by_usd[t] = by_usd.get(t, 0) + r.get("gain_usd", 0)
                by_krw[t] = by_krw.get(t, 0) + r.get("gain_krw", 0)

            lines += [
                f"{'종목':<8} {'실현손익(USD)':>14} {'실현손익(KRW)':>14}",
                SEP,
            ]
            for t in sorted(by_usd, key=lambda x: -by_usd[x]):
                gu = by_usd[t]
                gk = by_krw[t]
                sg = "▲" if gu >= 0 else "▼"
                lines.append(
                    f"{t:<8} {sg}${abs(gu):>12,.2f}  {sg}{abs(gk):>12,.0f}원"
                )
            lines += [
                SEP,
                f"실현 총손익    : {sign}{total_krw:,.0f}원  (${total_usd:,.2f})",
                f"기본공제(250만): -{min(EXEMPTION_KRW, max(0, int(total_krw))):,.0f}원",
                f"과세표준       : {taxable_krw:,.0f}원",
                f"예상 세금(22%) : {tax_krw:,.0f}원  (${tax_usd:,.2f})",
                "",
                f"※ 매도 기록 {count}건 기준 — 실현손익만 집계",
                "※ 손실 통산: 손실 종목이 수익 상쇄",
                "※ 국내 양도세 신고: 매년 5월 (전년도)",
                "",
                "/tax sim TICKER [수량]  — 매도 전 세금 시뮬레이션",
                "/tax sell TICKER 수량 매수단가 매도단가",
                "/tax history  — 전체 매도 기록",
            ]

        send_fn(chat_id, "\n".join(lines))
    except Exception as e:
        send_fn(chat_id, f"❌ 세금 추산 오류: {e}")
        logger.exception("cmd_tax summary")


_TAX_DISPATCH = {
    "import": lambda chat_id, args, send_fn: (
        _tax_import_apply(chat_id, send_fn)
        if len(args) > 1 and args[1].lower() == "apply"
        else send_fn(chat_id,
                     "사용법: /tax import apply\n\n"
                     "먼저 매도내역 PDF 또는 스크린샷을 채팅창에 전송하세요.")
    ),
    "delete":  lambda chat_id, args, send_fn: _tax_delete(chat_id, args, send_fn),
    "sim":     lambda chat_id, args, send_fn: _tax_sim(chat_id, args, send_fn),
    "sell":    lambda chat_id, args, send_fn: _tax_sell(chat_id, args, send_fn),
    "history": lambda chat_id, args, send_fn: _tax_history(chat_id, send_fn),
}


def cmd_tax(chat_id: str, args: list, send_fn):
    """실현손익 기록/조회 + 양도소득세 추산."""
    sub = args[0].lower() if args else ""
    handler = _TAX_DISPATCH.get(sub)
    if handler:
        handler(chat_id, args, send_fn)
    else:
        _tax_summary(chat_id, send_fn)
