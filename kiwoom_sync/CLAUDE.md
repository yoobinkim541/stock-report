# kiwoom_sync — 키움 Open API+ 잔고 동기화 (Windows)

## 역할
키움증권 해외주식 잔고(일반 + 소수점 계좌)를 매일 아침 Oracle Ubuntu 서버로 전송.
서버의 `portfolio_sync_server.py`가 수신 → `portfolio_snapshot.json` 자동 업데이트.

## 환경 요구사항

| 항목 | 값 |
|------|-----|
| OS | Windows (필수) |
| Python | **32bit (x86)** 필수 — 키움 COM API 제한 |
| 키움 Open API+ | HTS에서 신청 완료 후 설치 |
| KOA Studio | Open API+ 설치 시 함께 설치됨 |

## 첫 실행 순서

```
1. 32bit Python 설치
   https://www.python.org/downloads/ → "Windows installer (32-bit)" 다운로드

2. 패키지 설치 (32bit Python으로)
   C:\Python312-32\python.exe -m pip install -r requirements.txt

3. .env 설정
   cp .env.example .env
   → KIWOOM_ACCOUNT_GENERAL, KIWOOM_ACCOUNT_FRACTIONAL, KIWOOM_PASSWORD 입력
   → SYNC_TOKEN은 Ubuntu 서버 .env의 SYNC_TOKEN과 동일하게

4. TR 코드 확인 (핵심)
   → 아래 "TR 코드 확인" 섹션 참조

5. 테스트 실행
   C:\Python312-32\python.exe kiwoom_sync.py
```

## TR 코드 확인 방법 (필수)

`kiwoom_sync.py` 상단의 TODO 값들을 KOA Studio에서 확인해야 합니다.

```
KOA Studio 실행
  → 좌측 "TR목록" 탭
  → 검색창에 "해외" 입력
  → 해외주식 잔고 조회 TR 클릭
  → 하단 "입력데이터" 탭: INPUT_ACCOUNT, INPUT_PASSWORD 등 필드명 확인
  → 하단 "출력데이터(멀티)" 탭: FIELD_TICKER, FIELD_SHARES 등 필드명 확인
```

확인 후 `kiwoom_sync.py` 상단의 이 부분을 교체:
```python
TR_OVERSEAS_BALANCE = "opw07012"   # ← 실제 TR 코드
FIELD_TICKER = "종목코드"           # ← 실제 출력 필드명
FIELD_NAME   = "종목명"
FIELD_SHARES = "보유수량"
FIELD_AVG    = "매입평균가"
FIELD_CURR   = "현재가격"
```

## Task Scheduler 설정

```
작업 스케줄러 열기 (taskschd.msc)
  → 기본 작업 만들기
  → 이름: 키움잔고동기화
  → 트리거: 매일 08:30
             ☑ 예약된 시간을 놓친 경우 가능한 빨리 시작  ← 절전모드 대응
  → 동작: 프로그램 시작
           프로그램: C:\path\to\kiwoom_sync\run_sync.bat
  → 조건: ☑ 컴퓨터가 AC 전원에 연결된 경우에만 시작 (선택사항)
```

## 파일 구조

```
kiwoom_sync/
├── kiwoom_sync.py     ← 메인 동기화 스크립트
├── run_sync.bat       ← Task Scheduler 진입점
├── requirements.txt   ← 의존성 (32bit pip으로 설치)
├── .env               ← 로컬 전용, 커밋 금지
└── .env.example       ← 템플릿
```

## Ubuntu 서버 연결 구조

```
kiwoom_sync.py
  → POST http://서버IP:8765/sync
  → Authorization: Bearer {SYNC_TOKEN}
  → Body: { overseas_general: [...], overseas_fractional: [...] }

Ubuntu: portfolio_sync_server.py
  → portfolio_snapshot.json 업데이트
  → 텔레그램 알림 발송
```

## 디버깅

잔고가 0개로 조회될 때:
1. `sync.log` 확인 (run_sync.bat 실행 로그)
2. TR 코드/필드명이 맞는지 KOA Studio 재확인
3. 키움 HTS가 로그인된 상태인지 확인 (Open API+는 HTS 실행 불필요하지만 첫 로그인 필요)

서버 연결 실패 시:
1. Oracle Cloud 방화벽 Ingress rule 8765 포트 확인
2. `curl http://서버IP:8765/health` 로 서버 동작 확인
