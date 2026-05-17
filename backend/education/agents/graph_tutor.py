"""Knowledge graph teaching Agent."""

from education.agents.base import BASE_PROMPT, TEXTBOOK_GROUNDING_PROMPT, TeacherAgentSpec
from education.graph_tools import get_prerequisites, get_related_knowledge, get_teaching_path, search_knowledge_graph
from tools import search_textbook


def build_spec() -> TeacherAgentSpec:
    return TeacherAgentSpec(
        name="graph_tutor",
        description="Explains prerequisite knowledge, related concepts, and teaching paths.",
        system_prompt=(
            BASE_PROMPT
            + TEXTBOOK_GROUNDING_PROMPT
            + "You are the Knowledge Graph Agent. Prefer graph tools for prerequisite, relation, teaching-path, "
            "and misconception analysis. Use textbook search only when the teacher needs original textbook evidence. "
        ),
        tools=[
            search_textbook,
            search_knowledge_graph,
            get_related_knowledge,
            get_prerequisites,
            get_teaching_path,
        ],
    )
