"""Teacher material generation tools.

These tools prepare source-grounded context and output contracts for the Agent.
The Agent still writes the final content, which keeps generation behavior in one
conversation flow instead of nesting another LLM call inside a tool.
"""

import json

try:
    from langchain_core.tools import tool
except ImportError:
    from langchain_core.tools import tool

from rag_utils import retrieve_documents


QUESTION_TYPES = {
    "choice": "选择题",
    "blank": "填空题",
    "judge": "判断题",
    "short_answer": "简答题",
    "application": "应用题",
}

DIFFICULTIES = {
    "easy": "基础",
    "medium": "中等",
    "hard": "提高",
}

LESSON_SECTIONS = [
    "教学目标",
    "重点难点",
    "学情与前置知识",
    "课堂导入",
    "教学流程",
    "例题设计",
    "课堂练习",
    "板书设计",
    "课后作业",
]


def _safe_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _compact_chunk(doc: dict) -> dict:
    return {
        "chunk_id": doc.get("chunk_id", ""),
        "filename": doc.get("filename", ""),
        "page_number": doc.get("page_number", 0),
        "subject": doc.get("subject", ""),
        "grade": doc.get("grade", ""),
        "section_title": doc.get("section_title", ""),
        "knowledge_tags": doc.get("knowledge_tags") or [],
        "text": (doc.get("text") or "")[:900],
    }


def _load_graph_hints(knowledge_topic: str, limit: int = 8) -> list[dict]:
    if not knowledge_topic:
        return []

    from neo4j_client import graph_client

    try:
        return graph_client.execute_read(
            """
            MATCH (n)
            WHERE n:KnowledgePoint OR n:Concept OR n:Formula OR n:Method
            WITH n
            WHERE toLower(coalesce(n.name, '')) CONTAINS toLower($keyword)
               OR toLower(coalesce(n.description, '')) CONTAINS toLower($keyword)
            OPTIONAL MATCH (n)-[r]-(m)
            WHERE m:KnowledgePoint OR m:Concept OR m:Formula OR m:Method
            RETURN n.name AS name,
                   n.node_type AS node_type,
                   n.description AS description,
                   n.source_chunk_ids AS source_chunk_ids,
                   collect({
                       relation_type: type(r),
                       target_name: m.name,
                       confidence: r.confidence
                   })[0..5] AS relations
            LIMIT $limit
            """,
            {"keyword": knowledge_topic.strip(), "limit": max(1, min(limit, 20))},
        )
    except Exception:
        return []


def _prepare_source_context(
    topic: str,
    *,
    subject: str = "",
    grade: str = "",
    book_version: str = "",
    resource_type: str = "",
    section_title: str = "",
    extra_terms: list[str] | None = None,
) -> dict:
    query_parts = [topic, subject, grade] + (extra_terms or [])
    query = " ".join([part for part in query_parts if part])
    retrieved = retrieve_documents(
        query,
        top_k=5,
        subject=subject,
        grade=grade,
        book_version=book_version,
        resource_type=resource_type,
        section_title=section_title,
    )
    return {
        "query": query,
        "context_chunks": [_compact_chunk(doc) for doc in retrieved.get("docs", [])],
        "graph_hints": _load_graph_hints(topic),
        "retrieval_meta": retrieved.get("meta", {}),
    }


def _json_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


@tool("generate_questions")
def generate_questions(
    knowledge_topic: str,
    question_type: str = "short_answer",
    difficulty: str = "medium",
    count: int = 3,
    subject: str = "",
    grade: str = "",
    book_version: str = "",
    resource_type: str = "",
) -> str:
    """Prepare textbook and graph context for generating source-grounded teacher question sets."""
    topic = (knowledge_topic or "").strip()
    if not topic:
        return "knowledge_topic 参数不能为空"

    amount = _safe_int(count, default=3, minimum=1, maximum=10)
    normalized_type = (question_type or "short_answer").strip()
    normalized_difficulty = (difficulty or "medium").strip()
    type_label = QUESTION_TYPES.get(normalized_type, normalized_type)
    difficulty_label = DIFFICULTIES.get(normalized_difficulty, normalized_difficulty)

    source_context = _prepare_source_context(
        topic,
        subject=subject,
        grade=grade,
        book_version=book_version,
        resource_type=resource_type,
        extra_terms=[type_label, difficulty_label],
    )

    payload = {
        "task": "generate_questions",
        "generation_requirements": {
            "knowledge_topic": topic,
            "subject": subject,
            "grade": grade,
            "book_version": book_version,
            "resource_type": resource_type,
            "question_type": normalized_type,
            "question_type_label": type_label,
            "difficulty": normalized_difficulty,
            "difficulty_label": difficulty_label,
            "count": amount,
            "must_use_textbook_evidence": True,
            "must_include_source_chunk_ids": True,
        },
        **source_context,
        "output_schema": {
            "questions": [
                {
                    "stem": "题干",
                    "answer": "标准答案",
                    "analysis": "解析",
                    "rubric": "评分点或判分规则",
                    "difficulty": normalized_difficulty,
                    "knowledge_tags": [topic],
                    "source_chunk_ids": ["chunk_id"],
                }
            ]
        },
        "instruction": (
            "请基于 context_chunks 和 graph_hints 生成题目。不要编造教材外的事实；"
            "如果依据不足，请明确说明缺少教材依据，并只生成可被来源支持的题目。"
        ),
    }
    return _json_payload(payload)


@tool("generate_lesson_plan")
def generate_lesson_plan(
    teaching_topic: str,
    grade: str = "",
    subject: str = "",
    book_version: str = "",
    resource_type: str = "",
    lesson_duration: int = 45,
    teaching_style: str = "",
) -> str:
    """Prepare textbook and graph context for generating a teacher lesson plan."""
    topic = (teaching_topic or "").strip()
    if not topic:
        return "teaching_topic 参数不能为空"

    duration = _safe_int(lesson_duration, default=45, minimum=10, maximum=180)
    source_context = _prepare_source_context(
        topic,
        subject=subject,
        grade=grade,
        book_version=book_version,
        resource_type=resource_type,
        extra_terms=["备课", "教案", teaching_style],
    )

    payload = {
        "task": "generate_lesson_plan",
        "generation_requirements": {
            "teaching_topic": topic,
            "subject": subject,
            "grade": grade,
            "book_version": book_version,
            "resource_type": resource_type,
            "lesson_duration_minutes": duration,
            "teaching_style": teaching_style,
            "must_use_textbook_evidence": True,
            "must_include_source_chunk_ids": True,
        },
        **source_context,
        "output_schema": {
            "title": "课题名称",
            "lesson_duration_minutes": duration,
            "sections": {section: "内容" for section in LESSON_SECTIONS},
            "source_chunk_ids": ["chunk_id"],
        },
        "instruction": (
            "请基于 context_chunks 和 graph_hints 生成面向教师的教案。"
            "教学流程要按课堂时间推进，包含教师活动、学生活动和设计意图；"
            "重点难点要引用教材依据或图谱关系。"
        ),
    }
    return _json_payload(payload)


@tool("grade_answer_reference")
def grade_answer_reference(
    question: str,
    student_answer: str,
    standard_answer: str = "",
    rubric: str = "",
    max_score: int = 10,
    knowledge_topic: str = "",
    subject: str = "",
    grade: str = "",
    book_version: str = "",
    resource_type: str = "",
) -> str:
    """Prepare a structured grading-reference contract for a teacher, grounded by optional textbook context."""
    clean_question = (question or "").strip()
    clean_student_answer = (student_answer or "").strip()
    if not clean_question or not clean_student_answer:
        return "question 和 student_answer 参数不能为空"

    topic = (knowledge_topic or clean_question[:80]).strip()
    score = _safe_int(max_score, default=10, minimum=1, maximum=100)
    source_context = _prepare_source_context(
        topic,
        subject=subject,
        grade=grade,
        book_version=book_version,
        resource_type=resource_type,
        extra_terms=[clean_question, standard_answer, rubric, "批改", "评分"],
    )

    payload = {
        "task": "grade_answer_reference",
        "grading_inputs": {
            "question": clean_question,
            "student_answer": clean_student_answer,
            "standard_answer": standard_answer,
            "rubric": rubric,
            "max_score": score,
            "knowledge_topic": knowledge_topic,
            "subject": subject,
            "grade": grade,
            "book_version": book_version,
            "resource_type": resource_type,
        },
        **source_context,
        "output_schema": {
            "score": 0,
            "max_score": score,
            "matched_points": ["学生答案已经覆盖的评分点"],
            "missing_points": ["学生答案缺失或错误的评分点"],
            "error_analysis": "错因分析",
            "feedback": "给教师参考的修改建议",
            "related_knowledge": ["相关知识点"],
            "confidence": "high / medium / low",
            "needs_teacher_review": True,
            "source_chunk_ids": ["chunk_id"],
        },
        "instruction": (
            "请只给教师批改参考，不要把结果表述为自动定论。"
            "如果 standard_answer 或 rubric 为空，要降低置信度并说明依据不足；"
            "评分必须可追溯到 rubric、标准答案或 context_chunks。"
        ),
    }
    return _json_payload(payload)
