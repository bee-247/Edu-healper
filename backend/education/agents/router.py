"""LLM-based intent router for specialized teacher Agents."""

import os
from typing import Literal

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field, field_validator

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


class TeacherSubTask(BaseModel):
    """One executable teacher task routed to a specialist Agent."""

    route: TeacherAgentRoute = Field(description="Specialist Agent that should handle this subtask.")
    instruction: str = Field(description="Self-contained instruction for the selected Agent.")

    @field_validator("instruction")
    @classmethod
    def _clean_instruction(cls, value: str) -> str:
        return (value or "").strip()


class TeacherTaskPlan(BaseModel):
    """Ordered task plan for one teacher request."""

    tasks: list[TeacherSubTask] = Field(default_factory=list)


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


def plan_teacher_tasks(user_text: str) -> list[TeacherSubTask]:
    """Decompose a teacher request into ordered specialist Agent tasks."""
    model = _get_intent_router_model()
    if not model:
        return [TeacherSubTask(route="general", instruction=user_text)]

    prompt = f"""
你是教师端教育助手的任务规划 Agent。请把教师当前请求拆解为 1 到 5 个可以顺序执行的子任务，并为每个子任务选择最合适的专门 Agent。

可选 Agent：
- general：普通对话、教材问答、知识点讲解、混合请求中的讲解部分、前置知识、知识图谱、概念关系、相关知识、易混淆点、教学路径。
- question_generator：生成题目、试卷、练习、作业题、答案、解析、rubric。
- lesson_planner：备课、教案、教学设计、教学流程、板书、课堂练习、课后作业设计。
- grading_assistant：批改、评分、学生答案分析、错因分析、rubric 反馈、给分建议。

规划要求：
1. 如果请求只有一个明确任务，只返回一个子任务。
2. 如果请求包含多个目标，例如“讲知识点并出题”“先设计教案再给练习”“批改并给订正建议”，要拆成多个子任务。
3. 子任务必须按执行顺序排列，后续子任务可以基于前面子任务的结果。
4. instruction 必须是完整、可独立执行的中文指令，保留学科、年级、教材版本、题量、难度等约束。
5. 不要输出无法由上述 Agent 处理的任务；不要为了关键词机械拆分。
6. 判断是否拆分时看用户真实目标，而不是看某个词是否出现；同一个句子可能只是一个任务，也可能包含多个可交给不同 Agent 的任务。

判断示例：
- “给我介绍一下全等三角形部分的内容，并帮我找几个题目”
  应拆为两个任务：
  1. general：讲解并梳理全等三角形核心内容、重点和易错点。
  2. question_generator：围绕全等三角形生成若干题目，附答案和解析。
- “请先介绍全等三角形的核心知识点，再围绕这个知识点出 3 道题，并附答案解析”
  应拆为两个任务：
  1. general：先介绍全等三角形的核心知识点。
  2. question_generator：再围绕前序知识点生成 3 道题，并附答案解析。
- “围绕全等三角形出 5 道题，顺便覆盖易错点”
  通常是一个 question_generator 任务，因为“覆盖易错点”是出题约束，不是独立讲解任务。
- “帮我设计一节课，再配套课堂练习”
  应拆为 lesson_planner 和 question_generator，因为一个是教学设计，一个是题目生成。
- “批改这份学生答案，并给出订正建议”
  通常是一个 grading_assistant 任务，因为订正建议是批改反馈的一部分。
- “先解释学生错在哪里，再基于错因出 3 道巩固题”
  应拆为 grading_assistant 和 question_generator，因为先分析答案，再生成针对性练习。

教师请求：
{user_text}
""".strip()

    try:
        decision = model.with_structured_output(TeacherTaskPlan).invoke(
            [{"role": "user", "content": prompt}]
        )
        tasks = [
            task
            for task in decision.tasks[:5]
            if task.instruction and task.route in TeacherAgentRoute.__args__
        ]
        if tasks:
            return tasks
    except Exception:
        pass

    fallback_route = route_teacher_agent(user_text)
    return [TeacherSubTask(route=fallback_route, instruction=user_text)]
