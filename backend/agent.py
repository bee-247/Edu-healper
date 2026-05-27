from dotenv import load_dotenv
import os
import json
import asyncio
from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk, SystemMessage
from education.agents.registry import get_teacher_agent_specs
from education.agents.router import TeacherSubTask, plan_teacher_tasks, route_teacher_agent
from education.tool_context import reset_teacher_username, set_teacher_username
from tools import (
    get_last_rag_context,
    reset_tool_call_guards,
    set_rag_step_queue,
)
from datetime import datetime
from cache import cache
from database import SessionLocal
from models import User, ChatSession, ChatMessage
from token_usage_tracker import (
    get_session_token_usage,
    record_token_usage_from_message,
    record_token_usage_from_messages,
    reset_active_token_usage_session,
    set_active_token_usage_session,
)

load_dotenv()

API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")

class ConversationStorage:
    """对话存储（PostgreSQL + Redis）。"""

    @staticmethod
    def _messages_cache_key(user_id: str, session_id: str) -> str:
        return f"chat_messages:{user_id}:{session_id}"

    @staticmethod
    def _sessions_cache_key(user_id: str) -> str:
        return f"chat_sessions:{user_id}"

    @staticmethod
    def _to_langchain_messages(records: list[dict]) -> list:
        messages = []
        for msg_data in records:
            msg_type = msg_data.get("type")
            content = msg_data.get("content", "")
            if msg_type == "human":
                messages.append(HumanMessage(content=content))
            elif msg_type == "ai":
                messages.append(AIMessage(content=content))
            elif msg_type == "system":
                messages.append(SystemMessage(content=content))
        return messages

    def save(self, user_id: str, session_id: str, messages: list, metadata: dict = None, extra_message_data: list = None):
        """保存对话"""
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return

            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                session = ChatSession(user_id=user.id, session_id=session_id, metadata_json=metadata or {})
                db.add(session)
                db.flush()
            else:
                session.metadata_json = metadata or {}

            db.query(ChatMessage).filter(ChatMessage.session_ref_id == session.id).delete(synchronize_session=False)

            serialized = []
            now = datetime.utcnow()
            for idx, msg in enumerate(messages):
                rag_trace = None
                if extra_message_data and idx < len(extra_message_data):
                    extra = extra_message_data[idx] or {}
                    rag_trace = extra.get("rag_trace")

                db.add(
                    ChatMessage(
                        session_ref_id=session.id,
                        message_type=msg.type,
                        content=str(msg.content),
                        timestamp=now,
                        rag_trace=rag_trace,
                    )
                )
                serialized.append(
                    {
                        "type": msg.type,
                        "content": str(msg.content),
                        "timestamp": now.isoformat(),
                        "rag_trace": rag_trace,
                    }
                )

            session.updated_at = now
            db.commit()

            cache.set_json(self._messages_cache_key(user_id, session_id), serialized)
            cache.delete(self._sessions_cache_key(user_id))
        finally:
            db.close()

    def load(self, user_id: str, session_id: str) -> list:
        """加载对话"""
        cached = cache.get_json(self._messages_cache_key(user_id, session_id))
        if cached is not None:
            return self._to_langchain_messages(cached)

        records = self.get_session_messages(user_id, session_id)
        cache.set_json(self._messages_cache_key(user_id, session_id), records)
        return self._to_langchain_messages(records)

    def list_sessions(self, user_id: str) -> list:
        """列出用户的所有会话"""
        return [item["session_id"] for item in self.list_session_infos(user_id)]

    def list_session_infos(self, user_id: str) -> list[dict]:
        cached = cache.get_json(self._sessions_cache_key(user_id))
        if cached is not None:
            return cached

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return []

            sessions = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id)
                .order_by(ChatSession.updated_at.desc())
                .all()
            )
            result = []
            for s in sessions:
                count = db.query(ChatMessage).filter(ChatMessage.session_ref_id == s.id).count()
                result.append(
                    {
                        "session_id": s.session_id,
                        "updated_at": s.updated_at.isoformat(),
                        "message_count": count,
                    }
                )
            cache.set_json(self._sessions_cache_key(user_id), result)
            return result
        finally:
            db.close()

    def get_session_messages(self, user_id: str, session_id: str) -> list[dict]:
        cached = cache.get_json(self._messages_cache_key(user_id, session_id))
        if cached is not None:
            return cached

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return []
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                return []

            rows = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_ref_id == session.id)
                .order_by(ChatMessage.id.asc())
                .all()
            )
            result = [
                {
                    "type": row.message_type,
                    "content": row.content,
                    "timestamp": row.timestamp.isoformat(),
                    "rag_trace": row.rag_trace,
                }
                for row in rows
            ]
            cache.set_json(self._messages_cache_key(user_id, session_id), result)
            return result
        finally:
            db.close()

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """删除指定用户的会话，返回是否删除成功"""
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return False
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                return False

            db.delete(session)
            db.commit()
            cache.delete(self._messages_cache_key(user_id, session_id))
            cache.delete(self._sessions_cache_key(user_id))
            return True
        finally:
            db.close()



def create_agent_instances():
    model = init_chat_model(
        model=MODEL,
        model_provider="openai",
        api_key=API_KEY,
        base_url=BASE_URL,
        temperature=0.3,
        stream_usage=True,
    )

    agents = {
        name: create_agent(
            model=model,
            tools=spec.tools,
            system_prompt=spec.system_prompt,
        )
        for name, spec in get_teacher_agent_specs().items()
    }
    return agents, model


agents, model = create_agent_instances()


def _select_agent(user_text: str):
    route = route_teacher_agent(user_text)
    return agents.get(route) or agents["general"], route


def _plan_agent_tasks(user_text: str) -> list[TeacherSubTask]:
    tasks = plan_teacher_tasks(user_text)
    if not tasks:
        _, route = _select_agent(user_text)
        return [TeacherSubTask(route=route, instruction=user_text)]
    return tasks


def _extract_response_content(result) -> str:
    if isinstance(result, dict):
        if "output" in result:
            return result["output"]
        if "messages" in result and result["messages"]:
            msg = result["messages"][-1]
            return getattr(msg, "content", str(msg))
        return str(result)
    if hasattr(result, "content"):
        return result.content
    return str(result)


def _record_result_token_usage(user_id: str, session_id: str, result) -> None:
    if isinstance(result, dict) and isinstance(result.get("messages"), list):
        record_token_usage_from_messages(user_id, session_id, result["messages"])
    else:
        record_token_usage_from_message(user_id, session_id, result)


def _task_messages(base_messages: list, original_text: str, task: TeacherSubTask, previous_outputs: list[str]) -> list:
    task_prompt = f"""
用户原始请求：
{original_text}

当前子任务（由任务规划器拆分）：
{task.instruction}
""".strip()
    if previous_outputs:
        task_prompt += "\n\n前序子任务结果摘要，可作为当前任务依据：\n" + "\n\n".join(previous_outputs)
    return base_messages[:-1] + [HumanMessage(content=task_prompt)]


def _format_multi_task_response(parts: list[tuple[TeacherSubTask, str]]) -> str:
    if len(parts) == 1:
        return parts[0][1]
    sections = []
    for index, (task, content) in enumerate(parts, start=1):
        sections.append(f"### 任务 {index}：{task.route}\n\n{content}")
    return "\n\n".join(sections)


def _route_summary(tasks: list[TeacherSubTask]) -> str:
    return " -> ".join(task.route for task in tasks)


def _task_plan_payload(tasks: list[TeacherSubTask]) -> list[dict]:
    return [
        {"index": index, "route": task.route, "instruction": task.instruction}
        for index, task in enumerate(tasks, start=1)
    ]


def _log_task_plan(tasks: list[TeacherSubTask]) -> None:
    print(f"[agent_task_plan] {json.dumps(_task_plan_payload(tasks), ensure_ascii=False)}")

storage = ConversationStorage()

def summarize_old_messages(model, messages: list) -> str:
    """将旧消息总结为摘要"""
    # 提取旧对话
    old_conversation = "\n".join([
        f"{'用户' if msg.type == 'human' else 'AI'}: {msg.content}"
        for msg in messages
    ])

    # 生成摘要
    summary_prompt = f"""请总结以下对话的关键信息：

{old_conversation}
总结（包含用户信息、重要事实、待办事项）："""

    summary = model.invoke(summary_prompt).content
    return summary


def chat_with_agent(user_text: str, user_id: str = "default_user", session_id: str = "default_session"):
    """使用 Agent 处理用户消息并返回响应"""
    messages = storage.load(user_id, session_id)

    # 清理可能残留的 RAG 上下文，避免跨请求污染
    get_last_rag_context(clear=True)
    reset_tool_call_guards()
    
    if len(messages) > 24:
        summary = summarize_old_messages(model, messages[:16])

        messages = [
            SystemMessage(content=f"之前的对话摘要：\n{summary}")
        ] + messages[16:]

    messages.append(HumanMessage(content=user_text))
    usage_token = set_active_token_usage_session(user_id, session_id)
    task_plan = _plan_agent_tasks(user_text)
    _log_task_plan(task_plan)
    context_token = set_teacher_username(user_id)
    task_results = []
    previous_outputs = []
    try:
        for task in task_plan:
            selected_agent = agents.get(task.route) or agents["general"]
            result = selected_agent.invoke(
                {"messages": _task_messages(messages, user_text, task, previous_outputs)},
                config={"recursion_limit": 8},
            )
            response_part = _extract_response_content(result)
            _record_result_token_usage(user_id, session_id, result)
            task_results.append((task, response_part))
            previous_outputs.append(response_part)
    finally:
        reset_teacher_username(context_token)
        reset_active_token_usage_session(usage_token)

    response_content = _format_multi_task_response(task_results)
    
    messages.append(AIMessage(content=response_content))

    rag_context = get_last_rag_context(clear=True)
    rag_trace = rag_context.get("rag_trace") if rag_context else None

    extra_message_data = [None] * (len(messages) - 1) + [{"rag_trace": rag_trace}]
    storage.save(user_id, session_id, messages, extra_message_data=extra_message_data)

    return {
        "response": response_content,
        "rag_trace": rag_trace,
        "agent_route": _route_summary(task_plan),
    }


async def chat_with_agent_stream(user_text: str, user_id: str = "default_user", session_id: str = "default_session"):
    """使用 Agent 处理用户消息并流式返回响应。
    
    架构：使用统一输出队列 + 后台任务，确保 RAG 检索步骤在工具执行期间实时推送，
    而非等待工具完成后才显示。
    """
    messages = storage.load(user_id, session_id)

    # 清理可能残留的 RAG 上下文
    get_last_rag_context(clear=True)
    reset_tool_call_guards()

    # 统一输出队列：所有事件（content / rag_step）都汇入这里
    output_queue = asyncio.Queue()

    class _RagStepProxy:
        """代理对象：将 emit_rag_step 的原始 step dict 包装后放入统一输出队列。"""
        def put_nowait(self, step):
            output_queue.put_nowait({"type": "rag_step", "step": step})

    set_rag_step_queue(_RagStepProxy())

    if len(messages) > 24:
        summary = summarize_old_messages(model, messages[:16])
        messages = [
            SystemMessage(content=f"之前的对话摘要：\n{summary}")
        ] + messages[16:]

    messages.append(HumanMessage(content=user_text))
    usage_token = set_active_token_usage_session(user_id, session_id)
    task_plan = _plan_agent_tasks(user_text)
    _log_task_plan(task_plan)

    full_response = ""

    async def _agent_worker():
        """后台任务：运行 agent 并将内容 chunk 推入输出队列。"""
        nonlocal full_response
        context_token = set_teacher_username(user_id)
        previous_outputs = []
        try:
            await output_queue.put({"type": "task_plan", "tasks": _task_plan_payload(task_plan)})
            await output_queue.put({"type": "agent_route", "agent_route": _route_summary(task_plan)})
            for index, task in enumerate(task_plan, start=1):
                selected_agent = agents.get(task.route) or agents["general"]
                await output_queue.put(
                    {
                        "type": "task_start",
                        "task_index": index,
                        "task_total": len(task_plan),
                        "agent_route": task.route,
                        "instruction": task.instruction,
                    }
                )
                if len(task_plan) > 1:
                    heading = f"### 任务 {index}：{task.route}\n\n"
                    full_response += heading
                    await output_queue.put({"type": "content", "content": heading})

                task_output = ""
                async for msg, metadata in selected_agent.astream(
                    {"messages": _task_messages(messages, user_text, task, previous_outputs)},
                    stream_mode="messages",
                    config={"recursion_limit": 8},
                ):
                    record_token_usage_from_message(user_id, session_id, msg)
                    if not isinstance(msg, AIMessageChunk):
                        continue
                    if getattr(msg, "tool_call_chunks", None):
                        continue

                    content = ""
                    if isinstance(msg.content, str):
                        content = msg.content
                    elif isinstance(msg.content, list):
                        for block in msg.content:
                            if isinstance(block, str):
                                content += block
                            elif isinstance(block, dict) and block.get("type") == "text":
                                content += block.get("text", "")

                    if content:
                        task_output += content
                        full_response += content
                        await output_queue.put({"type": "content", "content": content})

                previous_outputs.append(task_output)
                if len(task_plan) > 1 and index < len(task_plan):
                    separator = "\n\n"
                    full_response += separator
                    await output_queue.put({"type": "content", "content": separator})
        except Exception as e:
            await output_queue.put({"type": "error", "content": str(e)})
        finally:
            reset_teacher_username(context_token)
            # 哨兵：通知主循环 agent 已完成
            await output_queue.put(None)

    # 启动后台任务
    agent_task = asyncio.create_task(_agent_worker())

    try:
        # 主循环：持续从统一队列取事件并 yield SSE
        # RAG 步骤在工具执行期间通过 call_soon_threadsafe 实时入队，不需要等 agent 产出 chunk
        while True:
            event = await output_queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"
    except GeneratorExit:
        # 客户端断开连接（AbortController）时，FastAPI 会向此生成器抛出 GeneratorExit
        # 我们必须在此处取消后台任务
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass  # 任务已成功取消
        raise  # 重新抛出 GeneratorExit 以便 FastAPI 正确处理关闭
    finally:
        # 正常结束或异常退出时清理
        set_rag_step_queue(None)
        if not agent_task.done():
             agent_task.cancel()
        reset_active_token_usage_session(usage_token)

    # 获取 RAG trace
    rag_context = get_last_rag_context(clear=True)
    rag_trace = rag_context.get("rag_trace") if rag_context else None

    # 发送 trace 信息
    if rag_trace:
        yield f"data: {json.dumps({'type': 'trace', 'rag_trace': rag_trace})}\n\n"

    token_usage = get_session_token_usage(user_id, session_id)
    yield f"data: {json.dumps({'type': 'token_usage', 'token_usage': token_usage})}\n\n"

    # 发送结束信号
    yield "data: [DONE]\n\n"

    # 保存对话
    messages.append(AIMessage(content=full_response))
    extra_message_data = [None] * (len(messages) - 1) + [{"rag_trace": rag_trace}]
    storage.save(user_id, session_id, messages, extra_message_data=extra_message_data)
