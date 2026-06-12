#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
conftest.py — pytest 전역 픽스처

store(SQLite 통합 저장소)를 격리된 임시 DB로 강제 → 테스트가 실제
~/.local/share/stock-report/stock_report.db (라이브 phase·anchor·세금 등)에
쓰지 않도록 보호한다. (store를 사용하는 core 코드가 테스트에서 실행돼도 안전.)

autouse 세션 픽스처: 테스트 수집 전에 STOCK_REPORT_DB 를 tmp 경로로 설정.
"""
import os
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_store_db():
    tmpdir = tempfile.mkdtemp(prefix="stock_report_test_db_")
    prev = os.environ.get("STOCK_REPORT_DB")
    os.environ["STOCK_REPORT_DB"] = os.path.join(tmpdir, "test.db")

    # 이미 import된 store 의 스키마 초기화 캐시 리셋 (새 DB 경로 반영)
    try:
        import store
        store._initialized.clear()
    except Exception:
        pass

    yield

    if prev is None:
        os.environ.pop("STOCK_REPORT_DB", None)
    else:
        os.environ["STOCK_REPORT_DB"] = prev
