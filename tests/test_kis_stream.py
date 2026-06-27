#!/usr/bin/env python3
"""test_kis_stream.py — KIS 실시간 WS 순수함수 (무네트워크·폐형해)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kis_stream as ks


def _fields(n, overrides):
    f = ["0"] * n
    for i, v in overrides.items():
        f[i] = str(v)
    return f


# ── 도메인 하드락 ─────────────────────────────────────────────────────────────

def test_assert_ws_url():
    ks._assert_ws_url(ks._WS_REAL)
    with pytest.raises(RuntimeError):
        ks._assert_ws_url("ws://ops.koreainvestment.com:31000")   # 모의 WS 거부
    with pytest.raises(RuntimeError):
        ks._assert_ws_url("ws://evil.example.com:21000")


# ── build_subscribe ───────────────────────────────────────────────────────────

def test_build_subscribe_register_and_unregister():
    reg = json.loads(ks.build_subscribe("AK", "H0STCNT0", "005930", register=True))
    assert reg["header"]["approval_key"] == "AK" and reg["header"]["tr_type"] == "1"
    assert reg["header"]["custtype"] == "P"
    assert reg["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "005930"}
    unreg = json.loads(ks.build_subscribe("AK", "H0STASP0", "005930", register=False))
    assert unreg["header"]["tr_type"] == "2"


# ── handle_pingpong ───────────────────────────────────────────────────────────

def test_handle_pingpong():
    ping = json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "x"}})
    assert ks.handle_pingpong(ping) == ping            # echo
    ack = json.dumps({"header": {"tr_id": "H0STCNT0"}, "body": {"rt_cd": "0"}})
    assert ks.handle_pingpong(ack) is None             # 일반 ACK 아님
    assert ks.handle_pingpong("0|H0STCNT0|001|005930^...") is None   # 데이터프레임
    assert ks.handle_pingpong("") is None


# ── parse_realtime_frame ──────────────────────────────────────────────────────

def test_parse_kr_trade():
    payload = "^".join(_fields(46, {0: "005930", 2: "71000", 13: "12345678"}))
    recs = ks.parse_realtime_frame(f"0|H0STCNT0|001|{payload}")
    assert recs == [{"symbol": "005930", "kind": "trade", "price": 71000.0, "volume": 12345678.0}]


def test_parse_kr_ask_levels_and_best():
    ov = {0: "005930", 3: "71100", 4: "71200", 13: "70900", 14: "70800",
          23: "10", 24: "20", 33: "5", 34: "7"}
    payload = "^".join(_fields(59, ov))
    recs = ks.parse_realtime_frame(f"0|H0STASP0|001|{payload}")
    r = recs[0]
    assert r["best_ask"] == 71100.0 and r["best_bid"] == 70900.0
    assert r["asks"][:2] == [(71100.0, 10.0), (71200.0, 20.0)]
    assert r["bids"][:2] == [(70900.0, 5.0), (70800.0, 7.0)]


def test_parse_multi_record_trade():
    r0 = _fields(46, {0: "005930", 2: "71000", 13: "100"})
    r1 = _fields(46, {0: "000660", 2: "120000", 13: "200"})
    payload = "^".join(r0 + r1)
    recs = ks.parse_realtime_frame(f"0|H0STCNT0|002|{payload}")
    assert [r["symbol"] for r in recs] == ["005930", "000660"]
    assert recs[1]["price"] == 120000.0


def test_parse_us_trade_hdfscnt0():
    """美 체결 HDFSCNT0 — SYMB[0]·LAST[10]·TVOL[19] (open-trading-api 공식 컬럼)."""
    payload = "^".join(_fields(25, {0: "AAPL", 10: "283.78", 19: "1000000"}))
    recs = ks.parse_realtime_frame(f"0|HDFSCNT0|001|{payload}")
    assert recs == [{"symbol": "AAPL", "kind": "trade", "price": 283.78, "volume": 1000000.0}]


def test_parse_us_ask_hdfsasp0():
    """美 호가 HDFSASP0 — PBID1[10]·PASK1[11]·VBID1[12]·VASK1[13] (1호가)."""
    payload = "^".join(_fields(16, {0: "AAPL", 10: "283.50", 11: "283.80", 12: "100", 13: "200"}))
    recs = ks.parse_realtime_frame(f"0|HDFSASP0|001|{payload}")
    r = recs[0]
    assert r["best_bid"] == 283.50 and r["best_ask"] == 283.80
    assert r["bids"] == [(283.50, 100.0)] and r["asks"] == [(283.80, 200.0)]


def test_us_ws_key_format():
    assert ks._us_ws_key("AAPL") == "DNASAAPL"     # 나스닥
    assert ks._us_ws_key("ORCL") == "DNYSORCL"     # 뉴욕
    assert ks._us_ws_key("SGOV") == "DAMSSGOV"     # 아멕스


def test_norm_us_symbol_strips_market_prefix():
    assert ks._norm_us_symbol("DNASAAPL") == "AAPL"
    assert ks._norm_us_symbol("RBAQAAPL") == "AAPL"   # 주간거래 접두
    assert ks._norm_us_symbol("AAPL") == "AAPL"       # 접두 없으면 그대로


def test_parse_rejects_encrypted_control_unknown():
    assert ks.parse_realtime_frame("1|H0STCNI0|001|enc...") == []     # 암호화 체결통보 미처리
    assert ks.parse_realtime_frame('{"header":{"tr_id":"PINGPONG"}}') == []  # JSON 제어
    assert ks.parse_realtime_frame("0|UNKNOWN9|001|a^b^c") == []       # 미지원 tr_id
    assert ks.parse_realtime_frame("") == []


# ── 체결통보 (T7) ─────────────────────────────────────────────────────────────

def test_decrypt_notice_roundtrip():
    """AES-256-CBC 복호화 — 알려진 key/iv 로 암호화→복호화 왕복."""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    from base64 import b64encode
    key, iv = "0" * 32, "0" * 16
    plain = "CUST^ACNT^ODER^005930^02^10^71000"
    ct = AES.new(key.encode(), AES.MODE_CBC, iv.encode()).encrypt(pad(plain.encode(), AES.block_size))
    assert ks.decrypt_notice(key, iv, b64encode(ct).decode()) == plain


def test_parse_notice_kr_fill_only():
    f = _fields(14, {4: "02", 8: "005930", 9: "10", 10: "71000", 11: "0930", 13: "2"})
    out = ks.parse_notice("^".join(f), ks.TR_KR_FILL)
    assert out == [{"symbol": "005930", "side": "buy", "qty": 10.0, "price": 71000.0,
                    "time": "0930", "tr_id": "H0STCNI0"}]
    f[13] = "1"                                   # 접수통보 → 제외
    assert ks.parse_notice("^".join(f), ks.TR_KR_FILL) == []


def test_parse_notice_us_fill_indices():
    """US H0GSCNI0 — symbol[7]·qty[8]·price[9]·cntg_yn[12] (KR 과 다른 인덱스)."""
    f = _fields(13, {4: "01", 7: "AAPL", 8: "5", 9: "283.78", 10: "2230", 12: "2"})
    out = ks.parse_notice("^".join(f), ks.TR_US_FILL)
    assert out == [{"symbol": "AAPL", "side": "sell", "qty": 5.0, "price": 283.78,
                    "time": "2230", "tr_id": "H0GSCNI0"}]


def test_parse_subscribe_ack_extracts_key_iv():
    ack = json.dumps({"header": {"tr_id": "H0STCNI0"}, "body": {"output": {"iv": "IV16", "key": "KEY32"}}})
    assert ks.parse_subscribe_ack(ack) == {"tr_id": "H0STCNI0", "key": "KEY32", "iv": "IV16"}
    assert ks.parse_subscribe_ack(json.dumps({"header": {"tr_id": "H0STASP0"}, "body": {"rt_cd": "0"}})) is None
    assert ks.parse_subscribe_ack("0|H0STCNT0|001|x") is None    # 데이터프레임 아님


# ── 읽기전용 구조 불변 ────────────────────────────────────────────────────────

def test_module_has_no_order_path():
    src = open(ks.__file__, encoding="utf-8").read()
    for forbidden in ("place_order", "/trading/order", "ORD_QTY", "OVRS_ORD_UNPR",
                      "VTTT", "kt10000", "kt10001"):
        assert forbidden not in src, f"읽기전용 위반: '{forbidden}'"
    assert src.count("requests.post") == 1     # approval 발급만


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
