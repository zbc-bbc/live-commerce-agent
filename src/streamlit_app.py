"""
Agent Service Toolkit - Streamlit Frontend

A complete multi-page frontend with:
- User authentication (login/register)
- Multi-session chat interface
- Admin panel for user management
- Per-agent UI theming with visual agent selector
"""

import asyncio
import json
import os
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime

import httpx
import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError

from client import AgentClient, AgentClientError
from schema import ChatMessage
from schema.task_data import TaskData, TaskDataStatus
from voice import VoiceManager

# ═══════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════

PAGE_LOGIN = "login"
PAGE_REGISTER = "register"
PAGE_CHAT = "chat"
PAGE_ADMIN = "admin"

APP_TITLE = "Agent Service Toolkit"
APP_ICON = "🧰"
USER_ID_COOKIE = "user_id"
AUTH_TOKEN_KEY = "auth_token"
USER_INFO_KEY = "user_info"
SESSION_DATA_KEY = "session_data"
ACTIVE_SESSION_KEY = "active_session"

# ═══════════════════════════════════════════════════════════════
#  Per-Agent Themes
# ═══════════════════════════════════════════════════════════════

AGENT_THEMES = {
    "chatbot": {
        "primary": "#6366f1", "secondary": "#818cf8",
        "bg_gradient": "linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)",
        "icon": "🤖", "short_name": "Chatbot",
        "description": "Simple conversational AI",
        "accent_emoji": "💬",
    },
    "research-assistant": {
        "primary": "#0ea5e9", "secondary": "#38bdf8",
        "bg_gradient": "linear-gradient(135deg, #0ea5e9 0%, #0284c7 100%)",
        "icon": "🔬", "short_name": "Research",
        "description": "Web search & analysis",
        "accent_emoji": "📊",
    },
    "rag-assistant": {
        "primary": "#8b5cf6", "secondary": "#a78bfa",
        "bg_gradient": "linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%)",
        "icon": "📚", "short_name": "RAG",
        "description": "Document Q&A",
        "accent_emoji": "📄",
    },
    "interrupt-agent": {
        "primary": "#f59e0b", "secondary": "#fbbf24",
        "bg_gradient": "linear-gradient(135deg, #f59e0b 0%, #d97706 100%)",
        "icon": "🔮", "short_name": "Interrupt",
        "description": "Personality prediction",
        "accent_emoji": "✨",
    },
    "command-agent": {
        "primary": "#10b981", "secondary": "#34d399",
        "bg_gradient": "linear-gradient(135deg, #10b981 0%, #059669 100%)",
        "icon": "⚡", "short_name": "Commands",
        "description": "Command execution",
        "accent_emoji": "⚙️",
    },
    "bg-task-agent": {
        "primary": "#ec4899", "secondary": "#f472b6",
        "bg_gradient": "linear-gradient(135deg, #ec4899 0%, #db2777 100%)",
        "icon": "🔄", "short_name": "BG Tasks",
        "description": "Background processing",
        "accent_emoji": "⏳",
    },
    "langgraph-supervisor-agent": {
        "primary": "#14b8a6", "secondary": "#2dd4bf",
        "bg_gradient": "linear-gradient(135deg, #14b8a6 0%, #0d9488 100%)",
        "icon": "🏗️", "short_name": "Supervisor",
        "description": "Agent orchestration",
        "accent_emoji": "🎯",
    },
    "langgraph-supervisor-hierarchy-agent": {
        "primary": "#f97316", "secondary": "#fb923c",
        "bg_gradient": "linear-gradient(135deg, #f97316 0%, #ea580c 100%)",
        "icon": "🏛️", "short_name": "Hierarchy",
        "description": "Hierarchical agents",
        "accent_emoji": "🏰",
    },
    "knowledge-base-agent": {
        "primary": "#06b6d4", "secondary": "#22d3ee",
        "bg_gradient": "linear-gradient(135deg, #06b6d4 0%, #0891b2 100%)",
        "icon": "🧠", "short_name": "Knowledge",
        "description": "Knowledge base RAG",
        "accent_emoji": "💡",
    },
    "github-mcp-agent": {
        "primary": "#18181b", "secondary": "#3f3f46",
        "bg_gradient": "linear-gradient(135deg, #18181b 0%, #27272a 100%)",
        "icon": "🐙", "short_name": "GitHub",
        "description": "GitHub automation",
        "accent_emoji": "🔧",
    },
    "live-commerce-agent": {
        "primary": "#ef4444", "secondary": "#f87171",
        "bg_gradient": "linear-gradient(135deg, #ef4444 0%, #dc2626 100%)",
        "icon": "🎯", "short_name": "直播助手",
        "description": "直播电商运营",
        "accent_emoji": "📺",
    },
}

DEFAULT_THEME = {
    "primary": "#6366f1", "secondary": "#818cf8",
    "bg_gradient": "linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)",
    "icon": "🧰", "short_name": "Agent",
    "description": "AI Agent", "accent_emoji": "🤖",
}


# ═══════════════════════════════════════════════════════════════
#  Core helpers
# ═══════════════════════════════════════════════════════════════

def get_or_create_user_id() -> str:
    if USER_ID_COOKIE in st.session_state:
        return st.session_state[USER_ID_COOKIE]
    v = st.query_params.get(USER_ID_COOKIE)
    if v:
        st.session_state[USER_ID_COOKIE] = v
        return v
    uid = str(uuid.uuid4())
    st.session_state[USER_ID_COOKIE] = uid
    st.query_params[USER_ID_COOKIE] = uid
    return uid


def get_current_theme(agent_key: str | None = None) -> dict:
    ac: AgentClient | None = st.session_state.get("agent_client")
    active = agent_key or (ac.agent if ac else None)
    return AGENT_THEMES.get(active) or DEFAULT_THEME


def is_authenticated() -> bool:
    return AUTH_TOKEN_KEY in st.session_state and USER_INFO_KEY in st.session_state


def get_user_role() -> str:
    return st.session_state.get(USER_INFO_KEY, {}).get("role", "user")


def is_admin() -> bool:
    return get_user_role() == "admin"


def logout() -> None:
    for k in [AUTH_TOKEN_KEY, USER_INFO_KEY, "agent_client", "voice_manager"]:
        st.session_state.pop(k, None)
    st.query_params.clear()
    st.rerun()


# ═══════════════════════════════════════════════════════════════
#  Session management
# ═══════════════════════════════════════════════════════════════

def get_session_name(messages) -> str:
    for m in messages:
        if m.type == "human":
            t = m.content.strip()
            if t:
                return t[:30] + ("…" if len(t) > 30 else "")
    return "新对话"


def init_session_manager() -> None:
    if ACTIVE_SESSION_KEY in st.session_state:
        return
    if SESSION_DATA_KEY not in st.session_state:
        st.session_state[SESSION_DATA_KEY] = {}
    tid = st.query_params.get("thread_id") or str(uuid.uuid4())
    msgs: list[ChatMessage] = []
    if st.query_params.get("thread_id"):
        try:
            msgs = st.session_state.agent_client.get_history(thread_id=tid).messages
        except AgentClientError:
            pass
    name = get_session_name(msgs) if msgs else "新对话"
    now = datetime.now().strftime("%H:%M")
    st.session_state[ACTIVE_SESSION_KEY] = tid
    st.session_state[SESSION_DATA_KEY][tid] = {"name": name, "created_at": now}
    st.session_state.thread_id = tid
    st.session_state.messages = msgs
    st.query_params["thread_id"] = tid


def save_current_session() -> None:
    aid = st.session_state.get(ACTIVE_SESSION_KEY)
    if aid and aid in st.session_state.get(SESSION_DATA_KEY, {}):
        msgs = st.session_state.get("messages", [])
        s = st.session_state[SESSION_DATA_KEY][aid]
        s["messages"] = msgs
        if s.get("name") == "新对话" and msgs:
            n = get_session_name(msgs)
            if n:
                s["name"] = n


def switch_session(sid: str) -> None:
    if sid == st.session_state.get(ACTIVE_SESSION_KEY):
        return
    save_current_session()
    st.session_state.pop("last_message", None)
    st.session_state[ACTIVE_SESSION_KEY] = sid
    st.session_state.thread_id = sid
    st.query_params["thread_id"] = sid
    cached = st.session_state.get(SESSION_DATA_KEY, {}).get(sid, {}).get("messages", [])
    if cached:
        st.session_state.messages = cached
    else:
        try:
            st.session_state.messages = st.session_state.agent_client.get_history(thread_id=sid).messages
        except AgentClientError:
            st.session_state.messages = []
    st.rerun()


def create_new_session() -> None:
    save_current_session()
    for k in ("last_audio", "last_message"):
        st.session_state.pop(k, None)
    sid = str(uuid.uuid4())
    now = datetime.now().strftime("%H:%M")
    st.session_state[SESSION_DATA_KEY][sid] = {"name": "新对话", "created_at": now}
    st.session_state[ACTIVE_SESSION_KEY] = sid
    st.session_state.thread_id = sid
    st.session_state.messages = []
    st.query_params["thread_id"] = sid
    st.rerun()


def delete_session(sid: str) -> None:
    if sid not in st.session_state.get(SESSION_DATA_KEY, {}):
        return
    st.session_state.pop("last_message", None)
    del st.session_state[SESSION_DATA_KEY][sid]
    if not st.session_state[SESSION_DATA_KEY]:
        create_new_session()
        return
    if sid == st.session_state.get(ACTIVE_SESSION_KEY):
        switch_session(next(iter(st.session_state[SESSION_DATA_KEY])))
    else:
        st.rerun()


# ═══════════════════════════════════════════════════════════════
#  Sidebar: session list
# ═══════════════════════════════════════════════════════════════

def render_session_list() -> None:
    sessions = st.session_state.get(SESSION_DATA_KEY, {})
    if not sessions:
        st.markdown('<div style="font-size:0.75rem;color:#94a3b8;padding:8px 4px;">No conversations yet</div>', unsafe_allow_html=True)
        return
    sorted_items = sorted(sessions.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)[:20]
    for sid, sdata in sorted_items:
        active = sid == st.session_state.get(ACTIVE_SESSION_KEY)
        name = sdata.get("name", "新对话")
        t = sdata.get("created_at", "")

        active_cls = "active" if active else ""
        item_html = f"""
        <div class="session-item {active_cls}" onclick="document.querySelector('[key=\\'chat_{sid}\\']')?.click()">
            <div>
                <div class="session-item-name">{name}</div>
                <div class="session-item-time">{t}</div>
            </div>
        </div>"""
        st.markdown(item_html, unsafe_allow_html=True)

        # Hidden buttons for session switching (triggered via CSS click)
        col_a, col_b = st.columns([6, 1])
        with col_a:
            btn_label = "▸" if active else name
            if st.button(btn_label, key=f"chat_{sid}", use_container_width=True):
                switch_session(sid)
        with col_b:
            if st.button("✕", key=f"del_{sid}", help="Delete"):
                delete_session(sid)


# ═══════════════════════════════════════════════════════════════
#  Auth UI
# ═══════════════════════════════════════════════════════════════

def _auth_css(theme: dict) -> str:
    return f"""
    <style>
        .auth-wrap {{ display:flex; justify-content:center; align-items:center; min-height:100vh;
            background:linear-gradient(135deg, #1e40af 0%, #2563eb 50%, #3b82f6 100%); padding:2rem; }}
        .auth-card {{ background:white; border-radius:16px; padding:2.5rem; max-width:400px; width:100%;
            box-shadow:0 25px 50px rgba(0,0,0,0.15); }}
        .auth-title {{ font-size:1.5rem; font-weight:600; text-align:center; margin-bottom:.25rem; color:#0f172a; }}
        .auth-sub {{ text-align:center; color:#64748b; margin-bottom:1.75rem; font-size:.875rem; }}
        .auth-icon {{ text-align:center; font-size:2.5rem; margin-bottom:.5rem; }}
        .stTextInput input {{ border-radius:8px !important; padding:10px 14px !important;
            border:1.5px solid #e2e8f0 !important; font-size:.875rem !important; }}
        .stTextInput input:focus {{ border-color:#2563eb !important;
            box-shadow:0 0 0 3px rgba(37,99,235,0.1) !important; }}
        .stButton button {{ background:#2563eb !important; color:white !important;
            border:none !important; border-radius:8px !important; padding:10px 20px !important;
            font-weight:500 !important; width:100% !important; transition:all .15s ease !important; }}
        .stButton button:hover {{ background:#1d4ed8 !important;
            box-shadow:0 4px 12px rgba(37,99,235,0.3) !important; }}
        .stAlert {{ border-radius:8px !important; }}
        .stForm {{ padding-top:0 !important; }}
    </style>"""


def render_login_page() -> None:
    theme = DEFAULT_THEME
    st.markdown(_auth_css(theme), unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown(f'<div class="auth-icon">{APP_ICON}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="auth-title">{APP_TITLE}</div>', unsafe_allow_html=True)
        is_first = False
        try:
            r = httpx.get(f"{_get_agent_url()}/auth/check", timeout=5)
            if r.status_code == 200:
                is_first = r.json().get("first_run", False)
        except Exception:
            pass
        if is_first:
            st.markdown('<div class="auth-sub">🚀 First-time setup — Create your admin account</div>', unsafe_allow_html=True)
            st.success("👋 **Welcome!** The first person to register becomes **admin** with full access.", icon="🎉")
        else:
            st.markdown('<div class="auth-sub">Sign in to your account</div>', unsafe_allow_html=True)
        with st.form("login_form", clear_on_submit=False):
            st.text_input("Username", placeholder="Enter your username", key="login_user")
            st.text_input("Password", type="password", placeholder="Enter your password", key="login_pass")
            if st.form_submit_button("Sign In", key="login_submit"):
                u, p = st.session_state.login_user, st.session_state.login_pass
                errors = []
                if not u:
                    errors.append("请输入账号")
                if not p:
                    errors.append("请输入登录密码")
                if errors:
                    st.error("；".join(errors))
                else:
                    try:
                        c = AgentClient(base_url=_get_agent_url(), get_info=False)
                        r = c.login(username=u, password=p)
                        st.session_state[AUTH_TOKEN_KEY] = r["access_token"]
                        st.session_state[USER_INFO_KEY] = r["user"]
                        st.rerun()
                    except AgentClientError as e:
                        err_msg = str(e)
                        if "USER_NOT_FOUND" in err_msg:
                            st.error("该账号未注册，请先注册")
                        elif "WRONG_PASSWORD" in err_msg:
                            st.error("密码输入错误，请重新核对密码")
                        elif "401" in err_msg or "Unauthorized" in err_msg:
                            st.error("登录失败，请检查账号和密码是否正确")
                        else:
                            st.error(f"登录失败：{err_msg}")
                    except Exception as e:
                        st.error(f"Connection error: {e}")
        if st.button("Create an account", key="goto_register", type="tertiary", use_container_width=True):
            st.query_params["page"] = PAGE_REGISTER
            st.rerun()


def render_register_page() -> None:
    theme = DEFAULT_THEME
    st.markdown(_auth_css(theme), unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown(f'<div class="auth-icon">{APP_ICON}</div>', unsafe_allow_html=True)
        st.markdown('<div class="auth-title">Create Account</div>', unsafe_allow_html=True)
        st.markdown('<div class="auth-sub">Register to get started</div>', unsafe_allow_html=True)
        st.info("💡 **Tip:** The first user to register becomes **Admin** automatically.", icon="ℹ️")
        with st.form("register_form", clear_on_submit=False):
            st.text_input("Username", placeholder="Choose a username (min 3 chars)", key="reg_user")
            st.text_input("Email", placeholder="Enter your email", key="reg_email")
            st.text_input("Password", type="password", placeholder="Choose a password (min 6 chars)", key="reg_pass")
            st.text_input("Confirm Password", type="password", placeholder="Confirm your password", key="reg_pass2")
            if st.form_submit_button("Create Account", use_container_width=True):
                u, e, p, p2 = [st.session_state.get(k) for k in ("reg_user","reg_email","reg_pass","reg_pass2")]
                if not u or not e or not p:
                    st.error("Please fill in all fields")
                elif p != p2:
                    st.error("Passwords do not match")
                elif len(p) < 6:
                    st.error("Password must be at least 6 characters")
                else:
                    try:
                        AgentClient(base_url=_get_agent_url(), get_info=False).register(username=u, email=e, password=p)
                        st.success("Account created! Please sign in.")
                        st.query_params["page"] = PAGE_LOGIN
                        st.rerun()
                    except AgentClientError as ex:
                        st.error(f"Registration failed: {ex}")
                    except Exception as ex:
                        st.error(f"Connection error: {ex}")
        if st.button("Already have an account? Sign in", key="goto_login", type="tertiary", use_container_width=True):
            st.query_params["page"] = PAGE_LOGIN
            st.rerun()


# ═══════════════════════════════════════════════════════════════
#  Visual Agent Selector
# ═══════════════════════════════════════════════════════════════

@st.dialog("Select Agent", width="large")
def _agent_selector_dialog(agent_list: list[str]) -> None:
    """Agent picker — streamlined with only Chatbot and 直播助手."""
    ALLOWED = {"chatbot", "live-commerce-agent"}
    filtered = [a for a in agent_list if a in ALLOWED]
    current = st.session_state.agent_client.agent
    cols_per_row = 2
    rows = [filtered[i:i+cols_per_row] for i in range(0, len(filtered), cols_per_row)]
    st.markdown(
        '<div style="text-align:center;margin-bottom:12px;font-size:.8125rem;color:#64748b;">'
        '选择你需要的助手</div>', unsafe_allow_html=True)
    for row in rows:
        cols = st.columns(cols_per_row)
        for i, ag_key in enumerate(row):
            t = AGENT_THEMES.get(ag_key) or DEFAULT_THEME
            active = ag_key == current
            with cols[i]:
                border = f"2px solid {t['primary']}" if active else "1px solid #e2e8f0"
                bg = f"{t['primary']}08" if active else "white"
                st.markdown(f"""
                <div style="padding:1.2rem;border-radius:12px;border:{border};background:{bg};
                    text-align:center;margin-bottom:.5rem;
                    box-shadow:{'0 4px 12px rgba(0,0,0,0.1)' if active else '0 1px 3px rgba(0,0,0,0.04)'};">
                    <div style="font-size:2.2rem;">{t['icon']}</div>
                    <div style="font-weight:600;font-size:1rem;color:#0f172a;margin-top:.3rem;">{t['short_name']}</div>
                    <div style="font-size:.75rem;color:#64748b;margin-top:.2rem;">{t['description']}</div>
                </div>""", unsafe_allow_html=True)
                if st.button(f"选择 {t['short_name']}", key=f"sel_{ag_key}", use_container_width=True,
                             type="primary" if active else "secondary"):
                    st.session_state.agent_client.agent = ag_key
                    st.rerun()


# ═══════════════════════════════════════════════════════════════
#  Chat interface
# ═══════════════════════════════════════════════════════════════

async def render_chat_interface() -> None:
    ac: AgentClient = st.session_state.agent_client
    user_info = st.session_state.get(USER_INFO_KEY, {})
    current_agent = ac.agent or "research-assistant"
    theme = get_current_theme(current_agent)

    # ── Sidebar ──
    with st.sidebar:
        # User profile header
        st.markdown(
            f'<div class="sidebar-user">'
            f'<div class="sidebar-user-avatar" style="background:{theme["primary"]};">'
            f'{user_info.get("username","U")[0].upper()}</div>'
            f'<div><div class="sidebar-user-name">{user_info.get("username","User")}</div>'
            f'<div class="sidebar-user-badge">{"🛡️ Admin" if user_info.get("role") == "admin" else "👤 User"}</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Agent indicator bar
        ac1, ac2 = st.columns([4, 1])
        with ac1:
            st.markdown(
                f'<div class="agent-bar">'
                f'<span class="agent-bar-icon">{theme["icon"]}</span>'
                f'<div><div class="agent-bar-name">{theme["short_name"]}</div>'
                f'<div class="agent-bar-desc">{theme["description"]}</div></div>'
                f'<div class="agent-bar-dot"></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with ac2:
            if st.button("🔄", key="switch_agent_btn", help="Switch Agent"):
                _agent_selector_dialog([a.key for a in ac.info.agents])

        # Admin link
        if is_admin():
            if st.button(":material/admin_panel_settings: Admin Panel", use_container_width=True, type="secondary"):
                st.query_params["page"] = PAGE_ADMIN
                st.rerun()
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

        # New Chat button — also clears live commerce state if in live mode
        if st.button("🎮 直播后台", key="live_backend_btn", use_container_width=True):
            lc_url = _get_agent_url().rstrip("/") + "/live-commerce/"
            js = f"<script>window.open('{lc_url}', '_blank')</script>"
            st.markdown(js, unsafe_allow_html=True)
        if st.button("➕ New Chat", key="new_chat_btn", use_container_width=True, type="primary"):
            if st.session_state.get("lc_broadcasting") or st.session_state.get("lc_danmaku_list"):
                _archive_live_session()
                _clear_live_session()
                st.session_state.lc_broadcasting = False
            create_new_session()

        # Session list
        st.markdown('<div class="sidebar-label">Chat History</div>', unsafe_allow_html=True)
        render_session_list()

        # Separator
        st.markdown("<hr style='margin:12px 0'>", unsafe_allow_html=True)

        # Bottom button group
        with st.popover("⚙️ Settings", use_container_width=True):
            try:
                model_idx = ac.info.models.index(ac.info.default_model)
            except ValueError:
                model_idx = 0
            model = st.selectbox("LLM Model", options=ac.info.models, index=model_idx, label_visibility="collapsed")
            use_streaming = st.toggle("Stream results", value=True)
            voice_mgr = st.session_state.get("voice_manager")
            st.toggle("Enable audio", value=True, disabled=not voice_mgr or not voice_mgr.tts, key="enable_audio",
                      help=("Configure VOICE_TTS_PROVIDER in .env to enable" if not voice_mgr or not voice_mgr.tts else None))
            st.caption(f"Signed in as **{user_info.get('username','?')}**")

        # Bottom action row
        bcol1, bcol2, bcol3 = st.columns(3)
        with bcol1:
            @st.dialog("Architecture")
            def arch_dialog():
                st.image("https://github.com/JoshuaC215/agent-service-toolkit/blob/main/media/agent_architecture.png?raw=true")
            if st.button("🏛️ Arch", key="arch_btn", use_container_width=True):
                arch_dialog()
        with bcol2:
            @st.dialog("Share")
            def share_dialog():
                params = f"?thread_id={st.session_state.thread_id}&{USER_ID_COOKIE}={get_or_create_user_id()}"
                st.markdown(
                    f"<div style='font-size:.875rem;margin-bottom:8px;'>Share this link to continue this conversation:</div>"
                    f"<code id='share-url-box' style='display:block;padding:8px 12px;background:#f1f5f9;"
                    f"border-radius:6px;font-size:.75rem;word-break:break-all;'>"
                    f"<span id='share-origin'></span>{params}</code>"
                    f"<div style='margin-top:8px;font-size:.75rem;color:#64748b;'>"
                    f"The link opens the same conversation thread on any device.</div>"
                    f"<script>"
                    f"document.getElementById('share-origin').textContent = window.location.origin;"
                    f"</script>",
                    unsafe_allow_html=True,
                )
                if st.button("📋 Copy Link", key="copy_share", use_container_width=True):
                    st.toast("Link ready! Copy from above.", icon="✅")
                st.info("Copy the URL to share or revisit this chat")
            if st.button("🔗 Share", key="share_btn", use_container_width=True):
                share_dialog()
        with bcol3:
            if st.button("🚪 Exit", key="logout_btn", use_container_width=True):
                logout()

        st.markdown("<hr style='margin:8px 0'>", unsafe_allow_html=True)
        st.caption("Agent Service Toolkit v0.1")

    # ── Main area ──
    # Live Commerce Dashboard — full B2B control panel layout
    if current_agent == "live-commerce-agent":
        await _render_live_commerce_dashboard(ac, model, use_streaming)
        return

    # ── Default chat interface ──
    _inject_chat_css(theme)

    messages: list[ChatMessage] = st.session_state.messages

    # Welcome
    if len(messages) == 0:
        _show_welcome(current_agent, theme)

    # Draw messages
    async def _iter() -> AsyncGenerator[ChatMessage, None]:
        for m in messages:
            yield m
    await draw_messages(_iter())

    # Audio playback
    voice = st.session_state.get("voice_manager")
    enable_audio = st.session_state.get("enable_audio", True)
    if voice and enable_audio and "last_audio" in st.session_state and st.session_state.last_message and messages and messages[-1].type == "ai":
        with st.session_state.last_message:
            st.audio(st.session_state.last_audio["data"], format=st.session_state.last_audio["format"])

    # ── Scroll-to-bottom button ──
    _scroll_to_bottom_button()

    # ── Input area ──
    has_msgs = len(messages) > 0
    chat_placeholder = "Ask anything…" if has_msgs else f"Ask {theme['short_name']} anything…"
    if voice:
        user_input = voice.get_chat_input()
    else:
        user_input = st.chat_input(chat_placeholder)

    if user_input:
        messages.append(ChatMessage(type="human", content=user_input))
        save_current_session()
        st.chat_message("human").write(user_input)
        try:
            if use_streaming:
                stream = ac.astream(message=user_input, model=model, thread_id=st.session_state.thread_id, user_id=get_or_create_user_id())
                await draw_messages(stream, is_new=True)
                if voice and enable_audio and st.session_state.messages:
                    lm = st.session_state.messages[-1]
                    if lm.type == "ai" and lm.content:
                        voice.render_message(lm.content, container=st.session_state.last_message, audio_only=True)
            else:
                response = await ac.ainvoke(message=user_input, model=model, thread_id=st.session_state.thread_id, user_id=get_or_create_user_id())
                messages.append(response)
                with st.chat_message("ai"):
                    if voice and enable_audio:
                        voice.render_message(response.content)
                    else:
                        st.write(response.content)
            st.rerun()
        except AgentClientError as e:
            st.error(f"Error generating response: {e}")
            st.stop()

    # Feedback
    if len(messages) > 0 and st.session_state.last_message:
        with st.session_state.last_message:
            await handle_feedback()


def _scroll_to_bottom_button() -> None:
    """Floating scroll-to-bottom button."""
    st.markdown("""
    <style>
    .scroll-btn {
        position:fixed; bottom:100px; right:30px; z-index:999;
        width:44px; height:44px; border-radius:50%; background:white;
        border:1px solid #e2e8f0; box-shadow:0 2px 8px rgba(0,0,0,0.12);
        display:flex; align-items:center; justify-content:center;
        cursor:pointer; font-size:1.3rem; transition:all .2s;
    }
    .scroll-btn:hover { transform:translateY(-2px); box-shadow:0 4px 16px rgba(0,0,0,0.18); }
    </style>
    <button class="scroll-btn" onclick="window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'})">
    ↓
    </button>
    """, unsafe_allow_html=True)


def _inject_chat_css(theme: dict) -> None:
    st.markdown(f"""
    <style>
        /* ── AI message cards ── */
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatar"][data-testid="stChatMessageAvatar-AI"]) {{
            background: var(--ds-bg-surface);
            border-radius: var(--ds-radius-lg);
            border-left: 3px solid {theme['primary']};
            box-shadow: var(--ds-shadow-sm);
            padding: 4px 0;
            margin: 6px 0 12px 0;
        }}
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatar"][data-testid="stChatMessageAvatar-AI"]) [data-testid="stChatMessageContent"] {{
            padding: 4px 8px;
        }}
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatar"][data-testid="stChatMessageAvatar-AI"]) p {{
            line-height: 1.6;
            margin-bottom: 10px;
        }}
        /* Agent avatar dot with theme color */
        [data-testid="stChatMessageAvatar-AI"] {{
            background: {theme['primary']} !important;
        }}

        /* ── Human message (right-aligned bubble) ── */
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatar"][data-testid="stChatMessageAvatar-USER"]) {{
            display: flex;
            flex-direction: row-reverse;
        }}
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatar"][data-testid="stChatMessageAvatar-USER"]) [data-testid="stChatMessageContent"] {{
            background: #f1f5f9;
            border-radius: 12px 12px 4px 12px;
            padding: 10px 16px;
            max-width: 72%;
            margin-left: auto;
        }}
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatar"][data-testid="stChatMessageAvatar-USER"]) p {{
            margin-bottom: 0;
        }}

        /* ── Sidebar user profile ── */
        .sidebar-user {{
            display: flex; align-items: center; gap: 12px;
            padding: 8px 4px 12px 4px;
            border-bottom: 1px solid var(--ds-border);
            margin-bottom: 12px;
        }}
        .sidebar-user-avatar {{
            width: 40px; height: 40px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 1.125rem; font-weight: 600; color: white;
            flex-shrink: 0;
        }}
        .sidebar-user-name {{
            font-size: 0.875rem; font-weight: 600; color: var(--ds-text-primary);
            line-height: 1.3;
        }}
        .sidebar-user-badge {{
            font-size: 0.75rem; color: var(--ds-text-muted);
        }}

        /* ── Session list items ── */
        .session-item {{
            display: flex; align-items: center; justify-content: space-between;
            padding: 8px 10px; border-radius: var(--ds-radius-md);
            cursor: pointer; transition: all 0.12s ease;
            border-left: 3px solid transparent;
        }}
        .session-item:hover {{
            background: #f8fafc;
        }}
        .session-item.active {{
            background: var(--ds-primary-light);
            border-left-color: var(--ds-primary);
        }}
        .session-item-name {{
            font-size: 0.8125rem; color: var(--ds-text-primary);
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }}
        .session-item.active .session-item-name {{
            font-weight: 600;
        }}
        .session-item-time {{
            font-size: 0.6875rem; color: var(--ds-text-muted);
            margin-top: 2px;
        }}

        /* ── Sidebar section labels ── */
        .sidebar-label {{
            font-size: 0.6875rem; font-weight: 600; text-transform: uppercase;
            letter-spacing: 0.05em; color: var(--ds-text-muted);
            padding: 0 4px; margin: 12px 0 6px 0;
        }}

        /* ── New Chat button ── */
        .new-chat-btn {{
            width: 100%; padding: 10px 16px; border-radius: var(--ds-radius-md);
            background: var(--ds-primary); color: white; border: none;
            font-family: var(--ds-font); font-size: 0.875rem; font-weight: 500;
            cursor: pointer; transition: all 0.15s ease;
            display: flex; align-items: center; justify-content: center; gap: 6px;
        }}
        .new-chat-btn:hover {{
            background: var(--ds-primary-hover);
            box-shadow: 0 2px 8px rgba(37,99,235,0.25);
        }}

        /* ── Sidebar button group ── */
        .sidebar-btn {{
            width: 100%; padding: 7px 12px; border-radius: var(--ds-radius-md);
            background: transparent; border: none;
            font-family: var(--ds-font); font-size: 0.8125rem; color: var(--ds-text-secondary);
            cursor: pointer; transition: all 0.12s ease;
            text-align: left; display: flex; align-items: center; gap: 8px;
        }}
        .sidebar-btn:hover {{
            background: #f1f5f9; color: var(--ds-text-primary);
        }}
        .sidebar-btn.danger:hover {{
            color: #dc2626; background: #fef2f2;
        }}

        /* ── Agent indicator bar ── */
        .agent-bar {{
            display: flex; align-items: center; gap: 8px;
            padding: 8px 12px; border-radius: var(--ds-radius-md);
            background: {theme['primary']}0a;
            border: 1px solid {theme['primary']}18;
            margin-bottom: 12px;
        }}
        .agent-bar-icon {{ font-size: 1.125rem; }}
        .agent-bar-name {{ font-size: 0.8125rem; font-weight: 600; color: var(--ds-text-primary); }}
        .agent-bar-desc {{ font-size: 0.7rem; color: var(--ds-text-muted); }}
        .agent-bar-dot {{
            width: 8px; height: 8px; border-radius: 50%;
            background: {theme['primary']}; margin-left: auto; flex-shrink: 0;
        }}

        /* ── Welcome message ── */
        .welcome-icon {{ font-size: 2rem; margin-bottom: 8px; }}
        .welcome-title {{ font-size: 1.125rem; font-weight: 600; color: var(--ds-text-primary); }}
        .welcome-body {{ font-size: 0.875rem; line-height: 1.7; color: var(--ds-text-secondary); }}
        .welcome-body p {{ margin-bottom: 10px; }}
        .feature-grid {{
            display: grid; grid-template-columns: auto 1fr; gap: 6px 12px;
            margin: 10px 0;
        }}
        .feature-grid > div {{ display: contents; }}
        .feature-icon {{ font-size: 1rem; }}
        .feature-text {{ font-size: 0.8125rem; color: var(--ds-text-secondary); }}

        /* ── Delete button in session list ── */
        .del-btn {{
            border: none; background: transparent; cursor: pointer;
            font-size: 0.8125rem; opacity: 0; transition: opacity 0.12s ease;
            padding: 2px 4px; border-radius: 4px;
        }}
        .session-item:hover .del-btn {{
            opacity: 0.5;
        }}
        .session-item:hover .del-btn:hover {{
            opacity: 1; background: #f1f5f9;
        }}
    </style>
    """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
#  Live Commerce Dashboard — B2B 直播中控台
# ═══════════════════════════════════════════════════════════════

LIVE_COMMERCE_STAGES = ["暖场", "产品介绍", "促销逼单", "答疑互动", "收尾总结"]
LIVE_COMMERCE_SCRIPT_TYPES = ["开场暖场", "逼单促销", "答疑互动", "产品介绍", "收尾总结"]

# ── Product data persistence (matches data_manager.py's DATA_DIR) ──
PRODUCTS_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "live_commerce", "data", "products_data.json")
ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "data", "live_commerce", "archives")

def _load_products_from_file() -> list[dict] | None:
    """Load products from local JSON file. Normalizes field names for frontend use.
    Returns None if file doesn't exist."""
    try:
        if os.path.exists(PRODUCTS_DATA_FILE):
            with open(PRODUCTS_DATA_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Normalize field names: ProductItem uses productId etc., frontend uses id
            normalized = []
            for item in raw:
                pid = item.get("productId", item.get("id", ""))
                normalized.append({
                    "id": pid,
                    "productId": pid,
                    "name": item.get("name", ""),
                    "category": item.get("category", ""),
                    "price": item.get("price", 0),
                    "price_unit": "元",
                    "stock": item.get("stock", 0),
                    "promotions": item.get("promotions") if isinstance(item.get("promotions"), list) else [],
                    "specs": "; ".join(item.get("specs", [])) if isinstance(item.get("specs"), list) else item.get("specs", ""),
                    "selling_points": item.get("selling_points", ""),
                    "notes": item.get("notes", ""),
                    "description": item.get("description", ""),
                })
            return normalized
    except Exception as e:
        import traceback
        traceback.print_exc()
    return None

def _save_products_to_file(products: list[dict]) -> None:
    """Save products to local JSON file for persistence (in productId format for data_manager compat)."""
    try:
        os.makedirs(os.path.dirname(PRODUCTS_DATA_FILE), exist_ok=True)
        # Convert from frontend format (id) to data_manager format (productId)
        save_data = []
        for p in products:
            entry = {k: v for k, v in p.items() if k != "id"}  # strip frontend-only 'id'
            if "productId" not in entry and "id" in p:
                entry["productId"] = p["id"]
            save_data.append(entry)
        with open(PRODUCTS_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.warning(f"⚠️ 商品数据保存失败：{e}")

def _detect_danmaku_intent(text: str) -> str:
    """Detect danmaku intent type using keyword matching."""
    t = text.lower()
    # ⚠️ 优先级最高：异议/负面评价检测（含质量、正品、品牌、效果、价格投诉）
    if any(kw in t for kw in [
        "太贵", "不值", "骗人", "假的", "假货", "假", "质量差", "差评", "退货", "退款",
        "割韭菜", "智商税", "有用吗", "是不是骗", "假的吧", "能退", "垃圾",
        "靠谱吗", "靠谱", "牌子", "正品", "不敢买", "担心", "怕是", "假一赔",
        "有用吗", "真的吗", "是不是真", "别是", "不会是真的吧",
    ]):
        return "objection"
    if any(kw in t for kw in ["多少钱", "价格", "怎么卖", "多钱", "价位", "优惠", "打折", "折扣", "便宜"]):
        return "price_query"
    if any(kw in t for kw in ["适合", "肤质", "规格", "尺寸", "容量", "能用", "油皮", "干皮", "敏感"]):
        return "spec_query"
    if any(kw in t for kw in ["算算", "算一算", "一起买", "合计", "总共", "几件"]):
        return "calculation"
    return "general"

def _build_objection_script(dm_text: str, product: dict) -> str:
    """Generate objection-handling conversion script based on detected objection type."""
    t = dm_text.lower()
    name = product.get("name", "这款商品")
    price = product.get("price", "")
    points = product.get("selling_points", "")
    promos = product.get("promotions", [])
    promo_str = "; ".join(promos[:2]) if isinstance(promos, list) else str(promos)[:80]

    if any(kw in t for kw in ["太贵", "不值", "割韭菜", "智商税", "这么贵"]):
        return (
            f"💡 **价格异议 · 安抚转化话术**\n\n"
            f"① 共情理解：「这位宝宝说得对，乍一看确实价格不算低~」\n"
            f"② 价值重塑：{points[:60]}\n"
            f"③ 价格锚定：日常价¥{price}，直播间专享价已是最优惠\n"
            f"④ 降低门槛：{promo_str if promo_str else '赠送运费险，7天无理由退换，完全没有后顾之忧'}\n"
            f"⑤ 引导转化：「真正用过就知道有多值，趁有优惠赶紧入手~」"
        )
    if any(kw in t for kw in ["质量", "假的", "假货", "正品", "骗", "垃圾", "牌子", "靠谱", "不敢买", "担心", "怕是"]):
        return (
            f"💡 **质量/正品异议 · 安抚转化话术**\n\n"
            f"① 安抚：「宝宝放心，咱们直播间所有商品都是品牌直供/官方授权」\n"
            f"② 信任背书：「{name}累计销量已突破万件，好评率99%以上」\n"
            f"③ 售后保障：「支持7天无理由退换，有任何问题随时找客服」\n"
            f"④ 引导：「买回去试试，不满意随时退，完全没有风险~」"
        )
    if any(kw in t for kw in ["效果", "有用"]):
        return (
            f"💡 **效果疑虑 · 安抚转化话术**\n\n"
            f"① 成分科普：{name}采用优质原料/配方\n"
            f"② 真实案例：「很多老粉都在回购，用完都回来好评了」\n"
            f"③ 降低门槛：「可以先买少量试试，觉得好用再回购」\n"
            f"④ 引导：「相信我，用过你就会爱上它！」"
        )
    # Default
    return (
        f"💡 **异议安抚转化话术**\n\n"
        f"① 先表示理解：「这位宝宝的顾虑我完全理解~」\n"
        f"② 正面回应：{points[:80]}\n"
        f"③ 给出证据：「支持7天无理由退换，不满意随时退」\n"
        f"④ 引导互动：「有什么具体问题可以问我，帮你详细解答~」"
    )


def _get_selected_product() -> dict:
    """Get the current selected product data from session state.
    Returns empty dict if no product is selected (so callers can .get() safely)."""
    return st.session_state.get("lc_selected_product_data") or {}


def _assert_product_selected() -> bool:
    """Check if a product is currently selected. If not, show a warning and return False."""
    prod = _get_selected_product()
    if not prod or not prod.get("id"):
        st.warning("⚠️ **请先在左侧【当前商品】下拉框选择直播商品，再发送弹幕**")
        return False
    return True


def _product_tag(prod: dict) -> str:
    """Short product tag for logs: '<P001> 玻尿酸精华'"""
    pid = prod.get("id", "—")
    pname = prod.get("name", "—")[:8]
    return f"<{pid}> {pname}"


def _archive_live_session() -> None:
    """Archive current session data to disk, then clear all live states."""
    prod = _get_selected_product()
    archive = {
        "archived_at": datetime.now().isoformat(),
        "product_id": prod.get("id"),
        "product_name": prod.get("name"),
        "danmaku_count": st.session_state.get("lc_total_danmaku", 0),
        "ai_replies": st.session_state.get("lc_ai_replies", 0),
        "stage": LIVE_COMMERCE_STAGES[st.session_state.get("lc_stage_idx", 0)],
        "danmaku_list": list(st.session_state.get("lc_danmaku_list", [])),
        "messages": [{"type": m.type, "content": m.content[:200]} for m in st.session_state.get("messages", [])],
        "tool_logs": list(st.session_state.get("lc_tool_logs", [])),
    }
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        fname = f"live_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{archive['product_id'] or 'none'}.json"
        with open(os.path.join(ARCHIVE_DIR, fname), "w", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # best-effort archiving, don't block the user flow


def _clear_live_session() -> None:
    """Clear all live commerce session transient states (messages, danmaku, logs, etc.)."""
    st.session_state.messages = []
    st.session_state.lc_danmaku_list = []
    st.session_state.lc_tool_logs = []
    st.session_state.lc_script_output = ""
    st.session_state.lc_objection_script = ""
    st.session_state.lc_total_danmaku = 0
    st.session_state.lc_ai_replies = 0


def _init_global_products() -> None:
    """Initialize global product data in session state if not present.
    Loads from persistent JSON file first, falls back to built-in data.
    Both Admin Panel and Live Dashboard share this same data source."""
    if "admin_products" not in st.session_state:
        # Try loading from persistent file first
        products = _load_products_from_file()
        if products:
            st.session_state.admin_products = products
            return
        # Fall back to built-in product data
        from data.live_commerce.products import ALL_PRODUCTS as _LC_PRODS
        st.session_state.admin_products = [
            {
                "id": p.id, "productId": p.id,
                "name": p.name, "category": p.category, "price": p.price,
                "price_unit": "元", "stock": p.stock,
                "promotions": [pr.description for pr in p.promotions],
                "specs": ", ".join(f"{k}:{v}" for k, v in p.specs.items()),
                "selling_points": "; ".join(p.selling_points),
                "notes": "; ".join(p.notes),
            }
            for p in _LC_PRODS
        ]
        # Save built-in data to file for persistence
        _save_products_to_file(st.session_state.admin_products)

def _init_lc_state() -> None:
    """Initialize live commerce dashboard state."""
    _init_global_products()
    if "lc_danmaku_list" not in st.session_state:
        st.session_state.lc_danmaku_list = []
    if "lc_stage_idx" not in st.session_state:
        st.session_state.lc_stage_idx = 0
    if "lc_script_output" not in st.session_state:
        st.session_state.lc_script_output = ""
    if "lc_selected_product" not in st.session_state:
        st.session_state.lc_selected_product = "P001"
    if "lc_selected_product_data" not in st.session_state:
        st.session_state.lc_selected_product_data = {}
    if "lc_total_danmaku" not in st.session_state:
        st.session_state.lc_total_danmaku = 0
    if "lc_ai_replies" not in st.session_state:
        st.session_state.lc_ai_replies = 0
    if "lc_online_users" not in st.session_state:
        # 与后端 settings.system.onlineCountBase 的默认值保持一致。
        # Streamlit 是独立进程，没有通往 WebSocket 数据管理器的通道，因此这里只能是一个
        # 静态展示值——真实在线人数（基数 + 真实观众数）请在运营台 /live-commerce/ 查看。
        st.session_state.lc_online_users = 120
    if "lc_tool_logs" not in st.session_state:
        st.session_state.lc_tool_logs = []
    if "lc_broadcasting" not in st.session_state:
        st.session_state.lc_broadcasting = False
    if "lc_objection_script" not in st.session_state:
        st.session_state.lc_objection_script = ""
    if "lc_pending_danmaku" not in st.session_state:
        st.session_state.lc_pending_danmaku = False
    if "lc_pending_danmaku_text" not in st.session_state:
        st.session_state.lc_pending_danmaku_text = ""
    if "lc_prev_product" not in st.session_state:
        st.session_state.lc_prev_product = ""

async def _render_live_commerce_dashboard(ac: AgentClient, model: str, use_streaming: bool) -> None:
    """Full B2B live commerce control panel dashboard replacing the chat area."""
    _init_lc_state()
    from data.live_commerce.danmaku import DANMAKU_QUESTIONS

    theme = get_current_theme("live-commerce-agent")
    st.markdown(f"""
    <style>
    .lc-top-bar {{ display:flex; align-items:center; gap:0; margin:-16px 0 16px 0; padding:16px 24px;
        background:white; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,0.05);
        border:1px solid #e2e8f0; }}
    .lc-top-left {{ display:flex; align-items:center; gap:16px; flex-shrink:0; }}
    .lc-badge {{ padding:4px 14px; border-radius:20px; font-size:.75rem; font-weight:600; letter-spacing:.02em; }}
    .lc-badge-live {{ background:#fef2f2; color:#dc2626; border:1px solid #fecaca; }}
    .lc-badge-stage {{ background:#eff6ff; color:#2563eb; border:1px solid #bfdbfe; }}
    .lc-metrics {{ display:flex; gap:0; margin-left:auto; }}
    .lc-metric {{ padding:0 20px; border-right:1px solid #f1f5f9; text-align:center; }}
    .lc-metric:last-child {{ border-right:none; }}
    .lc-metric-val {{ font-size:1.25rem; font-weight:700; color:#0f172a; line-height:1.2; }}
    .lc-metric-lbl {{ font-size:.6875rem; color:#94a3b8; margin-top:1px; }}

    .lc-panel {{ background:white; border-radius:12px; border:1px solid #e2e8f0;
        box-shadow:0 1px 2px rgba(0,0,0,0.04); padding:16px; height:100%; }}
    .lc-panel-title {{ font-size:.8125rem; font-weight:600; color:#0f172a;
        margin-bottom:12px; padding-bottom:8px; border-bottom:1px solid #f1f5f9; }}
    .lc-panel-scroll {{ max-height:340px; overflow-y:auto; margin:0 -4px; padding:0 4px; }}

    .lc-product-field {{ display:flex; justify-content:space-between; padding:4px 0;
        font-size:.8125rem; border-bottom:1px solid #f8fafc; }}
    .lc-product-field:last-child {{ border-bottom:none; }}
    .lc-product-label {{ color:#64748b; }}
    .lc-product-value {{ font-weight:500; color:#0f172a; }}
    .lc-product-price {{ font-size:1.5rem; font-weight:700; color:#dc2626; line-height:1.2; }}
    .lc-product-orig {{ font-size:.75rem; color:#94a3b8; text-decoration:line-through; }}
    .lc-promo-tag {{ display:inline-block; padding:1px 8px; border-radius:4px;
        background:#fef2f2; color:#dc2626; font-size:.6875rem; font-weight:500; }}
    .lc-promo-tag.orange {{ background:#fff7ed; color:#c2410c; }}

    .lc-danmaku {{ display:flex; align-items:flex-start; gap:8px; padding:6px 0;
        border-bottom:1px solid #f8fafc; }}
    .lc-danmaku:last-child {{ border-bottom:none; }}
    .lc-danmaku-avatar {{ width:28px; height:28px; border-radius:50%;
        display:flex; align-items:center; justify-content:center;
        font-size:.625rem; font-weight:600; color:white; flex-shrink:0; }}
    .lc-danmaku-body {{ flex:1; min-width:0; }}
    .lc-danmaku-user {{ font-size:.6875rem; font-weight:600; color:#475569; }}
    .lc-danmaku-text {{ font-size:.75rem; color:#0f172a; line-height:1.4; word-break:break-word; }}
    .lc-danmaku-time {{ font-size:.625rem; color:#cbd5e1; flex-shrink:0; }}

    .lc-stage-label {{ font-size:1.25rem; font-weight:700; text-align:center;
        padding:12px 0; color:#0f172a; }}
    .lc-stage-progress {{ display:flex; gap:4px; justify-content:center; margin:8px 0 12px 0; }}
    .lc-stage-dot {{ width:10px; height:10px; border-radius:50%; background:#e2e8f0; }}
    .lc-stage-dot.active {{ background:#2563eb; }}
    .lc-stage-dot.done {{ background:#10b981; }}

    .lc-script-out {{ background:#f8fafc; border-radius:8px; padding:12px;
        font-size:.8125rem; color:#0f172a; line-height:1.6;
        min-height:80px; max-height:160px; overflow-y:auto; white-space:pre-wrap; }}
    .lc-script-out:empty::before {{ content:'点击「生成话术」生成内容'; color:#94a3b8; }}

    .lc-btn-action {{ padding:6px 16px; border-radius:6px; font-size:.75rem; font-weight:500;
        border:1px solid #e2e8f0; background:white; color:#475569; cursor:pointer;
        transition:all .12s ease; }}
    .lc-btn-action:hover {{ background:#f1f5f9; border-color:#cbd5e1; }}

    .lc-info-row {{ display:flex; justify-content:space-between; padding:4px 0;
        font-size:.75rem; border-bottom:1px solid #f8fafc; }}
    .lc-info-label {{ color:#64748b; }}
    .lc-info-value {{ color:#0f172a; font-weight:500; }}

    /* Chat area inside dashboard */
    .lc-chat-container {{ background:white; border-radius:12px; border:1px solid #e2e8f0;
        box-shadow:0 1px 2px rgba(0,0,0,0.04); padding:12px; height:100%;
        display:flex; flex-direction:column; }}
    .lc-chat-messages {{ flex:1; overflow-y:auto; min-height:200px; max-height:380px;
        margin:0 -4px; padding:0 4px; }}
    /* Beautified chat bubbles */
    .lc-msg {{ margin:4px 0; padding:0 8px; }}
    .lc-msg-human {{ text-align:right; }}
    .lc-msg-human .lc-msg-bubble {{ display:inline-block; background:#f1f5f9;
        border-radius:12px 12px 4px 12px; padding:8px 14px; font-size:.8125rem;
        color:#0f172a; max-width:85%; text-align:left; }}
    .lc-msg-ai {{ text-align:left; }}
    .lc-msg-ai .lc-msg-bubble {{ display:inline-block; background:#ffffff;
        border-radius:12px 12px 12px 4px; padding:8px 14px; font-size:.8125rem;
        color:#0f172a; border:1px solid #e2e8f0; max-width:85%; }}

    /* Copy button */
    .lc-copy-btn {{ border:none; background:#f1f5f9; border-radius:4px;
        padding:2px 10px; font-size:.6875rem; color:#475569; cursor:pointer;
        transition:all .1s ease; float:right; }}
    .lc-copy-btn:hover {{ background:#e2e8f0; }}
    </style>
    """, unsafe_allow_html=True)

    # ── Top Status Bar ──
    stage_labels = {0:"暖场中",1:"产品介绍",2:"促销进行",3:"答疑互动",4:"收尾总结"}
    stage_colors = {0:"#2563eb",1:"#059669",2:"#dc2626",3:"#d97706",4:"#6366f1"}
    sidx = st.session_state.lc_stage_idx
    is_live = st.session_state.lc_broadcasting
    live_badge = f'<span class="lc-badge lc-badge-live">● {"直播中" if is_live else "未开播"}</span>' if is_live else \
        f'<span class="lc-badge" style="background:#f1f5f9;color:#94a3b8;border:1px solid #e2e8f0;">⏸ 未开播</span>'
    stage_badge = f'<span class="lc-badge lc-badge-stage" style="color:{stage_colors.get(sidx,"#2563eb")};">{LIVE_COMMERCE_STAGES[sidx]}</span>' if is_live else ""
    st.markdown(
        f'<div class="lc-top-bar">'
        f'<div class="lc-top-left">'
        f'{live_badge}{stage_badge}'
        f'</div>'
        f'<div class="lc-metrics">'
        f'<div class="lc-metric"><div class="lc-metric-val">{st.session_state.lc_total_danmaku}</div><div class="lc-metric-lbl">弹幕总数</div></div>'
        f'<div class="lc-metric"><div class="lc-metric-val">{st.session_state.lc_ai_replies}</div><div class="lc-metric-lbl">AI回复</div></div>'
        f'<div class="lc-metric"><div class="lc-metric-val">{st.session_state.lc_online_users}</div><div class="lc-metric-lbl">在线观众</div></div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── Three-column upper section ──
    col1, col2, col3 = st.columns([1.2, 1.4, 1])
    products_list = st.session_state.admin_products

    # === Column 1: Product Card ===
    with col1:
        st.markdown('<div class="lc-panel">', unsafe_allow_html=True)
        st.markdown('<div class="lc-panel-title">📦 当前商品</div>', unsafe_allow_html=True)
        prod_options = {f"{p['id']} {p['name']}": p['id'] for p in products_list}
        selected_label = st.selectbox(
            "选择商品", options=list(prod_options.keys()),
            index=next((i for i,k in enumerate(prod_options.keys()) if prod_options[k]==st.session_state.lc_selected_product), 0),
            key="lc_prod_sel", label_visibility="collapsed",
        )
        prod_id = prod_options[selected_label]
        st.session_state.lc_selected_product = prod_id
        # Find product from session state
        prod = next((p for p in products_list if p['id'] == prod_id), None)
        if prod:
            pu = prod.get("price_unit", "元")
            st.markdown(
                f'<div style="margin-top:8px;">'
                f'<div style="font-size:1.125rem;font-weight:600;color:#0f172a;">{prod["name"]}</div>'
                f'<div style="display:flex;gap:8px;margin:4px 0 10px 0;">'
                f'<span style="font-size:.6875rem;padding:1px 8px;border-radius:4px;background:#f1f5f9;color:#475569;">{prod["category"]}</span>'
                f'<span style="font-size:.6875rem;padding:1px 8px;border-radius:4px;background:#fef2f2;color:#dc2626;">库存 {prod["stock"]}</span>'
                f'</div>'
                f'<div class="lc-product-price">¥{prod["price"]:.2f}{pu}</div>'
                f'<div style="margin:8px 0;">'
                f'{"".join(f"<span class=\"lc-promo-tag\">🔥 {pr[:24]}…</span><br>" for pr in prod.get("promotions",[])[:2])}'
                f'</div>'
                f'<div class="lc-product-field"><span class="lc-product-label">品类</span><span class="lc-product-value">{prod["category"]}</span></div>'
                f'<div class="lc-product-field"><span class="lc-product-label">原价</span><span class="lc-product-value">¥{prod["price"]:.2f}{pu}</span></div>'
                f'<div class="lc-product-field"><span class="lc-product-label">库存</span><span class="lc-product-value">{prod["stock"]}</span></div>'
                f'<div class="lc-product-field"><span class="lc-product-label">规格</span><span class="lc-product-value">{prod.get("specs","—")}</span></div>'
                f'<div style="font-size:.75rem;color:#64748b;margin-top:6px;padding-top:8px;border-top:1px solid #f1f5f9;">{prod.get("selling_points","")[:60]}…</div>'
                f'</div>', unsafe_allow_html=True,
            )
            # Store full product info in session for RAG / tools
            st.session_state.lc_selected_product_data = prod
            # ── Product change detection: clear live session on switch ──
            prev = st.session_state.get("lc_prev_product", "")
            if prev and prev != prod_id:
                _archive_live_session()
                _clear_live_session()
                st.toast(f"已切换至「{prod['name']}」, 对话已重置", icon="🔄")
            st.session_state.lc_prev_product = prod_id

    # === Column 2: Danmaku Panel ===
    with col2:
        st.markdown('<div class="lc-panel">', unsafe_allow_html=True)
        st.markdown('<div class="lc-panel-title">💬 弹幕互动 <span style="font-weight:400;font-size:.75rem;color:#94a3b8;">（输入后自动关联下拉框商品处理）</span></div>', unsafe_allow_html=True)
        dm_col1, dm_col2 = st.columns([4, 1])
        with dm_col1:
            dm_text = st.text_input("", placeholder="输入弹幕内容…", key="lc_danmaku_input", label_visibility="collapsed")
        with dm_col2:
            if st.button("发送", key="lc_dm_send", type="primary", use_container_width=True):
                if dm_text and _assert_product_selected():
                    prod = _get_selected_product()
                    st.session_state.lc_danmaku_list.append({
                        "user": f"观众{len(st.session_state.lc_danmaku_list)+1}",
                        "text": dm_text,
                        "time": datetime.now().strftime("%H:%M"),
                        "color": ["#2563eb","#059669","#d97706","#dc2626","#7c3aed"][len(st.session_state.lc_danmaku_list)%5],
                        "product_id": prod.get("id"),
                        "product_name": prod.get("name"),
                    })
                    st.session_state.lc_total_danmaku += 1
                    st.session_state.lc_pending_danmaku = True
                    st.session_state.lc_pending_danmaku_text = dm_text
                    st.rerun()

        # Quick preset danmaku buttons
        preset_cols = st.columns(3)
        with preset_cols[0]:
            if st.button("💰 价格提问", key="dm_price", use_container_width=True):
                if _assert_product_selected():
                    txt = "这个多少钱？有优惠吗？"
                    prod = _get_selected_product()
                    st.session_state.lc_danmaku_list.append({"user":"观众A","text":txt,"time":datetime.now().strftime("%H:%M"),"color":"#2563eb","product_id":prod.get("id"),"product_name":prod.get("name")})
                    st.session_state.lc_total_danmaku += 1
                    st.session_state.lc_pending_danmaku = True
                    st.session_state.lc_pending_danmaku_text = txt
                    st.rerun()
        with preset_cols[1]:
            if st.button("📏 规格咨询", key="dm_spec", use_container_width=True):
                if _assert_product_selected():
                    txt = "这个适合油皮用吗？"
                    prod = _get_selected_product()
                    st.session_state.lc_danmaku_list.append({"user":"观众B","text":txt,"time":datetime.now().strftime("%H:%M"),"color":"#059669","product_id":prod.get("id"),"product_name":prod.get("name")})
                    st.session_state.lc_total_danmaku += 1
                    st.session_state.lc_pending_danmaku = True
                    st.session_state.lc_pending_danmaku_text = txt
                    st.rerun()
        with preset_cols[2]:
            if st.button("🔢 帮我算算", key="dm_calc", use_container_width=True):
                if _assert_product_selected():
                    txt = "主播帮我算算买两件多少钱"
                    prod = _get_selected_product()
                    st.session_state.lc_danmaku_list.append({"user":"观众C","text":txt,"time":datetime.now().strftime("%H:%M"),"color":"#d97706","product_id":prod.get("id"),"product_name":prod.get("name")})
                    st.session_state.lc_total_danmaku += 1
                    st.session_state.lc_pending_danmaku = True
                    st.session_state.lc_pending_danmaku_text = txt
                    st.rerun()

        # Row 2: Objection preset buttons
        preset_cols2 = st.columns(3)
        with preset_cols2[0]:
            if st.button("😤 嫌贵异议", key="dm_obj_price", use_container_width=True):
                if _assert_product_selected():
                    txt = "太贵了，感觉不值这个价，是不是割韭菜？"
                    prod = _get_selected_product()
                    st.session_state.lc_danmaku_list.append({"user":"观众D","text":txt,"time":datetime.now().strftime("%H:%M"),"color":"#dc2626","product_id":prod.get("id"),"product_name":prod.get("name")})
                    st.session_state.lc_total_danmaku += 1
                    st.session_state.lc_pending_danmaku = True
                    st.session_state.lc_pending_danmaku_text = txt
                    st.rerun()
        with preset_cols2[1]:
            if st.button("❓ 质量质疑", key="dm_obj_quality", use_container_width=True):
                if _assert_product_selected():
                    txt = "这牌子靠谱吗？不会是假货吧？"
                    prod = _get_selected_product()
                    st.session_state.lc_danmaku_list.append({"user":"观众E","text":txt,"time":datetime.now().strftime("%H:%M"),"color":"#dc2626","product_id":prod.get("id"),"product_name":prod.get("name")})
                    st.session_state.lc_total_danmaku += 1
                    st.session_state.lc_pending_danmaku = True
                    st.session_state.lc_pending_danmaku_text = txt
                    st.rerun()
        with preset_cols2[2]:
            if st.button("🤔 效果疑虑", key="dm_obj_effect", use_container_width=True):
                if _assert_product_selected():
                    txt = "真的有效果吗？别是智商税吧？"
                    prod = _get_selected_product()
                    st.session_state.lc_danmaku_list.append({"user":"观众F","text":txt,"time":datetime.now().strftime("%H:%M"),"color":"#dc2626","product_id":prod.get("id"),"product_name":prod.get("name")})
                    st.session_state.lc_total_danmaku += 1
                    st.session_state.lc_pending_danmaku = True
                    st.session_state.lc_pending_danmaku_text = txt
                    st.rerun()

        # Danmaku list
        st.markdown('<div class="lc-panel-scroll">', unsafe_allow_html=True)
        for dm in reversed(st.session_state.lc_danmaku_list[-30:]):
            st.markdown(
                f'<div class="lc-danmaku">'
                f'<div class="lc-danmaku-avatar" style="background:{dm.get("color","#2563eb")};">{dm["user"][:2]}</div>'
                f'<div class="lc-danmaku-body"><div class="lc-danmaku-user">{dm["user"]}</div><div class="lc-danmaku-text">{dm["text"]}</div></div>'
                f'<div class="lc-danmaku-time">{dm.get("time","")}</div>'
                f'</div>', unsafe_allow_html=True,
            )
        if not st.session_state.lc_danmaku_list:
            st.markdown('<div style="font-size:.75rem;color:#94a3b8;text-align:center;padding:20px 0;">暂无弹幕，输入或点击预设按钮模拟</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Danmaku auto‑processing: intent detection, objection handling, agent dispatch ──
    if st.session_state.pop("lc_pending_danmaku", None):
        dm_text = st.session_state.pop("lc_pending_danmaku_text", "")
        prod = _get_selected_product()
        if not prod or not prod.get("id"):
            st.warning("⚠️ **请在左侧【当前商品】下拉框选择直播商品后再发送弹幕**")
            st.rerun()
        intent = _detect_danmaku_intent(dm_text)
        now_str = datetime.now().strftime("%H:%M:%S")
        prod_tag = _product_tag(prod)

        # Build rich product context for agent prompt
        promo_str = "; ".join(prod.get("promotions", [])) if isinstance(prod.get("promotions"), list) else (prod.get("promotions") or "无")

        # Log the intent to tool logs (with product binding)
        st.session_state.lc_tool_logs.append({
            "time": now_str, "type": "intent", "params": f"意图: {intent} | 商品: {prod_tag}",
            "msg": f"🎯 弹幕意图识别: {dm_text[:30]}… → [{intent}]",
            "product_id": prod.get("id"),
            "product_name": prod.get("name"),
        })

        # If objection → generate soothing conversion script using this product's info
        if intent == "objection":
            objection_script = _build_objection_script(dm_text, prod)
            st.session_state.lc_objection_script = objection_script
            st.session_state.lc_tool_logs.append({
                "time": datetime.now().strftime("%H:%M:%S"), "type": "objection",
                "params": f"原弹幕: {dm_text} | 商品: {prod_tag}",
                "msg": f"🛡️ 异议自动识别 → 已生成安抚转化话术",
                "product_id": prod.get("id"),
                "product_name": prod.get("name"),
            })

        # Dispatch to agent with this product's context only (no cross-product data)
        msg_content = (
            f"【弹幕互动】观众发送了弹幕：{dm_text}\n\n"
            f"【当前商品：{prod_tag}】\n"
            f"名称：{prod.get('name','未选择')}\n"
            f"分类：{prod.get('category','—')}\n"
            f"原价：¥{prod.get('price','—')}\n"
            f"库存：{prod.get('stock','—')}件\n"
            f"卖点：{prod.get('selling_points','—')}\n"
            f"优惠活动：{promo_str}\n"
            f"售后政策：7天无理由退换，质量问题包邮退\n\n"
            f"请严格基于以上商品信息回答，不要引用其它商品数据。"
            f"如果是价格/优惠提问：仅计算本品单价和满减；"
            f"如果是规格/适用提问：仅基于本品品类属性和规格回答；"
            f"如果是质量/价格异议：依托本品卖点和售后政策进行安抚转化。"
            f"语气亲切自然像真人主播，适当使用emoji。"
        )
        st.session_state.messages.append(ChatMessage(type="human", content=msg_content))
        try:
            stream = ac.astream(
                message=msg_content, model=model,
                thread_id=st.session_state.thread_id,
                user_id=get_or_create_user_id(),
            )
            async for token_or_msg in stream:
                if isinstance(token_or_msg, ChatMessage) and token_or_msg.type == "ai":
                    st.session_state.messages.append(token_or_msg)
                    st.session_state.lc_ai_replies += 1
                    # Tool call logging with product binding
                    if token_or_msg.tool_calls:
                        for tc in token_or_msg.tool_calls:
                            st.session_state.lc_tool_logs.append({
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "type": "tool",
                                "msg": f"🔧 {tc['name']}",
                                "params": str(tc.get('args', {})),
                                "product_id": prod.get("id"),
                                "product_name": prod.get("name"),
                            })
                    st.session_state.lc_tool_logs = st.session_state.lc_tool_logs[-80:]
                elif isinstance(token_or_msg, str):
                    pass
            st.rerun()
        except AgentClientError as e:
            st.error(f"Agent处理弹幕失败: {e}")

    # === Column 3: Control Panel ===
    with col3:
        st.markdown('<div class="lc-panel">', unsafe_allow_html=True)
        st.markdown('<div class="lc-panel-title">🎛️ 场控面板</div>', unsafe_allow_html=True)

        if not st.session_state.lc_broadcasting:
            # Idle state — show Start button
            if st.button("🎬 开始直播", key="lc_start", use_container_width=True, type="primary"):
                _clear_live_session()
                st.session_state.lc_broadcasting = True
                st.session_state.lc_stage_idx = 0
                st.toast("直播已开始！", icon="🎬")
                st.rerun()
            st.markdown(
                '<div style="text-align:center;padding:20px 0;color:#94a3b8;font-size:.8125rem;">'
                '点击上方按钮开始直播<br><span style="font-size:.75rem;">开始后将自动启用弹幕互动和AI助手</span></div>',
                unsafe_allow_html=True,
            )
        else:
            # Stage navigation
            nav_cols = st.columns([1, 2, 1])
            with nav_cols[0]:
                if st.button("◀", key="lc_stage_prev", use_container_width=True):
                    st.session_state.lc_stage_idx = max(0, st.session_state.lc_stage_idx - 1)
                    st.rerun()
            with nav_cols[1]:
                st.markdown(f'<div class="lc-stage-label">{LIVE_COMMERCE_STAGES[sidx]}</div>', unsafe_allow_html=True)
            with nav_cols[2]:
                if st.button("▶", key="lc_stage_next", use_container_width=True):
                    st.session_state.lc_stage_idx = min(len(LIVE_COMMERCE_STAGES)-1, st.session_state.lc_stage_idx + 1)
                    st.rerun()
            # Stage dots
            dots_html = '<div class="lc-stage-progress">'
            for i in range(len(LIVE_COMMERCE_STAGES)):
                cls = "active" if i == sidx else ("done" if i < sidx else "")
                dots_html += f'<div class="lc-stage-dot {cls}"></div>'
            dots_html += '</div>'
            st.markdown(dots_html, unsafe_allow_html=True)

            # Quick actions — 4 columns
            st.markdown('<div style="display:flex;gap:6px;margin:8px 0;">', unsafe_allow_html=True)
            act_cols = st.columns(4)
            with act_cols[0]:
                if st.button("🔥 促销", key="lc_promo", use_container_width=True):
                    prod = st.session_state.lc_selected_product_data or {}
                    st.session_state.messages.append(ChatMessage(
                        type="human",
                        content=f"请为当前商品「{prod.get('name','')}」生成一段限时促销逼单话术，语气紧迫但真实，不要虚假宣传。"
                    ))
                    st.rerun()
            with act_cols[1]:
                if st.button("💡 答疑", key="lc_qa", use_container_width=True):
                    if st.session_state.lc_danmaku_list:
                        last_q = st.session_state.lc_danmaku_list[-1]["text"]
                        st.session_state.messages.append(ChatMessage(type="human", content=f"回答弹幕：{last_q}"))
                        st.rerun()
            with act_cols[2]:
                if st.button("📊 复盘", key="lc_report", use_container_width=True):
                    # Build comprehensive review prompt from session data
                    danmaku_count = st.session_state.lc_total_danmaku
                    reply_count = st.session_state.lc_ai_replies
                    danmaku_samples = [d["text"] for d in st.session_state.lc_danmaku_list[-15:]]
                    tool_log_summary = [log["msg"] for log in st.session_state.get("lc_tool_logs", [])[-10:]]
                    prod = st.session_state.lc_selected_product_data or {}
                    prompt = (
                        f"请根据以下本场直播数据生成结构化复盘报告：\n\n"
                        f"【直播概况】\n"
                        f"弹幕总数：{danmaku_count}\n"
                        f"AI回复数：{reply_count}\n"
                        f"当前直播阶段：{LIVE_COMMERCE_STAGES[sidx]}\n"
                        f"当前商品：{prod.get('name','—')} | ¥{prod.get('price','—')}\n\n"
                        f"【弹幕样本（最近15条）】\n"
                        + "\n".join(f"  - {s}" for s in danmaku_samples) + "\n\n"
                        f"【工具调用摘要】\n"
                        + "\n".join(f"  - {s}" for s in tool_log_summary) + "\n\n"
                        f"请输出包含以下章节的报告：\n"
                        f"1️⃣ 直播数据概览\n"
                        f"2️⃣ 高频观众问题分析（分类+频率）\n"
                        f"3️⃣ 下单转化阻碍识别（异议类型+具体问题）\n"
                        f"4️⃣ 优化建议（针对每个阻碍给出具体改进方案）\n"
                        f"5️⃣ 本场亮点与可复制经验\n"
                        f"要求：数据准确、分析深入、建议可落地。"
                    )
                    st.session_state.messages.append(ChatMessage(type="human", content=prompt))
                    st.rerun()
            with act_cols[3]:
                if st.button("📋 复制日志", key="lc_copy_logs", use_container_width=True):
                    log_text = "\n".join(
                        f"[{l.get('time','')}] {l.get('msg','')} | {l.get('params','')}"
                        for l in st.session_state.get("lc_tool_logs", [])[-20:]
                    )
                    st.toast("日志已复制到剪贴板" if log_text else "暂无日志", icon="📋")
            st.markdown('</div>', unsafe_allow_html=True)

            # End broadcast button — archive + clear
            if st.button("● 结束直播", key="lc_end", use_container_width=True, type="primary"):
                _archive_live_session()
                _clear_live_session()
                st.session_state.lc_broadcasting = False
                st.toast("直播已结束，数据已归档", icon="⏹️")
                st.rerun()

            # Tool call info (collapsible) — enhanced with type + params
            st.markdown('<div style="margin-top:10px;">', unsafe_allow_html=True)
            with st.expander("🤖 Agent 工具调用日志", expanded=False):
                logs = st.session_state.get("lc_tool_logs", [])
                if logs:
                    for log in logs[-15:]:
                        typ = log.get("type", "tool")
                        icon_map = {"tool": "🔧", "intent": "🎯", "objection": "🛡️"}
                        prefix = icon_map.get(typ, "•")
                        label = f'{prefix} {log.get("msg","")}'
                        params = log.get("params", "")
                        line = f'<span class="lc-info-label">{log.get("time","")}</span><span class="lc-info-value">{label}</span>'
                        st.markdown(f'<div class="lc-info-row">{line}</div>', unsafe_allow_html=True)
                        if params:
                            st.markdown(f'<div style="font-size:.625rem;color:#94a3b8;padding:0 0 2px 0;">{params}</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<div style="font-size:.75rem;color:#94a3b8;padding:8px 0;">暂无工具调用记录</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)  # end lc-panel

    # ── Bottom two-column section ──
    bcol1, bcol2 = st.columns([1, 1.2])

    # === Bottom Left: Script Generation (Agent-powered) ===
    with bcol1:
        st.markdown('<div class="lc-panel">', unsafe_allow_html=True)
        st.markdown('<div class="lc-panel-title">📝 话术生成</div>', unsafe_allow_html=True)
        sc_cols = st.columns([3, 1])
        with sc_cols[0]:
            script_type = st.selectbox("", options=LIVE_COMMERCE_SCRIPT_TYPES, key="lc_script_type", label_visibility="collapsed")

        # Handle pending agent-based script generation (runs on rerun after button click)
        if st.session_state.pop("lc_pending_script_gen", None):
            s_type = st.session_state.pop("lc_pending_script_type", script_type)
            prod = st.session_state.lc_selected_product_data or {}
            promo_str = "; ".join(prod.get("promotions", [])) if isinstance(prod.get("promotions"), list) else (prod.get("promotions") or "无")
            prompt = (
                f"你是带货主播小智，请为以下商品生成一段直播{s_type}。\n\n"
                f"【商品信息】\n"
                f"名称：{prod.get('name', '当前商品')}\n"
                f"分类：{prod.get('category', '—')}\n"
                f"价格：¥{prod.get('price', '—')}\n"
                f"卖点：{prod.get('selling_points', '—')}\n"
                f"优惠活动：{promo_str}\n\n"
                f"要求：语气亲切自然，像真人主播，适当使用emoji增强表达效果。"
                f"注意不要夸大宣传，不能做虚假承诺。字数控制在200字左右。"
            )
            collected = ""
            try:
                stream = ac.astream(
                    message=prompt, model=model,
                    thread_id=st.session_state.thread_id,
                    user_id=get_or_create_user_id(),
                )
                async for token_or_msg in stream:
                    if isinstance(token_or_msg, ChatMessage) and token_or_msg.type == "ai":
                        collected = token_or_msg.content or ""
                        break
                if collected:
                    st.session_state.lc_script_output = collected
            except AgentClientError as e:
                st.session_state.lc_script_output = f"❌ 生成失败：{e}"
            st.rerun()

        with sc_cols[1]:
            if st.button("生成话术", key="lc_gen_script", type="primary", use_container_width=True):
                st.session_state.lc_pending_script_gen = True
                st.session_state.lc_pending_script_type = script_type
                st.rerun()

        # Script output box
        loading = st.session_state.get("lc_pending_script_gen")
        if loading:
            st.markdown(f'<div class="lc-script-out" style="text-align:center;color:#94a3b8;">⏳ AI 正在生成话术中…</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="lc-script-out">{st.session_state.lc_script_output}</div>', unsafe_allow_html=True)
        if st.session_state.lc_script_output and not loading:
            # Copy via JavaScript
            escaped = st.session_state.lc_script_output.replace("`","\\`").replace("'","\\'").replace("\n","\\n")
            st.markdown(
                f'<button class="lc-copy-btn" onclick="navigator.clipboard.writeText(`{escaped}`).then(()=>{{this.innerText=\'✅ 已复制\';setTimeout(()=>{{this.innerText=\'📋 复制\'}},2000);}})">📋 复制</button>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

        # ── Objection script auto-generated by danmaku processing ──
        obj_script = st.session_state.get("lc_objection_script", "")
        if obj_script:
            st.markdown('<div style="margin-top:8px;">', unsafe_allow_html=True)
            st.markdown(f'<div style="font-size:.75rem;font-weight:600;color:#dc2626;margin-bottom:4px;">🛡️ 异议自动安抚话术</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="lc-script-out" style="border:1px solid #fecaca;background:#fef2f2;">{obj_script}</div>', unsafe_allow_html=True)
            escaped_obj = obj_script.replace("`","\\`").replace("'","\\'").replace("\n","\\n")
            st.markdown(
                f'<button class="lc-copy-btn" onclick="navigator.clipboard.writeText(`{escaped_obj}`).then(()=>{{this.innerText=\'✅ 已复制\';setTimeout(()=>{{this.innerText=\'📋 复制\'}},2000);}})">📋 复制</button>',
                unsafe_allow_html=True,
            )
            if st.button("✕ 关闭", key="lc_dismiss_obj", type="secondary", use_container_width=True):
                st.session_state.lc_objection_script = ""
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    # === Bottom Right: Chat Window (保留原有对话逻辑) ===
    with bcol2:
        st.markdown('<div class="lc-chat-container">', unsafe_allow_html=True)
        st.markdown('<div class="lc-panel-title">💬 Agent 对话窗口</div>', unsafe_allow_html=True)
        st.markdown('<div class="lc-chat-messages">', unsafe_allow_html=True)

        messages: list[ChatMessage] = st.session_state.messages
        for m in messages:
            if m.type == "human":
                st.markdown(f'<div class="lc-msg lc-msg-human"><div class="lc-msg-bubble">{m.content}</div></div>', unsafe_allow_html=True)
            elif m.type == "ai":
                st.markdown(f'<div class="lc-msg lc-msg-ai"><div class="lc-msg-bubble">{m.content}</div></div>', unsafe_allow_html=True)

        # Stream new messages
        async def _iter() -> AsyncGenerator[ChatMessage, None]:
            for m in st.session_state.messages:
                yield m
        st.session_state.last_message = None
        st.session_state.messages_snapshot = list(st.session_state.messages)
        st.markdown('</div>', unsafe_allow_html=True)  # end chat-messages

        # Chat input
        voice = st.session_state.get("voice_manager")
        user_input = voice.get_chat_input() if voice else st.chat_input("输入指令给直播助手…", key="lc_chat_input")
        if user_input:
            msg = ChatMessage(type="human", content=user_input)
            st.session_state.messages.append(msg)
            save_current_session()
            try:
                stream = ac.astream(message=user_input, model=model, thread_id=st.session_state.thread_id, user_id=get_or_create_user_id())
                # Read the stream
                async for token_or_msg in stream:
                    if isinstance(token_or_msg, ChatMessage) and token_or_msg.type == "ai":
                        st.session_state.messages.append(token_or_msg)
                        st.session_state.lc_ai_replies += 1
                        # Log tool calls with product binding
                        prod = _get_selected_product()
                        if token_or_msg.tool_calls:
                            for tc in token_or_msg.tool_calls:
                                st.session_state.lc_tool_logs.append({
                                    "time": datetime.now().strftime("%H:%M:%S"),
                                    "type": "tool",
                                    "msg": f"🔧 {tc['name']}",
                                    "params": str(tc.get('args', {})),
                                    "product_id": prod.get("id"),
                                    "product_name": prod.get("name"),
                                })
                        st.session_state.lc_tool_logs = st.session_state.lc_tool_logs[-80:]
                    elif isinstance(token_or_msg, str):
                        pass  # streaming tokens, handled by UI refresh
                st.rerun()
            except AgentClientError as e:
                st.error(f"Error: {e}")

        st.markdown('</div>', unsafe_allow_html=True)  # end lc-chat-container


def _show_welcome(agent_key: str, theme: dict) -> None:
    """Structured welcome message with feature grid layout."""
    t = AGENT_THEMES.get(agent_key) or theme
    icon = t["icon"]
    name = t["short_name"]

    base_welcome = {
        "chatbot": ("Simple conversational AI", [
            ("💬", "Ask me anything — I'll respond with helpful answers"),
        ]),
        "research-assistant": ("Web search & analysis assistant", [
            ("🔍", "Search the web for up-to-date information"),
            ("🧮", "Perform calculations and data analysis"),
            ("📊", "Summarize and synthesize research findings"),
        ]),
        "rag-assistant": ("Document Q&A with company knowledge base", [
            ("📄", "Ask about company policies and employee handbook"),
            ("💡", "Get answers based on AcmeTech's documents"),
        ]),
        "interrupt-agent": ("Interactive personality prediction", [
            ("🔮", "Share your birthday for a personality reading"),
            ("💬", "Engage in interactive conversational flows"),
        ]),
        "live-commerce-agent": ("直播电商运营助手", [
            ("📋", "查看全部商品资料和优惠活动"),
            ("💬", "模拟弹幕互动问答"),
            ("🧮", "计算优惠价格、查询库存"),
            ("📊", "生成直播复盘报告"),
        ]),
    }

    with st.chat_message("ai"):
        if agent_key in base_welcome:
            desc, features = base_welcome[agent_key]
            html = f'<div class="welcome-icon">{icon}</div>'
            html += f'<div class="welcome-title">{name}</div>'
            html += f'<div class="welcome-body" style="margin-top:4px;color:#64748b;font-size:0.8125rem;">{desc}</div>'
            html += '<div class="feature-grid">'
            for fe_icon, fe_text in features:
                html += f'<div class="feature-icon">{fe_icon}</div><div class="feature-text">{fe_text}</div>'
            html += '</div>'
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='welcome-icon'>{icon}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='welcome-title'>{name}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='welcome-body'>Hello! I'm a {name} agent. Ask me anything!</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
#  Admin panel
# ═══════════════════════════════════════════════════════════════

def render_admin_page() -> None:
    ac: AgentClient = st.session_state.agent_client
    user_info = st.session_state.get(USER_INFO_KEY, {})
    theme = get_current_theme()

    st.markdown(f"""
    <style>
        .admin-hdr {{ display:flex; align-items:center; gap:12px; margin-bottom:24px; }}
        .admin-hdr h1 {{ font-size:1.5rem; font-weight:600; color:var(--ds-text-primary); margin:0; }}
        .stat-card {{ background:white; border-radius:var(--ds-radius-lg); padding:20px;
            box-shadow:var(--ds-shadow-sm); border:1px solid var(--ds-border); transition:all .15s ease; }}
        .stat-card:hover {{ box-shadow:var(--ds-shadow-md); }}
        .stat-val {{ font-size:1.75rem; font-weight:700; color:var(--ds-primary); }}
        .stat-lbl {{ font-size:.8125rem; color:var(--ds-text-secondary); margin-top:4px; }}
        .badge-admin {{ background:{theme['primary']}14; color:{theme['primary']}; padding:2px 10px;
            border-radius:20px; font-size:.75rem; font-weight:500; }}
        .badge-user {{ background:#f1f5f9; color:#475569; padding:2px 10px;
            border-radius:20px; font-size:.75rem; font-weight:500; }}
        .u-avatar {{ width:36px; height:36px; border-radius:50%;
            background:var(--ds-primary); display:flex; align-items:center; justify-content:center;
            color:white; font-weight:600; font-size:.875rem; flex-shrink:0; }}
        .admin-table-row {{ display:flex; align-items:center; gap:12px; padding:8px 0;
            border-bottom:1px solid var(--ds-border-light); transition:background .12s ease; }}
        .admin-table-row:hover {{ background:#f8fafc; }}
        .agent-card {{ padding:12px; border-radius:var(--ds-radius-md); border:1px solid var(--ds-border);
            background:white; text-align:center; transition:all .12s ease; }}
        .agent-card:hover {{ border-color:#cbd5e1; box-shadow:var(--ds-shadow-sm); }}
    </style>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown('<div class="admin-hdr"><span style="font-size:1.75rem;">🛡️</span><h1>Admin Panel</h1></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f"<div style='display:flex;align-items:center;gap:8px;justify-content:flex-end;margin-top:4px;'>"
                    f"<span style='font-size:.875rem;color:var(--ds-text-secondary);'>👤 {user_info.get('username','Admin')}</span></div>",
                    unsafe_allow_html=True)
        col2a, col2b = st.columns(2)
        with col2a:
            if st.button("← Chat", key="back_chat", use_container_width=True):
                st.query_params["page"] = PAGE_CHAT; st.rerun()
        with col2b:
            if st.button("Exit", key="admin_logout", use_container_width=True):
                logout()
    st.markdown("<hr style='margin:0 0 24px 0'>", unsafe_allow_html=True)

    # Stats row
    try:
        ud = ac.list_users(page=1, per_page=1)
        total_users = ud.get("total", 0)
    except AgentClientError:
        total_users = "?"
    n_agents = len(ac.info.agents) if ac.info else "?"
    n_models = len(ac.info.models) if ac.info else "?"
    d_agent = ac.info.default_agent if ac.info else "?"

    sc = st.columns(4)
    for ci, (val, lbl) in enumerate([
        (total_users, "Total Users"),
        (n_agents, "Available Agents"),
        (n_models, "LLM Models"),
        (d_agent[:18] + "…" if len(str(d_agent)) > 20 else d_agent, "Default Agent"),
    ]):
        with sc[ci]:
            style = "font-size:1rem;" if ci == 3 else ""
            st.markdown(f'<div class="stat-card"><div class="stat-val" style="{style}">{val}</div>'
                        f'<div class="stat-lbl">{lbl}</div></div>', unsafe_allow_html=True)

    # Agent overview
    st.markdown("<hr style='margin:24px 0'>", unsafe_allow_html=True)
    st.markdown("<h2 style='font-size:1.125rem;font-weight:600;margin-bottom:12px;'>🤖 Agent Overview</h2>", unsafe_allow_html=True)
    if ac.info and ac.info.agents:
        ag_cols = st.columns(4)
        for i, agent in enumerate(ac.info.agents):
            t = AGENT_THEMES.get(agent.key) or DEFAULT_THEME
            with ag_cols[i % 4]:
                st.markdown(
                    f'<div class="agent-card">'
                    f'<div style="font-size:1.25rem;">{t["icon"]}</div>'
                    f'<div style="font-weight:500;font-size:.8125rem;margin-top:4px;color:var(--ds-text-primary);">{agent.key}</div>'
                    f'<div style="font-size:.6875rem;color:var(--ds-text-muted);margin-top:2px;">{agent.description[:36]}…</div>'
                    f'</div>', unsafe_allow_html=True,
                )

    # User management
    st.markdown("<hr style='margin:24px 0'>", unsafe_allow_html=True)
    st.markdown("<h2 style='font-size:1.125rem;font-weight:600;margin-bottom:12px;'>👥 User Management</h2>", unsafe_allow_html=True)

    page_size = 10
    tp = max(1, (total_users + page_size - 1) // page_size) if isinstance(total_users, int) else 1
    pn = st.session_state.get("admin_page", 1)
    pc1, pc2, pc3 = st.columns([1, 3, 1])
    with pc1:
        if pn > 1 and st.button("← Previous", key="prev_page"):
            st.session_state.admin_page = pn - 1; st.rerun()
    with pc3:
        if pn < tp and st.button("Next →", key="next_page"):
            st.session_state.admin_page = pn + 1; st.rerun()
    with pc2:
        st.markdown(f"<div style='text-align:center;font-size:.75rem;color:var(--ds-text-muted);'>Page {pn} of {tp}</div>", unsafe_allow_html=True)

    try:
        ud = ac.list_users(page=pn, per_page=page_size)
        users = ud.get("users", [])
    except AgentClientError as e:
        st.error(f"Failed to load users: {e}"); users = []

    if users:
        for u in users:
            cols = st.columns([0.5, 1.5, 2, 1, 1.5, 1.8])
            with cols[0]:
                st.markdown(f'<div class="u-avatar">{u.get("username","?")[0].upper()}</div>', unsafe_allow_html=True)
            with cols[1]:
                st.markdown(f"<span style='font-size:.875rem;font-weight:500;'>{u.get('username','?')}</span>", unsafe_allow_html=True)
            with cols[2]:
                st.markdown(f"<span style='font-size:.75rem;color:var(--ds-text-secondary);'>{u.get('email','')}</span>", unsafe_allow_html=True)
            with cols[3]:
                role = u.get("role","user")
                st.markdown(f'<span class="badge-{"admin" if role=="admin" else "user"}">{role}</span>', unsafe_allow_html=True)
            with cols[4]:
                st.markdown(f"<span style='font-size:.75rem;color:var(--ds-text-muted);'>{(u.get('created_at') or '')[:10]}</span>", unsafe_allow_html=True)
            with cols[5]:
                uid = u.get("id")
                if uid != user_info.get("id"):
                    if role != "admin":
                        if st.button("Promote", key=f"p_{uid}", type="secondary", use_container_width=True):
                            try:
                                ac.update_user_role(uid, "admin"); st.rerun()
                            except AgentClientError as ex:
                                st.error(f"Failed: {ex}")
                    else:
                        if st.button("Demote", key=f"d_{uid}", type="secondary", use_container_width=True):
                            try:
                                ac.update_user_role(uid, "user"); st.rerun()
                            except AgentClientError as ex:
                                st.error(f"Failed: {ex}")
                else:
                    st.markdown("<span style='font-size:.75rem;color:var(--ds-text-muted);'>(You)</span>", unsafe_allow_html=True)
    else:
        st.info("No users found.")

    # ═══════════════════════════════════════════════════════════════
    #  Product Management
    # ═══════════════════════════════════════════════════════════════
    st.markdown("<hr style='margin:24px 0'>", unsafe_allow_html=True)
    st.markdown("<h2 style='font-size:1.125rem;font-weight:600;margin-bottom:12px;'>📦 商品管理</h2>", unsafe_allow_html=True)

    # Initialize from shared global store
    _init_global_products()

    # ── Product Form ──
    edit_prod = st.session_state.get("edit_prod_id", None)
    is_editing = edit_prod is not None
    form_ver = edit_prod["id"] if is_editing else "new"
    form_key = f"pf_{form_ver}"

    with st.container():
        st.markdown(f'<div class="stat-card" style="padding:20px;">', unsafe_allow_html=True)
        lbl = f"✏️ 编辑商品 {edit_prod['name']}" if is_editing else "➕ 添加新商品"
        st.markdown(f"<div style='font-size:.875rem;font-weight:600;margin-bottom:12px;'>{lbl}</div>", unsafe_allow_html=True)

        default_name = edit_prod["name"] if is_editing else ""
        default_cat = edit_prod["category"] if is_editing else ""
        # price stored as string so users can type "29.90/斤" or "299.00"
        default_price = str(edit_prod["price"]) + edit_prod.get("price_unit", "") if is_editing else ""
        default_stock = edit_prod["stock"] if is_editing else 0
        default_promo = "\n".join(edit_prod["promotions"]) if is_editing else ""
        default_specs = edit_prod["specs"] if is_editing else ""
        default_sp = edit_prod["selling_points"].replace("; ","\n") if is_editing else ""
        default_notes = edit_prod["notes"] if is_editing else ""

        fcols = st.columns([1.5, 1.2, 1.2, 0.8])
        with fcols[0]:
            prod_name = st.text_input("商品名称", value=default_name, key=f"pm_name_{form_key}")
        with fcols[1]:
            prod_cat = st.text_input("品类", value=default_cat, key=f"pm_cat_{form_key}", placeholder="如：美妆护肤")
        with fcols[2]:
            prod_price = st.text_input("原价", value=default_price, key=f"pm_price_{form_key}", placeholder="如：29.90/斤 或 299")
        with fcols[3]:
            prod_stock = st.number_input("库存", value=default_stock, min_value=0, step=100, key=f"pm_stock_{form_key}")

        fcols2 = st.columns(2)
        with fcols2[0]:
            prod_promo = st.text_area("优惠规则（每行一条）", value=default_promo, key=f"pm_promo_{form_key}", height=68)
            prod_specs = st.text_input("规格参数（如：容量:30ml, 产地:广州）", value=default_specs, key=f"pm_specs_{form_key}")
        with fcols2[1]:
            prod_sp = st.text_area("卖点（每行一条）", value=default_sp, key=f"pm_sp_{form_key}", height=68)
            prod_notes = st.text_input("注意事项", value=default_notes, key=f"pm_notes_{form_key}")

        act_cols = st.columns([1, 1, 6])
        with act_cols[0]:
            if st.button("💾 保存", key=f"pm_save_{form_key}", type="primary", use_container_width=True):
                errors = []
                if not prod_name:
                    errors.append("请填写商品名称")
                if not prod_cat:
                    errors.append("请填写品类")
                # Parse price string: "29.90/斤" → 29.90 + "/斤"
                price_raw = prod_price.strip()
                price_num = 0.0
                price_unit = "元"
                if not price_raw:
                    errors.append("请填写商品原价")
                else:
                    import re
                    m = re.match(r"^(\d+\.?\d*)(.*)", price_raw)
                    if m:
                        price_num = float(m.group(1))
                        unit_suffix = m.group(2).strip()
                        if unit_suffix:
                            price_unit = unit_suffix
                        if price_num <= 0:
                            errors.append("原价必须大于0")
                    else:
                        errors.append(f"原价格式不正确：{price_raw}")
                # Stock cannot be negative (0 is allowed for out-of-stock items)
                if prod_stock < 0:
                    errors.append("库存不能为负数")
                if errors:
                    st.error("；".join(errors))
                else:
                    entry = {
                        "productId": f"P{len(st.session_state.admin_products)+1:03d}" if not is_editing else edit_prod.get("productId", edit_prod["id"]),
                        "name": prod_name, "category": prod_cat, "price": price_num,
                        "price_unit": price_unit, "stock": prod_stock,
                        "promotions": [x.strip() for x in prod_promo.split("\n") if x.strip()],
                        "specs": prod_specs, "selling_points": prod_sp.replace("\n","; "),
                        "notes": prod_notes,
                    }
                    # 保留 HTML 商品管理(data_manager)写入的扩展字段，避免覆盖丢失
                    if is_editing:
                        for k_extra in ("soldCount", "rating", "reviewCount", "images", "sellingPoints",
                                        "description", "isOnSale", "addedTime", "isInLibrary", "imageUrl"):
                            if k_extra in edit_prod and k_extra not in entry:
                                entry[k_extra] = edit_prod[k_extra]
                    # Also set in-memory 'id' for frontend consistency
                    entry["id"] = entry["productId"]
                    if is_editing:
                        idx = next((i for i,p in enumerate(st.session_state.admin_products) if p["id"]==edit_prod["id"]), None)
                        if idx is not None:
                            st.session_state.admin_products[idx] = entry
                        st.session_state.pop("edit_prod_id", None)
                    else:
                        st.session_state.admin_products.append(entry)
                    _save_products_to_file(st.session_state.admin_products)  # ⬅️ persist to file
                    st.rerun()
        with act_cols[1]:
            if is_editing and st.button("取消", key=f"pm_cancel_{form_key}", use_container_width=True):
                st.session_state.pop("edit_prod_id", None); st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Product List ──
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    products = st.session_state.admin_products
    if products:
        for i, p in enumerate(products):
            cols = st.columns([0.4, 1.8, 1, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8])
            with cols[0]:
                st.markdown(f'<div class="u-avatar" style="background:#2563eb;width:32px;height:32px;font-size:.75rem;">{p["id"][-3:]}</div>', unsafe_allow_html=True)
            with cols[1]:
                st.markdown(f"<span style='font-size:.8125rem;font-weight:500;'>{p['name']}</span>", unsafe_allow_html=True)
            with cols[2]:
                st.markdown(f"<span style='font-size:.75rem;color:#64748b;'>{p['category']}</span>", unsafe_allow_html=True)
            with cols[3]:
                unit = p.get("price_unit", "元")
                st.markdown(f"<span style='font-size:.8125rem;font-weight:600;color:#dc2626;'>¥{p['price']:.2f}{unit}</span>", unsafe_allow_html=True)
            with cols[4]:
                st.markdown(f"<span style='font-size:.75rem;color:#475569;'>库存 {p['stock']}</span>", unsafe_allow_html=True)
            with cols[5]:
                sp = p.get("selling_points","")
                st.markdown(f"<span style='font-size:.6875rem;color:#94a3b8;'>{sp[:16]}{'…' if len(sp)>16 else ''}</span>", unsafe_allow_html=True)
            with cols[6]:
                promo_list = p.get("promotions",[])
                promo_txt = promo_list[0][:12] if promo_list else "—"
                st.markdown(f"<span style='font-size:.6875rem;color:#94a3b8;'>{promo_txt}</span>", unsafe_allow_html=True)
            with cols[7]:
                if st.button("✏️", key=f"pm_edit_{i}", help="编辑"):
                    st.session_state.edit_prod_id = p
                    st.rerun()
            with cols[8]:
                if st.button("🗑️", key=f"pm_del_{i}", help="删除"):
                    st.session_state.admin_products.pop(i)
                    _save_products_to_file(st.session_state.admin_products)  # ⬅️ persist to file
                    st.rerun()
        st.markdown(f"<div style='font-size:.75rem;color:#94a3b8;padding:8px 0;'>共 {len(products)} 个商品</div>", unsafe_allow_html=True)
    else:
        st.info("暂无商品数据")


# ═══════════════════════════════════════════════════════════════
#  Chat message & feedback rendering
# ═══════════════════════════════════════════════════════════════

async def draw_messages(agen: AsyncGenerator[ChatMessage | str, None], is_new: bool = False) -> None:
    """Render chat messages from an async generator, with streaming support."""
    last_msg_type = None
    st.session_state.last_message = None
    stream_content = ""
    stream_ph = None

    while True:
        try:
            msg = await agen.__anext__()
        except StopAsyncIteration:
            break

        # ── Streaming token ──
        if isinstance(msg, str):
            if not stream_ph:
                if last_msg_type != "ai":
                    last_msg_type = "ai"
                    st.session_state.last_message = st.chat_message("ai")
                with st.session_state.last_message:
                    stream_ph = st.empty()
            stream_content += msg
            stream_ph.write(stream_content + "▌")
            continue

        if not isinstance(msg, ChatMessage):
            st.error(f"Unexpected: {type(msg)}")
            st.write(msg)
            st.stop()

        # ── Human message ──
        if msg.type == "human":
            last_msg_type = "human"
            st.chat_message("human").write(msg.content)
            continue

        # ── AI message ──
        if msg.type == "ai":
            if is_new:
                st.session_state.messages.append(msg)
            if last_msg_type != "ai":
                last_msg_type = "ai"
                st.session_state.last_message = st.chat_message("ai")
            with st.session_state.last_message:
                if msg.content:
                    if stream_ph:
                        stream_ph.write(msg.content)
                        stream_content = ""
                        stream_ph = None
                    else:
                        st.write(msg.content)
                if msg.tool_calls:
                    await _render_tool_calls_async(msg.tool_calls, agen, is_new)
            continue

        # ── Custom (task) message ──
        if msg.type == "custom":
            try:
                td = TaskData.model_validate(msg.custom_data)
            except ValidationError:
                st.error("Invalid CustomData")
                st.write(msg.custom_data)
                st.stop()
            if is_new:
                st.session_state.messages.append(msg)
            if last_msg_type != "task":
                last_msg_type = "task"
                st.session_state.last_message = st.chat_message(
                    name="task", avatar=":material/manufacturing:")
                with st.session_state.last_message:
                    status = TaskDataStatus()
            status.add_and_draw_task_data(td)
            continue

        # ── Tool message (standalone) ──
        if msg.type == "tool":
            if is_new:
                st.session_state.messages.append(msg)
            with st.chat_message("tool"):
                st.code(msg.content[:600], language="text")
            continue

        st.error(f"Unknown type: {msg.type}")
        st.write(msg)
        st.stop()


async def _render_tool_calls_async(tool_calls: list, agen: AsyncGenerator, is_new: bool) -> None:
    """Render tool call status widgets, reading tool results from the agen (async)."""
    call_map: dict[str, Any] = {}
    for tc in tool_calls:
        label = f"💼 {tc['name']}" if "transfer_to" in tc["name"] else f"🛠️ {tc['name']}"
        s = st.status(label, state="running" if is_new else "complete")
        call_map[tc["id"]] = s

    for tc in tool_calls:
        s = call_map[tc["id"]]
        if "transfer_to" in tc["name"]:
            s.update(expanded=True)
            await _handle_sub_agent_async(agen, s, is_new)
            break

        s.write("**Input:**")
        s.code(json.dumps(tc["args"], indent=2, ensure_ascii=False), language="json")

        # Read the next ToolMessage from the stream
        try:
            tr: ChatMessage = await agen.__anext__()
        except StopAsyncIteration:
            break
        if tr.type != "tool":
            continue
        if is_new:
            st.session_state.messages.append(tr)
        sid = tr.tool_call_id
        if sid and sid in call_map:
            s = call_map[sid]
        s.write("**Output:**")
        s.write(tr.content[:600])
        s.update(state="complete")


async def _handle_sub_agent_async(agen, status, is_new):
    """Recursively handle sub-agent tool call streams."""
    nested = {}
    try:
        first = await agen.__anext__()
        if is_new:
            st.session_state.messages.append(first)
    except StopAsyncIteration:
        return
    while True:
        try:
            sm = await agen.__anext__()
        except StopAsyncIteration:
            break
        if is_new:
            st.session_state.messages.append(sm)
        if sm.type == "tool" and sm.tool_call_id in nested:
            nested[sm.tool_call_id].write("**Output:**")
            nested[sm.tool_call_id].write(sm.content)
            continue
        if hasattr(sm, "tool_calls") and sm.tool_calls and any(
            "transfer_back_to" in tc.get("name", "") for tc in sm.tool_calls
        ):
            for tc in sm.tool_calls:
                if "transfer_back_to" in tc.get("name", ""):
                    try:
                        tr = await agen.__anext__()
                        if is_new:
                            st.session_state.messages.append(tr)
                    except StopAsyncIteration:
                        pass
            if status:
                status.update(state="complete")
            break
        if status:
            if sm.content:
                status.write(sm.content)
            if hasattr(sm, "tool_calls") and sm.tool_calls:
                for tc in sm.tool_calls:
                    if "transfer_to" in tc["name"]:
                        ns = status.status(
                            f"💼 {tc['name']}",
                            state="running" if is_new else "complete",
                            expanded=True,
                        )
                        await _handle_sub_agent_async(agen, ns, is_new)
                    else:
                        pop = status.popover(f"{tc['name']}", icon="🛠️")
                        pop.write(f"**Tool:** {tc['name']}")
                        pop.write("**Input:**")
                        pop.write(tc["args"])
                        nested[tc["id"]] = pop


async def handle_feedback() -> None:
    if "last_feedback" not in st.session_state:
        st.session_state.last_feedback = (None, None)
    rid = st.session_state.messages[-1].run_id
    fb = st.feedback("stars", key=rid)
    if fb is not None and (rid, fb) != st.session_state.last_feedback:
        score = (fb + 1) / 5.0
        try:
            await st.session_state.agent_client.acreate_feedback(run_id=rid, key="human-feedback-stars", score=score, kwargs={"comment": "In-line human feedback"})
            st.session_state.last_feedback = (rid, fb)
            st.toast("Feedback recorded", icon="⭐")
        except AgentClientError as e:
            st.error(f"Feedback error: {e}"); st.stop()


# ═══════════════════════════════════════════════════════════════
#  Main app
# ═══════════════════════════════════════════════════════════════

def _get_agent_url() -> str:
    u = os.getenv("AGENT_URL")
    if u:
        return u
    host = os.getenv("HOST", "0.0.0.0")
    port = os.getenv("PORT", "8080")
    return f"http://{host}:{port}"


async def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon=APP_ICON, menu_items={}, layout="wide")
    load_dotenv()

    # ═══ Design System — Global CSS ═══
    st.html("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        :root {
            --ds-primary: #2563eb;
            --ds-primary-hover: #1d4ed8;
            --ds-primary-light: #eff6ff;
            --ds-bg-page: #f1f5f9;
            --ds-bg-surface: #ffffff;
            --ds-bg-sidebar: #ffffff;
            --ds-text-primary: #0f172a;
            --ds-text-secondary: #475569;
            --ds-text-muted: #94a3b8;
            --ds-border: #e2e8f0;
            --ds-border-light: #f1f5f9;
            --ds-radius-sm: 6px;
            --ds-radius-md: 8px;
            --ds-radius-lg: 12px;
            --ds-shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
            --ds-shadow-md: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
            --ds-font: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }

        /* ── Global base ── */
        html, body, [class*="css"] {
            font-family: var(--ds-font) !important;
        }
        .stApp {
            background: var(--ds-bg-page);
        }

        /* ── Hide Streamlit chrome ── */
        [data-testid="stStatusWidget"] { visibility:hidden; height:0; position:fixed; }
        #MainMenu { visibility:hidden; }
        footer { display:none !important; }

        /* ── Buttons ── */
        .stButton button {
            font-family: var(--ds-font) !important;
            border-radius: var(--ds-radius-md) !important;
            font-weight: 500 !important;
            font-size: 0.875rem !important;
            transition: all 0.15s ease !important;
        }
        .stButton button[kind="primary"] {
            background: var(--ds-primary) !important;
            border: 1px solid var(--ds-primary) !important;
            color: white !important;
        }
        .stButton button[kind="primary"]:hover {
            background: var(--ds-primary-hover) !important;
            border-color: var(--ds-primary-hover) !important;
            box-shadow: 0 2px 8px rgba(37,99,235,0.25) !important;
        }
        .stButton button[kind="secondary"] {
            background: transparent !important;
            border: 1px solid var(--ds-border) !important;
            color: var(--ds-text-secondary) !important;
        }
        .stButton button[kind="secondary"]:hover {
            background: var(--ds-bg-page) !important;
            border-color: #cbd5e1 !important;
            color: var(--ds-text-primary) !important;
        }
        .stButton button[kind="secondaryFormSubmit"] {
            background: var(--ds-primary) !important;
            color: white !important;
            border: none !important;
        }
        .stButton button[kind="secondaryFormSubmit"]:hover {
            background: var(--ds-primary-hover) !important;
        }

        /* ── Select / Input ── */
        .stSelectbox div[data-baseweb="select"] > div {
            border-radius: var(--ds-radius-md) !important;
            border-color: var(--ds-border) !important;
        }
        .stTextInput input {
            border-radius: var(--ds-radius-md) !important;
            border: 1.5px solid var(--ds-border) !important;
            font-size: 0.875rem !important;
            font-family: var(--ds-font) !important;
            transition: border-color 0.15s ease, box-shadow 0.15s ease !important;
        }
        .stTextInput input:focus {
            border-color: var(--ds-primary) !important;
            box-shadow: 0 0 0 3px rgba(37,99,235,0.1) !important;
        }

        /* ── Chat input ── */
        .stChatInputContainer {
            border: 1.5px solid var(--ds-border) !important;
            border-radius: 10px !important;
            transition: border-color 0.15s ease, box-shadow 0.15s ease !important;
            background: var(--ds-bg-surface) !important;
        }
        .stChatInputContainer:focus-within {
            border-color: var(--ds-primary) !important;
            box-shadow: 0 0 0 3px rgba(37,99,235,0.08) !important;
        }
        .stChatInputContainer input {
            font-family: var(--ds-font) !important;
            font-size: 0.875rem !important;
        }
        .stChatInputContainer input::placeholder {
            color: var(--ds-text-muted) !important;
        }

        /* ── Sidebar ── */
        section[data-testid="stSidebar"] {
            background: var(--ds-bg-sidebar) !important;
            border-right: 1px solid var(--ds-border) !important;
        }
        section[data-testid="stSidebar"] .stButton button {
            font-size: 0.8125rem !important;
        }

        /* ── Toggle / Checkbox ── */
        .stToggle label, .stCheckbox label {
            font-family: var(--ds-font) !important;
            font-size: 0.8125rem !important;
        }

        /* ── Status / Toast ── */
        div[data-testid="stStatus"] { border-radius: var(--ds-radius-md) !important; }
        div[data-baseweb="toast"] { border-radius: var(--ds-radius-md) !important; }
        div[data-testid="stNotification"] { border-radius: var(--ds-radius-md) !important; }

        /* ── Dividers ── */
        hr { border-color: var(--ds-border) !important; margin: 0.75rem 0 !important; }

        /* ── Scrollbar ── */
        ::-webkit-scrollbar { width: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

        /* ── Tab / Popover ── */
        div[data-testid="stPopover"] button { border-radius: var(--ds-radius-md) !important; }

        /* ── Spinner ── */
        .stSpinner { border-color: var(--ds-primary) !important; }

        /* ── Feedback stars ── */
        .stFeedback svg { fill: #f59e0b !important; }
    </style>
    """)
    if st.get_option("client.toolbarMode") != "minimal":
        st.set_option("client.toolbarMode", "minimal")
        await asyncio.sleep(0.1)
        st.rerun()

    page = st.query_params.get("page", PAGE_LOGIN)

    if is_authenticated():
        # Ensure agent client exists for all authenticated pages
        if "agent_client" not in st.session_state:
            token = st.session_state.get(AUTH_TOKEN_KEY)
            try:
                with st.spinner("Connecting to agent service…"):
                    st.session_state.agent_client = AgentClient(base_url=_get_agent_url(), token=token)
            except AgentClientError as e:
                st.error(f"Connection error: {e}")
                st.markdown("The service might be booting up. Try again.")
                st.stop()

        if page == PAGE_ADMIN and is_admin():
            render_admin_page()
            return

        if "voice_manager" not in st.session_state:
            st.session_state.voice_manager = VoiceManager.from_env()
        init_session_manager()
        await render_chat_interface()
        return

    if page == PAGE_REGISTER:
        render_register_page()
    else:
        render_login_page()


if __name__ == "__main__":
    asyncio.run(main())
