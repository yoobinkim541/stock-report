@echo off
:: 키움 잔고 동기화 — Task Scheduler에서 이 파일을 실행
:: 32bit Python 경로를 본인 환경에 맞게 수정하세요
:: 기본 경로: C:\Python312-32\python.exe

set PYTHON32=C:\Python312-32\python.exe
set SCRIPT_DIR=%~dp0

cd /d "%SCRIPT_DIR%"
"%PYTHON32%" kiwoom_sync.py >> sync.log 2>&1
