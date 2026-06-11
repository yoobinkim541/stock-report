#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio_universe.py — 보유 종목 단일 소스 (Single Source of Truth)

모든 작업물(리포트·ML 파이프라인·주문서)은 보유 티커를 이 모듈에서 파생한다.

파이프라인:
  1. 현재 보유   — portfolio_snapshot.json (해외 일반 + 소수점 계좌)에서 파생
  2. 은퇴 티커   — 전량 청산 시 holding_manager.sell_holding() 이
                   retired_tickers.json 에 자동 기록
  3. 죽은 텍스트 — find_dead_ticker_mentions() 가 소스·런타임 설정에 남은
                   은퇴 티커 언급을 스캔. tests/bot_smoke_test.py 가 매일
                   09:00 KST 실행 → 발견 시 텔레그램 경보.

소스 라인에 의도적 언급이 필요하면 그 줄에 `ticker-ok` 주석을 달면 감사에서 제외.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime

PROJECT_DIR = os.getenv("STOCK_REPORT_PROJECT_DIR",
                        os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_SNAPSHOT_PATH = os.path.join(PROJECT_DIR, "portfolio_snapshot.json")
RETIRED_TICKERS_PATH = os.path.expanduser(
    "~/.local/share/stock-report/retired_tickers.json")

# 스냅샷을 읽을 수 없을 때의 폴백 — 실보유와 어긋나면 일일 감사가 잡는다
DEFAULT_PORTFOLIO_TICKERS = ["MSFT", "QQQI", "ORCL", "SAP", "UNH",
                             "SGOV", "NVDA", "GOOGL", "SPMO"]

# 전량 청산이 끝난 과거 보유 종목 (정적 시드 — retired_tickers.json 과 합산)
RETIRED_SEED = {"CPNG", "NOW", "CRM"}  # ticker-ok

# 보유 여부와 무관하게 전략·벤치마크로 코드에 상시 등장하는 티커 (감사 제외)
STRATEGY_TICKERS = {"QQQ", "QLD", "TQQQ", "UPRO", "SPY", "DIA", "SGOV",
                    "BIL", "SHV", "SHY", "VTI", "EFA", "TLT", "IEF",
                    "GLD", "DBC", "DBMF", "TMF"}

# 감사에서 제외할 경로 (디렉토리는 / 로 끝남)
_AUDIT_EXCLUDE = (
    "tests/",                    # 테스트 픽스처는 과거 티커 사용 가능
    "backtest/",                 # 과거 데이터 분석 스크립트
    "ml/universe.py",            # 시장 유니버스 (보유와 무관한 종목 스캔)
    "bot/attachment_parser.py",  # 과거 증권사 명세서 파싱용 이름맵
    "portfolio_universe.py",     # 이 파일 (RETIRED_SEED 정의)
)

# 보유 티커에서 파생되는 런타임 설정 파일 (은퇴 티커가 남으면 감사가 보고)
_RUNTIME_CONFIG_FILES = ("dca_weights.json", "target_weights.json",
                         "price_alerts.json")


def load_portfolio_tickers(path=PORTFOLIO_SNAPSHOT_PATH) -> list[str]:
    """portfolio_snapshot.json 에서 현재 보유 티커 목록을 파생."""
    try:
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception as exc:
        print(f"[WARN] {path} 로드 실패 ({exc}) — 기본 종목 목록으로 폴백",
              file=sys.stderr)
        return list(DEFAULT_PORTFOLIO_TICKERS)

    tickers = []
    for section, key in (("overseas_general", "holdings_usd"),
                         ("overseas_fractional", "holdings")):
        for h in snap.get(section, {}).get(key, []):
            ticker = h.get("ticker")
            shares = float(h.get("shares") or 0)
            value = float(h.get("value_usd") or 0)
            if ticker and (shares > 0 or value > 0) and ticker not in tickers:
                tickers.append(ticker)
    if not tickers:
        print(f"[WARN] {path} 에 보유 종목 없음 — 기본 종목 목록으로 폴백",
              file=sys.stderr)
        return list(DEFAULT_PORTFOLIO_TICKERS)
    return tickers


def _load_retired_file(path=RETIRED_TICKERS_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_retired_tickers(snapshot_path=PORTFOLIO_SNAPSHOT_PATH,
                         retired_path=RETIRED_TICKERS_PATH) -> set:
    """은퇴 티커 = (정적 시드 ∪ 런타임 기록) − 현재 보유 (재매수 시 자동 복귀)."""
    retired = set(RETIRED_SEED) | set(_load_retired_file(retired_path))
    return retired - set(load_portfolio_tickers(snapshot_path))


def record_retired_ticker(ticker: str, path=RETIRED_TICKERS_PATH) -> None:
    """전량 청산된 티커를 기록 (holding_manager.sell_holding 에서 호출)."""
    ticker = ticker.upper()
    data = _load_retired_file(path)
    data[ticker] = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def _source_files(project_dir: str) -> list:
    """감사 대상 소스 파일 목록 (git 추적 .py/.sh, 실패 시 os.walk 폴백)."""
    try:
        out = subprocess.run(
            ["git", "-C", project_dir, "ls-files", "*.py", "*.sh"],
            capture_output=True, text=True, timeout=30, check=True)
        files = [l for l in out.stdout.splitlines() if l.strip()]
        if files:
            return files
    except Exception:
        pass
    files = []
    skip_dirs = {".git", ".claude", "__pycache__", ".venv", "node_modules"}
    for root, dirs, names in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for n in names:
            if n.endswith((".py", ".sh")):
                files.append(os.path.relpath(os.path.join(root, n), project_dir))
    return files


def find_dead_ticker_mentions(project_dir: str = PROJECT_DIR,
                              retired=None) -> list:
    """
    소스 코드와 런타임 설정에 남은 은퇴 티커 언급을 찾아 보고.
    Returns: ["path:line TICKER — 내용" ...] (없으면 빈 리스트)
    """
    if retired is None:
        retired = load_retired_tickers()
    retired = set(retired) - STRATEGY_TICKERS
    if not retired:
        return []

    patterns = {t: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(t) + r"(?![A-Za-z0-9_])")
                for t in retired}
    findings = []

    for rel in _source_files(project_dir):
        if any(rel == ex or (ex.endswith("/") and rel.startswith(ex))
               for ex in _AUDIT_EXCLUDE):
            continue
        full = os.path.join(project_dir, rel)
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue
        for no, line in enumerate(lines, 1):
            if "ticker-ok" in line:
                continue
            for t, pat in patterns.items():
                if pat.search(line):
                    findings.append(f"{rel}:{no} {t} — {line.strip()[:80]}")

    # 런타임 설정 파일: 은퇴 티커가 키/값으로 남아있는지
    for name in _RUNTIME_CONFIG_FILES:
        cfg_path = os.path.join(project_dir, name)
        try:
            with open(cfg_path, encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        for t, pat in patterns.items():
            if pat.search(text):
                findings.append(f"{name} {t} — 런타임 설정에 은퇴 티커 잔존")

    return findings


if __name__ == "__main__":
    held = load_portfolio_tickers()
    retired = load_retired_tickers()
    print(f"보유 ({len(held)}): {', '.join(held)}")
    print(f"은퇴: {', '.join(sorted(retired)) or '없음'}")
    mentions = find_dead_ticker_mentions()
    if mentions:
        print(f"\n⚠️ 죽은 티커 언급 {len(mentions)}건:")
        for m in mentions:
            print(f"  {m}")
        sys.exit(1)
    print("✅ 죽은 티커 언급 없음")
