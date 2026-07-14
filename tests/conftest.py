#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
conftest.py — pytest 전역 설정

store(SQLite 통합 저장소)를 격리된 임시 DB로 강제 → 테스트가 실제
~/.local/share/stock-report/stock_report.db (라이브 phase·anchor·세금 등)에
쓰지 않도록 보호한다. (store를 사용하는 core 코드가 테스트에서 실행돼도 안전.)

중요: 환경변수 설정은 **모듈 레벨**(conftest import 시점)에서 수행한다.
pytest는 테스트 모듈 수집(import)보다 conftest를 먼저 import하므로, barbell_strategy
같은 모듈이 import 시점에 load_dca_weights() 등으로 store를 건드려도 이미 tmp DB를
가리킨다. (세션 픽스처는 수집 이후 실행되어 import-time 접근을 놓침.)
"""
import os
import tempfile

# ── import 시점에 즉시 격리 (테스트 모듈 수집 전) ────────────────────────
_TMPDIR = tempfile.mkdtemp(prefix="stock_report_test_db_")
os.environ["STOCK_REPORT_DB"] = os.path.join(_TMPDIR, "test.db")

# 공유 에이전트 메모리도 tmp 격리 — 테스트가 라이브 노트북/이벤트를 오염하지 않게
os.environ.setdefault("AGENT_MEMORY_DIR", os.path.join(_TMPDIR, "shared-memory"))
