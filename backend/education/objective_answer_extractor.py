"""Extract objective answers and answer-key rules from text or images."""

import base64
import json
import mimetypes
import os
import re
from typing import Any

from pydantic import BaseModel, Field

from education.objective_grader import ObjectiveAnswerRule

API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("VISION_MODEL") or os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")

_vision_model = None


class ObjectiveAnswerExtraction(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    confidence: dict[str, float] = Field(default_factory=dict)
    needs_review: list[str] = Field(default_factory=list)
    raw_text: str = ""
    warnings: list[str] = Field(default_factory=list)


class ObjectiveAnswerRuleExtraction(BaseModel):
    answer_rules: list[ObjectiveAnswerRule] = Field(default_factory=list)
    raw_text: str = ""
    warnings: list[str] = Field(default_factory=list)
    needs_teacher_confirmation: bool = True


def _parse_json_object(text: str) -> dict:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.S)
    if fenced:
        raw = fenced.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    return json.loads(raw)


def _get_vision_model():
    global _vision_model
    try:
        from dotenv import load_dotenv
        from langchain.chat_models import init_chat_model
    except ImportError as e:
        raise RuntimeError("图片识别依赖未安装，请安装 python-dotenv 与 langchain") from e

    load_dotenv()
    api_key = os.getenv("ARK_API_KEY") or API_KEY
    model_name = os.getenv("VISION_MODEL") or os.getenv("MODEL") or MODEL
    base_url = os.getenv("BASE_URL") or BASE_URL
    if not api_key or not model_name:
        return None
    if _vision_model is None:
        _vision_model = init_chat_model(
            model=model_name,
            model_provider="openai",
            api_key=api_key,
            base_url=base_url,
            temperature=0,
            stream_usage=True,
        )
    return _vision_model


def _extract_pairs_from_text(text: str) -> dict[str, str]:
    source = str(text or "").strip()
    if not source:
        return {}
    normalized = source.replace("\r\n", "\n")
    pattern = re.compile(
        r"(?P<no>\d+)\s*[\.、:：\)]\s*(?P<answer>.*?)(?=(?:\s+\d+\s*[\.、:：\)])|(?:\n\d+\s*[\.、:：\)])|$)",
        flags=re.S,
    )
    pairs = {}
    for match in pattern.finditer(normalized):
        no = match.group("no").strip()
        answer = match.group("answer").strip()
        answer = re.sub(r"^(答案|答)\s*[:：]?\s*", "", answer).strip()
        answer = answer.strip("。；;，,")
        if no and answer:
            pairs[no] = answer
    return pairs


def extract_objective_answers_from_text(text: str) -> ObjectiveAnswerExtraction:
    answers = _extract_pairs_from_text(text)
    return ObjectiveAnswerExtraction(
        answers=answers,
        confidence={question_no: 1.0 for question_no in answers},
        raw_text=text or "",
        warnings=[] if answers else ["未能从文本中提取到题号-答案"],
    )


def extract_answer_rules_from_text(text: str, *, default_score: float = 1) -> ObjectiveAnswerRuleExtraction:
    pairs = _extract_pairs_from_text(text)
    rules = []
    for question_no, answer in pairs.items():
        mode = "any_of"
        question_type = "single_choice"
        has_choice_separator = bool(re.search(r"[/|，,、；;]|\s+或\s*", answer))
        acceptable = [item.strip() for item in re.split(r"[/|，,、；;]|或", answer) if item.strip()]
        if (
            not has_choice_separator
            and len(answer.strip()) > 1
            and re.fullmatch(r"[A-Fa-f]+", answer.strip())
        ):
            question_type = "multiple_choice"
            mode = "all_of"
            acceptable = [answer.strip()]
        rules.append(
            ObjectiveAnswerRule(
                question_no=question_no,
                question_type=question_type,
                max_score=default_score,
                grading_mode=mode,
                acceptable_answers=acceptable or [answer],
            )
        )
    return ObjectiveAnswerRuleExtraction(
        answer_rules=rules,
        raw_text=text or "",
        warnings=[] if rules else ["未能从文本中提取到标准答案规则"],
        needs_teacher_confirmation=True,
    )


def _image_message(image_bytes: bytes, filename: str, prompt: str) -> list[dict[str, Any]]:
    mime_type = mimetypes.guess_type(filename or "")[0] or "image/png"
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
    ]


def _invoke_vision_json(image_bytes: bytes, filename: str, prompt: str) -> dict:
    model = _get_vision_model()
    if not model:
        raise RuntimeError("未配置可用的视觉模型，请设置 VISION_MODEL 或 MODEL 以及 ARK_API_KEY")
    response = model.invoke([{"role": "user", "content": _image_message(image_bytes, filename, prompt)}])
    return _parse_json_object(getattr(response, "content", response))


def extract_objective_answers_from_image(image_bytes: bytes, filename: str = "") -> ObjectiveAnswerExtraction:
    prompt = """
请从这张学生客观题作业/答题卡图片中提取题号和学生答案。只输出 JSON 对象：
{
  "answers": {"1": "A", "2": "C"},
  "confidence": {"1": 0.95, "2": 0.82},
  "needs_review": ["2"],
  "raw_text": "可选，识别到的原文",
  "warnings": []
}
不要批改，不要解释。
""".strip()
    payload = _invoke_vision_json(image_bytes, filename, prompt)
    return ObjectiveAnswerExtraction.model_validate(payload)


def extract_answer_rules_from_image(image_bytes: bytes, filename: str = "") -> ObjectiveAnswerRuleExtraction:
    prompt = """
请从这张标准答案/答案解析图片中提取客观题标准答案规则。只输出 JSON 对象：
{
  "answer_rules": [
    {
      "question_no": "1",
      "question_type": "single_choice",
      "max_score": 1,
      "grading_mode": "any_of",
      "acceptable_answers": ["A"]
    }
  ],
  "raw_text": "可选，识别到的原文",
  "warnings": [],
  "needs_teacher_confirmation": true
}
grading_mode 只能是 exact、any_of、all_of、partial、numeric_tolerance、regex。
如果图片中出现“或、/、均可”，使用 any_of；多选题默认 all_of；有误差要求时使用 numeric_tolerance 并填写 tolerance。
不要批改，不要解释。
""".strip()
    payload = _invoke_vision_json(image_bytes, filename, prompt)
    return ObjectiveAnswerRuleExtraction.model_validate(payload)
