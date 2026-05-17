"""Question generation Agent."""

from education.agents.base import ARTIFACT_MEMORY_PROMPT, BASE_PROMPT, TeacherAgentSpec
from education.artifact_tools import get_teacher_memory, save_teacher_artifact, update_teacher_memory
from education.generation_tools import generate_questions
from education.graph_tools import get_prerequisites, get_related_knowledge, search_knowledge_graph
from tools import search_textbook


def build_spec() -> TeacherAgentSpec:
    return TeacherAgentSpec(
        name="question_generator",
        description="Creates exercises, tests, homework questions, answers, analysis, and rubrics.",
        system_prompt=(
            BASE_PROMPT
            + ARTIFACT_MEMORY_PROMPT
            + "You are the Question Generator Agent. Prefer generate_questions for exercise, test, "
            "homework, and question-set requests. Use graph tools only to clarify prerequisites, related concepts, "
            "or misconception coverage. Always output stem, answer, analysis, rubric, difficulty, knowledge_tags, "
            "and source_chunk_ids. "
        ),
        tools=[
            search_textbook,
            search_knowledge_graph,
            get_related_knowledge,
            get_prerequisites,
            generate_questions,
            save_teacher_artifact,
            update_teacher_memory,
            get_teacher_memory,
        ],
    )
