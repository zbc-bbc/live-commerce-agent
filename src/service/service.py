import inspect
import json
import logging
import warnings
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from langchain_core._api import LangChainBetaWarning
from langchain_core.messages import AIMessage, AIMessageChunk, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langfuse import Langfuse  # type: ignore[import-untyped]
from langfuse.langchain import (
    CallbackHandler,  # type: ignore[import-untyped]
)
from langgraph.types import Command, Interrupt
from langsmith import Client as LangsmithClient
from langsmith import uuid7

from agents import DEFAULT_AGENT, AgentGraph, get_agent, get_all_agent_info, load_agent
from core import settings
from memory import initialize_database, initialize_store
from schema import (
    ChatHistory,
    ChatHistoryInput,
    ChatMessage,
    Feedback,
    FeedbackResponse,
    ServiceMetadata,
    StreamInput,
    UserInput,
)
from service.admin_routes import router as admin_router
from service.auth import HOST_COOKIE, get_user_from_token, init_auth_db
from service.auth_middleware import JWTAuthMiddleware
from service.viewer_auth import init_viewer_db
from service.auth_routes import get_current_user, router as auth_router
from service.upload_routes import router as upload_router, UPLOADS_DIR
from service.product_image_routes import router as product_image_router
from service.utils import (
    convert_message_content_to_string,
    langchain_to_chat_message,
    remove_tool_calls,
)

warnings.filterwarnings("ignore", category=LangChainBetaWarning)
logger = logging.getLogger(__name__)


def custom_generate_unique_id(route: APIRoute) -> str:
    """Generate idiomatic operation IDs for OpenAPI client generation."""
    return route.name


def verify_bearer(
    http_auth: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(HTTPBearer(description="Please provide AUTH_SECRET api key.", auto_error=False)),
    ],
) -> None:
    if not settings.AUTH_SECRET:
        return
    auth_secret = settings.AUTH_SECRET.get_secret_value()
    if not http_auth or http_auth.credentials != auth_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Configurable lifespan that initializes the appropriate database checkpointer, store,
    and agents with async loading - for example for starting up MCP clients.
    """
    try:
        # Initialize auth database (users, tokens)
        init_auth_db()

        # Initialize viewer (audience) account database — separate from auth.db
        init_viewer_db()

        # Initialize both checkpointer (for short-term memory) and store (for long-term memory)
        async with initialize_database() as saver, initialize_store() as store:
            # Set up both components
            if hasattr(saver, "setup"):  # ignore: union-attr
                await saver.setup()
            # Only setup store for Postgres as InMemoryStore doesn't need setup
            if hasattr(store, "setup"):  # ignore: union-attr
                await store.setup()

            if not settings.AUTH_SECRET:
                logger.warning(
                    "AUTH_SECRET is not configured — all API endpoints are unauthenticated. "
                    "Set AUTH_SECRET in your environment to enable bearer token authentication."
                )

            # Configure agents with both memory components and async loading
            agents = get_all_agent_info()
            for a in agents:
                try:
                    await load_agent(a.key)
                    logger.info(f"Agent loaded: {a.key}")
                except Exception as e:
                    logger.error(f"Failed to load agent {a.key}: {e}")
                    # Continue with other agents rather than failing startup

                agent = get_agent(a.key)
                # Set checkpointer for thread-scoped memory (conversation history)
                agent.checkpointer = saver
                # Set store for long-term memory (cross-conversation knowledge)
                agent.store = store
            # Initialize Live Commerce data
            try:
                from data.live_commerce.data_manager import get_data_manager
                dm = get_data_manager()
                if not dm.products:
                    dm.generate_mock_data()
                    logger.info("Live Commerce 模拟数据已初始化")
                else:
                    # 每次启动都刷新弹幕/评价/会话等模拟数据（页面数据才真实）
                    dm.generate_mock_data()
                    logger.info("Live Commerce 数据已刷新")
            except Exception as e:
                logger.error(f"Live Commerce 数据管理器初始化失败: {e}")
            except Exception as e:
                logger.error(f"Live Commerce 数据管理器初始化失败: {e}")
            yield
    except Exception as e:
        logger.error(f"Error during database/store/agents initialization: {e}")
        raise


app = FastAPI(lifespan=lifespan, generate_unique_id_function=custom_generate_unique_id)

# 全局 JWT 校验中间件。先注册它、再注册 CORS —— Starlette 后注册的中间件在最外层，
# 这样 401/403 响应也会带上 CORS 头，跨域前端才能读到具体错误。
app.add_middleware(JWTAuthMiddleware)

# CORS middleware - allow Streamlit frontend and other origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(dependencies=[Depends(verify_bearer)])


@router.get("/info")
async def info() -> ServiceMetadata:
    models = list(settings.AVAILABLE_MODELS)
    models.sort()
    return ServiceMetadata(
        agents=get_all_agent_info(),
        models=models,
        default_agent=DEFAULT_AGENT,
        default_model=settings.DEFAULT_MODEL,
    )


async def _handle_input(user_input: UserInput, agent: AgentGraph) -> tuple[dict[str, Any], UUID]:
    """
    Parse user input and handle any required interrupt resumption.
    Returns kwargs for agent invocation and the run_id.
    """
    run_id = uuid7()
    thread_id = user_input.thread_id or str(uuid4())
    user_id = user_input.user_id or str(uuid4())

    configurable = {"thread_id": thread_id, "user_id": user_id}
    if user_input.model is not None:
        configurable["model"] = user_input.model

    callbacks: list[Any] = []
    if settings.LANGFUSE_TRACING:
        # Initialize Langfuse CallbackHandler for Langchain (tracing)
        langfuse_handler = CallbackHandler()

        callbacks.append(langfuse_handler)

    if user_input.agent_config:
        # Check for reserved keys (including 'model' even if not in configurable)
        reserved_keys = {"thread_id", "user_id", "model"}
        if overlap := reserved_keys & user_input.agent_config.keys():
            raise HTTPException(
                status_code=422,
                detail=f"agent_config contains reserved keys: {overlap}",
            )
        configurable.update(user_input.agent_config)

    config = RunnableConfig(
        configurable=configurable,
        run_id=run_id,
        callbacks=callbacks,
    )

    # Check for interrupts that need to be resumed
    state = await agent.aget_state(config=config)
    interrupted_tasks = [
        task for task in state.tasks if hasattr(task, "interrupts") and task.interrupts
    ]

    input: Command | dict[str, Any]
    if interrupted_tasks:
        # assume user input is response to resume agent execution from interrupt
        input = Command(resume=user_input.message)
    else:
        input = {"messages": [HumanMessage(content=user_input.message)]}

    kwargs = {
        "input": input,
        "config": config,
    }

    return kwargs, run_id


@router.post("/{agent_id}/invoke", operation_id="invoke_with_agent_id")
@router.post("/invoke")
async def invoke(user_input: UserInput, agent_id: str = DEFAULT_AGENT) -> ChatMessage:
    """
    Invoke an agent with user input to retrieve a final response.

    If agent_id is not provided, the default agent will be used.
    Use thread_id to persist and continue a multi-turn conversation. run_id kwarg
    is also attached to messages for recording feedback.
    Use user_id to persist and continue a conversation across multiple threads.
    """
    # NOTE: Currently this only returns the last message or interrupt.
    # In the case of an agent outputting multiple AIMessages (such as the background step
    # in interrupt-agent, or a tool step in research-assistant), it's omitted. Arguably,
    # you'd want to include it. You could update the API to return a list of ChatMessages
    # in that case.
    agent: AgentGraph = get_agent(agent_id)
    kwargs, run_id = await _handle_input(user_input, agent)

    try:
        response_events: list[tuple[str, Any]] = await agent.ainvoke(**kwargs, stream_mode=["updates", "values"])  # type: ignore # fmt: skip
        response_type, response = response_events[-1]
        if response_type == "values":
            # Normal response, the agent completed successfully
            output = langchain_to_chat_message(response["messages"][-1])
        elif response_type == "updates" and "__interrupt__" in response:
            # The last thing to occur was an interrupt
            # Return the value of the first interrupt as an AIMessage
            output = langchain_to_chat_message(
                AIMessage(content=response["__interrupt__"][0].value)
            )
        else:
            raise ValueError(f"Unexpected response type: {response_type}")

        output.run_id = str(run_id)
        return output
    except Exception as e:
        logger.error(f"An exception occurred: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error")


async def message_generator(
    user_input: StreamInput, agent_id: str = DEFAULT_AGENT
) -> AsyncGenerator[str, None]:
    """
    Generate a stream of messages from the agent.

    This is the workhorse method for the /stream endpoint.
    """
    agent: AgentGraph = get_agent(agent_id)
    kwargs, run_id = await _handle_input(user_input, agent)

    try:
        # Process streamed events from the graph and yield messages over the SSE stream.
        async for stream_event in agent.astream(
            **kwargs, stream_mode=["updates", "messages", "custom"], subgraphs=True
        ):
            if not isinstance(stream_event, tuple):
                continue
            # Handle different stream event structures based on subgraphs
            if len(stream_event) == 3:
                # With subgraphs=True: (node_path, stream_mode, event)
                _, stream_mode, event = stream_event
            else:
                # Without subgraphs: (stream_mode, event)
                stream_mode, event = stream_event
            new_messages = []
            if stream_mode == "updates":
                for node, updates in event.items():
                    # A simple approach to handle agent interrupts.
                    # In a more sophisticated implementation, we could add
                    # some structured ChatMessage type to return the interrupt value.
                    if node == "__interrupt__":
                        interrupt: Interrupt
                        for interrupt in updates:
                            new_messages.append(AIMessage(content=interrupt.value))
                        continue
                    updates = updates or {}
                    update_messages = updates.get("messages", [])
                    # special cases for using langgraph-supervisor library
                    if "supervisor" in node or "sub-agent" in node:
                        # the only tools that come from the actual agent are the handoff and handback tools
                        if isinstance(update_messages[-1], ToolMessage):
                            if "sub-agent" in node and len(update_messages) > 1:
                                # If this is a sub-agent, we want to keep the last 2 messages - the handback tool, and it's result
                                update_messages = update_messages[-2:]
                            else:
                                # If this is a supervisor, we want to keep the last message only - the handoff result. The tool comes from the 'agent' node.
                                update_messages = [update_messages[-1]]
                        else:
                            update_messages = []
                    new_messages.extend(update_messages)

            if stream_mode == "custom":
                new_messages = [event]

            # LangGraph streaming may emit tuples: (field_name, field_value)
            # e.g. ('content', <str>), ('tool_calls', [ToolCall,...]), ('additional_kwargs', {...}), etc.
            # We accumulate only supported fields into `parts` and skip unsupported metadata.
            # More info at: https://langchain-ai.github.io/langgraph/cloud/how-tos/stream_messages/
            processed_messages = []
            current_message: dict[str, Any] = {}
            for message in new_messages:
                if isinstance(message, tuple):
                    key, value = message
                    # Store parts in temporary dict
                    current_message[key] = value
                else:
                    # Add complete message if we have one in progress
                    if current_message:
                        processed_messages.append(_create_ai_message(current_message))
                        current_message = {}
                    processed_messages.append(message)

            # Add any remaining message parts
            if current_message:
                processed_messages.append(_create_ai_message(current_message))

            for message in processed_messages:
                try:
                    chat_message = langchain_to_chat_message(message)
                    chat_message.run_id = str(run_id)
                except Exception as e:
                    logger.error(f"Error parsing message: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'content': 'Unexpected error'})}\n\n"
                    continue
                # LangGraph re-sends the input message, which feels weird, so drop it
                if chat_message.type == "human" and chat_message.content == user_input.message:
                    continue
                yield f"data: {json.dumps({'type': 'message', 'content': chat_message.model_dump()})}\n\n"

            if stream_mode == "messages":
                if not user_input.stream_tokens:
                    continue
                msg, metadata = event
                if "skip_stream" in metadata.get("tags", []):
                    continue
                # For some reason, astream("messages") causes non-LLM nodes to send extra messages.
                # Drop them.
                if not isinstance(msg, AIMessageChunk):
                    continue
                content = remove_tool_calls(msg.content)
                if content:
                    # Empty content in the context of OpenAI usually means
                    # that the model is asking for a tool to be invoked.
                    # So we only print non-empty content.
                    yield f"data: {json.dumps({'type': 'token', 'content': convert_message_content_to_string(content)})}\n\n"
    except Exception as e:
        logger.error(f"Error in message generator: {e}")
        yield f"data: {json.dumps({'type': 'error', 'content': 'Internal server error'})}\n\n"
    finally:
        yield "data: [DONE]\n\n"


def _create_ai_message(parts: dict) -> AIMessage:
    sig = inspect.signature(AIMessage)
    valid_keys = set(sig.parameters)
    filtered = {k: v for k, v in parts.items() if k in valid_keys}
    return AIMessage(**filtered)


def _sse_response_example() -> dict[int | str, Any]:
    return {
        status.HTTP_200_OK: {
            "description": "Server Sent Event Response",
            "content": {
                "text/event-stream": {
                    "example": "data: {'type': 'token', 'content': 'Hello'}\n\ndata: {'type': 'token', 'content': ' World'}\n\ndata: [DONE]\n\n",
                    "schema": {"type": "string"},
                }
            },
        }
    }


@router.post(
    "/{agent_id}/stream",
    response_class=StreamingResponse,
    responses=_sse_response_example(),
    operation_id="stream_with_agent_id",
)
@router.post("/stream", response_class=StreamingResponse, responses=_sse_response_example())
async def stream(user_input: StreamInput, agent_id: str = DEFAULT_AGENT) -> StreamingResponse:
    """
    Stream an agent's response to a user input, including intermediate messages and tokens.

    If agent_id is not provided, the default agent will be used.
    Use thread_id to persist and continue a multi-turn conversation. run_id kwarg
    is also attached to all messages for recording feedback.
    Use user_id to persist and continue a conversation across multiple threads.

    Set `stream_tokens=false` to return intermediate messages but not token-by-token.
    """
    return StreamingResponse(
        message_generator(user_input, agent_id),
        media_type="text/event-stream",
    )


@router.post("/feedback")
async def feedback(feedback: Feedback) -> FeedbackResponse:
    """
    Record feedback for a run to LangSmith.

    This is a simple wrapper for the LangSmith create_feedback API, so the
    credentials can be stored and managed in the service rather than the client.
    See: https://api.smith.langchain.com/redoc#tag/feedback/operation/create_feedback_api_v1_feedback_post
    """
    client = LangsmithClient()
    kwargs = feedback.kwargs or {}
    client.create_feedback(
        run_id=feedback.run_id,
        key=feedback.key,
        score=feedback.score,
        **kwargs,
    )
    return FeedbackResponse()


@router.post("/history")
async def history(input: ChatHistoryInput) -> ChatHistory:
    """
    Get chat history.
    """
    # TODO: Hard-coding DEFAULT_AGENT here is wonky
    agent: AgentGraph = get_agent(DEFAULT_AGENT)
    try:
        state_snapshot = await agent.aget_state(
            config=RunnableConfig(configurable={"thread_id": input.thread_id})
        )
        messages: list[AnyMessage] = state_snapshot.values["messages"]
        chat_messages: list[ChatMessage] = [langchain_to_chat_message(m) for m in messages]
        return ChatHistory(messages=chat_messages)
    except Exception as e:
        logger.error(f"An exception occurred: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error")


@app.get("/health")
async def health_check():
    """Health check endpoint."""

    health_status = {"status": "ok"}

    if settings.LANGFUSE_TRACING:
        try:
            langfuse = Langfuse()
            health_status["langfuse"] = "connected" if langfuse.auth_check() else "disconnected"
        except Exception as e:
            logger.error(f"Langfuse connection error: {e}")
            health_status["langfuse"] = "disconnected"

    return health_status


@app.get("/")
async def root():
    """Root endpoint — redirects to API docs."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs")


# 注册 WebSocket 端点
try:
    from service.live_commerce_ws import live_commerce_ws_handler
    @app.websocket("/ws/live-commerce")
    async def lc_websocket_endpoint(websocket: WebSocket):
        await live_commerce_ws_handler(websocket)
    logger.info("Live Commerce WebSocket 端点已注册")
except Exception as e:
    logger.error(f"Live Commerce WebSocket 端点注册失败: {e}")

# 注册静态文件服务（前端页面）
import os

# 观众端 · 直播间 H5 —— 独立目录 src/viewer，与运营台**不再共用目录**，
# 可整目录拷到 nginx/OSS/CDN 单独部署：后端地址由页面内的 LC_API 配置，
# 同源时留空即可（见 src/viewer/index.html 顶部注释）。本地仍由本服务托管。
#
# 【注册顺序】这一整块必须早于下面的 /live-commerce mount：Starlette 按注册顺序
# 取首个匹配，而 Mount 会吃掉 /live-commerce/ 之后的全部路径（下面那条旧地址
# 跳转就是被它吃掉的反例）。
from fastapi.responses import HTMLResponse, RedirectResponse
viewer_dir = os.path.join(os.path.dirname(__file__), "..", "viewer")
viewer_file = os.path.join(viewer_dir, "index.html")
if os.path.exists(viewer_file):
    @app.get("/viewer", response_class=HTMLResponse)
    async def viewer_page():
        # 每次请求都从磁盘读：改完 HTML 刷新浏览器即可生效，不必重启服务
        with open(viewer_file, "r", encoding="utf-8") as f:
            return f.read()
    logger.info("观众端路由: /viewer")

    # 旧地址 /live-commerce/viewer.html 随文件搬走而失效，跳转一下免得老书签 404。
    # 注册晚了它就是死代码：请求会落到 StaticFiles 上，文件已不在，答 404。
    @app.get("/live-commerce/viewer.html")
    async def viewer_legacy_redirect():
        return RedirectResponse(url="/viewer")
else:
    logger.warning(f"观众端目录不存在: {viewer_dir}")

# 运营台静态资源（viewer.html 已移出本目录，其余不动）
live_commerce_dir = os.path.join(os.path.dirname(__file__), "..", "live_commerce")
if os.path.exists(live_commerce_dir):

    # ── 运营台首页闸门 ──
    # 【注册顺序】必须早于下面的 mount：Starlette 按注册顺序取首个匹配，而
    # StaticFiles(html=True) 会把 /live-commerce/ 直接映射成 index.html。抢在它
    # 前面注册，未登录才是 302 去登录页；否则是"整页控制台先发下去、再由前端 JS
    # 跳登录"—— curl 不带任何凭证就能把整个运营台拿走。
    live_commerce_index = os.path.join(live_commerce_dir, "index.html")
    if os.path.exists(live_commerce_index):

        async def host_console_user(request: Request) -> dict | None:
            """认出访问运营台的人。先看闸门 cookie（浏览器地址栏导航带不了
            Authorization 头，只能靠它），再看 Authorization 头（方便 curl /
            脚本）。认不出返回 None。"""
            token = request.cookies.get(HOST_COOKIE, "")
            if not token:
                header = request.headers.get("authorization", "")
                if header[:7].lower() == "bearer ":
                    token = header[7:].strip()
            if not token:
                return None
            return await get_user_from_token(token)

        @app.get("/logout")
        async def host_logout():
            """退出运营台：清掉闸门 cookie 再回登录页。前端 localStorage 里的
            token 由页面自己清（见 index.html 的 doLogout）。"""
            resp = RedirectResponse(url="/login", status_code=302)
            resp.delete_cookie(HOST_COOKIE, path="/")
            return resp

        @app.get("/live-commerce/", response_class=HTMLResponse)
        @app.get("/live-commerce/index.html", response_class=HTMLResponse)
        async def host_console_page(request: Request):
            """登录才发控制台，否则一律先去登录页。"""
            if not await host_console_user(request):
                return RedirectResponse(url="/login", status_code=302)
            # 每次请求都从磁盘读：改完 HTML 刷新浏览器即可生效，不必重启服务
            with open(live_commerce_index, "r", encoding="utf-8") as f:
                return f.read()

        logger.info("运营台首页闸门已启用: /live-commerce/ 需登录，退出走 /logout")

    app.mount("/live-commerce", StaticFiles(directory=live_commerce_dir, html=True), name="live_commerce")
    logger.info(f"Live Commerce 前端已挂载: {live_commerce_dir}")

    # 添加独立登录页路由（避免静态文件缓存或路径问题）
    login_file = os.path.join(live_commerce_dir, "login.html")
    if os.path.exists(login_file):
        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            with open(login_file, "r", encoding="utf-8") as f:
                return f.read()
        logger.info("登录页面路由: /login")
else:
    logger.warning(f"Live Commerce 前端目录不存在: {live_commerce_dir}")

# 头像上传：读写目录 + 静态访问 /uploads/<file>（POST 走 /api/avatar/upload）
try:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
    logger.info(f"头像上传目录已就绪: {UPLOADS_DIR}  → /uploads")
except Exception as e:
    logger.warning(f"头像上传目录初始化失败: {e}")

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(upload_router)
app.include_router(product_image_router)
app.include_router(router)
