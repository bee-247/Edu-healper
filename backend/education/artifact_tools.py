"""Tools for teacher artifacts and teacher preference memory."""

import json
import re
from datetime import datetime
from typing import Any

try:
    from langchain_core.tools import tool
except ImportError:
    from langchain_core.tools import tool

from database import SessionLocal
from education.tool_context import get_teacher_username
from models import TeacherArtifact, TeacherMemory, User


def _split_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[,，、\n]", str(value)) if item.strip()]


def _parse_content_json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    text = str(value).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"content": parsed}
    except json.JSONDecodeError:
        return {"content": text}


def _current_user(db) -> User | None:
    username = get_teacher_username()
    if not username:
        return None
    return db.query(User).filter(User.username == username).first()


@tool("save_teacher_artifact")
def save_teacher_artifact(
    artifact_type: str,
    title: str,
    content_json: str,
    prompt: str = "",
    source_chunk_ids: str = "",
) -> str:
    """Save teacher-generated material such as question sets, lesson plans, homework, or grading references."""
    clean_type = (artifact_type or "").strip()
    clean_title = (title or "").strip()
    if not clean_type or not clean_title:
        return "artifact_type 和 title 不能为空"

    db = SessionLocal()
    try:
        user = _current_user(db)
        if not user:
            return "保存失败：无法识别当前教师用户"

        artifact = TeacherArtifact(
            teacher_id=user.id,
            artifact_type=clean_type,
            title=clean_title,
            prompt=prompt or "",
            content_json=_parse_content_json(content_json),
            source_chunk_ids=_split_list(source_chunk_ids),
        )
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        return f"已保存生成材料：id={artifact.id}，title={artifact.title}"
    finally:
        db.close()


@tool("update_teacher_memory")
def update_teacher_memory(
    memory_summary: str = "",
    preferred_subjects: str = "",
    preferred_grades: str = "",
    teaching_style: str = "",
    question_difficulty_preference: str = "",
    output_format_preference: str = "",
) -> str:
    """Update teacher preference memory used by future lesson planning and material generation."""
    db = SessionLocal()
    try:
        user = _current_user(db)
        if not user:
            return "更新失败：无法识别当前教师用户"

        memory = db.query(TeacherMemory).filter(TeacherMemory.teacher_id == user.id).first()
        if not memory:
            memory = TeacherMemory(teacher_id=user.id)
            db.add(memory)

        if memory_summary:
            memory.memory_summary = memory_summary.strip()
        if preferred_subjects:
            memory.preferred_subjects = _split_list(preferred_subjects)
        if preferred_grades:
            memory.preferred_grades = _split_list(preferred_grades)
        if teaching_style:
            memory.teaching_style = teaching_style.strip()
        if question_difficulty_preference:
            memory.question_difficulty_preference = question_difficulty_preference.strip()
        if output_format_preference:
            memory.output_format_preference = output_format_preference.strip()
        memory.updated_at = datetime.utcnow()
        db.commit()
        return "教师偏好记忆已更新"
    finally:
        db.close()


@tool("get_teacher_memory")
def get_teacher_memory() -> str:
    """Read the current teacher preference memory."""
    db = SessionLocal()
    try:
        user = _current_user(db)
        if not user:
            return "无法识别当前教师用户"

        memory = db.query(TeacherMemory).filter(TeacherMemory.teacher_id == user.id).first()
        if not memory:
            return "当前教师还没有偏好记忆"
        payload = {
            "memory_summary": memory.memory_summary,
            "preferred_subjects": memory.preferred_subjects,
            "preferred_grades": memory.preferred_grades,
            "teaching_style": memory.teaching_style,
            "question_difficulty_preference": memory.question_difficulty_preference,
            "output_format_preference": memory.output_format_preference,
            "updated_at": memory.updated_at.isoformat() if memory.updated_at else "",
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
    finally:
        db.close()
