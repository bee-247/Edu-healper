"""General teacher assistant Agent."""

from education.agents.base import ARTIFACT_MEMORY_PROMPT, BASE_PROMPT, TEXTBOOK_GROUNDING_PROMPT, TeacherAgentSpec
from education.artifact_tools import get_teacher_memory, save_teacher_artifact, update_teacher_memory
from education.graph_tools import search_knowledge_graph
from tools import search_textbook


def build_spec() -> TeacherAgentSpec:
    return TeacherAgentSpec(
        name="general",
        description="General teacher assistant for chat, textbook QA, and mixed requests.",
        system_prompt=(
            BASE_PROMPT
            + TEXTBOOK_GROUNDING_PROMPT
            + ARTIFACT_MEMORY_PROMPT
            + "Use search_knowledge_graph only when the teacher asks about structured knowledge relations. "
        ),
        tools=[
            search_textbook,
            search_knowledge_graph,
            save_teacher_artifact,
            update_teacher_memory,
            get_teacher_memory,
        ],
    )
