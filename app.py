"""
어업·농업 날씨 챗봇 - 웹 버전 (Streamlit)

실행:  streamlit run app.py
chatbot.py, .env 와 같은 폴더에 두세요.
"""

import importlib
import os

import streamlit as st

import chatbot

# chatbot.py를 고치면 Streamlit을 껐다 켜지 않아도 새 코드를 쓰도록 다시 불러옴
_mtime = os.path.getmtime(chatbot.__file__)
if getattr(chatbot, "_loaded_mtime", None) != _mtime:
    chatbot = importlib.reload(chatbot)
    chatbot._loaded_mtime = _mtime

st.set_page_config(page_title="어업·농업 날씨 챗봇", page_icon="🌊")
st.title("🌊🌾 어업·농업 날씨 챗봇")
st.caption("출항해도 되는지, 농약 쳐도 되는지 물어보세요. 기상청 단기예보·기상특보 기준")

if not chatbot.SERVICE_KEY:
    st.error(".env 파일에 KMA_SERVICE_KEY=발급받은키 를 넣어주세요.")
    st.stop()

# 예시 질문 버튼
EXAMPLES = ["오늘 목포 출항해도 돼?", "성산 풍랑주의보 내렸어?", "내일 무안 농약 쳐도 돼?", "모레 해남 밭일 날씨"]

if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "안녕하세요! 알아듣는 장소: " + ", ".join(p[0] for p in chatbot.PLACES)}
    ]

cols = st.columns(len(EXAMPLES))
clicked = None
for col, ex in zip(cols, EXAMPLES):
    if col.button(ex, use_container_width=True):
        clicked = ex

# 지난 대화 보여주기 (줄바꿈이 그대로 보이도록 끝에 공백 2칸)
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"].replace("\n", "  \n"))

question = st.chat_input("예) 내일 새벽 5시 목포 출항해도 돼?") or clicked
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("기상청 자료 확인 중..."):
            try:
                reply = chatbot.answer(question)
            except Exception as e:
                reply = f"문제가 생겼어요: {e}"
        st.markdown(reply.replace("\n", "  \n"))
    st.session_state.messages.append({"role": "assistant", "content": reply})
