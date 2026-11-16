"""출처의 발행자와 관점. 수집 분류, 위키 귀속 문구, 온톨로지의 독립 출처 계산이 같은 표를 쓴다.

관점(role)은 글쓴이가 어떤 자리에서 말하는지다. 증권사 리서치는 사실 전달과 자사 견해가 섞이므로
커뮤니티 글과도, 언론 보도와도 따로 센다. 발행자 id 는 채널이 아니라 기관 단위다: 같은 증권사의
텔레그램 글과 리포트 PDF 는 한 출처로 본다.
"""
from __future__ import annotations

ROLES: dict[str, str] = {
    "sell_side": "증권사 리서치",
    "community": "커뮤니티·전달 채널",
    "news": "언론",
    "official": "공식 통계·시장 데이터",
    "prediction_market": "예측시장",
    "other": "기타",
}

# 공개 미리보기(t.me/s/<채널>)가 열려 있는 채널만. 순서가 수집 순서다.
TELEGRAM_CHANNELS: dict[str, tuple[str, str]] = {
    "insidertracking": ("미국 주식 인사이더", "community"),       # 미국 속보·레딧 분석 전달
    "kwusa": ("키움증권", "sell_side"),                          # 미국주식 톡톡
    "mirae_dm": ("미래에셋증권", "sell_side"),                    # 선진국 전략 (김성근)
    "Samsung_Global_AI_SW": ("삼성증권", "sell_side"),           # 글로벌 AI/SW (이영진)
    "FastStockNewsUSA": ("급등일보 미국주식", "community"),        # 미국 뉴스 속보 전달
    "daishinstrategy": ("대신증권", "sell_side"),                # 전략 (이경민)
    "yuantais": ("유안타증권", "sell_side"),                      # 투자전략
    "shStrategy": ("신한투자증권", "sell_side"),                  # 주식전략·시황·퀀트
    "thekobeissiletter": ("The Kobeissi Letter", "community"),  # 영문 매크로 코멘트
}

# 증권사가 채널 글에 걸어 두는 공개 리포트 PDF 의 최종 호스트. 여기 없는 곳의 파일은 받지 않는다.
REPORT_HOSTS = frozenset({
    "bbn.kiwoom.com", "money2.daishin.com", "file.myasset.com", "bbs2.shinhansec.com",
    "img.securities.miraeasset.com", "securities.miraeasset.com",
})
# 위 호스트로 넘겨 주는 단축 주소. 리다이렉트 응답만 읽고, 목적지가 REPORT_HOSTS 가 아니면 따라가지 않는다.
LINK_SHORTENERS = frozenset({"r.jayjoai.com", "buly.kr", "han.gl"})

_TELEGRAM = {channel.lower(): (channel, name, role) for channel, (name, role) in TELEGRAM_CHANNELS.items()}
_ROOTS: dict[str, tuple[str, str]] = {
    "arca": ("아카라이브", "community"),
    "saveticker": ("SaveTicker", "news"),
    "saveticker_report_pdf": ("SaveTicker", "news"),
    "yahoo_headline": ("Yahoo Finance", "news"),
    "yahoo_finance": ("Yahoo Finance 시세", "official"),
    "fred": ("FRED", "official"),
    "worldgovernmentbonds": ("World Government Bonds", "official"),
    "economic_calendar": ("경제 캘린더", "official"),
    "polymarket": ("Polymarket", "prediction_market"),
    "kalshi": ("Kalshi", "prediction_market"),
}


def publisher(source: object) -> dict[str, str]:
    """``telegram:kwusa`` 같은 출처 이름 → ``{"id", "name", "role"}``. 모르는 출처도 항상 값을 돌려준다."""
    text = str(source or "").strip()
    root, _, rest = text.partition(":")
    root = root.lower()
    if root in {"telegram", "broker_report"}:
        known = _TELEGRAM.get(rest.lower())
        if known:
            return {"id": known[1], "name": known[1], "role": known[2]}
        return {"id": f"telegram:{rest.lower()}", "name": rest or "telegram", "role": "community"}
    if root == "rss":
        return {"id": f"rss:{rest.lower()}", "name": rest or "RSS", "role": "news"}
    name, role = _ROOTS.get(root, (text or "unknown", "other"))
    return {"id": name, "name": name, "role": role}


def sell_side_channels() -> list[str]:
    return [channel for channel, (_name, role) in TELEGRAM_CHANNELS.items() if role == "sell_side"]
