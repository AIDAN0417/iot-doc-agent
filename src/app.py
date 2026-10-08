"""Single-file, entirely local Streamlit ESP32 documentation chat."""

from __future__ import annotations

import logging
from typing import Any

import requests
import streamlit as st

from src.agent.llm import OllamaClient
from src.agent.loop import Agent
from src.agent.prompts import cited_urls
from src.bootstrap import detect_memory_gib, select_model
from src.config import Settings, load_settings
from src.rag.retrieve import Retriever

LOGGER = logging.getLogger(__name__)


@st.cache_resource
def resources(settings: Settings) -> tuple[Retriever, OllamaClient]:
    """Cache only shared inference resources; conversation stays in session state."""
    return Retriever(settings), OllamaClient(settings)


def service_ready(settings: Settings) -> bool:
    """Check the configured local service without relying on external proxies."""
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(settings.ollama_url + "/api/tags", timeout=settings.health_timeout_s, allow_redirects=False)
            response.raise_for_status()
            if response.status_code != 200:
                return False
            model = settings.chat_model or select_model(detect_memory_gib())
            return any(item["name"] == model for item in response.json().get("models", []))
    except (requests.RequestException, ValueError, KeyError):
        return False


def show_answer(message: dict[str, Any]) -> None:
    """Display a grounded answer, its actual citations and public tool execution records."""
    st.markdown(message["answer"])
    with st.expander("查看检索与工具记录"):
        for event in message["events"]:
            if event["action"] == "search_docs":
                st.write("检索官方文档")
                st.caption(event["input"]["query"])
                for source in event.get("sources", []):
                    st.markdown(f"[打开检索章节]({source})")
            else:
                st.write("估算芯片电量")
                st.json(event["result"])
        st.caption(f"本地响应 {message['elapsed_s']:.1f} 秒。此处展示工具操作记录。")
    if not cited_urls(message["answer"]):
        st.caption("本次回答未使用足够的文档证据。")


def main() -> None:
    """Render model/hardware status and keep a separate three-turn agent per browser session."""
    st.set_page_config(page_title="ESP32 文档助手", page_icon="📘", layout="centered")
    st.markdown("""<style>
    .stApp {background:#e8eff6;color:#153247;font-family:'Segoe UI','Microsoft YaHei',sans-serif}
    .stMainBlockContainer {max-width:850px;padding-top:2.3rem}
    h1 {font-weight:650;letter-spacing:-.04em;color:#153247}
    [data-testid="stChatMessage"] {background:white;border-left:3px solid #25668c;border-radius:8px;padding:1.25rem}
    [data-testid="stSidebar"] {background:#f7fafc;border-right:1px solid #cad9e5}
    [data-testid="stChatInput"] {border:1px solid #8da9bd}
    a {color:#25668c} code {color:#153247}
    button:focus-visible,a:focus-visible {outline:3px solid #d9a844;outline-offset:3px}
    </style>""", unsafe_allow_html=True)
    settings = load_settings()
    memory = detect_memory_gib()
    model = settings.chat_model or select_model(memory)
    ready = service_ready(settings)
    indexed = (settings.index_dir / "active.json").exists()
    st.title("ESP32 文档助手")
    st.write("从官方文档查找答案，保留代码与出处。支持中文和英文。")
    st.caption(f"当前模型 {model}　｜　可用内存上限 {memory:.1f} GiB　｜　文档检索 {settings.embed_model} / CPU")
    with st.sidebar:
        st.subheader("本地运行状态")
        st.write("● 模型已就绪" if ready else "○ 模型尚未就绪")
        st.write("● 文档索引已就绪" if indexed else "○ 文档索引尚未建立")
        st.caption("模型和检索均在本机运行。初始化完成后，问答无需联网。")
        if st.button("清空对话", use_container_width=True):
            if "agent" in st.session_state:
                st.session_state.agent.clear()
            st.session_state.messages = []
            st.session_state.pop("pending", None)
            st.rerun()
        st.caption("每次回答最多使用最近 3 轮对话。")
        st.markdown(f"[ESP-IDF 官方文档]({settings.docs_root})")
    if not ready:
        st.warning("先运行 `ollama serve`，再运行 `bash setup.sh`，准备本地模型。")
    if not indexed:
        st.info("文档尚未准备好。请按 README 完成文档抓取和索引构建。")
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if not st.session_state.messages:
        st.subheader("从一个问题开始")
        for example in ("ESP32 如何设置 5 秒后深度睡眠唤醒？", "GPIO34–39 可以启用内部上拉吗？", "估算 deep_sleep 24 小时的芯片电量。"):
            if st.button(example, use_container_width=True, disabled=not ready or not indexed):
                st.session_state.pending = example
    for message in st.session_state.messages:
        with st.chat_message("user"):
            st.markdown(message["question"])
        with st.chat_message("assistant"):
            show_answer(message)
    entered = st.chat_input("提问 ESP32 的 API、睡眠、GPIO 或功耗…", disabled=not ready or not indexed)
    question = entered or st.session_state.pop("pending", None)
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("正在检索官方文档并生成本地回答…"):
                try:
                    if "agent" not in st.session_state:
                        retriever, client = resources(settings)
                        st.session_state.agent = Agent(settings, retriever, client)
                    result = st.session_state.agent.ask(question)
                    message = {"question": question, "answer": result.answer, "events": result.events, "elapsed_s": result.elapsed_s}
                    st.session_state.messages.append(message)
                    show_answer(message)
                except (OSError, ValueError, RuntimeError) as exc:
                    LOGGER.exception("本地问答失败")
                    st.error(str(exc))


if __name__ == "__main__":
    main()
