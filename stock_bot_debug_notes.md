# Stock Bot Debug Notes

반복 방지용 작업 메모. 스톡봇 수정 중 실제로 겪은 문제, 원인, 재발 방지 규칙을 기록한다.

## 2026-06-04 — 포트폴리오 텍스트 업데이트/명령 라우팅

### 1. `TICKER / N주 / $value / ±$pnl` 포맷이 저장되지 않음

#### 증상

사용자가 아래처럼 모바일 포트폴리오 텍스트를 보냈을 때 저장 핸들러가 동작하지 않았다.

```text
QQQI / 35.4069주 / $2,018.88 / +$126.35 (6.68%)
ORCL / 6.3112주 / $1,438.85 / +$297.59 (26.08%)
```

확인 결과:

```text
handled False
saved_count 0
```

#### 원인

`parse_portfolio_from_text()`는 일부 값을 읽을 수 있었지만 `detect_content_type()`이 이 형식을 `unknown`으로 판단했다.
기존 감지 로직은 `포트폴리오`, `보유`, `평단`, `portfolio` 같은 키워드에 의존했다.

모바일 텍스트는 키워드 없이 티커/수량/평가액/손익만 있어서 `/ask` 상담 흐름으로 빠졌다.

#### 재발 방지

- 키워드 기반 감지만 믿지 말 것.
- 알려진 티커 + `N주` + `$금액` 패턴이 여러 줄이면 포트폴리오로 감지해야 한다.
- 새 모바일 입력 포맷을 받으면 아래 둘을 모두 테스트한다.
  - `detect_content_type(text) == "portfolio"`
  - `handle_plain_text(text, chat_id) is True`

---

### 2. 평가액/손익은 있는데 평단이 없는 포맷

#### 증상

입력에는 아래 값이 있다.

- 수량
- 평가액
- 손익
- 수익률

하지만 평단가는 없다.

```text
QQQI / 35.4069주 / $2,018.88 / +$126.35 (6.68%)
```

#### 위험

apply 단계가 `avg_price_usd`를 기준으로 원금/손익/수익률을 다시 계산한다.
평단을 0으로 두고 반영하면 다음 값이 망가질 수 있다.

- `cost_usd`
- `avg_price_usd`
- `pnl_usd`
- `return_pct`

#### 필요한 계산

평단이 없고 평가액/손익이 있으면 다음처럼 역산한다.

```text
cost_usd = value_usd - pnl_usd
avg_price_usd = cost_usd / shares
current_price_usd = value_usd / shares
return_pct = pnl_usd / cost_usd * 100
```

예:

```text
value_usd = 2018.88
pnl_usd = 126.35
shares = 35.4069
cost_usd = 1892.53
avg_price_usd ~= 53.45
current_price_usd ~= 57.02
```

#### 재발 방지

- `+$126.35`, `-$0.23`처럼 부호가 있는 달러 금액은 손익으로 파싱해야 한다.
- `_parse_money()`를 고칠 때 파일 내 중복 정의 여부를 먼저 확인한다.
- 손익 파싱 테스트는 `pnl_usd`, `cost_usd`, `avg_price_usd`, `current_price_usd`를 함께 검증한다.

---

### 3. `/portpolio` 오타가 내부 기능으로 처리되지 않음

#### 증상

봇이 아래처럼 응답했다.

```text
❓ 모르는 명령어: /portpolio
/help 로 목록 확인
```

#### 원인

명령어 라우터가 정확한 command만 처리했고 흔한 오타 alias가 없었다.

#### 적용한 방향

흔한 오타는 LLM까지 보내지 말고 내부 alias에서 즉시 보정한다.

```python
BOT_COMMAND_ALIASES = {
    "/portpolio": "/portfolio",
    "/protfolio": "/portfolio",
    "/porfolio": "/portfolio",
}
```

#### 재발 방지

- 사용자에게 자주 보이는 오타는 alias 테스트를 먼저 추가한다.
- 테스트는 `dispatch("/portpolio", chat_id)`가 `/portfolio`와 같은 흐름을 타는지 확인한다.

---

### 4. 자연어 내부 기능 요청이 LLM 상담으로 빠짐

#### 증상

```text
포트폴리오 보여줘
```

위 문장이 `/ask 포트폴리오 보여줘`로 정규화되고, 실제 내부 `/portfolio` 기능이 아니라 LLM 상담 답변으로 처리됐다.

#### 원인

plain text는 기본적으로 `/ask`로 라우팅되지만, `/ask` 내부에서 봇 기능 의도를 다시 분류하는 단계가 없었다.

#### 적용한 방향

`/ask`로 들어온 텍스트라도 안전한 내부 기능 요청이면 LLM 호출 전에 내부 명령으로 전환한다.

허용 대상은 조회성/저위험 명령 위주 allowlist로 제한한다.

- `/portfolio`
- `/status`
- `/phase`
- `/dca`
- `/sgov`
- `/history`
- `/rebalance`
- `/order`
- `/help`

#### 재발 방지

- LLM에게 모든 봇 내부 기능 실행 권한을 주지 말 것.
- 상태 변경/파일 적용/알림 변경/주문성 기능은 자동 실행하지 말고 별도 확인 흐름을 둔다.
- 자연어 라우팅 테스트는 LLM 함수가 호출되지 않았는지도 검증한다.

---

### 5. 패치 중 `_parse_money` 중복 정의로 수정 실패

#### 증상

`_parse_money()`를 부호 지원으로 바꾸려다 patch가 실패했다.

```text
Found 2 matches for old_string. Provide more context to make it unique, or use replace_all=True.
```

#### 원인

파일 내 동일하거나 유사한 `_parse_money` 정의가 2개 있었다.
부분 문자열만으로 patch하면 어느 위치를 고칠지 모호하다.

#### 재발 방지

- patch 전 `search_files(pattern="def _parse_money")`로 정의 위치를 확인한다.
- 같은 이름/유사 코드가 여러 개면 line 주변 context를 읽고 고유한 old_string으로 patch한다.
- `replace_all=True`는 정말 모든 정의를 바꿔도 안전할 때만 사용한다.

---

## 작업 체크리스트

포트폴리오/텔레그램 봇 관련 수정 시 아래 순서로 확인한다.

1. 입력 포맷 재현 테스트 추가
2. `detect_content_type()` 결과 확인
3. 파서 결과 필드 확인
   - `ticker`
   - `shares`
   - `value_usd`
   - `pnl_usd` if present
   - `cost_usd` if computable
   - `avg_price_usd`
   - `current_price_usd`
4. `handle_plain_text()` pending 저장 확인
5. apply 후 `portfolio_snapshot.json` 값 확인
6. 명령 라우팅 변경 시 LLM 호출 여부까지 테스트
7. 전체 테스트 실행
8. 봇 재시작
9. 재시작 로그에서 `setMyCommands 완료` 확인

## 현재 상태

- 부호 있는 손익 금액 파싱 완료
- 평단 없는 포맷의 `cost_usd`/`avg_price_usd` 역산 적용 완료
- `portfolio_snapshot.json` 실제 적용 검증 완료
- 전체 테스트 통과 확인

---

## 2026-06-04 — `/ask` LLM 파일 편집 허용

### 증상

사용자가 `/ask` 또는 자연어로 포트폴리오/알림/비중 파일 수정을 요청해도 상담 LLM은 `hermes chat -q`만 호출되어 파일 도구가 없었다.

### 적용한 방향

`stock_advisor.py`에서 상담 LLM 호출 시 다음을 적용한다.

- 작업 디렉터리: stock-report 프로젝트 루트
- Hermes toolset: `file`만 허용
- 프롬프트에 편집 allowlist 명시

편집 허용 파일:

- `portfolio_snapshot.json`
- `price_alerts.json`
- `target_weights.json`
- `dca_weights.json`
- `leverage_state.json`

### 재발 방지

- LLM에 전체 repo 편집 권한을 열지 말 것.
- 코드 파일, `.env`, 토큰/시크릿 파일은 프롬프트에서 명시적으로 금지한다.
- 테스트에서 `--toolsets file`, `cwd=PROJECT_DIR`, allowlist 문구를 함께 검증한다.
- 수정 후 전체 테스트를 통과시키고 스톡봇을 재시작한다.

---

## 2026-06-04 — Telegram 명령어 메뉴 — scope 등록 + `/help` 자동화

### 증상

Telegram BotFather에 등록된 명령어 목록이 일부만 표시됨. `/help`에서도 `/dividend`, `/apply_snapshot` 등이 빠져 있었다.

### 원인

1. `configure_bot_commands()`가 `setMyCommands`를 기본 scope에만 1회 호출 — Telegram 클라이언트(안드로이드/iOS)가 scope별로 캐시를 다르게 관리해서 `all_private_chats` 같은 추가 scope에 명령어가 보이지 않았다.
2. `cmd_help()`가 하드코딩 문자열이라 신규 명령어 추가 시 누락되기 쉬운 구조였다.

### 수정 내용

- **`configure_bot_commands()`**: 3개 scope로 `setMyCommands` 호출
  - `None` (default)
  - `{"type": "all_private_chats"}`
  - `{"type": "all_chat_administrators"}`
- **`cmd_help()`**: `BOT_COMMANDS` 리스트에서 동적으로 자동 생성

### 재발 방지

- `BOT_COMMANDS`를 유일한 명령어 소스로 유지
- 신규 명령어를 추가할 때 반드시 `BOT_COMMANDS` 리스트에만 등록
- `cmd_help()`는 수동 수정 금지 (자동 생성)

---

## 2026-06-04 — WorldGovernmentBonds 중복 제거로 5Y만 저장되는 버그

### 증상

WorldGovernmentBonds가 5Y/10Y/20Y/30Y 모두 파싱은 했지만 캐시에는 5Y 3건만 저장됨.

### 원인

`event_id()`가 `url` 필드를 유일키 해시로 사용하는데, WGB 이벤트는 모든 만기가 같은 URL을 공유해서 `append_events()`의 중복 제거 로직이 첫 번째 이벤트(5Y)만 남기고 나머지를 버렸다.

### 수정 내용

- `source_collector.py` `fetch_world_gov_bond_events()`: 각 만기 URL에 `#5Y`, `#10Y`, `#20Y`, `#30Y` fragment 추가

### 재발 방지

- 같은 source에서 만기별로 여러 이벤트를 만들면 URL에 만기 fragment를 포함시킬 것
- `append_events` 중복 제거는 `event_id(row)`로 동작하므로 같은 `url`을 공유하는 이벤트는 반드시 `url`에 구분자를 붙일 것

---

## 2026-06-04 — advisor 프롬프트에 거시/미시 + 낙관/비관/중립 관점 추가

### 수정 내용

`stock_advisor.py` `build_advisor_prompt()`:
- `[최근 신뢰 소스 요약]` 아래에 데이터 활용 가이드 추가 (FRED 국채 곡선, WorldGovernmentBonds 스프레드, 섹터 ETF, Fear & Greed)
- `[거시 평가 지침]` 섹션 신설: 거시적 관점(통화정책, 채권시장)과 미시적 관점(섹터, 개별 종목)을 모두 포함
- 비관론/낙관론/중립 시나리오를 각각 1~2문장으로 요약하도록 프롬프트 강제
- 답변 형식 변경: `1. 결론 (낙관/비관/중립 시나리오 포함)`, `2. 근거 (거시 + 미시 각각)`, `3. 실행 시 주의점`

---

## 2026-06-05 — QQQ NaN OHLC로 Phase 5 -100% 오발송

### 증상

Phase 5 크래시 에스컬레이션이 `QQQ 고점 대비 -100.0%`로 3회 발송됐다. 이후 smoke test도 `fetch_qqq_data: current price > 0`에서 실패했다.

### 원인

`yfinance`가 최신 QQQ 행에 `Volume`만 넣고 `Open/High/Low/Close`는 `NaN`으로 반환했다. 기존 `fetch_qqq_data()`는 마지막 행의 `Close`를 그대로 읽었고, `_safe_float(NaN)`이 `0.0`을 반환하면서 `drawdown_pct = -100%`가 됐다.

### 수정 내용

- `fetch_qqq_data()`는 `High/Low/Close`가 모두 유효하고 0보다 큰 행만 사용한다.
- 유효 OHLC 행이 없거나 `current/high/low`가 비정상이면 `{}`를 반환한다.
- `classify_market()`은 `current <= 0`, `high_52w <= 0`, `drawdown <= -80`인 비정상 QQQ 데이터를 `neutral/0`으로 처리한다.
- `notify_phase_change()`도 같은 가드로 Phase 알림 발송 직전에 한 번 더 차단한다.

### 재발 방지

- 시장 데이터 smoke 실패는 commit/push/restart 전에 반드시 해결한다.
- `drawdown <= -80` 같은 비현실적 값은 자동 주문/Phase 알림에 사용하지 않는다.
- yfinance 최신 행은 `Volume`만 있고 OHLC가 `NaN`일 수 있으므로 마지막 행을 맹신하지 않는다.

---
