from typing import Optional

from dotenv import load_dotenv
try:
    from langchain_core.tools import tool
except ImportError:
    from langchain_core.tools import tool

load_dotenv()

_LAST_RAG_CONTEXT = None
_KNOWLEDGE_TOOL_CALLS_THIS_TURN = 0
_RAG_STEP_QUEUE = None  # asyncio.Queue, set by agent before streaming
_RAG_STEP_LOOP = None   # asyncio loop, captured when setting queue


def _set_last_rag_context(context: dict):
    global _LAST_RAG_CONTEXT
    _LAST_RAG_CONTEXT = context


def get_last_rag_context(clear: bool = True) -> Optional[dict]:
    """获取最近一次 RAG 检索上下文，默认读取后清空。"""
    global _LAST_RAG_CONTEXT
    context = _LAST_RAG_CONTEXT
    if clear:
        _LAST_RAG_CONTEXT = None
    return context


def reset_tool_call_guards():
    """每轮对话开始时重置工具调用计数。"""
    global _KNOWLEDGE_TOOL_CALLS_THIS_TURN
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN = 0


def set_rag_step_queue(queue):
    """设置 RAG 步骤队列，并捕获当前事件循环以便跨线程调度。"""
    global _RAG_STEP_QUEUE, _RAG_STEP_LOOP
    _RAG_STEP_QUEUE = queue
    if queue:
        import asyncio
        try:
            _RAG_STEP_LOOP = asyncio.get_running_loop()
        except RuntimeError:
            _RAG_STEP_LOOP = asyncio.get_event_loop()
    else:
        _RAG_STEP_LOOP = None


def emit_rag_step(icon: str, label: str, detail: str = ""):
    """向队列发送一个 RAG 检索步骤。支持跨线程安全调用。"""
    global _RAG_STEP_QUEUE, _RAG_STEP_LOOP
    if _RAG_STEP_QUEUE is not None and _RAG_STEP_LOOP is not None:
        step = {"icon": icon, "label": label, "detail": detail}
        try:
            if not _RAG_STEP_LOOP.is_closed():
                _RAG_STEP_LOOP.call_soon_threadsafe(_RAG_STEP_QUEUE.put_nowait, step)
        except Exception:
            pass


def _run_textbook_search(
    query: str,
    tool_name: str,
    *,
    subject: str = "",
    grade: str = "",
    book_version: str = "",
    resource_type: str = "",
    section_title: str = "",
) -> str:
    global _KNOWLEDGE_TOOL_CALLS_THIS_TURN
    if _KNOWLEDGE_TOOL_CALLS_THIS_TURN >= 1:
        return (
            "TOOL_CALL_LIMIT_REACHED: textbook search has already been called once in this turn. "
            "Use the existing retrieval result and provide the final answer directly."
        )
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN += 1

    from rag_pipeline import run_rag_graph
    from rag_utils import retrieve_documents

    if any([subject, grade, book_version, resource_type, section_title]):
        retrieved = retrieve_documents(
                query,
                top_k=5,
                subject=subject,
                grade=grade,
                book_version=book_version,
                resource_type=resource_type,
                section_title=section_title,
            )
        docs = retrieved.get("docs", [])
        meta = retrieved.get("meta", {})
        rag_result = {
            "docs": docs,
            "rag_trace": {
                "tool_used": True,
                "tool_name": tool_name,
                "query": query,
                "retrieval_stage": "metadata_filtered",
                "retrieved_chunks": docs,
                "retrieval_mode": meta.get("retrieval_mode"),
                "filter_expr": meta.get("filter_expr"),
            },
        }
    else:
        rag_result = run_rag_graph(query)

    docs = rag_result.get("docs", []) if isinstance(rag_result, dict) else []
    rag_trace = rag_result.get("rag_trace", {}) if isinstance(rag_result, dict) else {}
    if rag_trace:
        rag_trace["tool_name"] = tool_name
        _set_last_rag_context({"rag_trace": rag_trace})

    if not docs:
        return "No relevant documents found in the knowledge base."

    formatted = []
    for i, result in enumerate(docs, 1):
        source = result.get("filename", "Unknown")
        page = result.get("page_number", "N/A")
        text = result.get("text", "")
        formatted.append(f"[{i}] {source} (Page {page}):\n{text}")

    return "Retrieved Chunks:\n" + "\n\n---\n\n".join(formatted)


@tool("search_textbook")
def search_textbook(
    query: str,
    subject: str = "",
    grade: str = "",
    book_version: str = "",
    resource_type: str = "",
    section_title: str = "",
) -> str:
    """Search textbook chunks using hybrid retrieval and return source-grounded snippets."""
    return _run_textbook_search(
        query,
        "search_textbook",
        subject=subject,
        grade=grade,
        book_version=book_version,
        resource_type=resource_type,
        section_title=section_title,
    )
