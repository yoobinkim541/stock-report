from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
FIELDS = ("indices", "investor_flow", "k200_futures", "breadth", "fx")
NAVER_INDEX_CODES = {"KOSPI": "kospi", "KOSDAQ": "kosdaq", "KPI200": "kospi200"}
NAVER_MARKETS = ("KOSPI", "KOSDAQ")
NAVER_BREADTH_SORTS = ("marketValue", "up", "down")
KIWOOM_MARKETS = {"kospi": "0", "kosdaq": "1"}
KIWOOM_INTRA_FLOW_METHODS = (
    "intraday_investor_trading_request_ka10063",
    "intraday_investor_trading",
)
KIWOOM_AFTER_HOURS_FLOW_METHODS = (
    "after_hours_investor_trading_request_ka10066",
    "after_hours_investor_trading",
)
KIWOOM_SECTOR_FLOW_METHODS = (
    "industrywise_investor_net_buy_request_ka10051",
    "industry_investor_net_buy",
)


def _asof(now: datetime | None = None) -> str:
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    return current.astimezone(KST).isoformat(timespec="seconds")


def _ts(now: datetime | None = None) -> float:
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    return current.timestamp()


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _float(value):
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _int(value):
    num = _float(value)
    return int(num) if num is not None else None


def _clean(row: dict, keys: tuple[str, ...]) -> dict:
    row = _dict(row)
    out = {}
    for key in keys:
        if key in row and row.get(key) is not None:
            out[key] = row.get(key)
    if row.get("source"):
        out["source"] = row.get("source")
    if row.get("as_of"):
        out["as_of"] = row.get("as_of")
    return out


def field_status(name: str, source: str, ok: bool, as_of: str | None, error: str | None = None) -> dict:
    out = {"field": name, "source": source, "ok": bool(ok), "as_of": as_of}
    if error:
        out["error"] = str(error)
    return out


def normalize_indices(raw: dict) -> dict:
    raw = _dict(raw)
    out = {}
    for key in ("kospi", "kosdaq", "kospi200"):
        row = _dict(raw.get(key) or raw.get(key.upper()))
        price = _float(row.get("price") or row.get("value") or row.get("last"))
        if price is None:
            continue
        item = _clean(row, ("source", "as_of"))
        item.update({"price": price})
        change_pct = _float(row.get("change_pct") or row.get("pct") or row.get("changeRate"))
        if change_pct is not None:
            item["change_pct"] = change_pct
        change = _float(row.get("change") or row.get("diff"))
        if change is not None:
            item["change"] = change
        out[key] = item
    return out


def normalize_investor_flow(raw: dict) -> dict:
    raw = _dict(raw)
    out = {}
    for key in ("kospi", "kosdaq"):
        row = _dict(raw.get(key) or raw.get(key.upper()))
        if not row:
            continue
        item = _clean(row, ("source", "as_of", "unit", "scope", "basis", "base_dt", "market"))
        for src, dst in (
            ("foreign_net", "foreign_net"),
            ("foreigner_net", "foreign_net"),
            ("institution_net", "institution_net"),
            ("inst_net", "institution_net"),
            ("individual_net", "individual_net"),
            ("retail_net", "individual_net"),
        ):
            num = _float(row.get(src))
            if num is not None:
                item[dst] = num
        if item:
            out[key] = item
    return out


def normalize_futures(raw: dict) -> dict | None:
    row = _dict(raw)
    if not row:
        return None
    price = _float(row.get("price") or row.get("last") or row.get("value"))
    foreign_net = _float(row.get("foreign_net") or row.get("foreigner_net"))
    change_pct = _float(row.get("change_pct") or row.get("pct") or row.get("changeRate"))
    basis = _float(row.get("basis"))
    volume = _float(row.get("volume"))
    if price is None and foreign_net is None and change_pct is None and basis is None and volume is None:
        return None
    out = _clean(row, ("source", "as_of", "symbol", "name"))
    if price is not None:
        out["price"] = price
    if change_pct is not None:
        out["change_pct"] = change_pct
    if foreign_net is not None:
        out["foreign_net"] = foreign_net
    if basis is not None:
        out["basis"] = basis
    if volume is not None:
        out["volume"] = volume
    return out


def normalize_breadth(raw: dict) -> dict | None:
    row = _dict(raw)
    if not row:
        return None
    out = _clean(row, ("source", "as_of"))
    for key in ("advancers", "decliners", "unchanged"):
        num = _int(row.get(key))
        if num is not None:
            out[key] = num
    return out if any(k in out for k in ("advancers", "decliners", "unchanged")) else None


def normalize_fx(raw: dict) -> dict:
    raw = _dict(raw)
    row = _dict(raw.get("usdkrw") or raw.get("USD/KRW") or raw)
    rate = _float(row.get("rate") or row.get("price") or row.get("value"))
    if rate is None:
        return {}
    out = _clean(row, ("source", "as_of"))
    out.update({"pair": "USD/KRW", "rate": rate})
    change = _float(row.get("change"))
    if change is not None:
        out["change"] = change
    out["usdkrw"] = {k: v for k, v in out.items() if k != "usdkrw"}
    return out


def _snapshot_file_path() -> Path | None:
    raw = os.getenv("KR_MARKET_MICROSTRUCTURE_SOURCE_FILE", "").strip()
    return Path(raw).expanduser() if raw else None


def naver_enabled() -> bool:
    return os.getenv("KR_MARKET_MICROSTRUCTURE_NAVER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


def _http_json(url: str) -> dict:
    try:
        import requests

        resp = requests.get(
            url,
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def parse_naver_index_payload(payload: dict) -> dict:
    out = {}
    for row in _dict(payload).get("datas") or []:
        row = _dict(row)
        key = NAVER_INDEX_CODES.get(str(row.get("symbolCode") or row.get("itemCode") or "").upper())
        if not key:
            continue
        price = _float(row.get("closePriceRaw") or row.get("closePrice"))
        if price is None:
            continue
        item = {
            "price": price,
            "source": "naver_realtime_index",
        }
        change_pct = _float(row.get("fluctuationsRatioRaw") or row.get("fluctuationsRatio"))
        if change_pct is not None:
            item["change_pct"] = change_pct
        change = _float(row.get("compareToPreviousClosePriceRaw") or row.get("compareToPreviousClosePrice"))
        if change is not None:
            item["change"] = change
        if row.get("localTradedAt"):
            item["as_of"] = str(row.get("localTradedAt"))
        if row.get("marketStatus"):
            item["market_status"] = str(row.get("marketStatus"))
        out[key] = item
    return out


def fetch_naver_indices() -> dict:
    if not naver_enabled():
        return {}
    out = {}
    for code in NAVER_INDEX_CODES:
        payload = _http_json(f"https://polling.finance.naver.com/api/realtime/domestic/index/{code}")
        out.update(parse_naver_index_payload(payload))
    return out


def parse_naver_breadth_payloads(payloads: dict) -> dict | None:
    markets = {}
    total_adv = total_dec = total_unchanged = 0
    for market in NAVER_MARKETS:
        rows = {sort: _dict(payloads.get((market, sort)) or payloads.get(f"{market}:{sort}")) for sort in NAVER_BREADTH_SORTS}
        total = _int(rows["marketValue"].get("totalCount"))
        adv = _int(rows["up"].get("totalCount"))
        dec = _int(rows["down"].get("totalCount"))
        if total is None or adv is None or dec is None:
            continue
        unchanged = max(0, total - adv - dec)
        key = market.lower()
        markets[key] = {
            "total": total,
            "advancers": adv,
            "decliners": dec,
            "unchanged": unchanged,
        }
        status = rows["marketValue"].get("marketStatus") or rows["up"].get("marketStatus") or rows["down"].get("marketStatus")
        if status:
            markets[key]["market_status"] = str(status)
        total_adv += adv
        total_dec += dec
        total_unchanged += unchanged
    if not markets:
        return None
    return {
        "advancers": total_adv,
        "decliners": total_dec,
        "unchanged": total_unchanged,
        "markets": markets,
        "source": "naver_stock_counts",
    }


def fetch_naver_breadth() -> dict | None:
    if not naver_enabled():
        return None
    payloads = {}
    for market in NAVER_MARKETS:
        for sort in NAVER_BREADTH_SORTS:
            payloads[(market, sort)] = _http_json(f"https://m.stock.naver.com/api/stocks/{sort}/{market}?page=1&pageSize=1")
    return parse_naver_breadth_payloads(payloads)


def fetch_snapshot_file() -> dict:
    path = _snapshot_file_path()
    if not path:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def kiwoom_enabled() -> bool:
    return os.getenv("KR_MARKET_MICROSTRUCTURE_KIWOOM_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


def kis_futures_enabled() -> bool:
    return os.getenv("KR_MARKET_MICROSTRUCTURE_KIS_FUTURES_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


def fetch_indices() -> dict:
    out = fetch_naver_indices()
    out.update(_dict(fetch_snapshot_file().get("indices")))
    return out


def _amount_unit_krw() -> float:
    return _float(os.getenv("KIWOOM_INVESTOR_FLOW_AMOUNT_UNIT_KRW", "1000000")) or 1000000.0


def parse_kiwoom_investor_flow_payload(payload: dict, *, market: str) -> dict:
    rows = _dict(payload).get("inds_netprps")
    if not isinstance(rows, list):
        return {}
    total_rows = []
    for row in rows:
        name = str(_dict(row).get("inds_nm") or "").strip()
        if name in {"종합", "전체", "코스피", "코스닥", "KOSPI", "KOSDAQ"} or name.startswith("종합("):
            total_rows.append(row)
    source_rows = total_rows or rows
    totals = {"foreign_net": 0.0, "institution_net": 0.0, "individual_net": 0.0}
    any_value = False
    for row in source_rows:
        row = _dict(row)
        values = {
            "foreign_net": _float(row.get("frgnr_netprps")),
            "institution_net": _float(row.get("orgn_netprps")),
            "individual_net": _float(row.get("ind_netprps")),
        }
        for key, value in values.items():
            if value is not None:
                totals[key] += value
                any_value = True
    if not any_value:
        return {}
    unit = _amount_unit_krw()
    item = {key: value * unit for key, value in totals.items()}
    item.update({"unit": "KRW", "source": "kiwoom_ka10051"})
    return {market.lower(): item}


def _kiwoom_market_phase(now: datetime | None = None) -> str:
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    current = current.astimezone(KST)
    # 장중 우선, 장마감 후에는 after-hours 우선
    return "intraday" if current.weekday() < 5 and (current.hour < 15 or (current.hour == 15 and current.minute < 40)) else "after_hours"


def _call_kiwoom_flow_method(api, method_names: tuple[str, ...], **kwargs):
    for name in method_names:
        func = getattr(api, name, None)
        if callable(func):
            return func(**kwargs)
    return None


def _build_kiwoom_clients():
    market_api = None
    sector_api = None
    token_manager = None
    try:
        from kiwoom_rest_api.auth.token import TokenManager
        token_manager = TokenManager()
    except Exception:
        token_manager = None
    try:
        from kiwoom_rest_api.koreanstock.market import Market
    except Exception:
        Market = None  # type: ignore[assignment]
    try:
        from kiwoom_rest_api.koreanstock.sector import Sector
    except Exception:
        Sector = None  # type: ignore[assignment]
    if token_manager is None:
        return None, None
    if Market is not None:
        market_api = Market(base_url="https://api.kiwoom.com", token_manager=token_manager, use_async=False)
    if Sector is not None:
        sector_api = Sector(base_url="https://api.kiwoom.com", token_manager=token_manager, use_async=False)
    return market_api, sector_api


def _flow_request_kwargs(*, market: str, base_dt: str, scope: str) -> dict:
    kwargs = {
        "mrkt_tp": KIWOOM_MARKETS.get(market, market),
        "amt_qty_tp": "0",
        "stex_tp": os.getenv("KIWOOM_INVESTOR_FLOW_STEX_TP", "3"),
        "base_dt": base_dt,
    }
    return kwargs


def _kiwoom_flow_payload(api, *, market: str, scope: str, base_dt: str):
    kwargs = _flow_request_kwargs(market=market, base_dt=base_dt, scope=scope)
    if scope == "after_hours":
        payload = _call_kiwoom_flow_method(api, KIWOOM_AFTER_HOURS_FLOW_METHODS, **kwargs)
    elif scope == "intraday":
        payload = _call_kiwoom_flow_method(api, KIWOOM_INTRA_FLOW_METHODS, **kwargs)
    else:
        payload = None
    if payload:
        return payload
    return _call_kiwoom_flow_method(api, KIWOOM_SECTOR_FLOW_METHODS, **kwargs)


def fetch_kiwoom_investor_flow() -> dict:
    if not kiwoom_enabled():
        return {}
    try:
        if not os.getenv("KIWOOM_API_KEY") or not os.getenv("KIWOOM_API_SECRET"):
            return {}
        market_api, sector_api = _build_kiwoom_clients()
        if market_api is None and sector_api is None:
            return {}
        base_dt = os.getenv("KIWOOM_INVESTOR_FLOW_BASE_DT") or datetime.now(KST).strftime("%Y%m%d")
        scope = _kiwoom_market_phase()
        out = {}
        for market in KIWOOM_MARKETS:
            payload = None
            source = None
            if market_api is not None:
                payload = _kiwoom_flow_payload(market_api, market=market, scope=scope, base_dt=base_dt)
                source = "kiwoom_ka10063" if scope == "intraday" else "kiwoom_ka10066"
            if not payload and sector_api is not None:
                payload = _kiwoom_flow_payload(sector_api, market=market, scope=scope, base_dt=base_dt)
                source = "kiwoom_ka10051"
            parsed = parse_kiwoom_investor_flow_payload(payload, market=market)
            if parsed:
                row = parsed.get(market)
                if isinstance(row, dict):
                    row["source"] = source
                    row["scope"] = "market" if source != "kiwoom_ka10051" else "industry"
                    row["basis"] = "intraday" if scope == "intraday" else "after_hours"
                    row["base_dt"] = base_dt or None
                out.update(parsed)
        return out
    except Exception:
        return {}


def fetch_investor_flow() -> dict:
    file_value = _dict(fetch_snapshot_file().get("investor_flow"))
    return file_value or fetch_kiwoom_investor_flow()


def fetch_kis_k200_futures() -> dict | None:
    if not kis_futures_enabled():
        return None
    try:
        from providers import kis_quote

        return kis_quote.get_k200_futures()
    except Exception:
        return None


def fetch_k200_futures() -> dict | None:
    value = fetch_snapshot_file().get("k200_futures")
    if isinstance(value, dict):
        return value
    return fetch_kis_k200_futures()


def fetch_breadth() -> dict | None:
    value = fetch_snapshot_file().get("breadth") or fetch_snapshot_file().get("advancers_decliners")
    if isinstance(value, dict):
        return value
    return fetch_naver_breadth()


def fetch_fx() -> dict:
    file_value = _dict(fetch_snapshot_file().get("fx"))
    if file_value:
        return file_value
    if os.getenv("AGENT_CONSOLE_TOSS_FX_ENABLED", "0").lower() not in {"1", "true", "yes", "on"}:
        return {}
    try:
        from providers import toss_api

        rate = toss_api.exchange_rate("USD", "KRW")
        if rate:
            return {"usdkrw": {"rate": float(rate), "source": "toss_api"}}
    except Exception:
        return {}
    return {}


def collect_sources(fetchers: dict | None = None) -> tuple[dict, list[str]]:
    funcs = {
        "indices": fetch_indices,
        "investor_flow": fetch_investor_flow,
        "k200_futures": fetch_k200_futures,
        "breadth": fetch_breadth,
        "fx": fetch_fx,
    }
    funcs.update(fetchers or {})
    sources: dict = {}
    errors: list[str] = []
    for name, func in funcs.items():
        try:
            value = func()
            if value:
                sources[name] = value
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return sources, errors


def build_snapshot(
    now: datetime | None = None,
    sources: dict | None = None,
    fetchers: dict | None = None,
) -> dict:
    errors: list[str] = []
    if sources is None:
        sources, errors = collect_sources(fetchers)
    sources = _dict(sources)
    as_of = _asof(now)
    indices = normalize_indices(sources.get("indices"))
    investor_flow = normalize_investor_flow(sources.get("investor_flow"))
    k200_futures = normalize_futures(sources.get("k200_futures"))
    breadth = normalize_breadth(sources.get("breadth"))
    fx = normalize_fx(sources.get("fx"))
    out = {
        "schema": "kr-market-microstructure.v1",
        "ts": _ts(now),
        "max_age_s": int(os.getenv("KR_MARKET_MICROSTRUCTURE_STALE_S", "120")),
        "as_of": as_of,
        "source": "kr_microstructure",
        "indices": indices,
        "investor_flow": investor_flow,
        "k200_futures": k200_futures,
        "breadth": breadth,
        "fx": fx,
        "field_status": {},
        "errors": errors,
    }
    for name in FIELDS:
        ok = bool(out.get(name))
        raw = sources.get(name)
        source = "injected" if raw else "unavailable"
        if isinstance(raw, dict):
            source = str(raw.get("source") or raw.get("provider") or source)
        out["field_status"][name] = field_status(name, source, ok, as_of, None if ok else "missing")
    out["unavailable"] = [
        {"field": name, "reason": status.get("error") or "missing"}
        for name, status in out["field_status"].items()
        if not status.get("ok")
    ]
    return out
