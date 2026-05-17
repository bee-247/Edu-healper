"""Lesson planning Agent."""

from education.agents.base import ARTIFACT_MEMORY_PROMPT, BASE_PROMPT, TeacherAgentSpec
from education.artifact_tools import get_teacher_memory, save_teacher_artifact, update_teacher_memory
from education.generation_tools import generate_lesson_plan
from education.graph_tools import get_prerequisites, get_related_knowledge, get_teaching_path, search_knowledge_graph
from tools import search_textbook


def build_spec() -> TeacherAgentSpec:
    return TeacherAgentSpec(
        name="lesson_planner",
        description="Creates lesson plans, teaching flows, blackboard design, exercises, and homework.",
        system_prompt=(
            BASE_PROMPT
            + ARTIFACT_MEMORY_PROMPT
            + "You are the Lesson Planner Agent. Prefer generate_lesson_plan for lesson preparation, teaching design, "
            "classroom flow, board design, and homework planning. Use get_teaching_path and get_prerequisites "
            "to organize teaching order. Output should be directly usable by teachers. "
        ),
        tools=[
            search_textbook,
            search_knowledge_graph,
            get_related_knowledge,
            get_prerequisites,
            get_teaching_path,
            generate_lesson_plan,
            save_teacher_artifact,
            update_teacher_memory,
            get_teacher_memory,
        ],
    )
