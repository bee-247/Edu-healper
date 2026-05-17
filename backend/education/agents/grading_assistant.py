"""Grading reference Agent."""

from education.agents.base import ARTIFACT_MEMORY_PROMPT, BASE_PROMPT, TeacherAgentSpec
from education.artifact_tools import get_teacher_memory, save_teacher_artifact
from education.generation_tools import grade_answer_reference
from education.graph_tools import get_related_knowledge, search_knowledge_graph
from tools import search_textbook


def build_spec() -> TeacherAgentSpec:
    return TeacherAgentSpec(
        name="grading_assistant",
        description="Creates grading references, rubric-based feedback, and teacher review notes.",
        system_prompt=(
            BASE_PROMPT
            + ARTIFACT_MEMORY_PROMPT
            + "You are the Grading Reference Agent. Prefer grade_answer_reference for grading requests. "
            "Never present grading as an automatic final verdict; keep teacher review visible. "
            "If standard_answer or rubric is missing, lower confidence and explain the limitation. "
        ),
        tools=[
            search_textbook,
            search_knowledge_graph,
            get_related_knowledge,
            grade_answer_reference,
            save_teacher_artifact,
            get_teacher_memory,
        ],
    )
