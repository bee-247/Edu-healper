"""Rule engine for objective-question grading."""

import re
from math import isclose
from typing import Literal

from pydantic import BaseModel, Field, field_validator


GradingMode = Literal["exact", "any_of", "all_of", "partial", "numeric_tolerance", "regex"]


class ObjectiveAnswerRule(BaseModel):
    question_no: str
    question_type: str = "single_choice"
    max_score: float = Field(default=1, gt=0)
    grading_mode: GradingMode = "any_of"
    acceptable_answers: list[str] = Field(default_factory=list)
    tolerance: float | None = None
    partial_score_per_option: float | None = None
    penalty_wrong_option: float = 0
    wrong_option_policy: str = ""
    case_sensitive: bool = False
    ignore_spaces: bool = True

    @field_validator("question_no", mode="before")
    @classmethod
    def _stringify_question_no(cls, value):
        return str(value or "").strip()

    @field_validator("acceptable_answers", mode="before")
    @classmethod
    def _listify_answers(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if not text:
            return []
        return [item.strip() for item in re.split(r"[/|,，、；;]", text) if item.strip()]


class ObjectiveGradingItem(BaseModel):
    question_no: str
    question_type: str = ""
    student_answer: str = ""
    standard_answer: str = ""
    score: float = 0
    max_score: float = 0
    is_correct: bool = False
    status: Literal["correct", "partial", "wrong", "missing", "needs_review"] = "wrong"
    comment: str = ""


class ObjectiveGradingResult(BaseModel):
    total_score: float = 0
    max_score: float = 0
    accuracy: float = 0
    items: list[ObjectiveGradingItem] = Field(default_factory=list)
    wrong_question_nos: list[str] = Field(default_factory=list)
    needs_teacher_review: bool = False


_FULLWIDTH_MAP = str.maketrans({
    "Ａ": "A", "Ｂ": "B", "Ｃ": "C", "Ｄ": "D", "Ｅ": "E", "Ｆ": "F",
    "ａ": "a", "ｂ": "b", "ｃ": "c", "ｄ": "d", "ｅ": "e", "ｆ": "f",
})


def normalize_answer(value: str, *, case_sensitive: bool = False, ignore_spaces: bool = True) -> str:
    text = str(value or "").strip().translate(_FULLWIDTH_MAP)
    text = text.strip("。.,，;；：:")
    if ignore_spaces:
        text = re.sub(r"\s+", "", text)
    text = text.replace("√", "对").replace("✓", "对").replace("×", "错").replace("✗", "错")
    truthy = {"true": "对", "正确": "对", "yes": "对"}
    falsy = {"false": "错", "错误": "错", "no": "错"}
    lowered = text.lower()
    if lowered in truthy:
        text = truthy[lowered]
    elif lowered in falsy:
        text = falsy[lowered]
    return text if case_sensitive else text.upper()


def _choice_set(value: str) -> set[str]:
    normalized = normalize_answer(value)
    return set(re.findall(r"[A-Z]", normalized))


def _to_number(value: str) -> float | None:
    text = normalize_answer(value)
    if text.endswith("%"):
        try:
            return float(text[:-1]) / 100
        except ValueError:
            return None
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            denominator = float(right)
            if isclose(denominator, 0):
                return None
            return float(left) / denominator
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _grade_rule(student_answer: str, rule: ObjectiveAnswerRule) -> ObjectiveGradingItem:
    normalized_student = normalize_answer(
        student_answer,
        case_sensitive=rule.case_sensitive,
        ignore_spaces=rule.ignore_spaces,
    )
    normalized_answers = [
        normalize_answer(item, case_sensitive=rule.case_sensitive, ignore_spaces=rule.ignore_spaces)
        for item in rule.acceptable_answers
    ]
    standard_text = " / ".join(rule.acceptable_answers)
    if not normalized_student:
        return ObjectiveGradingItem(
            question_no=rule.question_no,
            question_type=rule.question_type,
            student_answer=student_answer,
            standard_answer=standard_text,
            max_score=rule.max_score,
            status="missing",
            comment="未识别到学生答案",
        )

    mode = rule.grading_mode
    score = 0.0
    matched = False
    comment = "答案未命中标准答案"

    if mode in {"exact", "any_of"}:
        matched = normalized_student in normalized_answers
        score = rule.max_score if matched else 0.0
        comment = "答案命中可接受答案" if matched else comment
    elif mode == "all_of":
        student_set = _choice_set(student_answer)
        standard_set = set()
        for answer in rule.acceptable_answers:
            standard_set.update(_choice_set(answer))
        matched = bool(standard_set) and student_set == standard_set
        score = rule.max_score if matched else 0.0
        comment = "答案集合完全一致" if matched else "答案集合不一致"
    elif mode == "partial":
        student_set = _choice_set(student_answer)
        standard_set = set()
        for answer in rule.acceptable_answers:
            standard_set.update(_choice_set(answer))
        wrong = student_set - standard_set
        correct = student_set & standard_set
        if wrong and rule.wrong_option_policy == "zero_if_any_wrong":
            score = 0.0
            comment = "存在错选项，按规则不得分"
        else:
            per_option = rule.partial_score_per_option or (rule.max_score / max(1, len(standard_set)))
            score = max(0.0, min(rule.max_score, len(correct) * per_option - len(wrong) * rule.penalty_wrong_option))
            comment = f"命中 {len(correct)} 个选项，错选 {len(wrong)} 个选项"
        matched = score >= rule.max_score
    elif mode == "numeric_tolerance":
        student_num = _to_number(student_answer)
        tolerance = float(rule.tolerance if rule.tolerance is not None else 0)
        for answer in rule.acceptable_answers:
            answer_num = _to_number(answer)
            if student_num is not None and answer_num is not None and abs(student_num - answer_num) <= tolerance:
                matched = True
                break
        score = rule.max_score if matched else 0.0
        comment = "数值答案在允许误差内" if matched else "数值答案超出允许误差"
    elif mode == "regex":
        for pattern in rule.acceptable_answers:
            flags = 0 if rule.case_sensitive else re.I
            if re.fullmatch(pattern, str(student_answer or "").strip(), flags=flags):
                matched = True
                break
        score = rule.max_score if matched else 0.0
        comment = "答案匹配正则规则" if matched else "答案未匹配正则规则"

    status = "correct" if score >= rule.max_score else "partial" if score > 0 else "wrong"
    return ObjectiveGradingItem(
        question_no=rule.question_no,
        question_type=rule.question_type,
        student_answer=student_answer,
        standard_answer=standard_text,
        score=round(score, 2),
        max_score=rule.max_score,
        is_correct=status == "correct",
        status=status,
        comment=comment,
    )


def grade_objective_answers(
    *,
    student_answers: dict[str, str],
    answer_rules: list[ObjectiveAnswerRule | dict],
) -> ObjectiveGradingResult:
    rules = [rule if isinstance(rule, ObjectiveAnswerRule) else ObjectiveAnswerRule.model_validate(rule) for rule in answer_rules]
    items = [_grade_rule(student_answers.get(rule.question_no, ""), rule) for rule in rules]
    total = round(sum(item.score for item in items), 2)
    max_score = round(sum(item.max_score for item in items), 2)
    wrong = [item.question_no for item in items if item.status in {"wrong", "missing", "needs_review"}]
    correct_count = len([item for item in items if item.is_correct])
    return ObjectiveGradingResult(
        total_score=total,
        max_score=max_score,
        accuracy=round(correct_count / len(items), 4) if items else 0,
        items=items,
        wrong_question_nos=wrong,
        needs_teacher_review=bool(wrong),
    )
