"""Build teacher-facing reports for objective grading results."""

from education.objective_grader import ObjectiveGradingResult


def build_objective_grading_report(
    grading_result: ObjectiveGradingResult,
    *,
    extraction_confidence: dict[str, float] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    confidence = extraction_confidence or {}
    low_confidence = [
        question_no
        for question_no, value in confidence.items()
        if isinstance(value, (int, float)) and value < 0.8
    ]
    needs_review = grading_result.needs_teacher_review or bool(low_confidence)
    summary = (
        f"本次客观题得分 {grading_result.total_score}/{grading_result.max_score}，"
        f"正确率 {round(grading_result.accuracy * 100, 1)}%。"
    )
    if grading_result.wrong_question_nos:
        summary += f" 错题：{', '.join(grading_result.wrong_question_nos)}。"
    if low_confidence:
        summary += f" 低置信度识别题号：{', '.join(low_confidence)}。"

    return {
        "summary": summary,
        "total_score": grading_result.total_score,
        "max_score": grading_result.max_score,
        "accuracy": grading_result.accuracy,
        "wrong_questions": grading_result.wrong_question_nos,
        "low_confidence_questions": low_confidence,
        "items": [item.model_dump() for item in grading_result.items],
        "warnings": warnings or [],
        "needs_teacher_review": needs_review,
        "grading_mode": "rule_based_objective_batch",
    }
