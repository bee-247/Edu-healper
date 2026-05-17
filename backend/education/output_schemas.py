"""Pydantic contracts for teacher-facing generated content."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class QuestionItem(BaseModel):
    stem: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    analysis: str = ""
    rubric: str = ""
    difficulty: str = "medium"
    knowledge_tags: list[str] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)

    @field_validator("knowledge_tags", "source_chunk_ids", mode="before")
    @classmethod
    def _listify(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []


class QuestionSetOutput(BaseModel):
    questions: list[QuestionItem] = Field(..., min_length=1)
    limitation: str = ""
    source_chunk_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _collect_sources(self):
        ids = list(self.source_chunk_ids)
        for question in self.questions:
            ids.extend(question.source_chunk_ids)
        self.source_chunk_ids = list(dict.fromkeys([item for item in ids if item]))
        return self


class LessonPlanOutput(BaseModel):
    title: str = Field(..., min_length=1)
    lesson_duration_minutes: int = Field(default=45, ge=1, le=240)
    sections: dict[str, str] = Field(default_factory=dict)
    source_chunk_ids: list[str] = Field(default_factory=list)
    limitation: str = ""

    @field_validator("sections", mode="before")
    @classmethod
    def _sections_dict(cls, value):
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        return {"内容": str(value or "")}


class CriterionScore(BaseModel):
    criterion_id: str = ""
    description: str = ""
    max_score: float = Field(default=0, ge=0)
    awarded_score: float = Field(default=0, ge=0)
    status: Literal["matched", "partial", "missing"] = "missing"
    evidence: str = ""
    comment: str = ""

    @model_validator(mode="after")
    def _clamp_awarded(self):
        if self.awarded_score > self.max_score:
            self.awarded_score = self.max_score
        return self


class GradingReferenceOutput(BaseModel):
    score: float = Field(default=0, ge=0)
    max_score: float = Field(default=10, gt=0)
    criteria_scores: list[CriterionScore] = Field(default_factory=list)
    matched_points: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)
    error_analysis: str = ""
    feedback: str = ""
    related_knowledge: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    needs_teacher_review: bool = True
    source_chunk_ids: list[str] = Field(default_factory=list)
    grading_mode: str = "llm_rubric"
    limitation: str = ""

    @model_validator(mode="after")
    def _normalize_total(self):
        if self.criteria_scores:
            self.score = round(sum(item.awarded_score for item in self.criteria_scores), 2)
        if self.score > self.max_score:
            self.score = self.max_score
        return self
