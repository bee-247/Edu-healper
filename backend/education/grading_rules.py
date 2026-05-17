"""Deterministic grading helpers for objective questions."""

import re
from difflib import SequenceMatcher


OBJECTIVE_TYPES = {"choice", "judge", "blank"}


def _normalize_answer(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    return text.strip("。.,，;；：:")


def _extract_choice(value: str) -> str:
    match = re.search(r"\b([a-d])\b", str(value or "").lower())
    return match.group(1).upper() if match else _normalize_answer(value).upper()


def _is_close_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.92


def grade_objective_answer(
    *,
    question_type: str,
    student_answer: str,
    standard_answer: str,
    max_score: int,
) -> dict | None:
    """Return a deterministic grading payload when the question type is objective."""
    qtype = (question_type or "").strip().lower()
    if qtype not in OBJECTIVE_TYPES or not standard_answer:
        return None

    score = max(1, min(int(max_score or 10), 100))
    if qtype == "choice":
        student = _extract_choice(student_answer)
        standard = _extract_choice(standard_answer)
        matched = student == standard
    else:
        student = _normalize_answer(student_answer)
        standard = _normalize_answer(standard_answer)
        matched = _is_close_match(student, standard)

    criterion = {
        "criterion_id": "objective_answer",
        "description": "答案与标准答案一致",
        "max_score": score,
        "awarded_score": score if matched else 0,
        "status": "matched" if matched else "missing",
        "evidence": f"学生答案：{student_answer}；标准答案：{standard_answer}",
        "comment": "规则判分命中标准答案" if matched else "规则判分未命中标准答案",
    }

    return {
        "score": score if matched else 0,
        "max_score": score,
        "criteria_scores": [criterion],
        "matched_points": ["答案与标准答案一致"] if matched else [],
        "missing_points": [] if matched else ["答案与标准答案不一致"],
        "error_analysis": "" if matched else "客观题规则判分显示学生答案未命中标准答案。",
        "feedback": "可按满分处理。" if matched else "建议教师复核学生答案是否存在等价表述。",
        "related_knowledge": [],
        "confidence": "high",
        "needs_teacher_review": not matched,
        "grading_mode": "rule_based_objective",
        "source_chunk_ids": [],
    }


def parse_rubric(rubric: str, max_score: int | float) -> list[dict]:
    """Parse a free-form rubric into criterion rows with max scores.

    Supports common teacher formats such as:
    - "写出公式 2分；代入计算 3分；结论 1分"
    - "1. 方法正确（4分）\n2. 结果正确（2分）"
    """
    text = str(rubric or "").strip()
    total = max(1.0, float(max_score or 10))
    if not text:
        return []

    parts = [
        item.strip(" \t-•、；;。.")
        for item in re.split(r"[\n；;]+", text)
        if item.strip(" \t-•、；;。.")
    ]
    if not parts:
        parts = [text]

    criteria = []
    for idx, part in enumerate(parts, 1):
        score_match = re.search(r"(\d+(?:\.\d+)?)\s*分", part)
        item_score = float(score_match.group(1)) if score_match else 0.0
        description = re.sub(r"[（(]?\s*\d+(?:\.\d+)?\s*分\s*[）)]?", "", part).strip()
        description = re.sub(r"^\d+[\.、)]\s*", "", description).strip()
        criteria.append(
            {
                "criterion_id": f"c{idx}",
                "description": description or part,
                "max_score": item_score,
            }
        )

    specified = sum(item["max_score"] for item in criteria)
    if specified <= 0:
        equal_score = round(total / len(criteria), 2)
        for item in criteria:
            item["max_score"] = equal_score
        criteria[-1]["max_score"] = round(total - sum(item["max_score"] for item in criteria[:-1]), 2)
    elif abs(specified - total) > 0.01:
        ratio = total / specified
        for item in criteria:
            item["max_score"] = round(item["max_score"] * ratio, 2)
        criteria[-1]["max_score"] = round(total - sum(item["max_score"] for item in criteria[:-1]), 2)

    return criteria


def _tokenize_for_match(text: str) -> set[str]:
    normalized = _normalize_answer(text)
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]+", normalized))
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    tokens.update("".join(chinese_chars[i : i + 2]) for i in range(max(0, len(chinese_chars) - 1)))
    if not tokens and normalized:
        tokens = {normalized}
    return tokens


def estimate_criterion_match(description: str, student_answer: str) -> tuple[str, float, str]:
    """Small deterministic fallback used when an LLM omits per-criterion scores."""
    desc_tokens = _tokenize_for_match(description)
    answer_tokens = _tokenize_for_match(student_answer)
    if not desc_tokens or not answer_tokens:
        return "missing", 0.0, "缺少可用于规则匹配的关键词"

    if _normalize_answer(description) and _normalize_answer(description) in _normalize_answer(student_answer):
        return "matched", 1.0, "学生答案直接包含该评分点"

    overlap = len(desc_tokens & answer_tokens) / max(1, len(desc_tokens))
    if overlap >= 0.65:
        return "matched", 1.0, "学生答案覆盖该评分点关键词"
    if overlap >= 0.25:
        return "partial", 0.5, "学生答案部分覆盖该评分点关键词"
    return "missing", 0.0, "学生答案未明显覆盖该评分点"


def normalize_grading_by_rubric(
    *,
    content: dict,
    rubric_criteria: list[dict],
    student_answer: str,
    max_score: int | float,
) -> dict:
    """Ensure subjective grading has criterion rows and a consistent total score."""
    normalized = dict(content or {})
    total = max(1.0, float(max_score or normalized.get("max_score") or 10))
    normalized["max_score"] = total

    if not rubric_criteria:
        normalized["score"] = max(0.0, min(float(normalized.get("score") or 0), total))
        normalized.setdefault("criteria_scores", [])
        normalized.setdefault("needs_teacher_review", True)
        normalized.setdefault("confidence", "low" if not normalized.get("rubric") else "medium")
        return normalized

    existing_rows = normalized.get("criteria_scores") or []
    existing_by_id = {
        str(row.get("criterion_id", "")).strip(): row
        for row in existing_rows
        if isinstance(row, dict)
    }
    rows = []
    for criterion in rubric_criteria:
        criterion_id = criterion["criterion_id"]
        row = dict(existing_by_id.get(criterion_id) or {})
        status = str(row.get("status") or "").strip().lower()
        awarded = row.get("awarded_score")
        if status not in {"matched", "partial", "missing"} or awarded is None:
            status, ratio, comment = estimate_criterion_match(criterion["description"], student_answer)
            awarded = round(float(criterion["max_score"]) * ratio, 2)
            row["comment"] = row.get("comment") or comment

        max_item_score = float(criterion["max_score"])
        rows.append(
            {
                "criterion_id": criterion_id,
                "description": criterion["description"],
                "max_score": max_item_score,
                "awarded_score": max(0.0, min(float(awarded or 0), max_item_score)),
                "status": status if status in {"matched", "partial", "missing"} else "missing",
                "evidence": str(row.get("evidence") or ""),
                "comment": str(row.get("comment") or ""),
            }
        )

    normalized["criteria_scores"] = rows
    normalized["score"] = round(sum(item["awarded_score"] for item in rows), 2)
    normalized["matched_points"] = [item["description"] for item in rows if item["status"] == "matched"]
    normalized["missing_points"] = [item["description"] for item in rows if item["status"] == "missing"]
    normalized["needs_teacher_review"] = True
    normalized["confidence"] = normalized.get("confidence") if normalized.get("confidence") in {"high", "medium", "low"} else "medium"
    return normalized
