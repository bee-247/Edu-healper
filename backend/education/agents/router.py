"""LLM-based intent router for specialized teacher Agents."""

import os
from typing import Literal

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

load_dotenv()

API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")

TeacherAgentRoute = Literal[
    "general",
    "question_generator",
    "lesson_planner",
    "grading_assistant",
]

_intent_router_model = None


class IntentRouteDecision(BaseModel):
    """Specialized Agent chosen by the intent recognition model."""

    route: TeacherAgentRoute = Field(description="The next specialist Agent to handle the teacher request.")


def _get_intent_router_model():
    global _intent_router_model
    if not API_KEY or not MODEL:
        return None
    if _intent_router_model is None:
        _intent_router_model = init_chat_model(
            model=MODEL,
            model_provider="openai",
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0,
            stream_usage=True,
        )
    return _intent_router_model


def route_teacher_agent(user_text: str) -> str:
    """Use an intent recognition model to choose the next specialist Agent."""
    model = _get_intent_router_model()
    if not model:
        return "general"

    prompt = f"""
你是教师端教育助手的意图识别 Agent。请根据教师的当前请求，选择下一步应该交给哪个专门 Agent。

可选 Agent：
- general：普通对话、教材问答、混合请求、无法归类的问题。
  也处理前置知识、知识图谱、概念关系、相关知识、易混淆点、教学路径等图谱辅助类请求。
- question_generator：生成题目、试卷、练习、作业题、答案、解析、rubric。
- lesson_planner：备课、教案、教学设计、教学流程、板书、课堂练习、课后作业设计。
- grading_assistant：批改、评分、学生答案分析、错因分析、rubric 反馈、给分建议。

只选择最合适的一个 Agent。不要根据关键词机械匹配，要理解教师真实意图。

教师请求：
{user_text}
""".strip()

    try:
        decision = model.with_structured_output(IntentRouteDecision).invoke(
            [{"role": "user", "content": prompt}]
        )
        return decision.route
    except Exception:
        return "general"
