"""dashboard/pages/ — 퀀트 터미널 멀티페이지 (st.navigation). 각 모듈 render()."""


def leave_mpa_v1() -> None:
    """재기동 직후 첫 세션이 딥링크(/ticker 등)면 Streamlit 이 이 pages/ 폴더를 옛 MPA v1 로 보고
    페이지 파일을 직접 실행한다(빈 본문 + 영문 파일명 내비 + Deploy 버튼). 각 페이지 파일의
    `__main__` 가드가 이걸 불러 플래그를 내리고 app.py(st.navigation) 로 다시 실행한다.
    st.navigation 이 첫 실행에서 같은 플래그를 False 로 두므로 이후 세션은 영향 없다."""
    import streamlit as st
    from streamlit.runtime.pages_manager import PagesManager

    PagesManager.uses_pages_directory = False
    st.rerun()
