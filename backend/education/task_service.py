"""Structured teacher task service with specialist generation and verification."""

import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pydantic import ValidationError

from database import SessionLocal
from education.generation_tools import generate_lesson_plan, generate_questions, grade_answer_reference
from education.grading_rules import grade_objective_answer, normalize_grading_by_rubric, parse_rubric
from education.output_schemas import GradingReferenceOutput, LessonPlanOutput, QuestionSetOutput
from education.tool_context import reset_teacher_username, set_teacher_username
from models import TeacherArtifact, User

load_dotenv()

API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")

_generation_model = None
_verifier_model = None


def _get_generation_model():
    global _generation_model
    if _generation_model is None:
        _generation_model = init_chat_model(
            model=MODEL,
            model_provider="openai",
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0.2,
            stream_usage=True,
        )
    return _generation_model


def _get_verifier_model():
    global _verifier_model
    if _verifier_model is None:
        _verifier_model = init_chat_model(
            model=MODEL,
            model_provider="openai",
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0,
            stream_usage=True,
        )
    return _verifier_model


def _parse_json_object(text: str) -> dict:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.S)
    if fenced:
        raw = fenced.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"content": parsed}
    except json.JSONDecodeError:
        return {"content": text}


def _tool_payload(tool, args: dict) -> dict:
    result = tool.invoke(args)
    return _parse_json_object(result)


def _source_chunks(payload: dict) -> list[dict]:
    chunks = payload.get("context_chunks") or []
    return [
        {
            "chunk_id": item.get("chunk_id", ""),
            "filename": item.get("filename", ""),
            "page_number": item.get("page_number", 0),
            "section_title": item.get("section_title", ""),
            "text": item.get("text", ""),
        }
        for item in chunks
    ]


def _source_chunk_ids(payload: dict, content: dict) -> list[str]:
    ids = []
    for item in _source_chunks(payload):
        if item.get("chunk_id"):
            ids.append(item["chunk_id"])
    text = json.dumps(content, ensure_ascii=False)
    ids.extend(re.findall(r"[\w\u4e00-\u9fff .()（）-]+::p\d+::l\d+::\d+", text))
    return list(dict.fromkeys(ids))


def _generate_json(task_name: str, payload: dict, schema: type) -> dict:
    prompt = f"""
你是教师端教育平台的{task_name}专业 Agent。请严格基于任务上下文生成结果。

要求：
1. 只输出 JSON 对象，不要 Markdown。
2. 必须使用 context_chunks / graph_hints 中能支撑的内容。
3. 如果证据不足，在 JSON 中写明 limitation。
4. 保留 source_chunk_ids 字段。
5. JSON 必须符合 output_schema。

output_schema:
{json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)}

任务上下文：
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()
    response = _get_generation_model().invoke(prompt)
    return _parse_json_object(getattr(response, "content", response))


def _repair_json(task_name: str, payload: dict, raw_content: dict, validation_error: ValidationError, schema: type) -> dict:
    prompt = f"""
你是结构化输出修复器。请把原始 JSON 修复为符合 schema 的 JSON 对象。

规则：
1. 只输出 JSON 对象，不要 Markdown。
2. 不要新增教材依据之外的事实。
3. 缺失字段用空字符串、空数组或 limitation 说明。

schema:
{json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)}

validation_errors:
{validation_error.errors()}

任务上下文：
{json.dumps(payload, ensure_ascii=False, indent=2)}

原始 JSON：
{json.dumps(raw_content, ensure_ascii=False, indent=2)}
""".strip()
    response = _get_generation_model().invoke(prompt)
    return _parse_json_object(getattr(response, "content", response))


def _generate_validated_json(task_name: str, payload: dict, schema: type) -> dict:
    content = _generate_json(task_name, payload, schema)
    try:
        return schema.model_validate(content).model_dump()
    except ValidationError as first_error:
        repaired = _repair_json(task_name, payload, content, first_error, schema)
        try:
            return schema.model_validate(repaired).model_dump()
        except ValidationError as second_error:
            fallback = _schema_fallback(task_name, payload, repaired, second_error, schema)
            return schema.model_validate(fallback).model_dump()


def _schema_fallback(task_name: str, payload: dict, raw_content: dict, validation_error: ValidationError, schema: type) -> dict:
    limitation = "模型输出未通过结构化校验，已生成最小可复核结构。"
    raw_summary = json.dumps(raw_content, ensure_ascii=False)[:800]
    if schema is QuestionSetOutput:
        topic = (payload.get("generation_requirements") or {}).get("knowledge_topic") or task_name
        return {
            "questions": [
                {
                    "stem": f"{topic}：生成结果需要教师复核后使用。",
                    "answer": "模型未能返回合格结构化答案。",
                    "analysis": raw_summary,
                    "rubric": "",
                    "difficulty": (payload.get("generation_requirements") or {}).get("difficulty", "medium"),
                    "knowledge_tags": [topic],
                    "source_chunk_ids": [],
                }
            ],
            "source_chunk_ids": [],
            "limitation": f"{limitation} 校验错误：{validation_error.errors()}",
        }
    if schema is LessonPlanOutput:
        topic = (payload.get("generation_requirements") or {}).get("teaching_topic") or task_name
        return {
            "title": str(topic),
            "lesson_duration_minutes": (payload.get("generation_requirements") or {}).get("lesson_duration_minutes", 45),
            "sections": {"待复核内容": raw_summary},
            "source_chunk_ids": [],
            "limitation": f"{limitation} 校验错误：{validation_error.errors()}",
        }
    return {
        "score": 0,
        "max_score": (payload.get("grading_inputs") or {}).get("max_score", 10),
        "criteria_scores": [],
        "matched_points": [],
        "missing_points": ["模型未能返回合格结构化批改结果"],
        "error_analysis": raw_summary,
        "feedback": "请教师根据标准答案和 rubric 人工复核。",
        "related_knowledge": [],
        "confidence": "low",
        "needs_teacher_review": True,
        "source_chunk_ids": [],
        "grading_mode": "validation_fallback",
        "limitation": f"{limitation} 校验错误：{validation_error.errors()}",
    }


def _verify_content(task_name: str, content: dict, source_chunk_ids: list[str]) -> str:
    prompt = f"""
你是教育材料 Verifier Agent。请检查以下{task_name}结果是否满足：
- 有明确教材依据或说明依据不足
- source_chunk_ids 没有明显缺失
- 批改类结果没有宣称自动定论
- 输出能直接供教师复核使用

请用 3 条以内中文短句输出检查意见；如果没有问题，输出“校验通过”。

source_chunk_ids: {source_chunk_ids}
content:
{json.dumps(content, ensure_ascii=False, indent=2)}
""".strip()
    response = _get_verifier_model().invoke(prompt)
    return str(getattr(response, "content", response)).strip()


def _save_artifact(username: str, artifact_type: str, title: str, prompt: str, content: dict, source_chunk_ids: list[str]) -> int | None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            return None
        artifact = TeacherArtifact(
            teacher_id=user.id,
            artifact_type=artifact_type,
            title=title,
            prompt=prompt,
            content_json=content,
            source_chunk_ids=source_chunk_ids,
        )
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        return artifact.id
    finally:
        db.close()


def generate_question_set(request: Any, username: str) -> dict:
    args = request.model_dump()
    tool_args = {key: value for key, value in args.items() if key != "save"}
    context_token = set_teacher_username(username)
    try:
        payload = _tool_payload(generate_questions, tool_args)
    finally:
        reset_teacher_username(context_token)
    content = _generate_validated_json("智能出题", payload, QuestionSetOutput)
    source_ids = _source_chunk_ids(payload, content)
    notes = _verify_content("智能出题", content, source_ids)
    title = f"{request.knowledge_topic} 题目"
    saved_id = _save_artifact(username, "question_set", title, json.dumps(args, ensure_ascii=False), content, source_ids) if request.save else None
    return {
        "artifact_type": "question_set",
        "title": title,
        "content": content,
        "source_chunk_ids": source_ids,
        "source_chunks": _source_chunks(payload),
        "verifier_notes": notes,
        "agent_route": "supervisor -> question_generator -> verifier",
        "saved_artifact_id": saved_id,
    }


def generate_lesson(request: Any, username: str) -> dict:
    args = request.model_dump()
    tool_args = {key: value for key, value in args.items() if key != "save"}
    context_token = set_teacher_username(username)
    try:
        payload = _tool_payload(generate_lesson_plan, tool_args)
    finally:
        reset_teacher_username(context_token)
    content = _generate_validated_json("备课设计", payload, LessonPlanOutput)
    source_ids = _source_chunk_ids(payload, content)
    notes = _verify_content("备课设计", content, source_ids)
    title = f"{request.teaching_topic} 教案"
    saved_id = _save_artifact(username, "lesson_plan", title, json.dumps(args, ensure_ascii=False), content, source_ids) if request.save else None
    return {
        "artifact_type": "lesson_plan",
        "title": title,
        "content": content,
        "source_chunk_ids": source_ids,
        "source_chunks": _source_chunks(payload),
        "verifier_notes": notes,
        "agent_route": "supervisor -> lesson_planner -> verifier",
        "saved_artifact_id": saved_id,
    }


def generate_grading(request: Any, username: str) -> dict:
    rule_result = grade_objective_answer(
        question_type=request.question_type,
        student_answer=request.student_answer,
        standard_answer=request.standard_answer,
        max_score=request.max_score,
    )
    args = request.model_dump()
    if rule_result is not None:
        rule_result = GradingReferenceOutput.model_validate(rule_result).model_dump()
        notes = _verify_content("批改参考", rule_result, [])
        title = "客观题批改参考"
        saved_id = _save_artifact(username, "grading_reference", title, json.dumps(args, ensure_ascii=False), rule_result, []) if request.save else None
        return {
            "artifact_type": "grading_reference",
            "title": title,
            "content": rule_result,
            "source_chunk_ids": [],
            "source_chunks": [],
            "verifier_notes": notes,
            "agent_route": "supervisor -> rule_grader -> verifier",
            "saved_artifact_id": saved_id,
        }

    tool_args = {key: value for key, value in args.items() if key not in {"save", "question_type"}}
    context_token = set_teacher_username(username)
    try:
        payload = _tool_payload(grade_answer_reference, tool_args)
    finally:
        reset_teacher_username(context_token)
    rubric_criteria = parse_rubric(request.rubric, request.max_score)
    if rubric_criteria:
        payload["parsed_rubric_criteria"] = rubric_criteria
        payload["instruction"] = (
            payload.get("instruction", "")
            + " 请必须输出 criteria_scores，逐项对应 parsed_rubric_criteria 的 criterion_id，"
            + "并确保总分等于各项 awarded_score 之和。"
        )
    content = _generate_validated_json("批改参考", payload, GradingReferenceOutput)
    content = normalize_grading_by_rubric(
        content=content,
        rubric_criteria=rubric_criteria,
        student_answer=request.student_answer,
        max_score=request.max_score,
    )
    content = GradingReferenceOutput.model_validate(content).model_dump()
    source_ids = _source_chunk_ids(payload, content)
    notes = _verify_content("批改参考", content, source_ids)
    title = "主观题批改参考"
    saved_id = _save_artifact(username, "grading_reference", title, json.dumps(args, ensure_ascii=False), content, source_ids) if request.save else None
    return {
        "artifact_type": "grading_reference",
        "title": title,
        "content": content,
        "source_chunk_ids": source_ids,
        "source_chunks": _source_chunks(payload),
        "verifier_notes": notes,
        "agent_route": "supervisor -> grading_assistant -> verifier",
        "saved_artifact_id": saved_id,
    }
