#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
attachment_parser.py — 텔레그램 첨부파일 파서
PDF / 이미지에서 포트폴리오 현황·매도내역 추출 및 임시 보관
"""

import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR            = Path.home() / ".local" / "share" / "stock-report"
ATTACH_DIR          = DATA_DIR / "attachments"
PENDING_SNAPSHOT_FILE = DATA_DIR / "pending_snapshot.json"
PENDING_SELLS_FILE    = DATA_DIR / "pending_sells.json"

PENDING_TTL_HOURS = 72  # pending 파일 자동 만료

# 알려진 티커 → 회사명 매핑
KNOWN_TICKERS: dict[str, str] = {
    "MSFT":  "마이크로소프트",
    "QQQI":  "나스닥100 고배당 네오스 ETF",
    "ORCL":  "오라클",
    "NOW":   "서비스나우",
    "CRM":   "세일스포스",
    "SAP":   "SAP SE",
    "UNH":   "유나이티드헬스그룹",
    "SGOV":  "미국 초단기 국채 ETF",
    "CPNG":  "쿠팡",
    "NVDA":  "엔비디아",
    "GOOGL": "알파벳 A",
    "SPMO":  "S&P500 모멘텀 ETF",
}


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────
#  텍스트 추출
# ──────────────────────────────────────────────────────────

def extract_text_from_pdf(path: str) -> str | None:
    """PDF에서 텍스트 추출 (pypdf)."""
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        text = "\n".join(parts)
        return text if text.strip() else None
    except Exception as e:
        logger.warning(f"PDF 텍스트 추출 실패: {e}")
        return None


def extract_text_from_image(path: str) -> str | None:
    """이미지에서 OCR (tesseract kor+eng, subprocess 직접 호출)."""
    out_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            out_base = tf.name[:-4]
        out_file = out_base + ".txt"
        subprocess.run(
            ["tesseract", path, out_base, "-l", "kor+eng", "--psm", "6"],
            capture_output=True, text=True, timeout=30,
        )
        if os.path.exists(out_file):
            with open(out_file, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            return text if text.strip() else None
    except FileNotFoundError:
        logger.warning("tesseract 바이너리 없음 — OCR 불가")
    except Exception as e:
        logger.warning(f"OCR 실패: {e}")
    finally:
        if out_file and os.path.exists(out_file):
            os.unlink(out_file)
    return None


# ──────────────────────────────────────────────────────────
#  파싱 공통
# ──────────────────────────────────────────────────────────

_NUM_RE  = re.compile(r'[\d,]+(?:\.\d+)?')
_DATE_RE = re.compile(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})')


def _parse_nums(line: str) -> list[float]:
    return [float(n.replace(',', '')) for n in _NUM_RE.findall(line)]


def _find_ticker(line: str) -> str | None:
    upper = line.upper()
    for t in KNOWN_TICKERS:
        if re.search(r'(?<![A-Z])' + t + r'(?![A-Z])', upper):
            return t
    return None


def detect_content_type(text: str, caption: str = "") -> str:
    """
    텍스트 내용에서 '포트폴리오 현황' vs '매도내역' 자동 감지.
    반환: "portfolio" | "sell" | "unknown"
    """
    hint = (caption + " " + text[:500]).lower()
    sell_score = sum(1 for k in ["매도", "sell", "거래", "체결", "양도", "처분"] if k in hint)
    port_score = sum(1 for k in ["보유", "잔고", "현황", "평균", "평단", "portfolio", "계좌"] if k in hint)

    if sell_score > port_score:
        return "sell"
    if port_score > sell_score:
        return "portfolio"
    return "unknown"


# ──────────────────────────────────────────────────────────
#  포트폴리오 파싱
# ──────────────────────────────────────────────────────────

def parse_portfolio_from_text(text: str) -> list[dict]:
    """
    텍스트에서 보유 현황 파싱.
    각 줄에서 티커 + 수량(주수) + 평단가 + 현재가 순서로 추출.
    반환: [{"ticker","name","shares","avg_price_usd","current_price_usd"}, ...]
    """
    results: list[dict] = []
    seen: set[str] = set()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        ticker = _find_ticker(line)
        if not ticker or ticker in seen:
            continue

        nums = _parse_nums(line)
        # 날짜 숫자 제거
        for dm in _DATE_RE.finditer(line):
            date_ints = {int(dm.group(i)) for i in range(1, 4)}
            nums = [x for x in nums if x not in date_ints]

        if not nums:
            continue

        shares       = nums[0]
        avg_price    = nums[1] if len(nums) > 1 else 0.0
        current_price = nums[2] if len(nums) > 2 else avg_price

        seen.add(ticker)
        results.append({
            "ticker":            ticker,
            "name":              KNOWN_TICKERS[ticker],
            "shares":            round(shares, 4),
            "avg_price_usd":     round(avg_price, 4),
            "current_price_usd": round(current_price, 4),
        })

    return results


# ──────────────────────────────────────────────────────────
#  매도내역 파싱
# ──────────────────────────────────────────────────────────

def parse_sells_from_text(text: str) -> list[dict]:
    """
    텍스트에서 매도내역 파싱.
    각 줄: 날짜? 티커 수량 매수단가 매도단가 순서로 추출.
    반환: [{"date","ticker","name","qty","buy_price_usd","sell_price_usd"}, ...]
    """
    results: list[dict] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        ticker = _find_ticker(line)
        if not ticker:
            continue

        dm = _DATE_RE.search(line)
        if dm:
            date_str = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")

        nums = _parse_nums(line)
        if dm:
            date_ints = {int(dm.group(i)) for i in range(1, 4)}
            nums = [x for x in nums if x not in date_ints]

        if len(nums) < 3:
            continue

        results.append({
            "date":           date_str,
            "ticker":         ticker,
            "name":           KNOWN_TICKERS[ticker],
            "qty":            round(nums[0], 4),
            "buy_price_usd":  round(nums[1], 4),
            "sell_price_usd": round(nums[2], 4),
        })

    return results


# ──────────────────────────────────────────────────────────
#  Pending 파일 관리
# ──────────────────────────────────────────────────────────

def _is_expired(parsed_at: str) -> bool:
    try:
        return datetime.now() - datetime.fromisoformat(parsed_at) > timedelta(hours=PENDING_TTL_HOURS)
    except Exception:
        return False


def save_pending_snapshot(holdings: list[dict]):
    _ensure_dir()
    PENDING_SNAPSHOT_FILE.write_text(
        json.dumps({"parsed_at": datetime.now().isoformat(), "holdings": holdings},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_pending_snapshot() -> dict | None:
    if not PENDING_SNAPSHOT_FILE.exists():
        return None
    try:
        data = json.loads(PENDING_SNAPSHOT_FILE.read_text(encoding="utf-8"))
        if _is_expired(data.get("parsed_at", "")):
            PENDING_SNAPSHOT_FILE.unlink()
            return None
        return data
    except Exception:
        return None


def clear_pending_snapshot():
    if PENDING_SNAPSHOT_FILE.exists():
        PENDING_SNAPSHOT_FILE.unlink()


def save_pending_sells(sells: list[dict]):
    _ensure_dir()
    PENDING_SELLS_FILE.write_text(
        json.dumps({"parsed_at": datetime.now().isoformat(), "sells": sells},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_pending_sells() -> dict | None:
    if not PENDING_SELLS_FILE.exists():
        return None
    try:
        data = json.loads(PENDING_SELLS_FILE.read_text(encoding="utf-8"))
        if _is_expired(data.get("parsed_at", "")):
            PENDING_SELLS_FILE.unlink()
            return None
        return data
    except Exception:
        return None


def clear_pending_sells():
    if PENDING_SELLS_FILE.exists():
        PENDING_SELLS_FILE.unlink()


# ──────────────────────────────────────────────────────────
#  요약 메시지 빌더
# ──────────────────────────────────────────────────────────

def build_pending_snapshot_summary(pending: dict) -> str:
    holdings  = pending.get("holdings", [])
    parsed_at = pending.get("parsed_at", "")[:16]
    SEP = "─" * 44
    lines = [
        f"📋 포트폴리오 스냅샷 파싱 결과  ({parsed_at})",
        SEP,
        f"{'티커':<7} {'회사명':<16} {'주수':>7} {'평단가':>9} {'현재가':>9}",
        SEP,
    ]
    for h in holdings:
        lines.append(
            f"{h['ticker']:<7} {h['name'][:14]:<16} {h['shares']:>7.4f} "
            f"${h['avg_price_usd']:>7.2f} ${h['current_price_usd']:>7.2f}"
        )
    lines += [
        SEP,
        f"총 {len(holdings)}종목 인식",
        "",
        "✅ 적용: /apply_snapshot",
        "❌ 취소: 무시 (72시간 자동 만료)",
    ]
    return "\n".join(lines)


def build_pending_sells_summary(pending: dict) -> str:
    sells     = pending.get("sells", [])
    parsed_at = pending.get("parsed_at", "")[:16]
    SEP = "─" * 44
    lines = [
        f"📋 매도내역 파싱 결과  ({parsed_at})",
        SEP,
    ]
    for s in sells:
        gain = (s["sell_price_usd"] - s["buy_price_usd"]) * s["qty"]
        sg   = "▲" if gain >= 0 else "▼"
        lines.append(
            f"{s['date']}  {s['ticker']} ({s['name']})  {s['qty']}주\n"
            f"  매수 ${s['buy_price_usd']:.2f} → 매도 ${s['sell_price_usd']:.2f}"
            f"  {sg}${abs(gain):,.2f}"
        )
    lines += [
        SEP,
        f"총 {len(sells)}건",
        "",
        "✅ 세금 기록 반영: /tax import apply",
        "❌ 취소: 무시 (72시간 자동 만료)",
    ]
    return "\n".join(lines)
